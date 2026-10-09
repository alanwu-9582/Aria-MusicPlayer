"""Listening sessions and per-song play statistics.

A session is one continuous stretch of listening: it starts with the first
song and ends after a long quiet gap. It keeps when it started, how long music
actually played, the songs in order and what was saved meanwhile. Per-song
stats (plays, finished plays, time heard) feed Smart Collections and the
recommender's sense of what is familiar.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from aria import paths
from aria.core.models import Track, format_duration
from aria.core.player import PLAYING
from aria.core.storage import DebouncedSaver, read_json

SESSION_GAP = 30 * 60         # this long without music ends a session
MIN_TRACKS = 2                # shorter sessions aren't kept
MIN_LISTENED = 3 * 60
MAX_SESSIONS = 80
MAX_STATS = 3000
SAVE_EVERY = 30.0             # seconds of listening between periodic saves


def part_of_day(ts: float) -> str:
    h = time.localtime(ts).tm_hour
    if 5 <= h < 12:
        return "Morning"
    if 12 <= h < 17:
        return "Afternoon"
    if 17 <= h < 21:
        return "Evening"
    return "Late Night" if h < 5 else "Night"


@dataclass
class Session:
    started: float
    ended: float = 0.0
    listened: float = 0.0                  # seconds music actually played
    tracks: list[Track] = field(default_factory=list)
    saved: list[str] = field(default_factory=list)      # keys saved during the session
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def title(self) -> str:
        lt = time.localtime(self.started)
        return f"{time.strftime('%B', lt)} {lt.tm_mday} · {part_of_day(self.started)} Session"

    @property
    def card(self) -> str:
        """October 9 · Evening Session — 18 tracks"""
        n = len(self.tracks)
        return f"{self.title} — {n} track{'s' if n != 1 else ''}"

    @property
    def details(self) -> str:
        bits = [format_duration(self.listened)]
        if self.saved:
            bits.append(f"{len(self.saved)} saved")
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {"id": self.id, "started": self.started, "ended": self.ended, "listened": round(self.listened, 1),
                "tracks": [t.to_dict() for t in self.tracks], "saved": self.saved}

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        return cls(started=d.get("started", 0), ended=d.get("ended", 0), listened=d.get("listened", 0),
                   tracks=[Track.from_dict(t) for t in d.get("tracks", [])], saved=list(d.get("saved", [])),
                   id=d.get("id") or uuid.uuid4().hex[:12])


@dataclass
class Stat:
    plays: int = 0
    finished: int = 0
    heard: float = 0.0                     # seconds
    first: float = 0.0
    last: float = 0.0

    def to_dict(self) -> dict:
        return {"plays": self.plays, "finished": self.finished, "heard": round(self.heard, 1),
                "first": self.first, "last": self.last}


def finished_listening(heard: float, furthest: float, length: float) -> bool:
    """Heard most of it and got to (near) the end."""
    if length <= 0:
        return False
    return heard >= 0.5 * length and furthest >= length - max(12.0, 0.1 * length)


class Listening(QObject):
    changed = Signal()                     # a session started / ended / grew
    stats_changed = Signal()

    def __init__(self, playback, library):
        super().__init__()
        self.pb = playback
        self.library = library
        data = read_json(paths.LISTENING_FILE, {})
        self.sessions: list[Session] = [Session.from_dict(d) for d in data.get("sessions", [])]
        cur = data.get("current")
        self.current: Session | None = Session.from_dict(cur) if cur else None
        self.last_active: float = data.get("last_active", 0.0)
        self.stats: dict[str, Stat] = {k: Stat(**v) for k, v in data.get("stats", {}).items()}
        self.tracks: dict[str, Track] = {k: Track.from_dict(v) for k, v in data.get("tracks", {}).items()}
        self._saver = DebouncedSaver(paths.LISTENING_FILE, self._snapshot, 1500)

        self._key: str | None = None       # song being listened to
        self._heard = 0.0
        self._furthest = 0.0
        self._length = 0.0
        self._tick_at: float | None = None
        self._since_save = 0.0

        self.end_if_idle()
        playback.current_changed.connect(self._on_track)
        playback.position_changed.connect(self._on_position)
        playback.state_changed.connect(self._on_state)
        library.added.connect(self._on_saved)
        self._idle = QTimer(self)
        self._idle.setInterval(60_000)
        self._idle.timeout.connect(self.end_if_idle)
        self._idle.start()

    # ---- persistence ---------------------------------------------------------

    def _snapshot(self) -> dict:
        return {"current": self.current.to_dict() if self.current else None,
                "last_active": self.last_active,
                "sessions": [s.to_dict() for s in self.sessions],
                "stats": {k: s.to_dict() for k, s in self.stats.items()},
                "tracks": {k: t.to_dict() for k, t in self.tracks.items()}}

    def flush(self) -> None:
        self._close_track()
        self._saver.flush()

    # ---- queries -------------------------------------------------------------

    def stat(self, key: str) -> Stat | None:
        return self.stats.get(key)

    def played_keys(self) -> set[str]:
        return set(self.stats)

    def played_tracks(self) -> list[Track]:
        return list(self.tracks.values())

    def all_sessions(self) -> list[Session]:
        """Newest first, the running one included."""
        return ([self.current] if self.current else []) + self.sessions[::-1]

    def get(self, sid: str) -> Session | None:
        return next((s for s in self.all_sessions() if s.id == sid), None)

    def delete(self, sid: str) -> None:
        if self.current and self.current.id == sid:
            self.current = None
        self.sessions = [s for s in self.sessions if s.id != sid]
        self._saver.schedule()
        self.changed.emit()

    # ---- sessions ------------------------------------------------------------

    def end_if_idle(self) -> None:
        if self.current and time.time() - self.last_active > SESSION_GAP:
            self._end_session()

    def _end_session(self) -> None:
        s = self.current
        self.current = None
        if s and len(s.tracks) >= MIN_TRACKS and s.listened >= MIN_LISTENED:
            s.ended = s.ended or self.last_active or time.time()
            self.sessions.append(s)
            del self.sessions[:-MAX_SESSIONS]
        self._saver.schedule()
        self.changed.emit()

    def _ensure_session(self) -> Session:
        now = time.time()
        if self.current and now - self.last_active > SESSION_GAP:
            self._end_session()
        if not self.current:
            self.current = Session(started=now)
            self.changed.emit()
        self.last_active = now
        return self.current

    # ---- events --------------------------------------------------------------

    def _on_track(self, track: Track | None) -> None:
        if track is not None and track.key == self._key:
            return                        # same song re-announced (e.g. its download finished)
        self._close_track()
        if track is None:
            return
        self._key = track.key
        self._length = track.duration
        s = self._ensure_session()
        if not s.tracks or s.tracks[-1].key != track.key:
            s.tracks.append(track.copy())
        st = self.stats.setdefault(track.key, Stat(first=time.time()))
        st.plays += 1
        st.last = time.time()
        self.tracks[track.key] = track.copy()
        self._prune()
        self._saver.schedule()
        self.changed.emit()
        self.stats_changed.emit()

    def _on_state(self, state: str) -> None:
        if state != PLAYING:
            self._tick_at = None

    def _on_position(self, pos: float, length: float) -> None:
        if self._key is None or self.pb.state != PLAYING:
            self._tick_at = None
            return
        now = time.monotonic()
        dt = min(1.0, now - self._tick_at) if self._tick_at is not None else 0.0
        self._tick_at = now
        if length:
            self._length = length
        self._furthest = max(self._furthest, pos)
        self._heard += dt
        s = self._ensure_session()
        s.listened += dt
        self._since_save += dt
        if self._since_save >= SAVE_EVERY:
            self._since_save = 0.0
            self._saver.schedule()

    def _on_saved(self, keys: list[str]) -> None:
        if self.current and time.time() - self.last_active <= SESSION_GAP:
            fresh = [k for k in keys if k not in self.current.saved]
            if fresh:
                self.current.saved.extend(fresh)
                self._saver.schedule()
                self.changed.emit()

    def _close_track(self) -> None:
        """Book what was heard of the song that just stopped being current."""
        key = self._key
        if key is None:
            return
        st = self.stats.get(key)
        if st:
            st.heard += self._heard
            if finished_listening(self._heard, self._furthest, self._length):
                st.finished += 1
                self.stats_changed.emit()
        self._key = None
        self._heard = self._furthest = self._length = 0.0
        self._tick_at = None
        self._saver.schedule()

    def _prune(self) -> None:
        if len(self.stats) > MAX_STATS:
            for k, _s in sorted(self.stats.items(), key=lambda kv: kv[1].last)[: len(self.stats) - MAX_STATS]:
                self.stats.pop(k, None)
                self.tracks.pop(k, None)
