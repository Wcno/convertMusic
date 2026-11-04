import os
import tempfile
from pathlib import Path

import pytest

import converter
from converter import ConversionError, Progress, Result
from conftest import FAKE_FILE_SIZE, failing_downloader, fake_downloader

WATCH = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


# ----------------------------------------------------------------- links

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
        "https://www.youtube.com/live/jNQXAC9IVRw",
        "youtu.be/jNQXAC9IVRw",
        "WWW.YOUTUBE.COM/watch?v=jNQXAC9IVRw",
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
        "https://www.youtube.com/@channel",
        "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw",
        "ftp://youtube.com/watch?v=jNQXAC9IVRw",
        "javascript:alert(1)",
        "not a link at all",
    ],
)
def test_normalize_url_rejects_everything_else(raw):
    with pytest.raises(ConversionError):
        converter.normalize_url(raw)


# --------------------------------------------------------- format/quality

@pytest.mark.parametrize("raw, expected", [(None, "mp3"), ("MP3", "mp3"), ("mp4", "mp4"), (" .mp4 ", "mp4")])
def test_normalize_format(raw, expected):
    assert converter.normalize_format(raw) == expected


@pytest.mark.parametrize("raw", ["webm", "wav", "avi", "mp5"])
def test_normalize_format_rejects_unknown(raw):
    with pytest.raises(ConversionError, match="MP3"):
        converter.normalize_format(raw)


@pytest.mark.parametrize(
    "raw, fmt, expected",
    [
        (None, "mp3", "192"), ("low", "mp3", "128"), ("high", "mp3", "320"),
        ("128", "mp3", "128"), ("320k", "mp3", "320"), (" 192kbps ", "mp3", "192"),
        (None, "mp4", "720"), ("low", "mp4", "360"), ("high", "mp4", "1080"),
        ("720p", "mp4", "720"), ("1080", "mp4", "1080"),
    ],
)
def test_normalize_quality(raw, fmt, expected):
    assert converter.normalize_quality(raw, fmt) == expected


@pytest.mark.parametrize(
    "raw, fmt",
    [("64", "mp3"), ("999", "mp3"), ("best", "mp3"), ("-1", "mp3"), ("720", "mp3"),
     ("192", "mp4"), ("4k", "mp4"), ("2160", "mp4")],
)
def test_normalize_quality_rejects_unknown(raw, fmt):
    with pytest.raises(ConversionError, match=fmt.upper()):
        converter.normalize_quality(raw, fmt)


@pytest.mark.parametrize(
    "title, ext, expected",
    [
        ("Dei V - Martini (Official Video)", "mp3", "Dei V - Martini (Official Video).mp3"),
        ('AC/DC: "Back in Black"?', "mp3", "ACDC Back in Black.mp3"),
        ("   ...   ", "mp3", "audio.mp3"),
        ("Canción ñandú 🎵", "mp3", "Canción ñandú 🎵.mp3"),
        ("Clip", "mp4", "Clip.mp4"),
    ],
)
def test_safe_filename(title, ext, expected):
    assert converter.safe_filename(title, ext) == expected


def test_safe_filename_is_bounded():
    assert len(converter.safe_filename("x" * 500)) == 154


@pytest.mark.parametrize(
    "message, fragment",
    [
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot.", "YTDLP_COOKIES"),
        ("HTTP Error 429: Too Many Requests", "limitando"),
        ("ERROR: Postprocessing: ffprobe and ffmpeg not found", "FFmpeg"),
        ("ERROR: [youtube] x: Private video", "privado"),
        ("ERROR: Sign in to confirm your age", "iniciar sesión"),
        ("ERROR: Requested format is not available", "calidad menor"),
        ("ERROR: [youtube] x: Video unavailable", "no está disponible"),
        ("OSError: [Errno 28] No space left on device", "demasiado grande"),
        ("<urlopen error [Errno 11001] getaddrinfo failed>", "conexión"),
        ("something odd", "actualiza yt-dlp"),
    ],
)
def test_friendly_error(message, fragment):
    assert fragment in converter.friendly_error(RuntimeError(message))


def test_duration_limit(monkeypatch):
    monkeypatch.setattr(converter, "MAX_DURATION", {"mp3": 600, "mp4": 300})
    converter._check_duration({"duration": 600}, "mp3")
    with pytest.raises(ConversionError, match="10 minutos"):
        converter._check_duration({"duration": 601}, "mp3")
    with pytest.raises(ConversionError, match="MP4. El máximo aquí es 5 minutos"):
        converter._check_duration({"duration": 301}, "mp4")
    with pytest.raises(ConversionError, match="directos"):
        converter._check_duration({"duration": None, "is_live": True}, "mp3")


def test_format_options():
    mp3 = converter._format_options("mp3", "320")
    assert mp3["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}
    mp4 = converter._format_options("mp4", "720")
    assert mp4["merge_output_format"] == "mp4"
    assert "height<=720" in mp4["format"]
    assert mp4["format_sort"][0] == "res:720"
    assert not any(pp["key"] == "FFmpegExtractAudio" for pp in mp4["postprocessors"])


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
    events = converter.convert(WATCH, "mp3", "320", downloader=fake_downloader)
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
    assert result.size == FAKE_FILE_SIZE
    assert result.mimetype == "audio/mpeg"
    assert not folder.exists()


def test_convert_mp4():
    events = converter.convert(WATCH, "mp4", "1080", downloader=fake_downloader)
    result = next(e for e in events if isinstance(e, Result))
    assert result.filename == "Me at the zoo.mp4"
    assert result.mimetype == "video/mp4"
    assert result.path.read_bytes()[4:8] == b"ftyp"
    events.close()
    assert not result.path.parent.exists()


def test_convert_progress_is_monotonic_and_throttled():
    def chatty(url, fmt, quality, out_dir, report):
        for i in range(1000):
            report("download", i / 1000)
        report("download", 0.1)  # going backwards is ignored
        p = out_dir / "a.mp3"
        p.write_bytes(b"x")
        return p, {"title": "t"}

    fractions = [e.fraction for e in converter.convert(WATCH, "mp3", "192", downloader=chatty)
                 if isinstance(e, Progress)]
    assert fractions == sorted(fractions)
    assert len(fractions) < 200


def test_convert_failure_raises_friendly_error_and_cleans_up():
    before = set(Path(tempfile.gettempdir()).glob("convertmusic-*"))
    with pytest.raises(ConversionError, match="no está disponible"):
        list(converter.convert(WATCH, "mp3", "192", downloader=failing_downloader))
    assert set(Path(tempfile.gettempdir()).glob("convertmusic-*")) == before


def test_closing_early_removes_files():
    events = converter.convert(WATCH, "mp3", "192", downloader=fake_downloader)
    result = next(e for e in events if isinstance(e, Result))
    events.close()
    assert not result.path.parent.exists()


def test_downloader_without_file_is_an_error():
    def nothing(url, fmt, quality, out_dir, report):
        raise ConversionError("La conversión terminó sin generar un MP3.")

    with pytest.raises(ConversionError, match="sin generar"):
        list(converter.convert(WATCH, "mp3", "192", downloader=nothing))


@pytest.mark.skipif(not os.environ.get("RUN_NETWORK_TESTS"), reason="set RUN_NETWORK_TESTS=1 to hit YouTube")
@pytest.mark.parametrize("fmt, quality, magic", [("mp3", "128", (b"ID3", b"\xff\xfb")), ("mp4", "360", (b"ftyp",))])
def test_real_conversion(fmt, quality, magic):
    """End to end against YouTube: needs internet. FFmpeg and Deno come from pip."""
    assert converter.fetch_info(WATCH)["title"] == "Me at the zoo"
    for event in converter.convert(WATCH, fmt, quality):
        if isinstance(event, Result):
            assert event.filename == f"Me at the zoo.{fmt}"
            assert event.size > 50_000
            head = event.path.read_bytes()[:12]
            assert any(m in head for m in magic)
