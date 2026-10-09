"""Platform registry: link detection, search, stream resolution (cached)."""

from __future__ import annotations

import os
import re
import threading

from aria.providers import http  # noqa: F401  (sets up TLS before anything connects)
from aria.core.models import BILIBILI, LOCAL, SOUNDCLOUD, SPOTIFY, YOUTUBE, Stream, Track
from aria.providers.base import Provider
from aria.providers.bilibili import Bilibili
from aria.providers.generic import SoundCloud, Web
from aria.providers.spotify import Spotify
from aria.providers.youtube import YouTube, video_id

youtube = YouTube()
bilibili = Bilibili()
spotify = Spotify(youtube)
soundcloud = SoundCloud()
web = Web()

# Order matters: the generic web provider is the fallback.
PROVIDERS: list[Provider] = [youtube, bilibili, spotify, soundcloud, web]
BY_SOURCE: dict[str, Provider] = {p.source: p for p in PROVIDERS}
SEARCHABLE = [YOUTUBE, BILIBILI, SOUNDCLOUD]

_URL = re.compile(r"^(https?://|spotify:)|^(www\.)?(youtube\.com|youtu\.be|bilibili\.com|b23\.tv|open\.spotify\.com|soundcloud\.com)/|^BV[0-9A-Za-z]{10}$")


def is_link(text: str) -> bool:
    return bool(_URL.search(text.strip()))


def normalize_link(text: str) -> str:
    text = text.strip()
    if text.startswith(("spotify:", "http://", "https://")) or text.startswith("BV"):
        return text
    return "https://" + text


def provider_for(url: str) -> Provider:
    for p in PROVIDERS:
        if p.match(url):
            return p
    raise ValueError("不支援的連結")


def detect(url: str) -> tuple[str, str] | None:
    """(source, id) for a single-track link without network access, else None."""
    vid = video_id(url)
    if vid:
        return YOUTUBE, vid
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m and "bilibili" in url:
        return BILIBILI, m.group(1)
    return None


def parse(text: str) -> list[Track]:
    url = normalize_link(text)
    return provider_for(url).parse(url)


def search(source: str, query: str, limit: int = 20) -> list[Track]:
    return BY_SOURCE[source].search(query, limit)


# ---- streams -----------------------------------------------------------------

_streams: dict[str, Stream] = {}
_streams_lock = threading.Lock()


def stream(track: Track, force: bool = False) -> Stream:
    """Playable location for a track. Local copies win; remote urls are cached until they expire."""
    if track.local_path and os.path.exists(track.local_path):
        return Stream(url=track.local_path)
    if track.source == LOCAL:
        raise FileNotFoundError(track.local_path or track.id)
    with _streams_lock:
        cached = _streams.get(track.key)
    if cached and cached.fresh and not force:
        return cached
    s = BY_SOURCE[track.source].stream(track)
    with _streams_lock:
        _streams[track.key] = s
    return s


def has_fresh_stream(track: Track) -> bool:
    with _streams_lock:
        s = _streams.get(track.key)
    return bool(s and s.fresh)


def download(track: Track, progress=None) -> str:
    return BY_SOURCE[track.source].download(track, progress)


__all__ = ["youtube", "bilibili", "spotify", "soundcloud", "web", "PROVIDERS", "SEARCHABLE",
           "is_link", "parse", "search", "stream", "download", "detect", "SPOTIFY"]
