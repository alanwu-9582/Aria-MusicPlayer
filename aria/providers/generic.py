"""SoundCloud and any other site yt-dlp understands."""

from __future__ import annotations

import re

from aria.core.models import SOUNDCLOUD, WEB, Stream, Track
from aria.providers import ytdlp
from aria.providers.base import Provider, audio_template


def _from_info(info: dict, source: str) -> Track | None:
    url = info.get("webpage_url") or info.get("url") or ""
    if not url:
        return None
    return Track(source=source, id=url, title=info.get("title") or url,
                 artist=info.get("uploader") or info.get("channel") or info.get("artist") or "",
                 url=url, thumbnail=ytdlp.best_thumbnail(info), duration=float(info.get("duration") or 0))


class YtDlpProvider(Provider):
    """Track id is the page url; yt-dlp does the rest."""

    def parse(self, url: str) -> list[Track]:
        entries = ytdlp.flat(url, limit=300)
        return [t for t in (_from_info(e, self.source) for e in entries) if t]

    def stream(self, track: Track) -> Stream:
        return ytdlp.stream_of(ytdlp.extract(track.id))

    def download(self, track: Track, progress=None) -> str:
        return ytdlp.download(track.id, audio_template(track), progress)


class SoundCloud(YtDlpProvider):
    source = SOUNDCLOUD
    searchable = True

    def match(self, url: str) -> bool:
        return bool(re.search(r"soundcloud\.com/", url))

    def search(self, query: str, limit: int = 20) -> list[Track]:
        out = []
        for e in ytdlp.flat(f"scsearch{limit}:{query}"):
            t = _from_info({**e, "webpage_url": e.get("webpage_url") or e.get("url")}, SOUNDCLOUD)
            if t:
                out.append(t)
        return out


class Web(YtDlpProvider):
    source = WEB

    def match(self, url: str) -> bool:
        return url.startswith(("http://", "https://"))
