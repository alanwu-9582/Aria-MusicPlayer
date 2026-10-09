"""YouTube via yt-dlp."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import parse_qs, quote, urlparse

from aria.core import textnorm
from aria.core.models import YOUTUBE, Stream, Track
from aria.providers import ytdlp
from aria.providers.base import Provider, audio_template

_ID = re.compile(r"^[\w-]{11}$")


def video_id(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        else:
            m = re.match(r"/(?:shorts|embed|live|v)/([\w-]{11})", u.path)
            vid = m.group(1) if m else ""
    else:
        return None
    return vid if _ID.match(vid or "") else None


def playlist_id(url: str) -> str | None:
    u = urlparse(url)
    if "youtube.com" not in (u.hostname or "") and "youtu.be" not in (u.hostname or ""):
        return None
    pid = parse_qs(u.query).get("list", [""])[0]
    # RD… are endless auto-generated mixes, treat the link as a single video.
    return pid if pid and not pid.startswith("RD") else None


def thumb(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def from_entry(e: dict) -> Track | None:
    vid = e.get("id") or ""
    if not _ID.match(vid):
        return None
    return Track(
        source=YOUTUBE, id=vid,
        title=e.get("title") or vid,
        artist=e.get("channel") or e.get("uploader") or "",
        url=watch_url(vid), thumbnail=thumb(vid),
        duration=float(e.get("duration") or 0),
    )


class YouTube(Provider):
    source = YOUTUBE
    searchable = True

    def match(self, url: str) -> bool:
        return bool(video_id(url) or playlist_id(url))

    def search(self, query: str, limit: int = 20) -> list[Track]:
        return [t for t in map(from_entry, ytdlp.flat(f"ytsearch{limit}:{query}")) if t]

    def parse(self, url: str) -> list[Track]:
        pid = playlist_id(url)
        if pid and not video_id(url):
            entries = ytdlp.flat(f"https://www.youtube.com/playlist?list={pid}", limit=300)
            return [t for t in map(from_entry, entries) if t]
        vid = video_id(url)
        if not vid:
            raise ValueError("Unrecognised YouTube link")
        info = ytdlp.extract(watch_url(vid))
        t = from_entry(info)
        if not t:
            raise ValueError("Video not found")
        return [t]

    def stream(self, track: Track) -> Stream:
        return ytdlp.stream_of(ytdlp.extract(watch_url(track.id)))

    def download(self, track: Track, progress=None) -> str:
        return ytdlp.download(watch_url(track.id), audio_template(track), progress)

    def album(self, url: str) -> dict:
        pid = playlist_id(url)
        if not pid:
            raise ValueError("Not a playlist link")
        info = ytdlp.flat_info(f"https://www.youtube.com/playlist?list={pid}", limit=200)
        tracks = [t for t in map(from_entry, info.get("entries") or []) if t]
        if not tracks:
            raise ValueError("The playlist is empty")
        artists = Counter(textnorm.artist_key(textnorm.artist_of(t.title, t.artist)) for t in tracks)
        top = artists.most_common(1)[0][0] if artists else ""
        artist = next((textnorm.artist_of(t.title, t.artist) for t in tracks
                       if textnorm.artist_key(textnorm.artist_of(t.title, t.artist)) == top), "")
        title = re.sub(r"^(album\s*[-–:]\s*)", "", info.get("title") or "", flags=re.IGNORECASE)
        return {"title": title, "artist": artist or textnorm.clean_channel(info.get("channel") or ""),
                "cover": f"https://i.ytimg.com/vi/{tracks[0].id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/playlist?list={pid}", "source": YOUTUBE,
                "year": (info.get("modified_date") or "")[:4], "tracks": tracks,
                # YouTube Music album playlists start with OLAK5uy_
                "kind": "album" if pid.startswith("OLAK5uy_") or "album" in title.casefold() else "playlist"}

    def search_albums(self, query: str, limit: int = 12) -> list[dict]:
        """Playlists matching ``query`` (YouTube's playlist search filter)."""
        url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAw%253D%253D"
        out = []
        for e in ytdlp.flat(url, limit=limit):
            pid = e.get("id") or ""
            thumbs = e.get("thumbnails") or []
            vid = re.search(r"/vi/([\w-]{11})/", thumbs[-1]["url"]) if thumbs else None
            out.append({"title": e.get("title") or pid, "artist": textnorm.clean_channel(e.get("channel") or ""),
                        "cover": f"https://i.ytimg.com/vi/{vid.group(1)}/hqdefault.jpg" if vid else "",
                        "url": f"https://www.youtube.com/playlist?list={pid}", "source": YOUTUBE,
                        "kind": "album" if pid.startswith("OLAK5uy_") else "playlist"})
        return out

    def mix(self, vid: str, limit: int = 30, music: bool = True) -> list[Track]:
        """YouTube Music radio for a video (``music``), or the regular YouTube mix: songs related to it."""
        entries = ytdlp.flat(f"https://www.youtube.com/watch?v={vid}&list={'RDAMVM' if music else 'RD'}{vid}",
                             limit=limit)
        return [t for t in map(from_entry, entries) if t and t.id != vid]
