"""Prepare a browser cookies.txt for the YTDLP_COOKIES environment variable.

Keeps only YouTube/Google cookies (Vercel limits the size of environment
variables), checks that the file comes from a signed-in session, and prints
the result as one base64 line, which survives copy-paste into any dashboard.

    python scripts/prepare_cookies.py path/to/cookies.txt            # print the value
    python scripts/prepare_cookies.py path/to/cookies.txt --out v.txt # save it to a file

The output is a login credential: keep it private and never commit it.
"""

import argparse
import base64
import sys
from pathlib import Path

KEEP_DOMAINS = ("youtube.com", "google.com")
SIGNED_IN_MARKERS = ("LOGIN_INFO", "__Secure-3PSID", "SAPISID")


def filter_cookies(text: str) -> tuple[str, int]:
    """Return (Netscape cookies.txt with only YouTube/Google rows, number of rows kept)."""
    kept = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        domain = fields[0].removeprefix("#HttpOnly_").lstrip(".").lower()
        if any(domain == d or domain.endswith("." + d) for d in KEEP_DOMAINS):
            kept.append(line)
    return "# Netscape HTTP Cookie File\n" + "\n".join(kept) + "\n", len(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cookies", type=Path, help="cookies.txt exported from your browser")
    parser.add_argument("--out", type=Path, help="write the value to this file instead of printing it")
    args = parser.parse_args()

    text = args.cookies.read_text(encoding="utf-8", errors="replace")
    filtered, count = filter_cookies(text)
    if count == 0:
        print("No YouTube cookies found. Export them from a youtube.com tab (Netscape cookies.txt format).",
              file=sys.stderr)
        return 1
    if not any(marker in filtered for marker in SIGNED_IN_MARKERS):
        print("These cookies are not from a signed-in YouTube session. Sign in, then export again.",
              file=sys.stderr)
        return 1

    value = base64.b64encode(filtered.encode("utf-8")).decode("ascii")
    print(f"Kept {count} YouTube/Google cookies; value is {len(value):,} characters.", file=sys.stderr)
    if len(value) > 60_000:
        print("Warning: that is close to Vercel's 64 KB limit for all environment variables.", file=sys.stderr)
    if args.out:
        args.out.write_text(value, encoding="ascii")
        print(f"Saved to {args.out}. Paste its contents as YTDLP_COOKIES in Vercel.", file=sys.stderr)
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
