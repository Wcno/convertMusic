"""YouTube -> MP3 conversion.

Public interface:
    normalize_url(raw) / normalize_quality(raw)   validate user input
    fetch_info(url)                               metadata for the preview
    convert(url, quality)                         run one conversion, yielding
                                                  Progress events and one Result
    ConversionError                               message safe to show the user

Everything else (yt-dlp options, FFmpeg/Deno discovery, cookies, proxy,
threads, temporary folders, error wording) stays inside this module.
Each conversion is self-contained, so it works on a long-running local server
and on a stateless serverless function (Vercel) alike.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import parse_qs, urlparse

import yt_dlp

ALLOWED_QUALITIES = ("128", "192", "320")
DEFAULT_QUALITY = "192"

ON_VERCEL = bool(os.environ.get("VERCEL"))
# Vercel stops a request after 300 s, so long videos are refused up front there.
MAX_DURATION = int(os.environ.get("MAX_DURATION_SECONDS") or (30 * 60 if ON_VERCEL else 3 * 3600))

# Share of the overall progress bar used by the server side. The browser fills
# the rest while the finished MP3 travels to it.
DOWNLOAD_SHARE = 0.8

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class ConversionError(Exception):
    """An error whose message is safe to show to the user (Spanish)."""


@dataclass(frozen=True)
class Progress:
    stage: str  # "download" or "convert"
    fraction: float  # 0..DOWNLOAD_SHARE of the overall bar


@dataclass(frozen=True)
class Result:
    path: Path  # exists until the convert() generator finishes or is closed
    filename: str
    title: str
    size: int


# A downloader turns (url, quality, out_dir, report) into (mp3 path, info).
# `report(stage, fraction_or_None)` is called from the downloader's thread.
Downloader = Callable[[str, str, Path, Callable[[str, "float | None"], None]], "tuple[Path, dict]"]


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #

def normalize_url(raw: str | None) -> str:
    """Return a canonical watch URL for a YouTube video, or raise ConversionError."""
    url = (raw or "").strip()
    if not url:
        raise ConversionError("Pega un enlace de YouTube para empezar.")
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in _YOUTUBE_HOSTS:
        raise ConversionError("Ese enlace no es de YouTube. Usa un enlace de youtube.com o youtu.be.")

    video_id = None
    if host.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    else:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            video_id = parts[1]

    if not video_id or not _VIDEO_ID.match(video_id):
        raise ConversionError("No encuentro un video en ese enlace. Copia el enlace de un video concreto.")
    return f"https://www.youtube.com/watch?v={video_id}"


def normalize_quality(raw: str | None) -> str:
    quality = (raw or DEFAULT_QUALITY).strip().lower().removesuffix("k")
    if quality not in ALLOWED_QUALITIES:
        raise ConversionError("Calidad no válida. Elige 128, 192 o 320 kbps.")
    return quality


def safe_filename(title: str) -> str:
    """A filename that works on Windows, macOS and Linux."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip(" .")
    return (name or "audio")[:150] + ".mp3"


def friendly_error(exc: BaseException) -> str:
    """Turn yt-dlp / FFmpeg failures into a short message with a next step."""
    if isinstance(exc, ConversionError):
        return str(exc)
    text = str(exc).lower()
    if "not a bot" in text:
        return ("YouTube bloqueó la descarga desde este servidor (pide verificación anti-bots). "
                "Configura cookies o un proxy: lo explica el README.")
    if "429" in text or "too many requests" in text:
        return "YouTube está limitando las descargas desde este servidor. Espera unos minutos."
    if "ffmpeg" in text or "ffprobe" in text:
        return "No se encontró FFmpeg en el servidor. Instálalo y vuelve a intentarlo."
    if "private" in text:
        return "Este video es privado."
    if "sign in" in text or ("age" in text and "confirm" in text):
        return "YouTube pide iniciar sesión para este video (restricción de edad o similar)."
    if "unavailable" in text or "not available" in text or "removed" in text:
        return "Este video no está disponible."
    if "urlopen" in text or "timed out" in text or "network" in text or "getaddrinfo" in text:
        return "No hay conexión con YouTube. Revisa tu internet e inténtalo de nuevo."
    return "YouTube no dejó descargar este video. Prueba con otro enlace o actualiza yt-dlp."


# --------------------------------------------------------------------------- #
# yt-dlp setup
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _ffmpeg_location() -> str | None:
    """FFMPEG_LOCATION, else FFmpeg on PATH, else the copy bundled by imageio-ffmpeg."""
    if os.environ.get("FFMPEG_LOCATION"):
        return os.environ["FFMPEG_LOCATION"]
    if shutil.which("ffmpeg"):
        return None  # yt-dlp finds it on its own
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - optional dependency
        return None


@lru_cache(maxsize=1)
def _deno_path() -> str | None:
    """Deno solves YouTube's JavaScript challenges. The pip package ships a binary."""
    try:
        import deno
        return deno.find_deno_bin()
    except Exception:  # noqa: BLE001 - optional dependency
        return shutil.which("deno")


@lru_cache(maxsize=1)
def _cookie_file() -> str | None:
    """YTDLP_COOKIES holds the text of a cookies.txt file (for servers YouTube blocks)."""
    cookies = os.environ.get("YTDLP_COOKIES", "").strip()
    if not cookies:
        return None
    fd, path = tempfile.mkstemp(prefix="convertmusic-cookies-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(cookies.replace("\\n", "\n") + "\n")
    return path


def _base_options() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True}
    if ffmpeg := _ffmpeg_location():
        opts["ffmpeg_location"] = ffmpeg
    if deno := _deno_path():
        opts["js_runtimes"] = {"deno": {"path": deno}}
    if cookies := _cookie_file():
        opts["cookiefile"] = cookies
    if proxy := os.environ.get("YTDLP_PROXY"):
        opts["proxy"] = proxy
    return opts


def _check_duration(info: dict) -> None:
    duration = info.get("duration") or 0
    if info.get("is_live"):
        raise ConversionError("Los directos no se pueden convertir. Espera a que termine la emisión.")
    if duration > MAX_DURATION:
        raise ConversionError(f"El video es demasiado largo. El máximo aquí es {MAX_DURATION // 60} minutos.")


def fetch_info(url: str) -> dict:
    """Metadata for the preview: title, channel, duration, thumbnail."""
    try:
        with yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(friendly_error(exc)) from exc
    _check_duration(info)
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Sin título",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "url": url,
    }


def ytdlp_downloader(url: str, quality: str, out_dir: Path, report) -> tuple[Path, dict]:
    """The real downloader: fetch the best audio and encode it to MP3 with FFmpeg."""

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            report("download", (d.get("downloaded_bytes") or 0) / total if total else None)
        elif d.get("status") == "finished":
            report("convert", None)

    opts = {
        **_base_options(),
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [lambda d: d.get("status") == "started" and report("convert", None)],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality},
            {"key": "FFmpegMetadata"},
        ],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        _check_duration(info)
        info = ydl.process_ie_result(info, download=True)

    mp3s = sorted(out_dir.glob("*.mp3"))
    if not mp3s:
        raise ConversionError("La conversión terminó sin generar un MP3.")
    return mp3s[0], info


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

_FINISHED = object()


def convert(url: str, quality: str, *, downloader: Downloader | None = None) -> Iterator[Progress | Result]:
    """Run one conversion in its own temporary folder.

    Yields Progress events (monotonic, at most one per half percent) and then
    exactly one Result. The file at Result.path is deleted when the generator
    finishes or is closed, so read it before advancing past the Result.
    Failures raise ConversionError with a user-facing message.
    """
    downloader = downloader or ytdlp_downloader
    work_dir = Path(tempfile.mkdtemp(prefix="convertmusic-"))
    events: queue.Queue = queue.Queue()
    outcome: dict = {}

    def report(stage: str, fraction: float | None) -> None:
        events.put((stage, fraction))

    def run() -> None:
        try:
            outcome["value"] = downloader(url, quality, work_dir, report)
        except BaseException as exc:  # noqa: BLE001 - handed to the caller below
            outcome["error"] = exc
        finally:
            events.put(_FINISHED)

    threading.Thread(target=run, daemon=True).start()
    try:
        stage, level = "download", 0.0
        yield Progress(stage, level)
        while (event := events.get()) is not _FINISHED:
            new_stage, fraction = event
            if new_stage == "convert":
                new_level = DOWNLOAD_SHARE
            elif fraction is None:
                new_level = level
            else:
                new_level = max(0.0, min(fraction, 1.0)) * DOWNLOAD_SHARE
            new_level = max(level, new_level)
            if new_stage != stage or new_level - level >= 0.005:
                stage, level = new_stage, new_level
                yield Progress(stage, round(level, 4))

        if "error" in outcome:
            raise ConversionError(friendly_error(outcome["error"])) from outcome["error"]
        path, info = outcome["value"]
        path = Path(path)
        title = (info or {}).get("title") or "audio"
        yield Result(path=path, filename=safe_filename(title), title=title, size=path.stat().st_size)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
