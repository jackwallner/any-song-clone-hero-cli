#!/usr/bin/env python3
"""Scrape track listing from a Spotify playlist embed page.
Extracts the entity JSON blob from the Next.js __NEXT_DATA__ script."""
import sys, json, re, urllib.request


def find_tracklist(obj):
    """Recursively find the trackList array in a nested JSON object."""
    if isinstance(obj, dict):
        if "trackList" in obj and isinstance(obj["trackList"], list):
            return obj["trackList"]
        for v in obj.values():
            result = find_tracklist(v)
            if result:
                return result
    return None


def resolve_playlist(url):
    playlist_id = None
    patterns = [
        r"spotify:playlist:(\w+)",
        r"open\.spotify\.com/playlist/(\w+)",
        r"play\.spotify\.com/playlist/(\w+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            playlist_id = m.group(1)
            break

    if not playlist_id:
        print(json.dumps({"error": "Could not extract playlist ID from URL"}))
        sys.exit(1)

    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    req = urllib.request.Request(
        embed_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(json.dumps({"error": f"Failed to fetch playlist page: {e}"}))
        sys.exit(1)

    # Find the script tag containing trackList data
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    tracklist = None
    playlist_name = "Playlist"

    for script in scripts:
        if "trackList" not in script or len(script) < 1000:
            continue
        json_start = script.find("{")
        json_end = script.rfind("}") + 1
        if json_start < 0 or json_end <= json_start:
            continue
        try:
            data = json.loads(script[json_start:json_end])
        except json.JSONDecodeError:
            continue

        tl = find_tracklist(data)
        if tl:
            tracklist = tl

            # Find playlist name in the same data
            def find_name(obj):
                if isinstance(obj, dict):
                    if "name" in obj and isinstance(obj["name"], str) and "trackList" in obj:
                        return obj["name"]
                    for v in obj.values():
                        r = find_name(v)
                        if r:
                            return r
                return None

            name = find_name(data)
            if name:
                playlist_name = name
            break

    if not tracklist:
        print(json.dumps({"error": "Could not find track list in embed page"}))
        sys.exit(1)

    tracks = []
    for item in tracklist:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri", "")
        track_id = uri.replace("spotify:track:", "") if uri else ""
        tracks.append({
            "name": item.get("title", "Unknown"),
            "artist": item.get("subtitle", "Unknown"),
            "spotify_url": f"https://open.spotify.com/track/{track_id}" if track_id else "",
            "duration_ms": item.get("duration", 0),
        })

    result = {
        "playlist_name": playlist_name,
        "track_count": len(tracks),
        "tracks": tracks,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: playlist.py <spotify_playlist_url>"}))
        sys.exit(1)
    resolve_playlist(sys.argv[1])
