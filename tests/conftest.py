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
FAKE_MP3_SIZE = 3 * app_module.CHUNK + 1234


def fake_downloader(url, quality, out_dir, report):
    """Stands in for yt-dlp: reports progress and writes a fake MP3."""
    report("download", None)
    report("download", 0.5)
    report("download", 1.0)
    report("convert", None)
    path = out_dir / "jNQXAC9IVRw.mp3"
    body = (b"ID3" + quality.encode()).ljust(FAKE_MP3_SIZE, b"\x07")
    path.write_bytes(body)
    return path, {"title": FAKE_INFO["title"]}


def failing_downloader(url, quality, out_dir, report):
    report("download", 0.2)
    raise RuntimeError("ERROR: [youtube] abc: Video unavailable")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(converter, "ytdlp_downloader", fake_downloader)
    monkeypatch.setattr(converter, "fetch_info", lambda url: {**FAKE_INFO, "url": url})
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as c:
        yield c
