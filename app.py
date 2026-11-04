import base64
import json
from urllib.parse import quote

from flask import Flask, Response, jsonify, render_template, request

import converter
from converter import ConversionError, Progress, Result

app = Flask(__name__)

CHUNK = 3 * 64 * 1024  # a multiple of 3, so each base64 piece decodes on its own
STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _attachment(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").strip() or "audio.mp3"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _read_chunks(path, size=CHUNK):
    with open(path, "rb") as fh:
        while chunk := fh.read(size):
            yield chunk


def _ndjson_stream(events):
    """One conversion as newline-delimited JSON: progress, file, chunk..., done (or error).

    Streaming keeps the request alive while YouTube and FFmpeg work, and lets
    files larger than Vercel's 4.5 MB response limit through.
    """
    def line(obj):
        return json.dumps(obj, ensure_ascii=False) + "\n"

    try:
        for event in events:
            if isinstance(event, Progress):
                yield line({"type": "progress", "stage": event.stage, "progress": event.fraction})
            else:
                yield line({"type": "file", "filename": event.filename, "title": event.title, "size": event.size})
                for chunk in _read_chunks(event.path):
                    yield line({"type": "chunk", "data": base64.b64encode(chunk).decode("ascii")})
        yield line({"type": "done"})
    except ConversionError as exc:
        yield line({"type": "error", "message": str(exc)})
    finally:
        events.close()


@app.get("/")
@app.get("/convert")
def index():
    return render_template("index.html")


@app.get("/api/info")
def api_info():
    try:
        url = converter.normalize_url(request.args.get("url"))
        return jsonify(converter.fetch_info(url))
    except ConversionError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/convert")
def api_convert():
    data = request.get_json(silent=True) or request.form
    try:
        url = converter.normalize_url(data.get("url"))
        quality = converter.normalize_quality(data.get("quality"))
    except ConversionError as exc:
        return jsonify(error=str(exc)), 400
    events = converter.convert(url, quality)
    return Response(_ndjson_stream(events), mimetype="application/x-ndjson", headers=STREAM_HEADERS)


@app.post("/convert")
def convert_form():
    """Fallback for browsers without JavaScript: convert, then send the MP3 itself."""
    raw_url = request.form.get("url", "")
    try:
        url = converter.normalize_url(raw_url)
        quality = converter.normalize_quality(request.form.get("quality"))
    except ConversionError as exc:
        return render_template("index.html", error=str(exc), url=raw_url), 400

    events = converter.convert(url, quality)
    try:
        result = next(e for e in events if isinstance(e, Result))
    except ConversionError as exc:
        return render_template("index.html", error=str(exc), url=raw_url), 502

    def body():
        try:
            yield from _read_chunks(result.path)
        finally:
            events.close()  # deletes the temporary folder

    headers = {**STREAM_HEADERS, "Content-Disposition": _attachment(result.filename), "Content-Length": str(result.size)}
    return Response(body(), mimetype="audio/mpeg", headers=headers)


if __name__ == "__main__":
    app.run(debug=True)
