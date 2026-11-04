# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
The author (Wilcno) and a few friends. They paste a YouTube link, want the audio as an MP3, and leave. Used occasionally, on a laptop or phone, usually with music already in mind.

## Product Purpose
Convert a YouTube video into a downloadable MP3 in one step. Success: paste a link, see that it is the right video, pick a quality, get the file, with clear feedback while it works and a clear message when it fails.

## Operating Context
Deployed on Vercel at https://convert-music-pink.vercel.app/convert and runnable locally with `python app.py` (Flask + yt-dlp, with FFmpeg and Deno installed from pip). Shared with friends, not run as a public service. Each conversion is one streamed request (serverless functions keep no state; 300 s limit). YouTube may block Vercel's IPs; cookies or a proxy are configured through environment variables.

## Capabilities and Constraints
- Input: a single YouTube video URL (youtube.com, youtu.be, music.youtube.com, shorts).
- Output: MP3 at a chosen bitrate (128 / 192 / 320 kbps).
- Preview of title, channel, duration and thumbnail before converting.
- Downloaded files are temporary; nothing is kept on the server between requests.
- UI language: Spanish.

## Brand Commitments
- Credit line "By: Wilcno" stays on the page.
- Spanish copy.

## Evidence on Hand
No logo, imagery or testimonials. Video thumbnails come from YouTube at runtime.

## Product Principles
1. One task, one screen: paste, confirm, download.
2. Honest feedback: progress and errors reflect what is really happening.
3. Leave nothing behind: no files pile up on the machine.
