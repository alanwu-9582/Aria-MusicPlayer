"""Activity log: what Aria is doing, in plain words, for the Console.

Listens to signals the app already has (nothing here changes behaviour) and writes one line
per meaningful event — songs starting, pauses, queue and library changes, downloads, BPMs,
outputs, volume. Bursty things (dragging the volume, a queue being filled) are settled first,
so one gesture is one line.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from aria.core.models import format_duration
from aria.core.player import PAUSED, PLAYING

log = logging.getLogger("activity")


def _settle(parent: QObject, ms: int, fn) -> QTimer:
    t = QTimer(parent)
    t.setSingleShot(True)
    t.setInterval(ms)
    t.timeout.connect(fn)
    return t


class Activity(QObject):
    def __init__(self, playback, library, downloader, playlists, devices=None, balance=None):
        super().__init__()
        self.pb, self.library, self.playlists = playback, library, playlists
        self._state = ""
        self._queue = len(playback.queue)
        self._library = len(library)
        self._lists = len(playlists.items)
        self._album = None
        self._timer = None
        self._downloading: set[str] = set()

        playback.current_changed.connect(self._on_track)
        playback.state_changed.connect(self._on_state)
        playback.source_changed.connect(lambda local: log.info("↓ Offline file" if local else "Streaming"))
        playback.mode_changed.connect(self._on_mode)
        self._queue_t = _settle(self, 700, self._on_queue)
        playback.queue_changed.connect(self._queue_t.start)
        self._recs_t = _settle(self, 700, self._on_recs)
        playback.recommendations_changed.connect(self._recs_t.start)
        self._volume_t = _settle(self, 800, lambda: log.info("Volume %d%%", playback.player.volume))
        playback.volume_changed.connect(lambda _v: self._volume_t.start())
        if playback.tempo is not None:
            playback.tempo.updated.connect(self._on_bpm)

        library.added.connect(self._on_saved)
        library.changed.connect(self._on_library)
        playlists.changed.connect(self._on_lists)
        downloader.progress.connect(self._on_download)
        downloader.idle.connect(self._on_downloads_done)
        if devices is not None:
            devices.devices_changed.connect(lambda: log.info("Outputs changed · %d", len(devices.outputs())))
        if balance is not None:
            self._balance_t = _settle(self, 800, lambda: log.info("Balance %+d", round(balance.value * 100)))
            balance.changed.connect(lambda _v: self._balance_t.start())

    # ---- playback ------------------------------------------------------------

    def _title(self, key: str) -> str:
        pb = self.pb
        for t in ([pb.current] if pb.current else []) + pb.queue + pb.recommendations:
            if t.key == key:
                return t.title
        t = self.library.get(key)
        return t.title if t else key

    def _on_track(self, t) -> None:
        if t is None:
            log.info("Stopped")
            self._last = None
            return
        if t is getattr(self, "_last", None):
            return                                  # the same song, changed in place (Switch Source)
        self._last = t
        parts = [f"▶ “{t.title}”", t.source_label]
        if t.duration:
            parts.append(format_duration(t.duration))
        if t.local_path:
            parts.append("offline")
        log.info(" · ".join(parts))

    def _on_state(self, s: str) -> None:
        if s == PAUSED and self._state == PLAYING:
            log.info("‖ Paused · %s", format_duration(self.pb.player.position()))
        elif s == PLAYING and self._state == PAUSED:
            log.info("▶ Resumed")
        self._state = s

    def _on_mode(self) -> None:
        album, timer = self.pb.album, self.pb.timer
        if album is not None and self._album is None:
            log.info("Album “%s” · %d songs", album.get("title", ""), len(album.get("keys", [])))
        elif album is None and self._album is not None:
            log.info("Album mode off")
        if timer is not None and self._timer is None:
            log.info("Timer %s · %d songs", format_duration(timer.get("total", 0)), len(timer.get("keys", [])))
        elif timer is None and self._timer is not None:
            log.info("Timer off")
        self._album, self._timer = album, timer

    def _on_queue(self) -> None:
        n = len(self.pb.queue)
        if n != self._queue:
            diff = n - self._queue
            log.info("Queue %d (%+d)", n, diff)
            self._queue = n

    def _on_recs(self) -> None:
        recs = self.pb.recommendations
        if recs:
            log.info("For You · %d songs", len(recs))

    def _on_bpm(self, key: str) -> None:
        e = self.pb.tempo.data.get(key) or {}
        if e.get("bpm"):
            log.info("“%s” · %d BPM · %d%%", self._title(key), round(e["bpm"]), round(e.get("conf", 0) * 100))

    # ---- library -------------------------------------------------------------

    def _on_saved(self, keys: list) -> None:
        if len(keys) == 1:
            log.info("♥ “%s”", self._title(keys[0]))
        else:
            log.info("♥ %d songs", len(keys))

    def _on_library(self) -> None:
        n = len(self.library)
        if n < self._library:
            k = self._library - n
            log.info("Removed %d · %d left", k, n)
        self._library = n

    def _on_lists(self) -> None:
        n = len(self.playlists.items)
        if n > self._lists:
            log.info("+ Playlist “%s”", self.playlists.items[-1].name if self.playlists.items else "")
        elif n < self._lists:
            log.info("Playlist deleted · %d left", n)
        self._lists = n

    def _on_download(self, label: str, _f: float) -> None:
        if label not in self._downloading:
            self._downloading.add(label)
            log.info("↓ %s", label)

    def _on_downloads_done(self, ok: int, failed: int) -> None:
        self._downloading.clear()
        log.info("↓ Done · %d%s", ok, f" · {failed} failed" if failed else "")
