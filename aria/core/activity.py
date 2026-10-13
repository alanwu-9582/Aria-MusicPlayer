"""Activity log: what Aria did by itself, in plain words, for the Console.

The Console is for what happens in the background or automatically — a song starting on its
own (the last one ended, one failed, autoplay), an album or timer running out, audio outputs
appearing or going. What the listener does by hand (play, pause, volume, saving, playlists,
settings) is already on screen, so it isn't logged here. One line per event, with numbers.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject

from aria.core.models import format_duration

log = logging.getLogger("activity")

WHY = {"ended": "Next", "skipped": "Skipped to", "autoplay": "Autoplay"}


class Activity(QObject):
    def __init__(self, playback, library, downloader, playlists, devices=None, balance=None):
        super().__init__()
        self.pb = playback
        self._album = None
        self._timer = None
        self._outputs = len(devices.outputs()) if devices is not None else 0
        playback.current_changed.connect(self._on_track)
        playback.mode_changed.connect(self._on_mode)
        if devices is not None:
            devices.devices_changed.connect(lambda: self._on_outputs(len(devices.outputs())))

    # ---- playback ------------------------------------------------------------

    def _on_track(self, t) -> None:
        why = WHY.get(self.pb.auto_reason)
        if why is None or t is None:
            return                                  # the listener's own choice: nothing to report
        parts = [f"{why} · “{t.title}”", t.source_label]
        if t.duration:
            parts.append(format_duration(t.duration))
        log.info(" · ".join(parts))

    def _on_mode(self) -> None:
        """Only the endings Aria reaches by itself (starting them is the listener's doing)."""
        album, timer = self.pb.album, self.pb.timer
        if album is None and self._album is not None and self.pb.auto_reason:
            log.info("Album “%s” finished", self._album.get("title", ""))
        if timer is None and self._timer is not None and self.pb.auto_reason:
            log.info("Timer finished")
        self._album, self._timer = album, timer

    def _on_outputs(self, n: int) -> None:
        if n != self._outputs:                  # the list is re-read often; only a real change counts
            log.info("Audio outputs: %d → %d", self._outputs, n)
            self._outputs = n
