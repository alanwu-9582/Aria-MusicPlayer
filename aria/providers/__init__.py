"""Platform registry: link detection, search, stream resolution (cached)."""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import Future

from aria.providers import http  # noqa: F401  (sets up TLS before anything connects)
from aria.core.models import BILIBILI, LOCAL, SPOTIFY, YOUTUBE, Stream, Track
from aria.providers import errors
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
SEARCHABLE = [p.source for p in PROVIDERS if p.capabilities.search]       # youtube, bilibili, soundcloud

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
    raise ValueError("Unsupported link")


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


def album(text: str) -> dict:
    """Album / playlist link → {title, artist, cover, url, source, year, tracks}."""
    url = normalize_link(text)
    p = provider_for(url)
    if not hasattr(p, "album"):
        raise ValueError("That link isn’t an album or playlist")
    return p.album(url)


def search_albums(query: str, limit: int = 12) -> list[dict]:
    return youtube.search_albums(query, limit)


# ---- streams -----------------------------------------------------------------

_streams: dict[str, Stream] = {}
_streams_lock = threading.Lock()
_inflight: dict[str, Future] = {}          # one resolution per song at a time; others wait for it
WAIT = 90.0                                # longest a second caller waits for the first one's result


def stream(track: Track, force: bool = False, background: bool = False) -> Stream:
    """Playable location for a track. Local copies win; remote urls are cached until they expire
    (the expiry the address itself carries). Two callers asking for the same song at once share
    one resolution; network trouble is retried a few times (bounded); a provider that asked for a
    pause (429) isn't troubled by ``background`` work (prefetch) until it's over. Failures come
    back as :class:`errors.ProviderError` (classified)."""
    if track.local_path and os.path.exists(track.local_path):
        return Stream(url=track.local_path)
    if track.source == LOCAL:
        raise errors.ProviderError(errors.ProviderFailure(errors.ErrorKind.NOT_FOUND, LOCAL, False, None,
                                                          "The file isn’t there any more"), track.local_path or track.id)
    with _streams_lock:
        cached = _streams.get(track.key)
    if cached and cached.fresh and not force:
        return cached
    provider = BY_SOURCE[track.source]
    if background and errors.cooldowns.cooling(provider.source):
        raise errors.ProviderError(errors.ProviderFailure(errors.ErrorKind.RATE_LIMITED, provider.source, False,
                                                          errors.cooldowns.cooling(provider.source),
                                                          errors.MESSAGES[errors.ErrorKind.RATE_LIMITED]))
    with _streams_lock:
        pending = _inflight.get(track.key)
        if pending is None:
            pending = _inflight[track.key] = Future()
            mine = True
        else:
            mine = False
    if not mine:
        return pending.result(timeout=WAIT)          # the same song is being resolved: its answer is ours
    try:
        s = errors.retry(lambda: provider.stream(track), provider.source)
    except Exception as exc:
        pending.set_exception(exc)
        raise
    else:
        with _streams_lock:
            _streams[track.key] = s
        pending.set_result(s)
        return s
    finally:
        with _streams_lock:
            if _inflight.get(track.key) is pending:
                del _inflight[track.key]


def forget_stream(track: Track) -> None:
    """The cached address stopped working (403, expired): only this song's entry goes."""
    with _streams_lock:
        _streams.pop(track.key, None)


def has_fresh_stream(track: Track) -> bool:
    return cached_stream(track) is not None


def cached_stream(track: Track, local: bool = True) -> Stream | None:
    """A ready-to-play stream without any network access, or None."""
    if local and track.local_path and os.path.exists(track.local_path):
        return Stream(url=track.local_path)
    with _streams_lock:
        s = _streams.get(track.key)
    return s if s and s.fresh else None


def remote_stream(track: Track) -> Stream:
    """The online stream even when a downloaded copy exists (for switching back)."""
    return stream(track.copy() if not track.local_path else Track.from_dict({**track.to_dict(), "local_path": ""}))


def download(track: Track, progress=None) -> str:
    return BY_SOURCE[track.source].download(track, progress)


def download_to(track: Track, folder, progress=None) -> str:
    """A copy in ``folder`` instead of the audio folder (never linked to the library)."""
    from pathlib import Path
    from aria.providers import base
    Path(folder).mkdir(parents=True, exist_ok=True)
    token = base.FOLDER.set(Path(folder))
    try:
        return download(track, progress)
    finally:
        base.FOLDER.reset(token)


__all__ = ["youtube", "bilibili", "spotify", "soundcloud", "web", "PROVIDERS", "SEARCHABLE",
           "is_link", "parse", "search", "stream", "download", "detect", "SPOTIFY"]
