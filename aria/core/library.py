"""Saved tracks (the library) and their downloads."""

from __future__ import annotations

import logging
import os
import shutil
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
    added = Signal(list)                 # keys newly saved

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
        fresh: list[str] = []
        for t in reversed(tracks):
            if self._insert(t.copy(), 0):
                fresh.append(t.key)
        if fresh:
            self._changed()
            self.added.emit(fresh)
        return len(fresh)

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
                    continue
                from aria.core import lyrics
                lyrics.remove_sidecars(t.local_path)
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

    def move(self, src: int, dst: int) -> None:
        """Put a saved song somewhere else in the library's order (drag in the rack)."""
        if 0 <= src < len(self.tracks) and src != dst:
            t = self.tracks.pop(src)
            self.tracks.insert(max(0, min(dst, len(self.tracks))), t)
            self._changed()

    def replace(self, old_key: str, new: Track) -> None:
        """Swap a saved song for another version of it, in the same place (Switch Source).
        If that version is already saved, the two become one."""
        old = self._by_key.pop(old_key, None)
        if old is None:
            return
        i = self.tracks.index(old)
        have = self._by_key.get(new.key)
        if have is not None:
            del self.tracks[i]
            have.local_path = have.local_path or new.local_path
        else:
            t = new.copy()
            self.tracks[i] = t
            self._by_key[t.key] = t
        self._changed()

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
    """The download queue: one song at a time, each one checked before it counts.

    QUEUED → DOWNLOADING → VERIFYING → done (or failed). A song downloads into a staging folder
    inside the audio folder (``audio/.incoming``) and only moves into the audio folder — a rename
    on the same drive — once it's been checked (media_check): a half-written file, an error page
    or a cut-off download never shows up as offline audio. yt-dlp keeps its partial file there,
    so a download stopped half-way (quitting, a lost connection) continues where it was. The
    queue is kept on disk and picked up again at the next start. The same song asked for twice
    is one job. Before each song (and while it downloads) there must be room on the drive, or the
    queue pauses. Network trouble is tried once more at the end of the queue; lyrics are fetched
    afterwards and never decide whether the download worked.
    """

    progress = Signal(str, float)        # "Downloading 2/5 · title · 1.2 MB/s" (or "Verifying …"), 0..1 of the batch
    finished = Signal(str, str)          # key, path
    idle = Signal(int, int)              # ok, failed
    stopped = Signal(str)                # the queue paused, and why (no room on the drive)

    MIN_FREE = 300 * 1024 * 1024         # don't start a song with less free space than this
    STOP_FREE = 80 * 1024 * 1024         # stop a song that's downloading when it gets this low

    def __init__(self):
        super().__init__()
        self._pending: list[Track] = []
        self._current: Track | None = None
        self._retried: set[str] = set()
        self._busy = False
        self._ok = self._fail = 0
        self._total = 0
        self._saver = DebouncedSaver(paths.DOWNLOADS_FILE, self._snapshot, 600)

    @property
    def busy(self) -> bool:
        return self._busy

    def _snapshot(self) -> dict:
        jobs = ([self._current] if self._current is not None else []) + self._pending
        return {"version": 1, "jobs": [t.to_dict() for t in jobs]}

    def resume(self) -> int:
        """Pick up the downloads left unfinished last time (their partial files continue)."""
        jobs = [Track.from_dict(d) for d in read_json(paths.DOWNLOADS_FILE, {}).get("jobs", [])]
        n = self.add(jobs)
        if n:
            log.info("Continuing %d unfinished download%s", n, "s" if n != 1 else "")
        return n

    def add(self, tracks: list[Track]) -> int:
        queued = {t.key for t in self._pending} | ({self._current.key} if self._current else set())
        fresh = []
        for t in tracks:
            if t.key in queued or t.source == LOCAL or (t.local_path and os.path.exists(t.local_path)):
                continue                                    # one job per song
            queued.add(t.key)
            fresh.append(t)
        self._pending.extend(fresh)
        self._total += len(fresh)
        self._saver.schedule()
        if fresh and not self._busy:
            self._next()
        return len(fresh)

    def _room(self) -> int:
        try:
            paths.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(paths.AUDIO_DIR).free
        except OSError:
            return self.MIN_FREE

    def _next(self) -> None:
        self._current = None
        self._saver.schedule()
        if not self._pending:
            self._busy = False
            self.idle.emit(self._ok, self._fail)
            self._ok = self._fail = self._total = 0
            self._retried.clear()
            return
        if self._room() < self.MIN_FREE:
            self._busy = False
            log.error("↓ Paused: not enough free space for %d download%s", len(self._pending),
                      "s" if len(self._pending) != 1 else "")
            self.stopped.emit("Not enough free disk space — downloads paused")
            self._ok = self._fail = self._total = 0
            return                                          # kept on disk: they continue next time
        self._busy = True
        track = self._current = self._pending.pop(0)
        index = self._total - len(self._pending)
        total = max(1, self._total)
        title = track.title if len(track.title) <= 28 else track.title[:27] + "…"
        label = f"{index}/{total} · {title}" if total > 1 else title

        def bar(f: float) -> float:
            return (index - 1 + max(0.0, min(1.0, f))) / total

        def report(f: float, speed: float | None = None) -> None:
            # the bar covers the whole batch: finished songs plus the share of this one
            rate = f" · {speed / 1_048_576:.1f} MB/s" if speed and speed > 50_000 else ""
            self.progress.emit(f"Downloading {label}{rate}", bar(f))

        verifying = tasks.Progress(lambda _d, _t: self.progress.emit(f"Verifying {label}", bar(1.0)))
        report(0.0)

        def done(path) -> None:
            self._ok += 1
            log.info("↓ “%s”", track.title)
            self.finished.emit(track.key, path)
            self._next()

        def failed(exc: Exception) -> None:
            from aria.providers import errors
            failure = errors.classify(exc, track.source)
            if failure.retryable and track.key not in self._retried:
                self._retried.add(track.key)                # network trouble: once more, at the end
                self._pending.append(track)
                log.warning("↓ “%s” will be tried again: %s", track.title, failure.safe_message)
            else:
                self._fail += 1
                log.error("↓ Failed “%s”: %s", track.title, errors.redact(str(exc)))
            self._next()

        def work():
            path = self._fetch(track, report, verifying)
            try:                                         # its lyrics beside it (the best kind first); optional
                from aria.core import lyrics
                beside = track.copy()
                beside.local_path = path
                lyrics.fetch(beside, False, prefer_words=True)
            except Exception as exc:
                log.warning("No lyrics file for “%s”: %s", track.title, exc)
            return path

        tasks.run(work, done, failed, lane="download")

    def _fetch(self, track: Track, report, verifying) -> str:
        """Download into staging, check it, then publish it into the audio folder."""
        from aria.core import media_check
        from aria.providers import errors
        staging = paths.AUDIO_DIR / ".incoming"
        staging.mkdir(parents=True, exist_ok=True)

        def progress(f: float, speed: float | None = None) -> None:
            if self._room() < self.STOP_FREE:
                raise errors.ProviderError(errors.ProviderFailure(errors.ErrorKind.CANCELLED, track.source, False,
                                                                  None, "Not enough free disk space"))
            report(f, speed)

        staged = providers.download_to(track, staging, progress)
        verifying.emit(1, 1)
        ok, why = media_check.verify(staged, track.duration)
        if not ok:
            try:
                os.remove(staged)
            except OSError:
                pass
            raise errors.ProviderError(errors.ProviderFailure(errors.ErrorKind.UNPLAYABLE, track.source, False, None,
                                                              "The download wasn’t valid audio"), f"rejected: {why}")
        final = paths.AUDIO_DIR / Path(staged).name
        if final.exists() and media_check.verify(str(final), track.duration)[0]:
            os.remove(staged)                               # a good copy is there already: keep that one
            return str(final)
        os.replace(staged, final)                           # same drive: the file appears whole, at once
        return str(final)
