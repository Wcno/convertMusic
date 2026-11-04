import base64
import json

import pytest

import converter
from conftest import FAKE_FILE_SIZE, failing_downloader

URL = "https://youtu.be/jNQXAC9IVRw"


def read_stream(res):
    return [json.loads(line) for line in res.get_data(as_text=True).splitlines() if line.strip()]


def chunks(messages):
    return b"".join(base64.b64decode(m["data"]) for m in messages if m["type"] == "chunk")


# ------------------------------------------------------------------ page

@pytest.mark.parametrize("path", ["/", "/convert"])
def test_index_renders_interface(client, path):
    res = client.get(path)
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert 'lang="es"' in html
    assert 'name="url"' in html
    assert 'name="format" value="mp3"' in html and 'name="format" value="mp4"' in html
    for tier in ("low", "std", "high"):
        assert f'value="{tier}"' in html
    assert "By: wcno" in html
    assert "/static/styles.css" in html and "/static/app.js" in html


def test_static_assets_are_served(client):
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_unknown_page_is_404(client):
    assert client.get("/nope").status_code == 404


# ------------------------------------------------------------------ info

def test_info_returns_metadata(client):
    res = client.get("/api/info", query_string={"url": URL})
    data = res.get_json()
    assert res.status_code == 200
    assert data["title"] == "Me at the zoo"
    assert data["url"] == "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.parametrize("url", ["https://vimeo.com/1", "", "hola"])
def test_info_rejects_bad_url(client, url):
    res = client.get("/api/info", query_string={"url": url})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_info_reports_server_block(client, monkeypatch):
    def blocked(url):
        raise converter.ConversionError(converter.friendly_error(RuntimeError("Sign in to confirm you're not a bot")))

    monkeypatch.setattr(converter, "fetch_info", blocked)
    res = client.get("/api/info", query_string={"url": URL})
    assert res.status_code == 400
    assert "python app.py" in res.get_json()["error"]


# --------------------------------------------------------------- convert

def test_convert_streams_progress_and_mp3(client):
    res = client.post("/api/convert", json={"url": URL, "format": "mp3", "quality": "high"})
    assert res.status_code == 200
    assert res.mimetype == "application/x-ndjson"
    assert res.headers["Cache-Control"] == "no-store"

    messages = read_stream(res)
    kinds = [m["type"] for m in messages]
    assert kinds[0] == "progress"
    assert kinds[-1] == "done"
    assert kinds.count("file") == 1
    assert kinds.index("file") < kinds.index("chunk")
    assert kinds.count("chunk") == 4  # FAKE_FILE_SIZE spans four chunks

    progress = [m["progress"] for m in messages if m["type"] == "progress"]
    assert progress == sorted(progress) and max(progress) <= converter.DOWNLOAD_SHARE

    meta = next(m for m in messages if m["type"] == "file")
    assert meta == {"type": "file", "filename": "Me at the zoo.mp3", "title": "Me at the zoo",
                    "size": FAKE_FILE_SIZE, "mimetype": "audio/mpeg"}

    data = chunks(messages)
    assert len(data) == FAKE_FILE_SIZE
    assert data.startswith(b"ID3320")


def test_convert_streams_mp4(client):
    messages = read_stream(client.post("/api/convert", json={"url": URL, "format": "mp4", "quality": "high"}))
    meta = next(m for m in messages if m["type"] == "file")
    assert meta["filename"] == "Me at the zoo.mp4"
    assert meta["mimetype"] == "video/mp4"
    data = chunks(messages)
    assert data[4:8] == b"ftyp" and b"1080" in data[:24]
    assert len(data) == FAKE_FILE_SIZE
    assert messages[-1]["type"] == "done"


def test_quality_tiers_map_per_format(client, monkeypatch):
    seen = []

    def spy(url, fmt, quality, out_dir, report):
        seen.append((fmt, quality))
        p = out_dir / f"x.{fmt}"
        p.write_bytes(b"x")
        return p, {"title": "t"}

    monkeypatch.setattr(converter, "ytdlp_downloader", spy)
    for fmt in ("mp3", "mp4"):
        for tier in ("low", "std", "high"):
            client.post("/api/convert", json={"url": URL, "format": fmt, "quality": tier}).get_data()
    client.post("/api/convert", json={"url": URL}).get_data()  # defaults
    client.post("/api/convert", json={"url": URL, "format": "mp4", "quality": "1080p"}).get_data()
    assert seen == [("mp3", "128"), ("mp3", "192"), ("mp3", "320"),
                    ("mp4", "360"), ("mp4", "720"), ("mp4", "1080"),
                    ("mp3", "192"), ("mp4", "1080")]


def test_convert_accepts_form_data(client):
    res = client.post("/api/convert", data={"url": URL, "format": "mp4", "quality": "low"})
    assert read_stream(res)[-1]["type"] == "done"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"url": ""}, "Pega un enlace"),
        ({"url": "https://example.com"}, "no es de YouTube"),
        ({"url": URL, "quality": "64"}, "Calidad"),
        ({"url": URL, "format": "webm"}, "Formato"),
        ({"url": URL, "format": "mp4", "quality": "320"}, "MP4"),
    ],
)
def test_convert_validates_input(client, body, fragment):
    res = client.post("/api/convert", json=body)
    assert res.status_code == 400
    assert fragment in res.get_json()["error"]


def test_convert_failure_streams_error(client, monkeypatch):
    monkeypatch.setattr(converter, "ytdlp_downloader", failing_downloader)
    messages = read_stream(client.post("/api/convert", json={"url": URL}))
    assert messages[-1] == {"type": "error", "message": "Este video no está disponible."}
    assert not any(m["type"] in ("file", "chunk", "done") for m in messages)


# ------------------------------------------------------- no-JS fallback

def test_form_fallback_downloads_mp3(client):
    res = client.post("/convert", data={"url": URL, "quality": "std"})
    assert res.status_code == 200
    assert res.mimetype == "audio/mpeg"
    assert res.headers["Content-Length"] == str(FAKE_FILE_SIZE)
    disposition = res.headers["Content-Disposition"]
    assert 'filename="Me at the zoo.mp3"' in disposition
    assert "filename*=UTF-8''Me%20at%20the%20zoo.mp3" in disposition
    assert res.data.startswith(b"ID3192")
    assert len(res.data) == FAKE_FILE_SIZE


def test_form_fallback_downloads_mp4(client):
    res = client.post("/convert", data={"url": URL, "format": "mp4", "quality": "low"})
    assert res.status_code == 200
    assert res.mimetype == "video/mp4"
    assert 'filename="Me at the zoo.mp4"' in res.headers["Content-Disposition"]
    assert res.data[4:8] == b"ftyp" and b"360" in res.data[:24]


def test_form_fallback_handles_unicode_titles(client, monkeypatch):
    def accented(url, fmt, quality, out_dir, report):
        p = out_dir / f"x.{fmt}"
        p.write_bytes(b"x")
        return p, {"title": "Canción ñandú"}

    monkeypatch.setattr(converter, "ytdlp_downloader", accented)
    disposition = client.post("/convert", data={"url": URL}).headers["Content-Disposition"]
    assert 'filename="Cancin and.mp3"' in disposition
    assert "Canci%C3%B3n%20%C3%B1and%C3%BA.mp3" in disposition


def test_form_fallback_non_latin_title_keeps_extension(client, monkeypatch):
    def japanese(url, fmt, quality, out_dir, report):
        p = out_dir / f"x.{fmt}"
        p.write_bytes(b"x")
        return p, {"title": "日本の歌"}

    monkeypatch.setattr(converter, "ytdlp_downloader", japanese)
    disposition = client.post("/convert", data={"url": URL, "format": "mp4"}).headers["Content-Disposition"]
    assert 'filename="download.mp4"' in disposition


def test_form_fallback_shows_errors(client, monkeypatch):
    res = client.post("/convert", data={"url": "nope"})
    assert res.status_code == 400
    assert "YouTube" in res.get_data(as_text=True)

    monkeypatch.setattr(converter, "ytdlp_downloader", failing_downloader)
    res = client.post("/convert", data={"url": URL})
    html = res.get_data(as_text=True)
    assert res.status_code == 502
    assert "no está disponible" in html
    assert f'value="{URL}"' in html  # the link stays in the box


def test_error_message_is_escaped(client):
    res = client.post("/convert", data={"url": "<script>alert(1)</script>"})
    html = res.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
