"""libVLC playback: two decks behind one engine.

Two decks make cross-fades, seamless repeats and stream → local switches
possible: the next thing starts on the idle deck and the decks swap.
VLC events arrive on VLC's own thread and are re-emitted as queued Qt signals,
so no VLC call ever runs inside a VLC callback.
"""

from __future__ import annotations

import logging
import math
import os
import time

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

# Small network cache: playback starts sooner; yt-dlp/Bilibili CDNs are fast enough.
# DirectSound gives every player its own volume. The default Windows output
# (mmdevice) sets the volume of the whole process's audio session, so the two
# decks overwrite each other and the level isn't the one the app remembers.
VLC_ARGS = ["--no-video", "--quiet", "--network-caching=700", "--file-caching=200",
            "--http-reconnect", "--no-metadata-network-access"]
if os.name == "nt":
    VLC_ARGS.append("--aout=directsound")


class Deck(QObject):
    """One libVLC media player."""

    state_changed = Signal(object, str)
    ended = Signal(object)
    failed = Signal(object)
    _vlc_event = Signal(str)

    def __init__(self, instance):
        super().__init__()
        self._instance = instance
        self.mp = instance.media_player_new()
        self.state = IDLE
        events = self.mp.event_manager()
        for etype, name in ((vlc.EventType.MediaPlayerPlaying, "playing"),
                            (vlc.EventType.MediaPlayerPaused, "paused"),
                            (vlc.EventType.MediaPlayerEndReached, "ended"),
                            (vlc.EventType.MediaPlayerEncounteredError, "error")):
            events.event_attach(etype, lambda _e, n=name: self._vlc_event.emit(n))
        self._vlc_event.connect(self._on_event)

    def load(self, stream: Stream, start: float = 0) -> None:
        media = self._instance.media_new(stream.url)
        for key, value in stream.headers.items():
            k = key.lower()
            if k == "referer":
                media.add_option(f":http-referrer={value}")
            elif k == "user-agent":
                media.add_option(f":http-user-agent={value}")
        if start > 0.5:
            media.add_option(f":start-time={start:.2f}")
        self.mp.set_media(media)
        media.release()
        self._set(LOADING)
        self.mp.play()

    def stop(self) -> None:
        if self.state != IDLE:
            self.mp.stop()
            self._set(IDLE)

    def set_volume(self, v: int) -> None:
        self.mp.audio_set_volume(max(0, min(100, v)))

    def position(self) -> float:
        return max(0, self.mp.get_time()) / 1000

    def length(self) -> float:
        return max(0, self.mp.get_length()) / 1000

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(self, state)

    @Slot(str)
    def _on_event(self, name: str) -> None:
        if name == "playing":
            self._set(PLAYING)
        elif name == "paused":
            self._set(PAUSED)
        elif name == "ended":
            self._set(IDLE)
            self.ended.emit(self)
        elif name == "error":
            self._set(IDLE)
            self.failed.emit(self)


class _Fade:
    __slots__ = ("deck", "start", "end", "t0", "dur", "done")

    def __init__(self, deck, start, end, dur, done=None):
        self.deck, self.start, self.end, self.dur, self.done = deck, start, end, max(0.01, dur), done
        self.t0 = time.monotonic()


class Player(QObject):
    """The engine the rest of the app talks to. Same surface as a single player,
    plus ``crossfade()``, ``soft_resume`` and a ``near_end`` cue."""

    state_changed = Signal(str)
    position_changed = Signal(float, float)   # seconds, length seconds
    ended = Signal()
    failed = Signal()
    near_end = Signal(float)                  # remaining seconds, once per track

    def __init__(self, volume: int = 70):
        super().__init__()
        self._instance = vlc.Instance(VLC_ARGS)
        self.decks = [Deck(self._instance), Deck(self._instance)]
        self._active = 0
        self._volume = volume
        self._gain = [1.0, 0.0]               # per-deck fade gain
        self._soft = 1.0                      # soft-resume multiplier (active deck)
        self._fades: list[_Fade] = []
        self.lead = 0.0                       # seconds before the end to emit near_end
        self._cued = False
        self._pending_xfade: tuple[Deck, float] | None = None
        self._fade_out_deck: Deck | None = None

        for d in self.decks:
            d.state_changed.connect(self._on_deck_state)
            d.ended.connect(self._on_deck_ended)
            d.failed.connect(self._on_deck_failed)

        self._ticker = QTimer(self)          # position; runs only while playing
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)
        self._fader = QTimer(self)           # volume ramps
        self._fader.setInterval(25)
        self._fader.timeout.connect(self._step_fades)

    # ---- properties ------------------------------------------------------------

    @property
    def deck(self) -> Deck:
        return self.decks[self._active]

    @property
    def state(self) -> str:
        return self.deck.state

    @property
    def volume(self) -> int:
        return self._volume

    def position(self) -> float:
        return self.deck.position()

    def length(self) -> float:
        return self.deck.length()

    # ---- commands ----------------------------------------------------------------

    def load(self, stream: Stream, start: float = 0, fade_in: float = 0.0) -> None:
        """Play on the active deck, silencing everything else."""
        self._fades.clear()
        self._pending_xfade = None
        other = self.decks[1 - self._active]
        other.stop()
        self._gain[1 - self._active] = 0.0
        self._gain[self._active] = 0.0 if fade_in else 1.0
        self._soft = 1.0
        self._cued = False
        self._apply()
        self.deck.load(stream, start)
        if fade_in:
            self._pending_xfade = (self.deck, fade_in)   # fade in once audio starts

    def crossfade(self, stream: Stream, duration: float, start: float = 0) -> None:
        """Start ``stream`` on the idle deck and blend into it over ``duration`` seconds."""
        old = self.deck
        if old.state != PLAYING or duration <= 0:
            self.load(stream, start)
            return
        self._fades = [f for f in self._fades if f.deck is not old]
        self._active = 1 - self._active
        new = self.deck
        self._gain[self._active] = 0.0
        self._soft = 1.0
        self._cued = False
        self._apply()
        self._pending_xfade = (new, duration)
        self._fade_out_deck = old
        new.load(stream, start)

    def toggle(self) -> None:
        if self.state == PLAYING:
            self.pause()
        elif self.state == PAUSED:
            self.resume()

    def pause(self) -> None:
        # Pause every deck so a cross-fade in progress doesn't keep sounding.
        for d in self.decks:
            if d.state == PLAYING:
                d.mp.set_pause(1)

    def resume(self, soft_seconds: float = 0.0) -> None:
        if soft_seconds > 0:
            self._soft = 0.12
            self._apply()
            self._fades.append(_Fade("soft", 0.12, 1.0, soft_seconds))
            self._fader.start()
        self.deck.mp.set_pause(0)

    def restart(self, smooth: bool = True) -> None:
        """Jump to the start; with ``smooth`` a quick dip hides the seek."""
        if self.state not in (PLAYING, PAUSED):
            return
        if not smooth:
            self.seek(0)
            return
        deck = self.deck

        def jump():
            deck.mp.set_time(0)
            self._fades.append(_Fade(self._active, 0.0, 1.0, 0.35))
            self._fader.start()

        self._fades.append(_Fade(self._active, self._gain[self._active], 0.0, 0.15, jump))
        self._fader.start()

    def stop(self) -> None:
        self._fades.clear()
        self._pending_xfade = None
        for d in self.decks:
            d.stop()
        self._ticker.stop()
        self._fader.stop()
        self.state_changed.emit(IDLE)
        self.position_changed.emit(0.0, 0.0)

    def seek(self, seconds: float) -> None:
        if self.state in (PLAYING, PAUSED) and self.deck.mp.is_seekable():
            self.deck.mp.set_time(int(seconds * 1000))
            if seconds < self.length() - self.lead - 1:
                self._cued = False
            self._tick()

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self._apply()

    def release(self) -> None:
        self._ticker.stop()
        self._fader.stop()
        for d in self.decks:
            d.mp.stop()
            d.mp.release()
        self._instance.release()

    # ---- volume ----------------------------------------------------------------

    def _apply(self) -> None:
        for i, d in enumerate(self.decks):
            g = self._gain[i] * (self._soft if i == self._active else 1.0)
            d.set_volume(round(self._volume * g))

    def _step_fades(self) -> None:
        now = time.monotonic()
        finished = []
        for f in self._fades:
            t = min(1.0, (now - f.t0) / f.dur)
            # equal-power curves (sin in, cos out): no loudness dip mid cross-fade
            ease = math.sin(t * math.pi / 2) if f.end > f.start else 1 - math.cos(t * math.pi / 2)
            value = f.start + (f.end - f.start) * ease
            if f.deck == "soft":
                self._soft = value
            else:
                self._gain[f.deck] = value
            if t >= 1.0:
                finished.append(f)
        self._apply()
        for f in finished:
            self._fades.remove(f)
            if f.done:
                f.done()
        if not self._fades:
            self._fader.stop()

    # ---- deck events -------------------------------------------------------------

    def _on_deck_state(self, deck: Deck, state: str) -> None:
        if self._pending_xfade and deck is self._pending_xfade[0] and state == PLAYING:
            _new, dur = self._pending_xfade
            self._pending_xfade = None
            i_new = self.decks.index(deck)
            self._fades.append(_Fade(i_new, self._gain[i_new], 1.0, dur))
            old = self._fade_out_deck
            if old is not None and old is not deck and old.state != IDLE:
                i_old = self.decks.index(old)
                self._fades.append(_Fade(i_old, self._gain[i_old], 0.0, dur, old.stop))
            self._fade_out_deck = None
            self._fader.start()
        if deck is not self.deck:
            return
        if state == PLAYING:
            self._apply()
            # The audio output may still be opening; make sure the level sticks.
            QTimer.singleShot(150, self._apply)
            QTimer.singleShot(600, self._apply)
            self._ticker.start()
        elif state == PAUSED:
            self._ticker.stop()
            self._tick()
        elif state == IDLE:
            self._ticker.stop()
        self.state_changed.emit(state)

    def _on_deck_ended(self, deck: Deck) -> None:
        if deck is self.deck:
            self.ended.emit()

    def _on_deck_failed(self, deck: Deck) -> None:
        if deck is self.deck:
            self.failed.emit()

    def _tick(self) -> None:
        pos, length = self.position(), self.length()
        self.position_changed.emit(pos, length)
        if self.lead > 0 and length > self.lead + 5 and not self._cued and length - pos <= self.lead:
            self._cued = True
            self.near_end.emit(length - pos)
