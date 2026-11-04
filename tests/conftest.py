import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import converter  # noqa: E402

FAKE_INFO = {
    "id": "jNQXAC9IVRw",
    "title": "Me at the zoo",
    "channel": "jawed",
    "duration": 19,
    "thumbnail": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
    "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
}

# Large enough to span several streamed chunks.
FAKE_FILE_SIZE = 3 * app_module.CHUNK + 1234
MAGIC = {"mp3": b"ID3", "mp4": b"\x00\x00\x00\x18ftypmp42"}


def fake_downloader(url, fmt, quality, out_dir, report):
    """Stands in for yt-dlp: reports progress and writes a fake MP3 or MP4.

    The file starts with the format's magic bytes followed by the quality,
    so tests can check what was requested.
    """
    report("download", None)
    report("download", 0.5)
    report("download", 1.0)
    report("convert", None)
    path = out_dir / f"jNQXAC9IVRw.{fmt}"
    path.write_bytes((MAGIC[fmt] + quality.encode()).ljust(FAKE_FILE_SIZE, b"\x07"))
    return path, {"title": FAKE_INFO["title"]}


def failing_downloader(url, fmt, quality, out_dir, report):
    report("download", 0.2)
    raise RuntimeError("ERROR: [youtube] abc: Video unavailable")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(converter, "ytdlp_downloader", fake_downloader)
    monkeypatch.setattr(converter, "fetch_info", lambda url: {**FAKE_INFO, "url": url})
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as c:
        yield c
