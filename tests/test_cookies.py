import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

import converter

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "prepare_cookies.py"

COOKIES = "\n".join([
    "# Netscape HTTP Cookie File",
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\tabc",
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tPREF\tf6=40000000",
    "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-3PSID\txyz",
    "accounts.google.com\tFALSE\t/\tTRUE\t1893456000\tLSID\tqqq",
    ".example.com\tTRUE\t/\tFALSE\t1893456000\ttracker\t1",
    ".notyoutube.com\tTRUE\t/\tFALSE\t1893456000\tevil\t1",
]) + "\n"


@pytest.fixture(autouse=True)
def fresh_cache():
    converter._cookie_file.cache_clear()
    yield
    converter._cookie_file.cache_clear()


def run_script(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_prepare_cookies_keeps_only_youtube_and_google(tmp_path):
    src = tmp_path / "cookies.txt"
    src.write_text(COOKIES, encoding="utf-8")
    res = run_script(str(src))
    assert res.returncode == 0, res.stderr
    decoded = base64.b64decode(res.stdout.strip()).decode()
    assert "LOGIN_INFO" in decoded and "__Secure-3PSID" in decoded and "LSID" in decoded
    assert "example.com" not in decoded and "notyoutube" not in decoded
    assert "Kept 4" in res.stderr


def test_prepare_cookies_rejects_signed_out_and_empty(tmp_path):
    signed_out = tmp_path / "out.txt"
    signed_out.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tx\n", encoding="utf-8")
    assert "not from a signed-in" in run_script(str(signed_out)).stderr

    empty = tmp_path / "empty.txt"
    empty.write_text(".example.com\tTRUE\t/\tTRUE\t0\ta\tb\n", encoding="utf-8")
    res = run_script(str(empty))
    assert res.returncode == 1 and "No YouTube cookies" in res.stderr


def test_prepare_cookies_out_file(tmp_path):
    src = tmp_path / "cookies.txt"
    src.write_text(COOKIES, encoding="utf-8")
    out = tmp_path / "value.txt"
    assert run_script(str(src), "--out", str(out)).returncode == 0
    assert base64.b64decode(out.read_text()).decode().startswith("# Netscape")


@pytest.mark.parametrize(
    "value",
    [
        COOKIES,                                                   # pasted as-is
        base64.b64encode(COOKIES.encode()).decode(),               # from prepare_cookies.py
        COOKIES.replace("\n", "\n").replace("\t", "\t"),         # escaped on one line
    ],
)
def test_cookie_env_formats(monkeypatch, value):
    monkeypatch.setenv("YTDLP_COOKIES", value)
    path = converter._cookie_file()
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("# Netscape HTTP Cookie File")
    assert ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\tabc" in text
    assert converter._base_options()["cookiefile"] == path
    Path(path).unlink()


@pytest.mark.parametrize("value", ["", "   ", "not cookies", "bm90IGNvb2tpZXM="])
def test_invalid_cookie_env_is_ignored(monkeypatch, value):
    monkeypatch.setenv("YTDLP_COOKIES", value)
    assert converter._cookie_file() is None
    assert "cookiefile" not in converter._base_options()


def test_status_never_reveals_cookies(monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIES", base64.b64encode(COOKIES.encode()).decode())
    info = converter.status()
    assert info["cookies"] is True and info["cookies_signed_in"] is True
    assert info["ffmpeg"] is True and info["deno"] is True
    assert "abc" not in str(info) and "LOGIN_INFO" not in str(info)


def test_expired_cookie_message(monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIES", COOKIES)
    msg = converter.friendly_error(RuntimeError("Sign in to confirm you're not a bot"))
    assert "caducaron" in msg
    monkeypatch.delenv("YTDLP_COOKIES")
    assert "python app.py" in converter.friendly_error(RuntimeError("Sign in to confirm you're not a bot"))


def test_health_route(client, monkeypatch):
    monkeypatch.delenv("YTDLP_COOKIES", raising=False)
    data = client.get("/api/health").get_json()
    assert data["cookies"] is False
    assert set(data) == {"cookies", "cookies_signed_in", "ffmpeg", "deno", "vercel", "yt_dlp"}


# ------------------------------------------------------- bundled binaries

def test_ensure_executable_copies_non_executable_binary(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("execute bits are a POSIX concept")
    binary = tmp_path / "tool"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o644)
    runnable = converter._ensure_executable(str(binary))
    assert runnable != str(binary) and os.access(runnable, os.X_OK)


def test_ensure_executable_handles_missing_and_ok(tmp_path):
    assert converter._ensure_executable(None) is None
    assert converter._ensure_executable(str(tmp_path / "missing")) is None
    ok = tmp_path / "ok.exe"
    ok.write_bytes(b"x")
    ok.chmod(0o755)
    assert converter._ensure_executable(str(ok)) == str(ok)


def _fake_release(tmp_path, body=b"#!/bin/sh\necho deno\n"):
    import hashlib
    import zipfile
    archive = tmp_path / "deno.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("deno", body)
    return archive.as_uri(), hashlib.sha256(archive.read_bytes()).hexdigest()


def test_download_deno_verifies_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(converter.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    url, digest = _fake_release(tmp_path)
    path = converter._download_deno(url, digest)
    assert path and Path(path).read_bytes().startswith(b"#!/bin/sh")
    if os.name != "nt":
        assert os.access(path, os.X_OK)
    # Second call reuses the cached copy without downloading again.
    assert converter._download_deno("file:///does/not/exist", digest) == path
    leftovers = [p.name for p in Path(path).parent.iterdir() if p.name.endswith(".part")]
    assert leftovers == []


def test_download_deno_rejects_bad_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(converter.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    url, _ = _fake_release(tmp_path)
    assert converter._download_deno(url, "0" * 64) is None
    assert list((tmp_path / "tmp" / "convertmusic-bin").iterdir()) == []


def test_download_deno_survives_network_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(converter.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    assert converter._download_deno((tmp_path / "missing.zip").as_uri(), "0" * 64) is None


def test_deno_is_downloaded_only_on_vercel(monkeypatch):
    monkeypatch.setattr(converter.shutil, "which", lambda name: None)
    monkeypatch.delenv("DENO_PATH", raising=False)
    monkeypatch.setitem(sys.modules, "deno", None)  # pip package not installed
    monkeypatch.setattr(converter.sys, "platform", "linux")
    monkeypatch.setattr(converter.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(converter, "_download_deno", lambda: "/tmp/convertmusic-bin/deno-x")
    try:
        monkeypatch.setattr(converter, "ON_VERCEL", False)
        converter._deno_path.cache_clear()
        assert converter._deno_path() is None
        monkeypatch.setattr(converter, "ON_VERCEL", True)
        converter._deno_path.cache_clear()
        assert converter._deno_path() == "/tmp/convertmusic-bin/deno-x"
    finally:
        converter._deno_path.cache_clear()
