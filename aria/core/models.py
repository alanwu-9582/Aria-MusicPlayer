"""Plain data types shared by every layer."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields

YOUTUBE = "youtube"
BILIBILI = "bilibili"
SPOTIFY = "spotify"
SOUNDCLOUD = "soundcloud"
WEB = "web"          # anything else yt-dlp understands
LOCAL = "local"

SOURCE_LABELS = {
    YOUTUBE: "YouTube",
    BILIBILI: "Bilibili",
    SPOTIFY: "Spotify",
    SOUNDCLOUD: "SoundCloud",
    WEB: "Web",
    LOCAL: "Local",
}


@dataclass
class Track:
    source: str
    id: str                       # id inside the source (video id, BV id, spotify id, url…)
    title: str
    artist: str = ""
    url: str = ""                 # canonical page url
    thumbnail: str = ""
    duration: float = 0.0         # seconds, 0 = unknown
    local_path: str = ""          # downloaded copy
    # Spotify (DRM) tracks are played through a matched YouTube video.
    match_id: str = ""
    added_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Track:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def copy(self) -> Track:
        return Track.from_dict(self.to_dict())


@dataclass
class Stream:
    """A playable location for a track, possibly short-lived."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: float = 0.0       # epoch seconds, 0 = never

    @property
    def fresh(self) -> bool:
        return not self.expires_at or self.expires_at - time.time() > 60


def format_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "–:––"
    s = int(round(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
