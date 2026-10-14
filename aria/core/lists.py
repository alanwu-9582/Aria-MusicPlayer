"""User collections: playlists and the record shelf.

The shelf holds everything the listener collects: albums and playlists found online, and every
one of their own playlists and Smart Collections (those stand there by themselves)."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QObject, Signal

from aria import paths
from aria.core.models import Track
from aria.core.smart import SmartCollection
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
    kind: str = "album"               # album | playlist | smart
    playlist_id: str = ""             # the user's own playlist: tracks come from Playlists, live
    smart_id: str = ""                # a Smart Collection: tracks come from its rules, live
    custom_cover: str = ""            # a picture the listener chose (a file name in data/covers)

    @property
    def own(self) -> bool:
        """One of the listener's own lists (a playlist or a Smart Collection), not found online."""
        return bool(self.playlist_id or self.smart_id)

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
    """The user's playlists and Smart Collections (rule-based lists)."""

    changed = Signal()                   # added / removed / renamed / reordered lists
    playlist_changed = Signal(str)       # tracks of one list changed (or a smart list's rules)

    def __init__(self):
        super().__init__()
        data = read_json(paths.PLAYLISTS_FILE, {})
        self.items = [Playlist.from_dict(d) for d in data.get("playlists", [])]
        self.smart = [SmartCollection.from_dict(d) for d in data.get("smart", [])]
        self._saver = DebouncedSaver(paths.PLAYLISTS_FILE, lambda: {
            "playlists": [p.to_dict() for p in self.items], "smart": [s.to_dict() for s in self.smart]})

    def get(self, pid: str) -> Playlist | None:
        return next((p for p in self.items if p.id == pid), None)

    # ---- smart collections -----------------------------------------------------

    def get_smart(self, sid: str) -> SmartCollection | None:
        return next((s for s in self.smart if s.id == sid), None)

    def add_smart(self, sc: SmartCollection) -> SmartCollection:
        self.smart.append(sc)
        self._saver.schedule()
        self.changed.emit()
        return sc

    def update_smart(self, sc: SmartCollection) -> None:
        self._saver.schedule()
        self.changed.emit()
        self.playlist_changed.emit(sc.id)

    def delete_smart(self, sid: str) -> None:
        self.smart = [s for s in self.smart if s.id != sid]
        self._saver.schedule()
        self.changed.emit()

    # ---- playlists -------------------------------------------------------------

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

    def replace(self, old_key: str, new: Track) -> None:
        """Every playlist holding the song gets the other version in its place (Switch Source)."""
        for p in self.items:
            if not any(t.key == old_key for t in p.tracks):
                continue
            had_new = any(t.key == new.key for t in p.tracks)
            out = []
            for t in p.tracks:
                if t.key == old_key:
                    if not had_new:
                        out.append(new.copy())
                        had_new = True
                else:
                    out.append(t)
            p.tracks = out
            self._touch(p.id)

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


def _drop_cover(album: Album) -> None:
    """Delete the picture chosen for a shelf entry (it's going, or getting another one)."""
    if album.custom_cover:
        try:
            (paths.COVERS_DIR / album.custom_cover).unlink()
        except OSError:
            pass


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

    def entry_for(self, list_id: str) -> Album | None:
        """The shelf entry of one of the listener's playlists or Smart Collections."""
        return next((a for a in self.albums if list_id and list_id in (a.playlist_id, a.smart_id)), None)

    def sync_playlists(self, playlists: Playlists) -> None:
        """Every playlist and Smart Collection stands on the shelf: new ones go in front, renames
        follow, deleted ones come off."""
        changed = False
        for a in list(self.albums):
            if not a.own:
                continue
            p = playlists.get(a.playlist_id) if a.playlist_id else playlists.get_smart(a.smart_id)
            if p is None:
                _drop_cover(a)
                self.albums.remove(a)
                changed = True
            elif a.title != p.name:
                a.title = p.name
                changed = True
        have = {a.playlist_id or a.smart_id for a in self.albums if a.own}
        fresh = [Album(title=p.name, kind="playlist", playlist_id=p.id, source="aria")
                 for p in playlists.items if p.id not in have]
        fresh += [Album(title=s.name, kind="smart", smart_id=s.id, source="aria")
                  for s in playlists.smart if s.id not in have]
        if fresh:
            self.albums[:0] = fresh[::-1]                  # the newest first
            changed = True
        if changed:
            self._changed()

    def set_cover(self, aid: str, name: str) -> None:
        """A picture from data/covers for this entry ("" = back to the automatic cover)."""
        a = self.get(aid)
        if a is not None:
            _drop_cover(a)
            a.custom_cover = name
            self._changed()

    def add(self, album: Album) -> bool:
        if self.has(album.url):
            return False
        self.albums.insert(0, album)
        self._changed()
        return True

    def remove(self, aid: str) -> None:
        for a in self.albums:
            if a.id == aid:
                _drop_cover(a)
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
