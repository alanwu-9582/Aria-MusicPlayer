"""libVLC playback wrapped as a Qt object. VLC events arrive on VLC's own thread
and are re-emitted as queued Qt signals, so no VLC call ever runs inside a VLC callback."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from aria import paths
from aria.core.models import Stream

if paths.VLC_DIR.exists():
    os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(paths.VLC_DIR / "libvlc.dll"))
    os.environ.setdefault("PYTHON_VLC_MODULE_PATH", str(paths.VLC_DIR))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(paths.VLC_DIR))

import vlc  # noqa: E402  (needs the env above)

log = logging.getLogger(__name__)

IDLE, LOADING, PLAYING, PAUSED = "idle", "loading", "playing", "paused"


class Player(QObject):
    state_changed = Signal(str)
    position_changed = Signal(float, float)   # seconds, length seconds
    ended = Signal()
    failed = Signal()

    _vlc_event = Signal(str)

    def __init__(self, volume: int = 70):
        super().__init__()
        self._instance = vlc.Instance(["--no-video", "--quiet", "--network-caching=1500",
                                       "--http-reconnect", "--no-metadata-network-access"])
        self._mp = self._instance.media_player_new()
        self._volume = volume
        self.state = IDLE

        events = self._mp.event_manager()
        for etype, name in ((vlc.EventType.MediaPlayerPlaying, "playing"),
                            (vlc.EventType.MediaPlayerPaused, "paused"),
                            (vlc.EventType.MediaPlayerEndReached, "ended"),
                            (vlc.EventType.MediaPlayerEncounteredError, "error")):
            events.event_attach(etype, lambda _e, n=name: self._vlc_event.emit(n))
        self._vlc_event.connect(self._on_vlc_event)

        # Position is polled only while playing; nothing ticks when idle.
        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)

    # ---- commands ----------------------------------------------------------

    def load(self, stream: Stream, start: float = 0) -> None:
        media = self._instance.media_new(stream.url)
        for key, value in stream.headers.items():
            k = key.lower()
            if k == "referer":
                media.add_option(f":http-referrer={value}")
            elif k == "user-agent":
                media.add_option(f":http-user-agent={value}")
        if start > 1:
            media.add_option(f":start-time={start:.1f}")
        self._mp.set_media(media)
        media.release()
        self._set_state(LOADING)
        self._mp.play()

    def toggle(self) -> None:
        if self.state == PLAYING:
            self._mp.set_pause(1)
        elif self.state == PAUSED:
            self._mp.set_pause(0)

    def stop(self) -> None:
        self._mp.stop()
        self._ticker.stop()
        self._set_state(IDLE)
        self.position_changed.emit(0.0, 0.0)

    def seek(self, seconds: float) -> None:
        if self.state in (PLAYING, PAUSED) and self._mp.is_seekable():
            self._mp.set_time(int(seconds * 1000))
            self._tick()

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self._mp.audio_set_volume(self._volume)

    @property
    def volume(self) -> int:
        return self._volume

    def position(self) -> float:
        return max(0, self._mp.get_time()) / 1000

    def length(self) -> float:
        return max(0, self._mp.get_length()) / 1000

    def release(self) -> None:
        self._ticker.stop()
        self._mp.stop()
        self._mp.release()
        self._instance.release()

    # ---- internals ---------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    @Slot(str)
    def _on_vlc_event(self, name: str) -> None:
        if name == "playing":
            self._mp.audio_set_volume(self._volume)   # libVLC resets volume per media
            self._set_state(PLAYING)
            self._ticker.start()
        elif name == "paused":
            self._set_state(PAUSED)
            self._ticker.stop()
            self._tick()
        elif name == "ended":
            self._ticker.stop()
            self._set_state(IDLE)
            self.ended.emit()
        elif name == "error":
            self._ticker.stop()
            self._set_state(IDLE)
            self.failed.emit()

    def _tick(self) -> None:
        self.position_changed.emit(self.position(), self.length())
