"""YouTube -> MP3 / MP4 conversion.

Public interface:
    normalize_url(raw)                 validate a YouTube link
    normalize_format(raw)              "mp3" or "mp4"
    normalize_quality(raw, fmt)        tier ("low"/"std"/"high") or explicit value
                                       -> "128"/"192"/"320" (mp3) or "360"/"720"/"1080" (mp4)
    fetch_info(url)                    metadata for the preview
    convert(url, fmt, quality)         run one conversion, yielding Progress
                                       events and then exactly one Result
    ConversionError                    message safe to show the user

Everything else (yt-dlp options, FFmpeg/Deno discovery, cookies, proxy,
threads, temporary folders, error wording) stays inside this module.
Each conversion is self-contained, so it works on a long-running local server
and on a stateless serverless function (Vercel) alike.
"""

from __future__ import annotations

import base64
import binascii
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

FORMATS = ("mp3", "mp4")
DEFAULT_FORMAT = "mp3"
TIERS = ("low", "std", "high")
DEFAULT_TIER = "std"
QUALITIES = {
    "mp3": {"low": "128", "std": "192", "high": "320"},  # kbps
    "mp4": {"low": "360", "std": "720", "high": "1080"},  # max video height
}
MIME_TYPES = {"mp3": "audio/mpeg", "mp4": "video/mp4"}

ON_VERCEL = bool(os.environ.get("VERCEL"))
# Vercel stops a request after maxDuration (60 s) and /tmp holds ~500 MB,
# so long videos are refused up front there. Video is far larger than audio.
MAX_DURATION = {
    "mp3": int(os.environ.get("MAX_DURATION_SECONDS") or (15 * 60 if ON_VERCEL else 3 * 3600)),
    "mp4": int(os.environ.get("MAX_VIDEO_DURATION_SECONDS") or (10 * 60 if ON_VERCEL else 3 * 3600)),
}

# Share of the overall progress bar used by the server side. The browser fills
# the rest while the finished file travels to it.
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
    mimetype: str


# A downloader turns (url, fmt, quality, out_dir, report) into (file path, info).
# `report(stage, fraction_or_None)` is called from the downloader's thread.
Downloader = Callable[[str, str, str, Path, Callable[[str, "float | None"], None]], "tuple[Path, dict]"]


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


def normalize_format(raw: str | None) -> str:
    fmt = (raw or DEFAULT_FORMAT).strip().lower().lstrip(".")
    if fmt not in FORMATS:
        raise ConversionError("Formato no válido. Elige MP3 (audio) o MP4 (video).")
    return fmt


def normalize_quality(raw: str | None, fmt: str = DEFAULT_FORMAT) -> str:
    """Accept a tier (low/std/high) or an explicit value such as 320, 320k, 720 or 720p."""
    table = QUALITIES[fmt]
    value = (raw or DEFAULT_TIER).strip().lower()
    if value in table:
        return table[value]
    value = value.removesuffix("kbps").removesuffix("k").removesuffix("p")
    if value in table.values():
        return value
    options = ", ".join(table.values())
    unit = "kbps" if fmt == "mp3" else "p"
    raise ConversionError(f"Calidad no válida para {fmt.upper()}. Elige {options} {unit}.")


def safe_filename(title: str, ext: str = "mp3") -> str:
    """A filename that works on Windows, macOS and Linux."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip(" .")
    return f"{(name or 'audio')[:150]}.{ext}"


def friendly_error(exc: BaseException) -> str:
    """Turn yt-dlp / FFmpeg failures into a short message with a next step."""
    if isinstance(exc, ConversionError):
        return str(exc)
    text = str(exc).lower()
    if "not a bot" in text and _cookie_text():
        return ("YouTube rechazó las cookies del servidor (seguramente caducaron). "
                "Exporta unas nuevas y actualiza YTDLP_COOKIES en Vercel. Lo explica el README.")
    if "not a bot" in text:
        return ("YouTube no permite descargas desde servidores en la nube como este. "
                "Usa la app en tu ordenador (python app.py) o añade tus cookies de YouTube "
                "en el servidor (variable YTDLP_COOKIES). Lo explica el README.")
    if "429" in text or "too many requests" in text:
        return "YouTube está limitando las descargas desde este servidor. Espera unos minutos."
    if "ffmpeg" in text or "ffprobe" in text:
        return "No se encontró FFmpeg en el servidor. Instálalo y vuelve a intentarlo."
    if "private" in text:
        return "Este video es privado."
    if "sign in" in text or ("age" in text and "confirm" in text):
        return "YouTube pide iniciar sesión para este video (restricción de edad o similar)."
    if "requested format is not available" in text:
        return "Este video no está disponible en esa calidad. Prueba con una calidad menor."
    if "unavailable" in text or "not available" in text or "removed" in text:
        return "Este video no está disponible."
    if "no space left" in text:
        return "El archivo es demasiado grande para este servidor. Prueba con menos calidad o un video más corto."
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
    """Deno runs YouTube's player JavaScript for yt-dlp. The pip package ships a binary."""
    try:
        import deno
        return deno.find_deno_bin()
    except Exception:  # noqa: BLE001 - optional dependency
        return shutil.which("deno")


def _cookie_text() -> str | None:
    """The owner's cookies.txt from YTDLP_COOKIES: plain text or base64 (scripts/prepare_cookies.py)."""
    raw = os.environ.get("YTDLP_COOKIES", "").strip()
    if not raw:
        return None
    if "\t" not in raw and "\\t" not in raw:
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    text = raw.replace("\\n", "\n").replace("\\t", "\t")
    lines = [line for line in text.splitlines() if line.count("\t") >= 6]
    return text if lines else None


@lru_cache(maxsize=1)
def _cookie_file() -> str | None:
    """Write the cookies to a temporary file for yt-dlp (it also saves refreshed cookies there)."""
    text = _cookie_text()
    if not text:
        return None
    fd, path = tempfile.mkstemp(prefix="convertmusic-cookies-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        if not text.startswith("# Netscape"):
            fh.write("# Netscape HTTP Cookie File\n")
        fh.write(text.rstrip("\n") + "\n")
    return path


def status() -> dict:
    """What this server has available, without revealing any secret."""
    text = _cookie_text() or ""
    return {
        "cookies": bool(text),
        "cookies_signed_in": any(name in text for name in ("LOGIN_INFO", "__Secure-3PSID", "SAPISID")),
        "ffmpeg": bool(_ffmpeg_location() or shutil.which("ffmpeg")),
        "deno": bool(_deno_path()),
        "vercel": ON_VERCEL,
        "yt_dlp": yt_dlp.version.__version__,
    }


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


def _check_duration(info: dict, fmt: str = DEFAULT_FORMAT) -> None:
    if info.get("is_live"):
        raise ConversionError("Los directos no se pueden convertir. Espera a que termine la emisión.")
    limit = MAX_DURATION[fmt]
    if (info.get("duration") or 0) > limit:
        raise ConversionError(
            f"El video es demasiado largo para {fmt.upper()}. El máximo aquí es {limit // 60} minutos.")


def _format_options(fmt: str, quality: str) -> dict:
    if fmt == "mp3":
        return {
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality},
                {"key": "FFmpegMetadata"},
            ],
        }
    height = int(quality)
    return {
        # Best video up to the chosen height plus best audio, merged into MP4.
        # H.264 + AAC are preferred so the file plays everywhere.
        "format": f"bv*[height<={height}]+ba/b[height<={height}]/b",
        "format_sort": [f"res:{height}", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegMetadata"}],
    }


def fetch_info(url: str) -> dict:
    """Metadata for the preview: title, channel, duration, thumbnail."""
    try:
        with yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(friendly_error(exc)) from exc
    if info.get("is_live"):
        raise ConversionError("Los directos no se pueden convertir. Espera a que termine la emisión.")
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Sin título",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "url": url,
    }


def ytdlp_downloader(url: str, fmt: str, quality: str, out_dir: Path, report) -> tuple[Path, dict]:
    """The real downloader: fetch from YouTube and produce an MP3 or MP4 with FFmpeg."""
    # MP4 downloads two streams (video, then audio); weight them by typical size.
    weights = [0.85, 0.15] if fmt == "mp4" else [1.0]
    parts: list[str] = []

    def progress_hook(d):
        name = d.get("filename") or d.get("tmpfilename") or ""
        if name and name not in parts:
            parts.append(name)
        index = min(max(len(parts) - 1, 0), len(weights) - 1)
        done_share = sum(weights[:index])
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            fraction = (d.get("downloaded_bytes") or 0) / total if total else None
            report("download", None if fraction is None else done_share + weights[index] * fraction)
        elif d.get("status") == "finished":
            report("download", done_share + weights[index])

    def postprocessor_hook(d):
        if d.get("status") == "started":
            report("convert", None)

    opts = {
        **_base_options(),
        **_format_options(fmt, quality),
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        _check_duration(info, fmt)
        info = ydl.process_ie_result(info, download=True)

    files = sorted(out_dir.glob(f"*.{fmt}"))
    if not files:
        raise ConversionError(f"La conversión terminó sin generar un {fmt.upper()}.")
    return files[0], info


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

_FINISHED = object()


def convert(url: str, fmt: str, quality: str, *, downloader: Downloader | None = None) -> Iterator[Progress | Result]:
    """Run one conversion in its own temporary folder.

    `fmt` and `quality` must already be normalized. Yields Progress events
    (monotonic, at most one per half percent) and then exactly one Result.
    The file at Result.path is deleted when the generator finishes or is
    closed, so read it before advancing past the Result. Failures raise
    ConversionError with a user-facing message.
    """
    downloader = downloader or ytdlp_downloader
    work_dir = Path(tempfile.mkdtemp(prefix="convertmusic-"))
    events: queue.Queue = queue.Queue()
    outcome: dict = {}

    def report(stage: str, fraction: float | None) -> None:
        events.put((stage, fraction))

    def run() -> None:
        try:
            outcome["value"] = downloader(url, fmt, quality, work_dir, report)
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
        title = (info or {}).get("title") or ("audio" if fmt == "mp3" else "video")
        yield Result(
            path=path,
            filename=safe_filename(title, fmt),
            title=title,
            size=path.stat().st_size,
            mimetype=MIME_TYPES[fmt],
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
