#!/usr/bin/env python3
"""Fetch song lyrics with synced karaoke timestamps from LRCLIB, fallback to plain text APIs."""

import sys, json, urllib.request, urllib.parse, re


def fetch_lrclib(artist, title):
    """Fetch synced LRC lyrics from LRCLIB (free karaoke timestamp API)."""
    url = (
        "https://lrclib.net/api/get?"
        + urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SongHero/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        synced = data.get("syncedLyrics", "")
        duration = data.get("duration", 0)
        if synced and len(synced) > 20:
            return {"lrc": synced, "duration": float(duration)}
    except Exception:
        pass
    return None


def parse_lrc(lrc_text):
    """Parse LRC format into timed events: [mm:ss.xx]text"""
    events = []
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    for line in lrc_text.split("\n"):
        match = pattern.match(line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        text = match.group(3).strip()
        if not text:
            continue
        time_sec = minutes * 60 + seconds
        events.append({"time": round(time_sec, 2), "word": text})
    events.sort(key=lambda e: e["time"])
    return events


def fetch_lyrics_ovh(artist, title):
    """Plain text fallback from lyrics.ovh."""
    url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SongHero/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        lyrics = data.get("lyrics", "")
        if lyrics and len(lyrics) > 20:
            return lyrics
    except Exception:
        pass
    return None


def clean_plain_lyrics(raw_text):
    """Clean up plain text lyrics into lines."""
    skip_patterns = [
        r"^\d+ contributors$",
        r"^paroles de la chanson",
        r"^lyrics powered by",
        r"^\.\.\.$",
        r"^you might also like",
        r"^embed$",
    ]
    lines = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.search(p, stripped, re.IGNORECASE) for p in skip_patterns):
            continue
        lines.append({"text": stripped, "section": "verse"})
    return lines


def fetch_lyrics(artist, title):
    """Fetch lyrics — prefers synced LRC, falls back to plain text."""

    # Primary: LRCLIB synced karaoke lyrics
    lrc = fetch_lrclib(artist, title)
    if lrc:
        events = parse_lrc(lrc["lrc"])
        if events:
            return {
                "synced": True,
                "source": "lrclib",
                "lrc_duration": lrc["duration"],
                "events": events,
                "line_count": len(events),
            }

    # Fallback: lyrics.ovh plain text
    raw = fetch_lyrics_ovh(artist, title)
    if raw:
        lines = clean_plain_lyrics(raw)
        if lines:
            return {
                "synced": False,
                "source": "lyrics.ovh",
                "lines": lines,
                "line_count": len(lines),
            }

    return {"error": "No lyrics found for this song"}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: lyrics.py <title> <artist>"}))
        sys.exit(1)

    title = sys.argv[1]
    artist = sys.argv[2]

    result = fetch_lyrics(artist, title)
    print(json.dumps(result, ensure_ascii=False))
