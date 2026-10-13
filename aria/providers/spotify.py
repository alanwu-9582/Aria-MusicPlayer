"""Spotify links. Spotify audio is DRM-protected, so tracks are read for their
metadata (embed page, no account needed) and played through a matching YouTube video.

Spotify has no search without an account, so finding things (a song's Spotify version, an
artist and the playlists around them) goes through a web search for open.spotify.com links,
each then read from Spotify itself."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from aria.core import textnorm
from aria.core.models import SPOTIFY, Stream, Track
from aria.providers import http
from aria.providers.base import Provider
from aria.providers.youtube import YouTube

_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?|spotify:)(track|album|playlist|artist)[/:]([A-Za-z0-9]{22})")
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

    def _entity(self, url: str) -> tuple[str, str, dict]:
        m = _LINK.search(url)
        if not m:
            raise ValueError("Unrecognised Spotify link")
        kind, sid = m.groups()
        page = http.get(f"https://open.spotify.com/embed/{kind}/{sid}").text
        data = _NEXT_DATA.search(page)
        if not data:
            raise RuntimeError("Spotify changed its page format")
        return kind, sid, json.loads(data.group(1))["props"]["pageProps"]["state"]["data"]["entity"]

    def album(self, url: str) -> dict:
        kind, sid, entity = self._entity(url)
        release = entity.get("releaseDate") or {}
        year = (release.get("isoString") or "")[:4] if isinstance(release, dict) else str(release)[:4]
        if kind == "artist":
            # an artist's page carries their top tracks: a playlist named after them
            name = entity.get("name") or entity.get("title", "")
            return {"title": f"{name} · Top Tracks", "artist": name, "cover": _cover(entity),
                    "url": f"https://open.spotify.com/artist/{sid}", "source": SPOTIFY, "year": "",
                    "tracks": self._tracks(kind, sid, entity), "kind": "playlist"}
        return {"title": entity.get("name") or entity.get("title", ""),
                "artist": (entity.get("subtitle") or _artists(entity)).replace("\xa0", " "),
                "cover": _cover(entity), "url": f"https://open.spotify.com/{kind}/{sid}",
                "source": SPOTIFY, "year": year, "tracks": self._tracks(kind, sid, entity),
                "kind": "album" if kind == "album" else "playlist"}

    def parse(self, url: str) -> list[Track]:
        kind, sid, entity = self._entity(url)
        return self._tracks(kind, sid, entity)

    def _tracks(self, kind: str, sid: str, entity: dict) -> list[Track]:
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

    # ---- finding things (no account: a web search for Spotify links) ------------------

    @staticmethod
    def find_links(query: str, kind: str = "track") -> list[str]:
        """Spotify ids of ``kind`` (track | artist | playlist | album) a web search turns up."""
        r = http.session().get("https://search.yahoo.com/search", params={"p": query}, timeout=http.TIMEOUT)
        pat = re.compile(rf"open\.spotify\.com/(?:intl-[\w-]+/)?{kind}/([A-Za-z0-9]{{22}})")
        return list(dict.fromkeys(pat.findall(unquote(unquote(r.text)))))

    def search_artist(self, query: str, playlists: int = 8) -> list[dict]:
        """An artist's top tracks plus public playlists of their songs, ready for the shelf
        (each as :meth:`album` returns it). Spotify's own editorial lists can't be read."""
        artists = self.find_links(f"{query} spotify artist", "artist")[:2]
        lists = self.find_links(f"{query} spotify playlist", "playlist")[:playlists]
        urls = [f"https://open.spotify.com/artist/{a}" for a in artists] + \
               [f"https://open.spotify.com/playlist/{p}" for p in lists]

        def read(url):
            try:
                return self.album(url)
            except Exception:
                return None                      # private, editorial or gone
        with ThreadPoolExecutor(5) as ex:
            found = [a for a in ex.map(read, urls) if a and a["tracks"]]
        names = [a["artist"] for a in found if "/artist/" in a["url"]] or [query]
        keys = {textnorm.artist_key(n) for n in names} | {textnorm.artist_key(query)}

        def by_them(a: dict) -> bool:
            if "/artist/" in a["url"]:
                return True
            mine = sum(1 for t in a["tracks"] if any(k and k in textnorm.artist_key(t.artist) for k in keys))
            return mine >= max(3, len(a["tracks"]) * 0.5)      # mostly their songs
        return [a for a in found if by_them(a)]

    def youtube_match(self, track: Track) -> Track:
        """Find the YouTube upload that best matches a Spotify track."""
        with self._lock:
            known = track.match_id or self._matches.get(track.id)
        if known:
            return Track(source="youtube", id=known, title=track.title, artist=track.artist)
        candidates = self.youtube.search(f"{track.artist} {track.title}", limit=6)
        if not candidates:
            raise RuntimeError("No matching song on YouTube")
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
