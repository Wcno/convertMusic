import os
import tempfile
from pathlib import Path

import pytest

import converter
from converter import ConversionError, Progress, Result
from conftest import FAKE_MP3_SIZE, failing_downloader, fake_downloader

WATCH = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


# ----------------------------------------------------------------- input

@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "http://youtube.com/watch?v=jNQXAC9IVRw&t=10s",
        "https://m.youtube.com/watch?v=jNQXAC9IVRw",
        "https://music.youtube.com/watch?v=jNQXAC9IVRw&list=RD123",
        "https://youtu.be/jNQXAC9IVRw?si=abc",
        "https://www.youtube.com/shorts/jNQXAC9IVRw",
        "https://www.youtube.com/embed/jNQXAC9IVRw",
        "youtu.be/jNQXAC9IVRw",
        "  https://www.youtube.com/watch?v=jNQXAC9IVRw  ",
    ],
)
def test_normalize_url_accepts_youtube_videos(raw):
    assert converter.normalize_url(raw) == WATCH


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "   ",
        "https://vimeo.com/123456",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/playlist?list=PL123",
        "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw",
        "ftp://youtube.com/watch?v=jNQXAC9IVRw",
        "javascript:alert(1)",
    ],
)
def test_normalize_url_rejects_everything_else(raw):
    with pytest.raises(ConversionError):
        converter.normalize_url(raw)


@pytest.mark.parametrize("raw, expected", [(None, "192"), ("128", "128"), ("320k", "320"), (" 192 ", "192")])
def test_normalize_quality(raw, expected):
    assert converter.normalize_quality(raw) == expected


@pytest.mark.parametrize("raw", ["64", "999", "best", "-1"])
def test_normalize_quality_rejects_unknown(raw):
    with pytest.raises(ConversionError):
        converter.normalize_quality(raw)


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Dei V - Martini (Official Video)", "Dei V - Martini (Official Video).mp3"),
        ('AC/DC: "Back in Black"?', "ACDC Back in Black.mp3"),
        ("   ...   ", "audio.mp3"),
        ("Canción ñandú 🎵", "Canción ñandú 🎵.mp3"),
    ],
)
def test_safe_filename(title, expected):
    assert converter.safe_filename(title) == expected


def test_safe_filename_is_bounded():
    assert len(converter.safe_filename("x" * 500)) == 154


@pytest.mark.parametrize(
    "message, fragment",
    [
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot.", "anti-bots"),
        ("HTTP Error 429: Too Many Requests", "limitando"),
        ("ERROR: Postprocessing: ffprobe and ffmpeg not found", "FFmpeg"),
        ("ERROR: [youtube] x: Private video", "privado"),
        ("ERROR: Sign in to confirm your age", "iniciar sesión"),
        ("ERROR: [youtube] x: Video unavailable", "no está disponible"),
        ("<urlopen error [Errno 11001] getaddrinfo failed>", "conexión"),
        ("something odd", "actualiza yt-dlp"),
    ],
)
def test_friendly_error(message, fragment):
    assert fragment in converter.friendly_error(RuntimeError(message))


def test_duration_limit(monkeypatch):
    monkeypatch.setattr(converter, "MAX_DURATION", 600)
    converter._check_duration({"duration": 600})
    with pytest.raises(ConversionError, match="10 minutos"):
        converter._check_duration({"duration": 601})
    with pytest.raises(ConversionError, match="directos"):
        converter._check_duration({"duration": None, "is_live": True})


def test_fetch_info_wraps_failures(monkeypatch):
    class Boom:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download): raise RuntimeError("ERROR: Private video")

    monkeypatch.setattr(converter.yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(ConversionError, match="privado"):
        converter.fetch_info(WATCH)


# ---------------------------------------------------------------- options

def test_options_use_env_and_bundled_tools(monkeypatch):
    for fn in (converter._ffmpeg_location, converter._deno_path, converter._cookie_file):
        fn.cache_clear()
    monkeypatch.setenv("FFMPEG_LOCATION", r"C:\ffmpeg\bin")
    monkeypatch.setenv("YTDLP_PROXY", "http://proxy:8080")
    monkeypatch.setenv("YTDLP_COOKIES", "# Netscape HTTP Cookie File\\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc")
    try:
        opts = converter._base_options()
        assert opts["ffmpeg_location"] == r"C:\ffmpeg\bin"
        assert opts["proxy"] == "http://proxy:8080"
        assert opts["noplaylist"] is True
        assert Path(opts["js_runtimes"]["deno"]["path"]).exists()
        cookies = Path(opts["cookiefile"]).read_text(encoding="utf-8")
        assert cookies.splitlines()[1].startswith(".youtube.com")
        os.remove(opts["cookiefile"])
    finally:
        for fn in (converter._ffmpeg_location, converter._deno_path, converter._cookie_file):
            fn.cache_clear()


def test_bundled_ffmpeg_is_used_when_none_installed(monkeypatch):
    converter._ffmpeg_location.cache_clear()
    monkeypatch.delenv("FFMPEG_LOCATION", raising=False)
    monkeypatch.setattr(converter.shutil, "which", lambda name: None)
    try:
        assert Path(converter._ffmpeg_location()).exists()
    finally:
        converter._ffmpeg_location.cache_clear()


# ------------------------------------------------------------- conversion

def test_convert_yields_progress_then_result_and_cleans_up():
    events = converter.convert(WATCH, "320", downloader=fake_downloader)
    progress = []
    for event in events:
        if isinstance(event, Progress):
            progress.append((event.stage, event.fraction))
        else:
            result = event
            assert result.path.exists()  # readable while the generator is paused here
            assert result.path.read_bytes().startswith(b"ID3320")
            folder = result.path.parent

    assert progress == [("download", 0.0), ("download", 0.4), ("download", 0.8), ("convert", 0.8)]
    assert result.filename == "Me at the zoo.mp3"
    assert result.title == "Me at the zoo"
    assert result.size == FAKE_MP3_SIZE
    assert not folder.exists()


def test_convert_progress_is_monotonic_and_throttled():
    def chatty(url, quality, out_dir, report):
        for i in range(1000):
            report("download", i / 1000)
        report("download", 0.1)  # going backwards is ignored
        p = out_dir / "a.mp3"
        p.write_bytes(b"x")
        return p, {"title": "t"}

    fractions = [e.fraction for e in converter.convert(WATCH, "192", downloader=chatty) if isinstance(e, Progress)]
    assert fractions == sorted(fractions)
    assert len(fractions) < 200


def test_convert_failure_raises_friendly_error_and_cleans_up(monkeypatch):
    before = set(Path(tempfile.gettempdir()).glob("convertmusic-*"))
    with pytest.raises(ConversionError, match="no está disponible"):
        list(converter.convert(WATCH, "192", downloader=failing_downloader))
    assert set(Path(tempfile.gettempdir()).glob("convertmusic-*")) == before


def test_closing_early_removes_files():
    events = converter.convert(WATCH, "192", downloader=fake_downloader)
    result = next(e for e in events if isinstance(e, Result))
    events.close()
    assert not result.path.parent.exists()


def test_downloader_without_mp3_is_an_error():
    def nothing(url, quality, out_dir, report):
        raise ConversionError("La conversión terminó sin generar un MP3.")

    with pytest.raises(ConversionError, match="sin generar"):
        list(converter.convert(WATCH, "192", downloader=nothing))


@pytest.mark.skipif(not os.environ.get("RUN_NETWORK_TESTS"), reason="set RUN_NETWORK_TESTS=1 to hit YouTube")
def test_real_conversion():
    """End to end against YouTube: needs internet. FFmpeg and Deno come from pip."""
    info = converter.fetch_info(WATCH)
    assert info["title"] == "Me at the zoo"

    for event in converter.convert(WATCH, "128"):
        if isinstance(event, Result):
            assert event.filename == "Me at the zoo.mp3"
            assert event.size > 50_000
            assert event.path.read_bytes()[:3] in (b"ID3", b"\xff\xfb")
