import base64
import json

import converter
from conftest import FAKE_MP3_SIZE, failing_downloader

URL = "https://youtu.be/jNQXAC9IVRw"


def read_stream(res):
    return [json.loads(line) for line in res.get_data(as_text=True).splitlines() if line.strip()]


def test_index_renders_interface(client):
    for path in ("/", "/convert"):
        res = client.get(path)
        html = res.get_data(as_text=True)
        assert res.status_code == 200, path
        assert 'lang="es"' in html
        assert 'name="url"' in html
        for q in ("128", "192", "320"):
            assert f'value="{q}"' in html
        assert "By: Wilcno" in html
        assert "/static/styles.css" in html and "/static/app.js" in html


def test_static_assets_are_served(client):
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_info_returns_metadata(client):
    res = client.get("/api/info", query_string={"url": URL})
    data = res.get_json()
    assert res.status_code == 200
    assert data["title"] == "Me at the zoo"
    assert data["url"] == "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_info_rejects_bad_url(client):
    res = client.get("/api/info", query_string={"url": "https://vimeo.com/1"})
    assert res.status_code == 400
    assert "YouTube" in res.get_json()["error"]


def test_info_reports_yt_dlp_failures(client, monkeypatch):
    def blocked(url):
        raise converter.ConversionError(converter.friendly_error(RuntimeError("Sign in to confirm you're not a bot")))

    monkeypatch.setattr(converter, "fetch_info", blocked)
    res = client.get("/api/info", query_string={"url": URL})
    assert res.status_code == 400
    assert "anti-bots" in res.get_json()["error"]


def test_convert_streams_progress_and_file(client):
    res = client.post("/api/convert", json={"url": URL, "quality": "320"})
    assert res.status_code == 200
    assert res.mimetype == "application/x-ndjson"
    assert res.headers["Cache-Control"] == "no-store"

    messages = read_stream(res)
    kinds = [m["type"] for m in messages]
    assert kinds[0] == "progress"
    assert kinds[-1] == "done"
    assert kinds.count("file") == 1
    assert kinds.index("file") < kinds.index("chunk")
    assert kinds.count("chunk") == 4  # FAKE_MP3_SIZE spans four chunks

    progress = [m["progress"] for m in messages if m["type"] == "progress"]
    assert progress == sorted(progress) and max(progress) <= converter.DOWNLOAD_SHARE

    meta = next(m for m in messages if m["type"] == "file")
    assert meta == {"type": "file", "filename": "Me at the zoo.mp3", "title": "Me at the zoo", "size": FAKE_MP3_SIZE}

    data = b"".join(base64.b64decode(m["data"]) for m in messages if m["type"] == "chunk")
    assert len(data) == FAKE_MP3_SIZE
    assert data.startswith(b"ID3320")


def test_convert_accepts_form_data(client):
    res = client.post("/api/convert", data={"url": URL, "quality": "128"})
    assert read_stream(res)[-1]["type"] == "done"


def test_convert_validates_input(client):
    assert client.post("/api/convert", json={"url": ""}).status_code == 400
    assert client.post("/api/convert", json={"url": "https://example.com"}).status_code == 400
    res = client.post("/api/convert", json={"url": URL, "quality": "64"})
    assert res.status_code == 400
    assert "Calidad" in res.get_json()["error"]


def test_convert_failure_streams_error(client, monkeypatch):
    monkeypatch.setattr(converter, "ytdlp_downloader", failing_downloader)
    messages = read_stream(client.post("/api/convert", json={"url": URL}))
    assert messages[-1] == {"type": "error", "message": "Este video no está disponible."}
    assert not any(m["type"] in ("file", "chunk", "done") for m in messages)


def test_form_fallback_downloads_file(client):
    res = client.post("/convert", data={"url": URL, "quality": "192"})
    assert res.status_code == 200
    assert res.mimetype == "audio/mpeg"
    assert res.headers["Content-Length"] == str(FAKE_MP3_SIZE)
    disposition = res.headers["Content-Disposition"]
    assert 'filename="Me at the zoo.mp3"' in disposition
    assert "filename*=UTF-8''Me%20at%20the%20zoo.mp3" in disposition
    assert res.data.startswith(b"ID3192")
    assert len(res.data) == FAKE_MP3_SIZE


def test_form_fallback_handles_unicode_titles(client, monkeypatch):
    def accented(url, quality, out_dir, report):
        p = out_dir / "x.mp3"
        p.write_bytes(b"ID3")
        return p, {"title": "Canción ñandú"}

    monkeypatch.setattr(converter, "ytdlp_downloader", accented)
    res = client.post("/convert", data={"url": URL})
    disposition = res.headers["Content-Disposition"]
    assert 'filename="Cancin and.mp3"' in disposition
    assert "Canci%C3%B3n%20%C3%B1and%C3%BA.mp3" in disposition


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
