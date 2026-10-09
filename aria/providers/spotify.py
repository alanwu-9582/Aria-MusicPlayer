"""Spotify links. Spotify audio is DRM-protected, so tracks are read for their
metadata (embed page, no account needed) and played through a matching YouTube video."""

from __future__ import annotations

import json
import re
import threading

from aria.core import textnorm
from aria.core.models import SPOTIFY, Stream, Track
from aria.providers import http
from aria.providers.base import Provider
from aria.providers.youtube import YouTube

_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?|spotify:)(track|album|playlist)[/:]([A-Za-z0-9]{22})")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _cover(entity: dict) -> str:
    images = (entity.get("visualIdentity") or {}).get("image") or []
    if not images:
        images = (entity.get("coverArt") or {}).get("sources") or []
    return max(images, key=lambda i: i.get("maxWidth") or 0)["url"] if images else ""


def _artists(entity: dict) -> str:
    names = [a.get("name", "") for a in entity.get("artists") or []]
    return ", ".join(n for n in names if n) or (entity.get("subtitle") or "").replace("\xa0", " ")


class Spotify(Provider):
    source = SPOTIFY

    def __init__(self, youtube: YouTube):
        self.youtube = youtube
        self._matches: dict[str, str] = {}
        self._lock = threading.Lock()

    def match(self, url: str) -> bool:
        return bool(_LINK.search(url))

    def parse(self, url: str) -> list[Track]:
        m = _LINK.search(url)
        if not m:
            raise ValueError("無法辨識的 Spotify 連結")
        kind, sid = m.groups()
        page = http.get(f"https://open.spotify.com/embed/{kind}/{sid}").text
        data = _NEXT_DATA.search(page)
        if not data:
            raise RuntimeError("Spotify 頁面格式已變更")
        entity = json.loads(data.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        cover = _cover(entity)
        if kind == "track":
            return [Track(source=SPOTIFY, id=sid, title=entity.get("name") or entity.get("title", ""),
                          artist=_artists(entity), url=f"https://open.spotify.com/track/{sid}",
                          thumbnail=cover, duration=(entity.get("duration") or 0) / 1000)]
        tracks = []
        for item in entity.get("trackList") or []:
            tid = (item.get("uri") or "").rsplit(":", 1)[-1]
            if not tid:
                continue
            tracks.append(Track(source=SPOTIFY, id=tid, title=item.get("title", ""),
                                artist=(item.get("subtitle") or "").replace("\xa0", " "),
                                url=f"https://open.spotify.com/track/{tid}", thumbnail=cover,
                                duration=(item.get("duration") or 0) / 1000))
        return tracks

    def youtube_match(self, track: Track) -> Track:
        """Find the YouTube upload that best matches a Spotify track."""
        with self._lock:
            known = track.match_id or self._matches.get(track.id)
        if known:
            return Track(source="youtube", id=known, title=track.title, artist=track.artist)
        candidates = self.youtube.search(f"{track.artist} {track.title}", limit=6)
        if not candidates:
            raise RuntimeError("在 YouTube 找不到對應的歌曲")
        best = max(candidates, key=lambda c: _score(track, c))
        with self._lock:
            self._matches[track.id] = best.id
        track.match_id = best.id
        return best

    def stream(self, track: Track) -> Stream:
        return self.youtube.stream(self.youtube_match(track))

    def download(self, track: Track, progress=None) -> str:
        yt = self.youtube_match(track)
        return self.youtube.download(Track(source=yt.source, id=yt.id, title=track.title, artist=track.artist), progress)


def _score(want: Track, cand: Track) -> float:
    score = 0.0
    if textnorm.same_song(want.title, want.artist, cand.title, cand.artist):
        score += 3
    first_artist = want.artist.split(",")[0].strip().casefold()
    if first_artist and first_artist in f"{cand.title} {cand.artist}".casefold():
        score += 1.5
    if cand.artist.endswith(" - Topic"):
        score += 1          # auto-generated "official audio" uploads
    if textnorm.is_variant(cand.title) and not textnorm.is_variant(want.title):
        score -= 3
    if want.duration and cand.duration:
        score -= min(abs(want.duration - cand.duration) / 15, 3)
    return score
