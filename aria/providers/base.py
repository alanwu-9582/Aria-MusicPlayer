"""Provider interface: one per streaming platform."""

from __future__ import annotations

import contextvars
import re
from pathlib import Path
from typing import Callable

from aria import paths
from aria.core.models import Stream, Track
from aria.providers.errors import ProviderCapabilities

Progress = Callable[..., None]           # (fraction 0..1, bytes per second or None)


class Provider:
    source = ""
    capabilities = ProviderCapabilities()       # what this provider can do (callers ask this, not the source name)

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


FOLDER: contextvars.ContextVar[Path | None] = contextvars.ContextVar("audio_folder", default=None)


def audio_template(track: Track) -> str:
    """yt-dlp output template inside the audio folder (or ``FOLDER``, for a throwaway copy)."""
    named = track.artist and track.artist.casefold() not in track.title.casefold()
    base = safe_filename(f"{track.artist} - {track.title}" if named else track.title)
    return str((FOLDER.get() or paths.AUDIO_DIR) / f"{base} [{safe_filename(track.id, 40)}].%(ext)s")
