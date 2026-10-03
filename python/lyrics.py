#!/usr/bin/env python3
"""Fetch source-based synced lyrics, with an optional plain-text fallback."""

import json
import math
import re
import sys
import urllib.parse

from sources import fetch_json


def fetch_lrclib(artist: str, title: str):
    url = "https://lrclib.net/api/get?" + urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    try:
        data = fetch_json(url, limit=1024 * 1024)
        synced = data.get("syncedLyrics")
        duration = float(data.get("duration") or 0)
        if isinstance(synced, str) and synced.strip() and math.isfinite(duration) and duration >= 0:
            return {"lrc": synced, "duration": duration}
    except (ValueError, OSError, TypeError):
        pass
    return None


def parse_lrc(lrc_text: str) -> list[dict]:
    offset_match = re.search(r"\[offset:([+-]?\d+)\]", lrc_text, re.IGNORECASE)
    offset = int(offset_match.group(1)) / 1000 if offset_match else 0
    timestamp = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]")
    events = []
    for line in lrc_text.splitlines():
        matches = list(timestamp.finditer(line))
        if not matches:
            continue
        text = line[matches[-1].end():].strip()
        if not text:
            continue
        for match in matches:
            seconds = float(match.group(2))
            time = int(match.group(1)) * 60 + seconds + offset
            if seconds < 60 and math.isfinite(time) and time >= 0:
                events.append({"time": round(time, 3), "word": text})
    events.sort(key=lambda event: event["time"])
    return events


def fetch_lyrics_ovh(artist: str, title: str):
    url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist, safe='')}/{urllib.parse.quote(title, safe='')}"
    try:
        data = fetch_json(url, limit=1024 * 1024)
        lyrics = data.get("lyrics")
        return lyrics if isinstance(lyrics, str) and lyrics.strip() else None
    except (ValueError, OSError, TypeError):
        return None


def clean_plain_lyrics(raw_text: str) -> list[dict]:
    skip = re.compile(r"^(?:\d+ contributors|paroles de la chanson.*|lyrics powered by.*|\.\.\.|you might also like|embed)$", re.IGNORECASE)
    lines = []
    section, group = None, 0
    for raw in raw_text.splitlines():
        text = raw.strip()
        if not text or skip.match(text):
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", text)
        if header:
            label = re.sub(r"\s+", "_", header.group(1).lower())
            label = re.sub(r"_?\d+$", "", label)
            if label in {"intro", "verse", "pre_chorus", "chorus", "bridge", "solo", "outro"}:
                section = label
                group += 1
            continue
        lines.append({"text": text, "section": section, "group": group})
    return lines


def fetch_lyrics(artist: str, title: str) -> dict:
    lrc = fetch_lrclib(artist, title)
    if lrc:
        events = parse_lrc(lrc["lrc"])
        if events:
            return {"synced": True, "source": "lrclib", "lrc_duration": lrc["duration"],
                    "events": events, "line_count": len(events)}
    raw = fetch_lyrics_ovh(artist, title)
    if raw:
        lines = clean_plain_lyrics(raw)
        if lines:
            return {"synced": False, "estimated": True, "source": "lyrics.ovh", "lines": lines, "line_count": len(lines)}
    return {"error": "No lyrics found for this song"}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: lyrics.py <title> <artist>"}))
        sys.exit(1)
    print(json.dumps(fetch_lyrics(sys.argv[2], sys.argv[1]), ensure_ascii=False, allow_nan=False))
