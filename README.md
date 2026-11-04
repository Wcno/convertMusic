# convertMusic

Turn a YouTube video into an MP3. Paste a link, check the preview, pick a quality, and download.

**Live:** <https://convert-music-pink.vercel.app/convert>

![convertMusic interface](docs/screenshot.png)

The interface is designed like a piece of studio rack gear: the link goes into the input slot, the video shows up in the display with its length, quality and file size, and an LED meter fills with the **real** progress of the download, the MP3 conversion and the transfer to your browser. The UI is in Spanish.

## Features

- **Video preview**: title, channel, length, thumbnail and estimated file size before you convert.
- **Choice of quality**: 128, 192 or 320 kbps.
- **Live progress**: the meter follows the real download, then the conversion, then the transfer.
- **Clear errors**: private, unavailable, too-long or blocked videos get a plain message that says what to do.
- **Nothing stored**: each conversion runs in its own temporary folder, deleted as soon as the file has been sent.
- **Runs anywhere**: locally, or on Vercel's serverless functions. FFmpeg and Deno come from pip, so nothing needs to be installed on the server.
- **Works without JavaScript**: the form still converts and downloads in one step.
- Accepts `youtube.com/watch`, `youtu.be`, `music.youtube.com`, `/shorts/` and `/embed/` links. Playlists are ignored; only the video in the link is converted.

## Run it locally

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Wcno/convertMusic.git
cd convertMusic

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>.

`requirements.txt` installs yt-dlp, [Deno](https://deno.com) (yt-dlp needs a JavaScript runtime to read YouTube) and a bundled FFmpeg binary (`imageio-ffmpeg`). If FFmpeg is already installed on your system, that copy is used instead.

## Deploy to Vercel

The repo is ready for Vercel's zero-config Flask support:

- `app.py` exposes the Flask `app`, which Vercel detects automatically.
- `vercel.json` allows each request up to 300 seconds (the Hobby plan maximum) and leaves tests and docs out of the bundle.
- `.python-version` pins Python 3.12.

Push to `master` and Vercel builds and deploys it. The page is served at both `/` and `/convert`.

### YouTube blocks cloud servers

YouTube often answers requests from data centres (Vercel, AWS and similar) with **"Sign in to confirm you're not a bot"**. The app then shows *"YouTube bloqueó la descarga desde este servidor…"*. To get past it, set one of these in **Vercel → Project → Settings → Environment Variables**, then redeploy:

| Variable | What it does |
|---|---|
| `YTDLP_COOKIES` | The full text of a `cookies.txt` file exported from a browser that is signed in to YouTube (see the [yt-dlp guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)). Use a secondary Google account: YouTube can flag accounts used this way. Cookies expire and need refreshing now and then. |
| `YTDLP_PROXY` | A proxy URL such as `http://user:pass@host:port`. A residential proxy is the most reliable option. |

Other optional variables:

| Variable | Default | Purpose |
|---|---|---|
| `MAX_DURATION_SECONDS` | 1800 on Vercel, 10800 locally | Longest video accepted. On Vercel each request must finish within 300 seconds. |
| `FFMPEG_LOCATION` | – | Path to a specific FFmpeg binary or folder. |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers link validation, quality options, file naming, error messages, configuration (cookies, proxy, bundled FFmpeg and Deno), progress and cleanup of a conversion, every route, the streamed response format and the no-JavaScript fallback. It replaces yt-dlp with a fake downloader, so it runs offline in under a second.

To also run one real conversion against YouTube (needs internet, and an IP address YouTube is not blocking):

```bash
# Windows PowerShell
$env:RUN_NETWORK_TESTS=1; python -m pytest
# macOS / Linux
RUN_NETWORK_TESTS=1 python -m pytest
```

## How it works

Every conversion happens inside one request, so the server keeps nothing between requests. That is what lets it run on serverless functions, where each request may land on a different machine.

```
browser ── GET  /api/info?url=…  ───────────▶ yt-dlp reads title, channel, thumbnail
        ── POST /api/convert {url, quality} ─▶ streamed reply, one JSON object per line:
                                                {"type":"progress","stage":"download","progress":0.42}
                                                {"type":"progress","stage":"convert","progress":0.8}
                                                {"type":"file","filename":"…mp3","title":"…","size":123456}
                                                {"type":"chunk","data":"<base64>"}   (repeated)
                                                {"type":"done"}      or   {"type":"error","message":"…"}
```

The browser rebuilds the MP3 from the chunks and saves it, so "Descargar de nuevo" works without asking the server again. Because the reply is streamed, files larger than Vercel's 4.5 MB response limit still go through.

| Route | Purpose |
|---|---|
| `GET /`, `GET /convert` | The page |
| `GET /api/info?url=` | Video metadata for the preview |
| `POST /api/convert` | Convert. JSON or form body: `url`, `quality` (`128`, `192`, `320`; default `192`). Streams the reply described above |
| `POST /convert` | No-JavaScript fallback: converts and returns the MP3 file itself |

### Project layout

```
app.py              Flask routes and the streamed reply format
converter.py        Link checks, yt-dlp/FFmpeg/Deno setup, convert() generator
templates/index.html
static/styles.css   Rack-unit design
static/app.js       Preview, stream reading, progress meter and download
tests/              pytest suite (fake downloader, no network)
vercel.json         Function duration and bundle exclusions
```

`converter.convert(url, quality)` is the core: it yields progress events and then one result whose file stays on disk until the generator finishes. A test can pass `downloader=` to replace yt-dlp.

## Troubleshooting

| Message | Fix |
|---|---|
| "YouTube bloqueó la descarga desde este servidor…" | YouTube is blocking the server's IP. Set `YTDLP_COOKIES` or `YTDLP_PROXY` (see above). |
| "YouTube está limitando las descargas…" | Too many requests from this IP in a short time. Wait a few minutes. |
| "YouTube no dejó descargar este video…" | YouTube changed something. Update yt-dlp: `pip install -U "yt-dlp[default]"`, then redeploy. |
| "El video es demasiado largo…" | Raise `MAX_DURATION_SECONDS`, bearing in mind Vercel's 300-second limit per request. |
| "La conexión se cortó antes de terminar…" | The request hit the time limit or the network dropped. Try a shorter video or try again. |

## Note

For personal use only. Download only content you have the rights to, and respect YouTube's Terms of Service and the creators' copyright.

---

By: Wilcno
