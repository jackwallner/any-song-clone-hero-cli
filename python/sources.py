"""Bounded, certificate-verified source requests and Spotify input parsing."""

import json
import re
from typing import Optional
import urllib.parse
import urllib.request


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str]) -> None:
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in self.hosts or parsed.username or parsed.password:
            raise ValueError("Source redirected to an unexpected destination")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(url: str, *, limit: int = 4 * 1024 * 1024, timeout: int = 15,
               headers: Optional[dict] = None, data: Optional[bytes] = None,
               content_type: Optional[str] = None) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source must be an HTTPS URL")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "SongHero/1.0", **(headers or {})})
    opener = urllib.request.build_opener(SafeRedirect({parsed.hostname}))
    with opener.open(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type and declared and declared != content_type:
            raise ValueError(f"Unexpected source content type: {declared}")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Source response exceeded the size limit")
    return body.decode("utf-8", errors="replace")


def fetch_json(url: str, **kwargs) -> dict:
    return json.loads(fetch_text(url, content_type="application/json", **kwargs))


def parse_spotify_input(value: str, expected_type: Optional[str] = None) -> dict[str, str]:
    if not isinstance(value, str):
        raise ValueError("A Spotify URL or URI is required")
    match = re.fullmatch(r"spotify:(track|playlist):([A-Za-z0-9]{22})", value)
    if not match:
        parsed = urllib.parse.urlparse(value)
        match = re.fullmatch(r"/(?:intl-[a-z-]+/)?(?:embed/)?(track|playlist)/([A-Za-z0-9]{22})/?", parsed.path)
        if (parsed.scheme != "https" or parsed.hostname not in {"open.spotify.com", "play.spotify.com"}
                or parsed.username or parsed.password or parsed.port or not match):
            raise ValueError("Use a complete Spotify HTTPS link or URI with a valid 22-character ID")
    kind, spotify_id = match.groups()
    if expected_type and kind != expected_type:
        raise ValueError(f"Expected a Spotify {expected_type} link")
    return {"type": kind, "id": spotify_id, "url": f"https://open.spotify.com/{kind}/{spotify_id}"}
