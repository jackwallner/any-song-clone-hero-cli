#!/usr/bin/env python3
"""Resolve Spotify track URL to metadata using the embed page."""
import sys, json, re, ssl, urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

def resolve_spotify(url):
    track_id = None
    patterns = [
        r'spotify:track:(\w+)',
        r'open\.spotify\.com/track/(\w+)',
        r'play\.spotify\.com/track/(\w+)',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            track_id = m.group(1)
            break

    if not track_id:
        print(json.dumps({"error": "Could not extract track ID from URL"}))
        sys.exit(1)

    embed_url = f"https://open.spotify.com/embed/track/{track_id}"
    req = urllib.request.Request(embed_url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml'
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        # Extract the entity JSON from the embed page
        entity_match = re.search(r'"entity"\s*:\s*(\{[^}]+\})', html)
        if entity_match:
            entity_json = entity_match.group(1)
            
            # The entity might be truncated, extend the match
            # Try to find the full entity by looking for the closing structure
            full_match = re.search(r'"entity"\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})', html)
            if full_match:
                try:
                    entity = json.loads(full_match.group(1))
                    name = entity.get("name", entity.get("title", "Unknown"))
                    artists = entity.get("artists", [])
                    artist = artists[0]["name"] if artists else "Unknown"
                    
                    result = {
                        "id": track_id,
                        "name": name,
                        "artist": artist,
                        "artists": [a["name"] for a in artists],
                        "album": entity.get("album", {}).get("name", ""),
                        "album_art": "",
                        "year": entity.get("album", {}).get("release_date", "")[:4],
                        "duration_ms": entity.get("duration", {}).get("milliseconds", 0),
                    }
                    print(json.dumps(result))
                    return
                except (json.JSONDecodeError, KeyError):
                    pass

        # Fallback: extract from response HTML more broadly
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', html)
        artist_matches = re.findall(r'"name"\s*:\s*"([^"]+)"', html)
        
        name = title_match.group(1) if title_match else "Unknown"
        # The first "name" after the entity is usually the artist
        artist = artist_matches[0] if artist_matches else "Unknown"
        
        # Handle duplicates (first name is often the track name itself)
        if artist == name and len(artist_matches) > 1:
            artist = artist_matches[1]

        result = {
            "id": track_id,
            "name": name,
            "artist": artist,
            "artists": [artist],
            "album": "",
            "album_art": "",
            "year": "",
            "duration_ms": 0,
        }
        print(json.dumps(result))

    except Exception as e:
        print(json.dumps({"error": f"Failed to resolve: {e}"}))
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: spotify.py <spotify_url>"}))
        sys.exit(1)
    resolve_spotify(sys.argv[1])
