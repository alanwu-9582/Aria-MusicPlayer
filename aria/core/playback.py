"""Queue, history, auto-recommendation and stream resolution around the Player."""

from __future__ import annotations

import logging
import os
import random
import time

from PySide6.QtCore import QObject, QTimer, Signal

from aria import paths, providers
from aria.core import tasks
from aria.core.models import LOCAL, Stream, Track
from aria.core.player import IDLE, LOADING, PAUSED, PLAYING, Player
from aria.core.recommend import Recommender, pick_related
from aria.core.storage import DebouncedSaver, read_json

log = logging.getLogger(__name__)

HISTORY_LIMIT = 200
AUTOPLAY_BATCH = 5
REPEAT_MODES = ("off", "all", "one")
SOFT_RESUME_AFTER = 90        # seconds paused before resuming softly
SOFT_RESUME_RAMP = 3.0        # seconds to come back to full volume
SOURCE_SWITCH_FADE = 0.8
QUEUE_SEEDS = 3               # queue songs (beyond the current one) that steer recommendations


class Playback(QObject):
    current_changed = Signal(object)          # Track | None
    queue_changed = Signal()
    history_changed = Signal()
    recommendations_changed = Signal()
    recommending = Signal(bool)
    state_changed = Signal(str)               # idle | loading | playing | paused
    position_changed = Signal(float, float)
    notice = Signal(str, str)                 # kind, text  → toast
    source_changed = Signal(bool)             # True = playing the downloaded file

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
        self._paused_at: float | None = None
        self._pause_on_start = False
        self._warming: set[str] = set()
        self.playing_local = False

        self.player.state_changed.connect(self._on_player_state)
        self.player.near_end.connect(self._on_near_end)
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
        # Editing the queue steers recommendations too (waits for the edits to settle).
        self._queue_recs_timer = QTimer(self)
        self._queue_recs_timer.setSingleShot(True)
        self._queue_recs_timer.setInterval(2500)
        self._queue_recs_timer.timeout.connect(self.refresh_recommendations)
        self.queue_changed.connect(self._queue_recs_timer.start)
        self.apply_settings()

    def apply_settings(self) -> None:
        """Re-read playback preferences (called when the settings page changes them)."""
        secs = float(self.settings["crossfade_secs"])
        self.player.lead = secs + 0.6 if self.settings["crossfade"] else 0.0

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
        if self._resolving or self.player.state == LOADING:
            # Still fetching / buffering: pressing pause means "never mind".
            self.stop()
            return
        if self.player.state == PLAYING:
            self.player.pause()
            self._paused_at = time.monotonic()
        elif self.player.state == PAUSED:
            long_pause = self._paused_at and time.monotonic() - self._paused_at >= SOFT_RESUME_AFTER
            self.player.resume(SOFT_RESUME_RAMP if self.settings["soft_resume"] and long_pause else 0)
            self._paused_at = None
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
            self.player.restart(smooth=True)
            return
        if not self.history:
            if self.current:
                self.player.restart(smooth=True)
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

    def _seeds(self) -> list[Track]:
        """What recommendations are based on: the current song first, then songs
        spread across the whole queue, then the latest one played."""
        seeds = [self.current] if self.current else []
        q = self.queue
        if q:
            picks = sorted({0, len(q) // 3, (2 * len(q)) // 3, len(q) - 1})
            seeds += [q[i] for i in picks][:QUEUE_SEEDS]
        if self.history:
            seeds.append(self.history[-1])
        seen, out = set(), []
        for t in seeds:
            if t.key not in seen:
                seen.add(t.key)
                out.append(t)
        return out

    def refresh_recommendations(self, force: bool = False) -> bool:
        """Fetch recommendations in the background; False when there's nothing to base them on."""
        seeds = self._seeds()
        if not seeds:
            return False
        signature = "|".join(t.key for t in seeds)
        if not force and self._recs_for == signature and self.recommendations:
            return True
        ticket = self._recs.next()
        self._recs_for = signature
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
                log.warning("Recommendations failed: %s", exc)
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
            log.info("Autoplay added %d songs", len(picks))
        elif not self._autoplay_waiting:
            self._autoplay_waiting = self.refresh_recommendations(force=True)

    def _resume_after_recommendations(self) -> None:
        picks = pick_related(self.recommendations, self._exclusions(), AUTOPLAY_BATCH)
        self._set_resolving(False)
        if not picks:
            self.notice.emit("info", "No more recommendations")
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
        self._paused_at = None
        ready = None if force else providers.cached_stream(track)
        if ready is not None:           # already resolved / downloaded: no round-trip
            self._set_resolving(False)
            self._load(track, ready)
            return
        self.player.stop()
        self._set_resolving(True)

        def done(stream):
            if not self._resolve.is_current(ticket):
                return
            self._set_resolving(False)
            self._load(track, stream)

        def failed(exc):
            if not self._resolve.is_current(ticket):
                return
            self._set_resolving(False)
            self._skip_after_error(track, exc)

        tasks.run(lambda: providers.stream(track, force=force), done, failed)

    def _load(self, track: Track, stream: Stream) -> None:
        self.player.load(stream)
        self._set_playing_local(bool(track.local_path) and stream.url == track.local_path)

    def _set_playing_local(self, local: bool) -> None:
        if local != self.playing_local:
            self.playing_local = local
            self.source_changed.emit(local)

    def _skip_after_error(self, track: Track, exc) -> None:
        log.error("Can’t play %s: %s", track.title, exc)
        self._failures += 1
        if self._failures >= 3:
            self._failures = 0
            self.notice.emit("danger", "Several songs failed — stopped")
            self.stop()
            return
        self.notice.emit("warning", "Couldn’t play — skipped")
        self.next()

    def _prefetch(self) -> None:
        """Resolve the next streams ahead of time so the change-over is instant."""
        for nxt in self.queue[:2]:
            self.warm(nxt)

    def warm(self, track: Track) -> None:
        """Resolve ``track``'s stream in the background (e.g. when it's selected)."""
        if track.source == LOCAL or providers.has_fresh_stream(track) or track.key in self._warming:
            return
        self._warming.add(track.key)
        done = lambda _r: self._warming.discard(track.key)  # noqa: E731
        tasks.run(lambda: providers.stream(track), done, done)

    # ---- seamless transitions ------------------------------------------------

    def _on_near_end(self, remaining: float) -> None:
        """Cross-fade into what comes next (or into the same song when repeating one)."""
        if not self.settings["crossfade"] or not self.current:
            return
        if self.repeat == "one":
            nxt = self.current
        else:
            if not self.queue and self.autoplay:
                self._autoplay_fill()
            if not self.queue:
                return
            nxt = self.queue[0]
        stream = providers.cached_stream(nxt)
        if stream is None:
            self.warm(nxt)              # not ready: fall back to a normal change at the end
            return
        duration = max(1.0, min(float(self.settings["crossfade_secs"]), remaining - 0.3))
        if nxt is not self.current:
            self._push_history(self.current)
            if self.repeat == "all":
                self.queue.append(self.current)
            self.queue.pop(0)
            self.queue_changed.emit()
            self._set_current(nxt)
        self._retried_key = None
        self.player.crossfade(stream, duration)
        self._set_playing_local(bool(nxt.local_path) and stream.url == nxt.local_path)

    def switch_source(self, local: bool) -> None:
        """Hop between the online stream and the downloaded file without losing the place."""
        track = self.current
        if not track or local == self.playing_local:
            return
        if local and not (track.local_path and os.path.exists(track.local_path)):
            return

        def go(stream: Stream):
            if self.current is not track:
                return
            start = self.player.position() + (0.15 if local else 0.6)
            if self.player.state == PLAYING:
                self.player.crossfade(stream, SOURCE_SWITCH_FADE, start=start)
            else:
                self._pause_on_start = self.player.state == PAUSED
                self.player.load(stream, start=start)
            self._set_playing_local(local)

        if local:
            go(Stream(url=track.local_path))
        else:
            tasks.run(lambda: providers.remote_stream(track), go,
                      lambda e: log.error("Couldn’t switch to streaming: %s", e))

    def forget_local(self, key: str) -> None:
        """A downloaded file was deleted: copies of that track go back to streaming."""
        for t in self.queue + self.history + ([self.current] if self.current else []):
            if t.key == key:
                t.local_path = ""
        self.save()

    def local_available(self, key: str, path: str) -> None:
        """A download finished: every copy of that track learns its file; the playing one switches."""
        for t in self.queue + self.history + ([self.current] if self.current else []):
            if t.key == key:
                t.local_path = path
        self.save()
        if (self.current and self.current.key == key and not self.playing_local
                and self.settings["auto_local"] and self.player.state in (PLAYING, PAUSED)):
            self.switch_source(True)
            self.notice.emit("success", "Now playing the downloaded file")
        if self.current and self.current.key == key:
            self.current_changed.emit(self.current)

    def _on_player_state(self, state: str) -> None:
        if state == PLAYING and self._pause_on_start:
            self._pause_on_start = False
            self.player.pause()
            self._paused_at = time.monotonic()
        if state == PLAYING:
            self._failures = 0
            self._retried_key = None
            QTimer.singleShot(1500, self._prefetch)
            if self.autoplay and len(self.queue) <= 1 and not self.recommendations and not self._recs_busy:
                self.refresh_recommendations()
        self.state_changed.emit(self.state)

    def _on_ended(self) -> None:
        if self.repeat == "one" and self.current:
            self._start(self.current)       # cached stream → restarts instantly
            return
        self.next()

    def _on_failed(self) -> None:
        track = self.current
        if not track:
            return
        # Remote URLs expire; retry once with a freshly resolved stream.
        if self._retried_key != track.key and not track.local_path:
            self._retried_key = track.key
            log.info("Refreshing stream: %s", track.title)
            self._start(track, force=True)
            return
        self._skip_after_error(track, "playback error")

    def shutdown(self) -> None:
        self._saver.flush()
        self.player.release()
