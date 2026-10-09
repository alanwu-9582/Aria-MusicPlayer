"""Saved tracks (the library) and their downloads."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from aria import paths, providers
from aria.core import tasks
from aria.core.models import LOCAL, Track
from aria.core.storage import DebouncedSaver, legacy_track, read_json, write_json

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm", ".wma", ".mka"}


class Library(QObject):
    changed = Signal()
    track_updated = Signal(str)          # key

    def __init__(self, tracks: list[Track] | None = None):
        super().__init__()
        data = read_json(paths.LIBRARY_FILE, {})
        loaded = tracks if tracks is not None else [Track.from_dict(d) for d in data.get("tracks", [])]
        self.tracks: list[Track] = []
        self._by_key: dict[str, Track] = {}
        for t in loaded:
            self._insert(t, len(self.tracks))
        self._saver = DebouncedSaver(paths.LIBRARY_FILE, self._snapshot)
        if tracks is not None:
            self._saver.flush()

    def _snapshot(self) -> dict:
        return {"version": 2, "tracks": [t.to_dict() for t in self.tracks]}

    def _insert(self, t: Track, index: int) -> bool:
        if t.key in self._by_key:
            return False
        self.tracks.insert(index, t)
        self._by_key[t.key] = t
        return True

    def flush(self) -> None:
        self._saver.flush()

    def _changed(self) -> None:
        self._saver.schedule()
        self.changed.emit()

    # ---- queries -------------------------------------------------------------

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def get(self, key: str) -> Track | None:
        return self._by_key.get(key)

    def __len__(self) -> int:
        return len(self.tracks)

    # ---- edits -------------------------------------------------------------

    def add(self, tracks: list[Track]) -> int:
        """Newest on top. Returns how many were new."""
        added = 0
        for t in reversed(tracks):
            if self._insert(t.copy(), 0):
                added += 1
        if added:
            self._changed()
        return added

    def toggle(self, track: Track) -> bool:
        """Save or un-save; returns the new saved state."""
        if track.key in self:
            self.remove([track.key], delete_files=False)
            return False
        self.add([track])
        return True

    def remove(self, keys: list[str], delete_files: bool = False) -> None:
        keyset = set(keys)
        for t in [t for t in self.tracks if t.key in keyset]:
            self.tracks.remove(t)
            del self._by_key[t.key]
            if delete_files and t.local_path and t.source != LOCAL:
                try:
                    os.remove(t.local_path)
                except OSError:
                    pass
        self._changed()

    def update(self, track: Track) -> None:
        mine = self._by_key.get(track.key)
        if not mine:
            return
        for field in ("title", "artist", "thumbnail", "duration", "local_path", "match_id"):
            value = getattr(track, field)
            if value:
                setattr(mine, field, value)
        self._saver.schedule()
        self.track_updated.emit(track.key)

    def set_local(self, key: str, path: str) -> None:
        t = self._by_key.get(key)
        if t:
            t.local_path = path
            self._saver.schedule()
            self.track_updated.emit(key)

    # ---- files ---------------------------------------------------------------

    def import_file(self, path: str) -> int:
        data = read_json(Path(path), None)
        if data is None:
            raise ValueError("Couldn’t read the playlist file")
        if "tracks" in data:
            tracks = [Track.from_dict(d) for d in data["tracks"]]
        else:  # v1 export: {"saved": [{title, author, watch_url, …}]}
            tracks = [t for t in map(legacy_track, data.get("saved", [])) if t]
        for t in tracks:
            if t.local_path and not os.path.exists(t.local_path):
                t.local_path = ""
        self._relink_downloads(tracks)
        return self.add(tracks)

    def export_file(self, path: str) -> int:
        tracks = [dict(t.to_dict(), local_path="") for t in self.tracks if t.source != LOCAL]
        write_json(Path(path), {"version": 2, "tracks": tracks})
        return len(tracks)

    @staticmethod
    def _relink_downloads(tracks: list[Track]) -> None:
        """Re-attach files already in the audio folder (they carry [id] in the name)."""
        try:
            files = os.listdir(paths.AUDIO_DIR)
        except OSError:
            return
        for t in tracks:
            if t.local_path:
                continue
            tag = f"[{t.id}]"
            hit = next((f for f in files if tag in f and not f.endswith(".part")), None)
            if hit:
                t.local_path = str(paths.AUDIO_DIR / hit)


def local_track(path: str) -> Track:
    p = Path(path)
    return Track(source=LOCAL, id=str(p), title=p.stem, url=p.as_uri(), local_path=str(p))


class Downloader(QObject):
    """Sequential download queue."""

    progress = Signal(str, float)        # label, 0..1
    finished = Signal(str, str)          # key, path
    idle = Signal(int, int)              # ok, failed

    def __init__(self):
        super().__init__()
        self._pending: list[Track] = []
        self._busy = False
        self._ok = self._fail = 0
        self._total = 0

    @property
    def busy(self) -> bool:
        return self._busy

    def add(self, tracks: list[Track]) -> int:
        queued = {t.key for t in self._pending}
        fresh = [t for t in tracks if t.key not in queued and t.source != LOCAL
                 and not (t.local_path and os.path.exists(t.local_path))]
        self._pending.extend(fresh)
        self._total += len(fresh)
        if fresh and not self._busy:
            self._next()
        return len(fresh)

    def _next(self) -> None:
        if not self._pending:
            self._busy = False
            self.idle.emit(self._ok, self._fail)
            self._ok = self._fail = self._total = 0
            return
        self._busy = True
        track = self._pending.pop(0)
        index = self._total - len(self._pending)
        label = f"{index}/{self._total}"
        self.progress.emit(label, 0.0)
        report = lambda f: self.progress.emit(label, f)  # noqa: E731  (called from the worker; signal is queued)

        def done(path: str):
            self._ok += 1
            log.info("Downloaded %s", track.title)
            self.finished.emit(track.key, path)
            self._next()

        def failed(exc: Exception):
            self._fail += 1
            log.error("Download failed %s: %s", track.title, exc)
            self._next()

        tasks.run(lambda: providers.download(track, report), done, failed)
