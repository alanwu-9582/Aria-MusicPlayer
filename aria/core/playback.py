"""Queue, history, auto-recommendation and stream resolution around the Player."""

from __future__ import annotations

import logging
import random

from PySide6.QtCore import QObject, QTimer, Signal

from aria import paths, providers
from aria.core import tasks
from aria.core.models import LOCAL, Track
from aria.core.player import IDLE, LOADING, PAUSED, PLAYING, Player
from aria.core.recommend import Recommender, pick_related
from aria.core.storage import DebouncedSaver, read_json

log = logging.getLogger(__name__)

HISTORY_LIMIT = 200
AUTOPLAY_BATCH = 5
REPEAT_MODES = ("off", "all", "one")


class Playback(QObject):
    current_changed = Signal(object)          # Track | None
    queue_changed = Signal()
    history_changed = Signal()
    recommendations_changed = Signal()
    recommending = Signal(bool)
    state_changed = Signal(str)               # idle | loading | playing | paused
    position_changed = Signal(float, float)
    notice = Signal(str, str)                 # kind, text  → toast

    def __init__(self, settings, legacy_queue: list[Track] | None = None):
        super().__init__()
        self.settings = settings
        self.player = Player(settings["volume"])
        self.recommender = Recommender(providers.youtube, providers.spotify)

        self.current: Track | None = None
        self.queue: list[Track] = []
        self.history: list[Track] = []
        self.recommendations: list[Track] = []
        self.autoplay: bool = settings["autoplay"]
        self.repeat: str = settings["repeat"]

        self._resolve = tasks.Latest()
        self._recs = tasks.Latest()
        self._recs_for: str | None = None
        self._recs_busy = False
        self._autoplay_waiting = False
        self._failures = 0
        self._retried_key: str | None = None
        self._resolving = False

        self.player.state_changed.connect(self._on_player_state)
        self.player.position_changed.connect(self.position_changed)
        self.player.ended.connect(self._on_ended)
        self.player.failed.connect(self._on_failed)

        self._saver = DebouncedSaver(paths.SESSION_FILE, self._snapshot, 800)
        self._restore(legacy_queue)

        # Recommendations follow the current track, but not on every quick skip.
        self._recs_timer = QTimer(self)
        self._recs_timer.setSingleShot(True)
        self._recs_timer.setInterval(1200)
        self._recs_timer.timeout.connect(self.refresh_recommendations)

    # ---- state ---------------------------------------------------------------

    @property
    def state(self) -> str:
        if self.player.state == IDLE and self._resolving:
            return LOADING
        return self.player.state

    @property
    def recs_busy(self) -> bool:
        return self._recs_busy

    def _snapshot(self) -> dict:
        queue = ([self.current] if self.current else []) + self.queue
        return {"queue": [t.to_dict() for t in queue],
                "history": [t.to_dict() for t in self.history[-50:]]}

    def _restore(self, legacy_queue: list[Track] | None) -> None:
        data = read_json(paths.SESSION_FILE, {})
        self.queue = legacy_queue if legacy_queue is not None else [Track.from_dict(d) for d in data.get("queue", [])]
        self.history = [Track.from_dict(d) for d in data.get("history", [])]

    def save(self) -> None:
        self._saver.schedule()

    def flush(self) -> None:
        self._saver.flush()

    # ---- transport -----------------------------------------------------------

    def toggle(self) -> None:
        if self.player.state in (PLAYING, PAUSED):
            self.player.toggle()
        elif self.current:
            self._start(self.current)
        elif self.queue:
            self.next()

    def stop(self) -> None:
        self._resolve.next()
        self._set_resolving(False)
        self.player.stop()

    def next(self) -> None:
        """Manual skip (also used when a track ends)."""
        if self.current:
            self._push_history(self.current)
            if self.repeat == "all":
                self.queue.append(self.current)
        self._advance()

    def _advance(self) -> None:
        if not self.queue and self.autoplay:
            self._autoplay_fill()
            if not self.queue and self._autoplay_waiting:
                # Recommendations are on their way; refresh_recommendations resumes.
                self.player.stop()
                self._set_resolving(True)
                return
        if not self.queue:
            self._set_current(None)
            self.stop()
            self.queue_changed.emit()
            return
        track = self.queue.pop(0)
        self.queue_changed.emit()
        self._play(track)

    def previous(self) -> None:
        if self.current and self.player.position() > 3:
            self.player.seek(0)
            return
        if not self.history:
            if self.current:
                self.player.seek(0)
            return
        prev = self.history.pop()
        self.history_changed.emit()
        if self.current:
            self.queue.insert(0, self.current)
            self.queue_changed.emit()
        self._play(prev)

    def play_now(self, track: Track) -> None:
        if self.current:
            self._push_history(self.current)
        self._play(track)

    def play_queue_item(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            track = self.queue.pop(index)
            self.queue_changed.emit()
            self.play_now(track)

    def play_history_item(self, index: int) -> None:
        if 0 <= index < len(self.history):
            self.play_now(self.history[index].copy())

    def seek(self, seconds: float) -> None:
        self.player.seek(seconds)

    def set_volume(self, volume: int) -> None:
        self.player.set_volume(volume)
        self.settings["volume"] = self.player.volume

    def set_autoplay(self, on: bool) -> None:
        self.autoplay = on
        self.settings["autoplay"] = on
        if on and not self.recommendations:
            self.refresh_recommendations()

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        self.settings["repeat"] = self.repeat
        return self.repeat

    # ---- queue edits ---------------------------------------------------------

    def enqueue(self, tracks: list[Track], play_next: bool = False) -> None:
        if not tracks:
            return
        tracks = [t.copy() for t in tracks]
        if play_next:
            self.queue[0:0] = tracks
        else:
            self.queue.extend(tracks)
        self.queue_changed.emit()
        self.save()
        if not self.current and self.player.state == IDLE and not self._resolving:
            self.next()
        else:
            self._prefetch()

    def remove(self, indices: list[int]) -> None:
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < len(self.queue):
                del self.queue[i]
        self.queue_changed.emit()
        self.save()

    def move(self, src: int, dst: int) -> None:
        if 0 <= src < len(self.queue) and src != dst:
            item = self.queue.pop(src)
            self.queue.insert(max(0, min(dst, len(self.queue))), item)
            self.queue_changed.emit()
            self.save()

    def reorder(self, keys_in_order: list[int]) -> None:
        """Apply a permutation coming from a drag-and-drop list."""
        if sorted(keys_in_order) == list(range(len(self.queue))):
            self.queue = [self.queue[i] for i in keys_in_order]
            self.queue_changed.emit()
            self.save()

    def shuffle(self) -> None:
        random.shuffle(self.queue)
        self.queue_changed.emit()
        self.save()

    def clear(self) -> None:
        self.queue.clear()
        self.queue_changed.emit()
        self.save()

    def clear_history(self) -> None:
        self.history.clear()
        self.history_changed.emit()
        self.save()

    # ---- recommendations -----------------------------------------------------

    def refresh_recommendations(self, force: bool = False) -> bool:
        """Fetch recommendations in the background; False when there's nothing to base them on."""
        seeds = ([self.current] if self.current else []) + self.history[::-1][:2]
        if not seeds:
            return False
        if not force and self._recs_for == seeds[0].key and self.recommendations:
            return True
        ticket = self._recs.next()
        self._recs_for = seeds[0].key
        exclude = self._exclusions()
        self._set_recs_busy(True)

        def done(result: list[Track]):
            if not self._recs.is_current(ticket):
                return
            self._set_recs_busy(False)
            self.recommendations = result
            self.recommendations_changed.emit()
            if self._autoplay_waiting:
                self._autoplay_waiting = False
                self._resume_after_recommendations()

        def failed(exc):
            if self._recs.is_current(ticket):
                self._set_recs_busy(False)
                log.warning("推薦失敗: %s", exc)
                if self._autoplay_waiting:
                    self._autoplay_waiting = False
                    self._resume_after_recommendations()

        tasks.run(lambda: self.recommender.recommend(seeds, exclude, 15), done, failed)
        return True

    def _set_recs_busy(self, busy: bool) -> None:
        self._recs_busy = busy
        self.recommending.emit(busy)

    def _exclusions(self) -> list[Track]:
        return self.history[-100:] + self.queue + ([self.current] if self.current else [])

    def _autoplay_fill(self) -> None:
        """Move fresh recommendations into the queue (they're re-filtered against what was played since)."""
        picks = pick_related(self.recommendations, self._exclusions(), AUTOPLAY_BATCH)
        if picks:
            self.queue.extend(picks)
            taken = {t.id for t in picks}
            self.recommendations = [t for t in self.recommendations if t.id not in taken]
            self.recommendations_changed.emit()
            self.queue_changed.emit()
            log.info("自動推薦加入 %d 首", len(picks))
        elif not self._autoplay_waiting:
            self._autoplay_waiting = self.refresh_recommendations(force=True)

    def _resume_after_recommendations(self) -> None:
        picks = pick_related(self.recommendations, self._exclusions(), AUTOPLAY_BATCH)
        self._set_resolving(False)
        if not picks:
            self.notice.emit("info", "沒有更多推薦")
            self._set_current(None)
            self.stop()
            return
        self._autoplay_fill()
        if self.player.state == IDLE:
            self._advance()

    def accept_recommendation(self, index: int, play: bool = False) -> None:
        if 0 <= index < len(self.recommendations):
            track = self.recommendations.pop(index)
            self.recommendations_changed.emit()
            if play:
                self.play_now(track)
            else:
                self.enqueue([track])

    # ---- internals -----------------------------------------------------------

    def _push_history(self, track: Track) -> None:
        if self.history and self.history[-1].key == track.key:
            return
        self.history.append(track)
        del self.history[:-HISTORY_LIMIT]
        self.history_changed.emit()

    def _set_current(self, track: Track | None) -> None:
        self.current = track
        self.current_changed.emit(track)
        self.save()
        if track:
            self._recs_timer.start()

    def _play(self, track: Track) -> None:
        self._set_current(track)
        self._start(track)

    def _set_resolving(self, on: bool) -> None:
        if self._resolving != on:
            self._resolving = on
            self.state_changed.emit(self.state)

    def _start(self, track: Track, force: bool = False) -> None:
        ticket = self._resolve.next()
        self.player.stop()
        self._set_resolving(True)

        def done(stream):
            if not self._resolve.is_current(ticket):
                return
            self._set_resolving(False)
            self.player.load(stream)

        def failed(exc):
            if not self._resolve.is_current(ticket):
                return
            self._set_resolving(False)
            self._skip_after_error(track, exc)

        tasks.run(lambda: providers.stream(track, force=force), done, failed)

    def _skip_after_error(self, track: Track, exc) -> None:
        log.error("無法播放 %s: %s", track.title, exc)
        self._failures += 1
        if self._failures >= 3:
            self._failures = 0
            self.notice.emit("danger", "連續無法播放，已停止")
            self.stop()
            return
        self.notice.emit("warning", "無法播放，已跳過")
        self.next()

    def _prefetch(self) -> None:
        """Resolve the next stream ahead of time so the change-over is instant."""
        if not self.queue:
            return
        nxt = self.queue[0]
        if nxt.source == LOCAL or nxt.local_path or providers.has_fresh_stream(nxt):
            return
        tasks.run(lambda: providers.stream(nxt), lambda _s: None, lambda _e: None)

    def _on_player_state(self, state: str) -> None:
        if state == PLAYING:
            self._failures = 0
            self._retried_key = None
            QTimer.singleShot(1500, self._prefetch)
            if self.autoplay and len(self.queue) <= 1 and not self.recommendations and not self._recs_busy:
                self.refresh_recommendations()
        self.state_changed.emit(self.state)

    def _on_ended(self) -> None:
        if self.repeat == "one" and self.current:
            self._start(self.current)
            return
        self.next()

    def _on_failed(self) -> None:
        track = self.current
        if not track:
            return
        # Remote URLs expire; retry once with a freshly resolved stream.
        if self._retried_key != track.key and not track.local_path:
            self._retried_key = track.key
            log.info("重新取得串流: %s", track.title)
            self._start(track, force=True)
            return
        self._skip_after_error(track, "播放錯誤")

    def shutdown(self) -> None:
        self._saver.flush()
        self.player.release()
