#!/usr/bin/env python3
"""Resolve public Spotify track metadata without a Spotify API key."""

from html.parser import HTMLParser
import json
import re
import sys

from sources import fetch_text, parse_spotify_input


class SpotifyPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.scripts: list[str] = []
        self._script = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            if key and attributes.get("content") is not None:
                self.meta[key] = attributes["content"].strip()
        if tag == "script":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def parse_track_page(html: str, track_id: str) -> dict:
    page = SpotifyPage()
    page.feed(html)
    name = page.meta.get("og:title", "")
    description = page.meta.get("og:description", "")
    parts = [part.strip() for part in description.split("·")]
    if not name or not description or not parts[0]:
        raise ValueError("Spotify page has no usable title or artist. The track may be unavailable.")
    artist = parts[0]
    album = parts[1] if len(parts) >= 4 else ""
    release = page.meta.get("music:release_date", "") or (parts[-1] if len(parts) >= 4 else "")
    year = re.search(r"\b(\d{4})\b", release)
    duration = page.meta.get("music:duration", "")
    duration_ms = round(float(duration) * 1000) if re.fullmatch(r"\d+(?:\.\d+)?", duration) else 0
    return {"id": track_id, "name": name, "artist": artist, "artists": [artist],
            "album": album, "album_art": page.meta.get("og:image", ""),
            "year": year.group(1) if year else "", "duration_ms": duration_ms}


def resolve_spotify(url: str) -> dict:
    track = parse_spotify_input(url, "track")
    html = fetch_text(track["url"], content_type="text/html")
    return parse_track_page(html, track["id"])


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: spotify.py <spotify_url>")
        print(json.dumps(resolve_spotify(sys.argv[1]), ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
