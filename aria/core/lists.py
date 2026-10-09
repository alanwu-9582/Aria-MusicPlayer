"""User collections: playlists and the record shelf (albums)."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QObject, Signal

from aria import paths
from aria.core.models import Track
from aria.core.storage import DebouncedSaver, read_json


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Playlist:
    name: str
    id: str = field(default_factory=_new_id)
    tracks: list[Track] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created,
                "tracks": [t.to_dict() for t in self.tracks]}

    @classmethod
    def from_dict(cls, d: dict) -> Playlist:
        return cls(name=d.get("name", "Playlist"), id=d.get("id") or _new_id(), created=d.get("created", 0),
                   tracks=[Track.from_dict(t) for t in d.get("tracks", [])])


@dataclass
class Album:
    title: str
    artist: str = ""
    cover: str = ""
    url: str = ""
    source: str = ""
    year: str = ""
    tracks: list[Track] = field(default_factory=list)
    id: str = field(default_factory=_new_id)
    added: float = field(default_factory=time.time)
    kind: str = "album"               # album | playlist
    playlist_id: str = ""             # set for the user's own playlists: tracks come from Playlists, live

    @property
    def duration(self) -> float:
        return sum(t.duration for t in self.tracks)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tracks"] = [t.to_dict() for t in self.tracks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Album:
        d = dict(d)
        tracks = [Track.from_dict(t) for t in d.pop("tracks", [])]
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known, tracks=tracks)


class Playlists(QObject):
    changed = Signal()                   # added / removed / renamed / reordered lists
    playlist_changed = Signal(str)       # tracks of one list changed

    def __init__(self):
        super().__init__()
        self.items = [Playlist.from_dict(d) for d in read_json(paths.PLAYLISTS_FILE, {}).get("playlists", [])]
        self._saver = DebouncedSaver(paths.PLAYLISTS_FILE, lambda: {"playlists": [p.to_dict() for p in self.items]})

    def get(self, pid: str) -> Playlist | None:
        return next((p for p in self.items if p.id == pid), None)

    def create(self, name: str, tracks: list[Track] | None = None) -> Playlist:
        p = Playlist(name=name.strip() or "Untitled Playlist", tracks=[t.copy() for t in tracks or []])
        self.items.append(p)
        self._saver.schedule()
        self.changed.emit()
        return p

    def rename(self, pid: str, name: str) -> None:
        p = self.get(pid)
        if p and name.strip():
            p.name = name.strip()
            self._saver.schedule()
            self.changed.emit()

    def delete(self, pid: str) -> None:
        self.items = [p for p in self.items if p.id != pid]
        self._saver.schedule()
        self.changed.emit()

    def add(self, pid: str, tracks: list[Track]) -> int:
        p = self.get(pid)
        if not p:
            return 0
        have = {t.key for t in p.tracks}
        fresh = []
        for t in tracks:
            if t.key not in have:
                have.add(t.key)
                fresh.append(t.copy())
        p.tracks.extend(fresh)
        self._touch(pid)
        return len(fresh)

    def remove(self, pid: str, rows: list[int]) -> None:
        p = self.get(pid)
        if p:
            for i in sorted(set(rows), reverse=True):
                if 0 <= i < len(p.tracks):
                    del p.tracks[i]
            self._touch(pid)

    def move(self, pid: str, src: int, dst: int) -> None:
        p = self.get(pid)
        if p and 0 <= src < len(p.tracks):
            t = p.tracks.pop(src)
            p.tracks.insert(max(0, min(dst, len(p.tracks))), t)
            self._touch(pid)

    def set_local(self, key: str, path: str) -> None:
        for p in self.items:
            for t in p.tracks:
                if t.key == key:
                    t.local_path = path
        self._saver.schedule()

    def _touch(self, pid: str) -> None:
        self._saver.schedule()
        self.playlist_changed.emit(pid)

    def flush(self) -> None:
        self._saver.flush()


class Shelf(QObject):
    """Albums in the user's own order."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.albums = [Album.from_dict(d) for d in read_json(paths.SHELF_FILE, {}).get("albums", [])]
        self._saver = DebouncedSaver(paths.SHELF_FILE, lambda: {"albums": [a.to_dict() for a in self.albums]})

    def get(self, aid: str) -> Album | None:
        return next((a for a in self.albums if a.id == aid), None)

    def has(self, url: str) -> bool:
        return any(a.url == url for a in self.albums if url)

    def has_playlist(self, pid: str) -> bool:
        return any(a.playlist_id == pid for a in self.albums)

    def add_playlist(self, playlist: Playlist) -> bool:
        if self.has_playlist(playlist.id):
            return False
        self.albums.insert(0, Album(title=playlist.name, kind="playlist", playlist_id=playlist.id, source="aria"))
        self._changed()
        return True

    def sync_playlists(self, playlists: Playlists) -> None:
        """Follow renames; drop shelf entries whose playlist was deleted."""
        changed = False
        for a in list(self.albums):
            if not a.playlist_id:
                continue
            p = playlists.get(a.playlist_id)
            if p is None:
                self.albums.remove(a)
                changed = True
            elif a.title != p.name:
                a.title = p.name
                changed = True
        if changed:
            self._changed()

    def add(self, album: Album) -> bool:
        if self.has(album.url):
            return False
        self.albums.insert(0, album)
        self._changed()
        return True

    def remove(self, aid: str) -> None:
        self.albums = [a for a in self.albums if a.id != aid]
        self._changed()

    def move(self, src: int, dst: int) -> None:
        if 0 <= src < len(self.albums) and src != dst:
            a = self.albums.pop(src)
            self.albums.insert(max(0, min(dst, len(self.albums))), a)
            self._changed()

    def _changed(self) -> None:
        self._saver.schedule()
        self.changed.emit()

    def flush(self) -> None:
        self._saver.flush()
