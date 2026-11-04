import base64
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
