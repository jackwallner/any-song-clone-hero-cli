#!/usr/bin/env python3
"""Resolve playable tracks exposed by Spotify's public playlist embed."""

import json
import sys

from sources import fetch_text, parse_spotify_input
from spotify import SpotifyPage


def find_tracklist(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("trackList"), list):
            return obj
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        result = find_tracklist(child)
        if result is not None:
            return result
    return None


def parse_playlist_page(html: str) -> dict:
    page = SpotifyPage()
    page.feed(html)
    entity = None
    for script in page.scripts:
        try:
            entity = find_tracklist(json.loads(script))
        except (ValueError, RecursionError):
            continue
        if entity is not None:
            break
    if entity is None:
        raise ValueError("Could not find a public Spotify playlist track list")
    tracks, seen = [], set()
    skipped = 0
    for item in entity["trackList"]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            track = parse_spotify_input(item.get("uri", ""), "track")
        except ValueError:
            skipped += 1
            continue
        if track["id"] in seen:
            skipped += 1
            continue
        seen.add(track["id"])
        duration = item.get("duration", 0)
        tracks.append({"name": item.get("title") or "Unknown", "artist": item.get("subtitle") or "Unknown",
                       "spotify_url": track["url"], "duration_ms": duration if isinstance(duration, (int, float)) else 0})
    return {"playlist_name": entity.get("name") or page.meta.get("og:title") or "Playlist",
            "track_count": len(tracks), "skipped_count": skipped, "tracks": tracks}


def resolve_playlist(url: str) -> dict:
    playlist = parse_spotify_input(url, "playlist")
    html = fetch_text(f"https://open.spotify.com/embed/playlist/{playlist['id']}", content_type="text/html")
    return parse_playlist_page(html)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: playlist.py <spotify_playlist_url>")
        print(json.dumps(resolve_playlist(sys.argv[1]), ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
