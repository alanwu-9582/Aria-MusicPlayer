"""Queue, history, auto-recommendation and stream resolution around the Player.

Two temporary modes sit on top of the normal queue:
- Album mode plays an album in its own order, gapless, with no recommendations
  slipped in; afterwards everything is as it was.
- A listening timer plays a set of songs chosen to fill a time budget and stops
  when the last one ends.
"""

from __future__ import annotations

import logging
import os
import random
import time

from PySide6.QtCore import QObject, QTimer, Signal

from aria import paths, providers
from aria.core import tasks, timefit
from aria.core.models import LOCAL, Stream, Track
from aria.core.player import IDLE, LOADING, PAUSED, PLAYING, Player
from aria.core.recommend import Recommender, assemble, pick_related
from aria.core.storage import DebouncedSaver, read_json

log = logging.getLogger(__name__)

HISTORY_LIMIT = 200
AUTOPLAY_BATCH = 5
REPEAT_MODES = ("off", "all", "one")
SOFT_RESUME_AFTER = 90        # seconds paused before resuming softly
SOFT_RESUME_RAMP = 3.0        # seconds to come back to full volume
SOURCE_SWITCH_FADE = 0.8
QUEUE_SEEDS = 3               # queue songs (beyond the current one) that steer recommendations
GAPLESS_LEAD = 8.0            # seconds before the end to buffer the next song when not cross-fading


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
    volume_changed = Signal(int)
    file_deleted = Signal(str)                # key whose downloaded file was deleted later
    mode_changed = Signal()                   # album mode / listening timer started or ended

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
        self.library = None                       # set by the app: lets recommendations follow your taste
        self.listening = None                     # set by the app: play stats (what's familiar)
        self.album: dict | None = None            # {"title", "keys"} while an album plays in order
        self.timer: dict | None = None            # {"keys", "total"} while a listening timer runs
        self._armed: Track | None = None          # song buffered for a gapless start
        self._armed_local = False
        self._pools: list | None = None           # last radios, so the Discovery dial re-ranks offline
        self._pool_seeds: list[Track] = []
        self._rank = tasks.Latest()
        self._pending_delete: dict[str, str] = {}   # key → file to delete once it's no longer playing
        self._delete_timer = QTimer(self)
        self._delete_timer.setInterval(2000)
        self._delete_timer.timeout.connect(self._try_deletes)

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
        self.queue_changed.connect(self._check_armed)
        self.player.advanced.connect(self._on_advanced)
        self.apply_settings()

    def apply_settings(self) -> None:
        """Re-read playback preferences (called when the settings page changes them)."""
        secs = float(self.settings["crossfade_secs"])
        self.player.lead = secs + 0.6 if self.crossfading else GAPLESS_LEAD

    @property
    def crossfading(self) -> bool:
        """Cross-fade between songs (albums always play gapless instead)."""
        return bool(self.settings["crossfade"]) and self.album is None

    @property
    def autoplaying(self) -> bool:
        """Recommendations may be added right now (not inside an album or a timed set)."""
        return self.autoplay and self.album is None and self.timer is None

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
            if self.repeat == "all" and self.album is None:
                self.queue.append(self.current)
        if self._album_over_after(self.current):
            self.end_album()
            if not self.queue:              # the album was the whole plan: stop quietly
                self._set_current(None)
                self.stop()
                return
        self._advance()

    def _advance(self) -> None:
        if not self.queue and self.autoplaying:
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
        before = self.player.volume
        self.player.set_volume(volume)
        self.settings["volume"] = self.player.volume
        if self.player.volume != before:
            self.volume_changed.emit(self.player.volume)

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
        was_empty = not self.queue
        tracks = [t.copy() for t in tracks]
        if play_next:
            self.queue[0:0] = tracks
        else:
            self.queue.extend(tracks)
        self.queue_changed.emit()
        self.save()
        # Nothing playing and nothing was waiting: start. (A restored queue only grows.)
        if was_empty and not self.current and self.player.state == IDLE and not self._resolving:
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
        self.end_album()                    # shuffling an album means leaving album order
        random.shuffle(self.queue)
        self.queue_changed.emit()
        self.save()

    def clear(self) -> None:
        self.queue.clear()
        self.end_album()
        self.cancel_timer()
        self.queue_changed.emit()
        self.save()

    # ---- album mode ----------------------------------------------------------

    def play_album(self, tracks: list[Track], title: str, start: int = 0) -> None:
        """Play an album from ``start`` in its own order: gapless, nothing slipped in between.
        Whatever was queued waits until the album is over."""
        tracks = [t.copy() for t in tracks[start:]]
        if not tracks:
            return
        self.cancel_timer()
        if self.current:
            self._push_history(self.current)
        self.album = {"title": title, "keys": [t.key for t in tracks]}
        self.queue[0:0] = tracks[1:]
        self.queue_changed.emit()
        self.apply_settings()
        self.mode_changed.emit()
        self.save()
        self._play(tracks[0])

    def end_album(self) -> None:
        if self.album is not None:
            self.album = None
            self.apply_settings()
            self.mode_changed.emit()

    def _album_over_after(self, track: Track | None) -> bool:
        """True when ``track`` is the album's last song (or playback has left the album)."""
        if self.album is None or track is None:
            return False
        keys = self.album["keys"]
        return track.key == keys[-1] or track.key not in keys

    # ---- listening timer -----------------------------------------------------

    def start_timer(self, tracks: list[Track], seconds: float, top_up: bool = False) -> None:
        """Play exactly ``tracks`` (chosen to fill ``seconds``) and stop when the last one ends.
        Songs that were queued wait behind them. With ``top_up``, a set that falls short is
        completed with recommendations (fetched first if there are none yet)."""
        if not tracks:
            return
        self.end_album()
        chosen = [t.copy() for t in tracks]
        keys = {t.key for t in chosen}
        if self.current:
            self._push_history(self.current)
        self.queue = chosen[1:] + [t for t in self.queue if t.key not in keys]
        self.timer = {"keys": [t.key for t in chosen], "total": seconds, "top_up": top_up}
        self.queue_changed.emit()
        self.mode_changed.emit()
        self.save()
        self._play(chosen[0])
        if top_up:
            self._top_up_timer()

    def _timer_overlap(self) -> float:
        return float(self.settings["crossfade_secs"]) if self.crossfading else 0.0

    def _top_up_timer(self) -> None:
        """Fill what the queue couldn't with recommendations, placed after the timed songs."""
        t = self.timer
        if t is None or not t.get("top_up"):
            return
        ov = self._timer_overlap()
        timed = [x for x in ([self.current] if self.current else []) + self.queue if x.key in t["keys"]]
        gap = t["total"] - timefit.playing_time(timed, ov)
        if gap < 60:
            t["top_up"] = False
            return
        pool = [x for x in pick_related(self.recommendations, self._exclusions(), 40) if x.duration]
        extra = timefit.fit(pool, gap, ov)
        if not extra:
            if not self._recs_busy and not t.get("asked"):
                t["asked"] = True                 # one fetch; the timer stays short if that doesn't help
                self.refresh_recommendations(force=True)
            return
        t["top_up"] = False
        taken = {x.id for x in extra}
        self.recommendations = [x for x in self.recommendations if x.id not in taken]
        self.recommendations_changed.emit()
        at = sum(1 for x in self.queue if x.key in t["keys"])
        self.queue[at:at] = [x.copy() for x in extra]
        t["keys"] += [x.key for x in extra]
        self.queue_changed.emit()
        self.mode_changed.emit()
        self.notice.emit("info", f"Added {len(extra)} recommended song{'s' if len(extra) != 1 else ''} to fill the time")
        self.save()

    def cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer = None
            self.mode_changed.emit()

    def timer_remaining(self) -> float:
        """Seconds of music left in the timed set (it pauses with the music)."""
        if self.timer is None:
            return 0.0
        keys = self.timer["keys"]
        left = 0.0
        songs = 0
        if self.current and self.current.key in keys:
            length = self.player.length() or self.current.duration
            left += max(0.0, length - self.player.position())
            songs += 1
        for t in self.queue:
            if t.key in keys:
                left += t.duration
                songs += 1
        if self.crossfading and songs > 1:
            left -= float(self.settings["crossfade_secs"]) * (songs - 1)
        return max(0.0, left)

    def _is_timer_end(self, track: Track | None) -> bool:
        """The timed set is over once this song ends (no more of its songs are waiting)."""
        if self.timer is None or track is None:
            return False
        keys = set(self.timer["keys"])
        return track.key in keys and not any(t.key in keys for t in self.queue)

    # ---- discovery -----------------------------------------------------------

    def set_discovery(self, value: float) -> None:
        """0 = familiar … 1 = unexpected. Re-ranks the radios already fetched (no network)."""
        self.settings["discovery"] = round(max(0.0, min(1.0, value)), 2)
        if not self._pools:
            return
        ticket = self._rank.next()
        pools, seeds, exclude, taste = self._pools, self._pool_seeds, self._exclusions(), self._taste()

        def done(result: list[Track]):
            if self._rank.is_current(ticket):
                self.recommendations = result
                self.recommendations_changed.emit()

        tasks.run(lambda: assemble(pools, seeds, exclude, 15, **taste), done,
                  lambda e: log.warning("Re-ranking failed: %s", e))

    def _taste(self) -> dict:
        return {"history": list(self.history[-200:]),
                "library": list(self.library.tracks) if self.library is not None else [],
                "played": self.listening.played_keys() if self.listening is not None else set(),
                "discovery": float(self.settings["discovery"])}

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

        def done(found):
            pools, result = found
            if not self._recs.is_current(ticket):
                return
            self._rank.next()                 # a dial re-rank still in flight is now stale
            self._pools, self._pool_seeds = pools, seeds
            self._set_recs_busy(False)
            self.recommendations = result
            self.recommendations_changed.emit()
            if self.timer is not None and self.timer.get("top_up"):
                self._top_up_timer()
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

        taste = self._taste()

        def work():
            pools = self.recommender.fetch(seeds, taste["history"], taste["library"])
            return pools, assemble(pools, seeds, exclude, 15, **taste)

        tasks.run(work, done, failed)
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
        if self.album is not None and (track is None or track.key not in self.album["keys"]):
            self.end_album()                # playing something else leaves album mode
        if self.timer is not None and (track is None or track.key not in self.timer["keys"]):
            self.cancel_timer()
        self._armed = None
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

    def _next_up(self) -> Track | None:
        """What follows the current song when it ends naturally."""
        if not self.current:
            return None
        if self.repeat == "one":
            return self.current
        if self._is_timer_end(self.current):
            return None                     # time's up after this one
        if self._album_over_after(self.current):
            return None                     # the album ends in silence
        if not self.queue and self.autoplaying:
            self._autoplay_fill()
        return self.queue[0] if self.queue else None

    def _on_near_end(self, remaining: float) -> None:
        """Cross-fade, or buffer for a gapless start, into what comes next
        (or into the same song when repeating one)."""
        nxt = self._next_up()
        if nxt is None:
            return
        stream = providers.cached_stream(nxt)
        if stream is None:
            self.warm(nxt)              # not ready: fall back to a normal change at the end
            return
        if not self.crossfading:
            self._armed = nxt
            self._armed_local = bool(nxt.local_path) and stream.url == nxt.local_path
            self.player.queue_next(stream)
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

    def _on_advanced(self) -> None:
        """The buffered song took over (gapless): the bookkeeping of a normal change."""
        nxt, cur, local = self._armed, self.current, self._armed_local
        self._armed = None
        if nxt is None or cur is None:
            return
        if nxt is not cur:
            self._push_history(cur)
            if self.repeat == "all" and self.album is None:
                self.queue.append(cur)
            if nxt in self.queue:
                self.queue.remove(nxt)
            self.queue_changed.emit()
            self._set_current(nxt)
        self._retried_key = None
        self._set_playing_local(local)

    def _check_armed(self) -> None:
        """The queue changed after the next song was buffered: buffer the right one instead."""
        if self._armed is None or not self.player.has_queued:
            return
        if self._next_up() is not self._armed:
            self._armed = None
            self.player.cancel_queued()
            remaining = self.player.length() - self.player.position()
            if remaining > 1.5:
                self._on_near_end(remaining)

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

    def delete_after_playback(self, key: str, path: str) -> None:
        """Delete a downloaded file that's playing right now as soon as it stops being used."""
        self._pending_delete[key] = path
        self._delete_timer.start()

    def deleting_later(self, key: str) -> bool:
        return key in self._pending_delete

    def _try_deletes(self) -> None:
        for key, path in list(self._pending_delete.items()):
            if self.current and self.current.key == key:
                continue                         # still playing (or paused on it)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                continue                         # still open (e.g. fading out): try again shortly
            del self._pending_delete[key]
            self.forget_local(key)
            self.file_deleted.emit(key)
            log.info("Deleted the downloaded file of a removed song: %s", os.path.basename(path))
        if not self._pending_delete:
            self._delete_timer.stop()

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
        if self._is_timer_end(self.current):
            self.cancel_timer()
            self._push_history(self.current)
            self._set_current(None)
            self.stop()
            self.notice.emit("info", "Time’s up")
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
        if self._pending_delete:                 # nothing plays any more: delete what was waiting
            self.current = None
            self._try_deletes()
