"""Provider interface: one per streaming platform."""

from __future__ import annotations

import re
from typing import Callable

from aria import paths
from aria.core.models import Stream, Track

Progress = Callable[[float], None]


class Provider:
    source = ""
    searchable = False

    def match(self, url: str) -> bool:
        """Whether this provider handles ``url``."""
        return False

    def search(self, query: str, limit: int = 20) -> list[Track]:
        raise NotImplementedError

    def parse(self, url: str) -> list[Track]:
        """Turn a track / playlist / album link into tracks."""
        raise NotImplementedError

    def stream(self, track: Track) -> Stream:
        raise NotImplementedError

    def download(self, track: Track, progress: Progress | None = None) -> str:
        raise NotImplementedError


def safe_filename(name: str, limit: int = 80) -> str:
    name = re.sub(r'[\/:*?"<>|\r\n\t]+', " ", name).strip(" .")
    return name[:limit] or "track"


def audio_template(track: Track) -> str:
    """yt-dlp output template inside the audio folder."""
    named = track.artist and track.artist.casefold() not in track.title.casefold()
    base = safe_filename(f"{track.artist} - {track.title}" if named else track.title)
    return str(paths.AUDIO_DIR / f"{base} [{safe_filename(track.id, 40)}].%(ext)s")
