#!/usr/bin/env python3
"""Scrape track metadata from the public Spotify track page meta tags.
No API keys required — parses og: and music: meta tags from the HTML."""
import sys, json, re, ssl, urllib.request

ssl._create_default_https_context = ssl._create_unverified_context


def resolve_spotify(url):
    track_id = None
    patterns = [
        r"spotify:track:(\w+)",
        r"open\.spotify\.com/track/(\w+)",
        r"play\.spotify\.com/track/(\w+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            track_id = m.group(1)
            break

    if not track_id:
        print(json.dumps({"error": "Could not extract track ID from URL"}))
        sys.exit(1)

    track_url = f"https://open.spotify.com/track/{track_id}"
    req = urllib.request.Request(
        track_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(json.dumps({"error": f"Failed to fetch track page: {e}"}))
        sys.exit(1)

    def meta(pattern, group=1, default=""):
        m = re.search(pattern, html)
        return m.group(group).strip() if m else default

    name = meta(r'<meta property="og:title" content="([^"]+)"')
    if not name:
        print(json.dumps({"error": "Could not find track title on page"}))
        sys.exit(1)

    description = meta(r'<meta property="og:description" content="([^"]+)"')
    artist = name
    album = ""
    year = ""

    if description:
        parts = [p.strip() for p in description.split("\u00b7")]
        if len(parts) >= 4:
            artist = parts[0]
            album = parts[1]
            year = parts[-1]
        elif len(parts) >= 2:
            artist = parts[0]

    release_date = meta(r'<meta name="music:release_date" content="([^"]+)"')
    if release_date and not year:
        year = release_date[:4]

    duration_s = meta(r'<meta name="music:duration" content="(\d+)"')
    duration_ms = int(duration_s) * 1000 if duration_s else 0

    album_art = meta(r'<meta property="og:image" content="([^"]+)"')

    result = {
        "id": track_id,
        "name": name,
        "artist": artist,
        "artists": [artist],
        "album": album,
        "album_art": album_art,
        "year": year,
        "duration_ms": duration_ms,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: spotify.py <spotify_url>"}))
        sys.exit(1)
    resolve_spotify(sys.argv[1])
