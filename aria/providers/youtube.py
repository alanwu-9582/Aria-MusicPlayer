"""YouTube via yt-dlp."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

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
            raise ValueError("無法辨識的 YouTube 連結")
        info = ytdlp.extract(watch_url(vid))
        t = from_entry(info)
        if not t:
            raise ValueError("找不到影片")
        return [t]

    def stream(self, track: Track) -> Stream:
        return ytdlp.stream_of(ytdlp.extract(watch_url(track.id)))

    def download(self, track: Track, progress=None) -> str:
        return ytdlp.download(watch_url(track.id), audio_template(track), progress)

    def mix(self, vid: str, limit: int = 30) -> list[Track]:
        """YouTube Music radio for a video: songs related to it."""
        entries = ytdlp.flat(f"https://www.youtube.com/watch?v={vid}&list=RDAMVM{vid}", limit=limit)
        return [t for t in map(from_entry, entries) if t and t.id != vid]
