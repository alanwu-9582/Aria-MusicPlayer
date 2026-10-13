"""Tempo Flow: songs with a similar beat follow one another, and the tempo drifts.

BPM comes from a short excerpt of the song (30 s from the 30 s mark): the bundled
VLC decodes it to a small mono WAV — a downloaded file is read locally, a
stream only fetches that range, never the whole song — and an onset envelope's
autocorrelation finds the beat. Results are cached with their confidence.

Ordering is a layer on top of the recommendations (after relevance and the
Discovery dial): it only reorders recommended songs, never what the user queued,
and never changes playback speed. Half/double tempo counts as close (70 ≈ 140),
low-confidence estimates count for less, unknown BPM is neutral.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import threading
import time
import wave

from PySide6.QtCore import QObject, QTimer, Signal

from aria import paths
from aria.core import tasks, workers
from aria.core.models import LOCAL, Track
from aria.core.storage import DebouncedSaver, read_json

log = logging.getLogger(__name__)

ANALYSIS_VERSION = 1
SAMPLE_RATE = 11025
EXCERPT_START, EXCERPT_LEN = 30, 30
CACHE_DAYS = 365
RETRY_FAILED = 86400
GAP = 1.5                 # seconds between two analyses
MIN_BPM, MAX_BPM = 55, 210


# ---- estimation ------------------------------------------------------------------

def estimate(samples, rate: int = SAMPLE_RATE) -> tuple[float, float] | None:
    """(bpm, confidence 0..1) from mono samples, or None when there's no beat to find."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float32)
    if x.size < rate * 8:
        return None
    x = x / (np.max(np.abs(x)) + 1e-9)
    n_fft, hop = 1024, 256
    frames = 1 + (x.size - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft), axis=1))
    spec = np.log1p(100 * spec)
    flux = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")       # remove slow loudness changes
    flux = np.maximum(flux, 0)
    if not flux.any():
        return None
    fps = rate / hop
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    ac = np.correlate(flux, flux, mode="full")[flux.size - 1:]
    ac /= ac[0] + 1e-9
    lags = np.arange(ac.size)
    bpms = np.where(lags > 0, 60.0 * fps / np.maximum(lags, 1), 0)
    ok = (bpms >= MIN_BPM) & (bpms <= MAX_BPM)
    if not ok.any():
        return None
    # people tap near 120: weigh the candidates with a broad log-normal around it
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpms, 1) / 120.0) / 0.9) ** 2)
    score = np.where(ok, np.maximum(ac, 0) * prior, 0)
    lag = int(np.argmax(score))
    if 1 <= lag < ac.size - 1:                                     # parabolic refinement
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    else:
        shift = 0.0
    bpm = 60.0 * fps / (lag + shift)
    # confidence: how far the beat's peak stands out from the rest of the tempo range
    band = ac[ok]
    stand_out = (ac[lag] - band.mean()) / (band.std() * 5 + 1e-9)
    # a real beat also comes back one period later; noise doesn't
    again = ac[min(ac.size - 1, 2 * lag)] if 2 * lag < ac.size else ac[lag]
    conf = float(np.clip(stand_out, 0, 1) * np.clip(min(ac[lag], again) / 0.12, 0, 1))
    return round(float(bpm), 1), round(conf, 2)


def read_wav(path: str):
    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            return None, 0
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        ch = w.getnchannels()
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
        return data, w.getframerate()


_vlc_lock = threading.Lock()


def decode_excerpt(src: str, dst: str, headers: dict | None = None, timeout: float = 45.0,
                   whole: bool = False, rate: int = 0) -> bool:
    """30 s of ``src`` (or all of it) as a mono WAV, via VLC's transcoder (faster than real time)."""
    rate = rate or SAMPLE_RATE
    import vlc  # imported here: aria.core.player has set up the library path
    with _vlc_lock:
        inst = vlc.Instance(["--quiet", "--no-video", "--no-sout-video", "--no-sout-keep"])
    try:
        m = inst.media_new(src)
        for k, v in (headers or {}).items():
            if k.lower() == "referer":
                m.add_option(f":http-referrer={v}")
            elif k.lower() == "user-agent":
                m.add_option(f":http-user-agent={v}")
        if not whole:
            m.add_option(f":start-time={EXCERPT_START}")
            m.add_option(f":stop-time={EXCERPT_START + EXCERPT_LEN}")
        safe = dst.replace("'", "")
        m.add_option(f":sout=#transcode{{acodec=s16l,channels=1,samplerate={rate}}}"
                     f":std{{access=file,mux=wav,dst='{safe}'}}")
        mp = inst.media_player_new()
        mp.set_media(m)
        done = threading.Event()
        em = mp.event_manager()
        for et in (vlc.EventType.MediaPlayerEndReached, vlc.EventType.MediaPlayerEncounteredError,
                   vlc.EventType.MediaPlayerStopped):
            em.event_attach(et, lambda _e: done.set())
        mp.play()
        done.wait(timeout)
        mp.stop()
        mp.release()
        m.release()
    finally:
        inst.release()
    return os.path.exists(dst) and os.path.getsize(dst) > rate * 2 * 8


def analyze(track: Track, stream_fn) -> tuple[float, float, str] | None:
    """Blocking. (bpm, confidence, source) for ``track``; source is "file" or "stream"."""
    if track.local_path and os.path.exists(track.local_path):
        src, headers, source = track.local_path, {}, "file"
    elif track.source == LOCAL:
        return None
    else:
        s = stream_fn(track)
        src, headers, source = s.url, s.headers, "stream"
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="aria-bpm-")
    os.close(fd)
    try:
        if not decode_excerpt(src, tmp, headers):
            return None
        data, rate = read_wav(tmp)
        if data is None:
            return None
        found = estimate(data, rate)
        return (found[0], found[1], source) if found else None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---- distance & ordering -----------------------------------------------------------

def tempo_distance(a: float, b: float) -> float:
    """BPM apart, counting half / double time as the same feel (70 ≈ 140)."""
    return min(abs(a - b), abs(a - 2 * b) * 0.85 + 2, abs(2 * a - b) * 0.85 + 2)


STRENGTH = {  # (tolerance BPM, weight, songs in one tempo band before it moves on)
    "gentle": (22.0, 0.35, 3),
    "balanced": (15.0, 0.60, 4),
    "strong": (10.0, 0.85, 5),
}
NEUTRAL = 0.35          # unknown BPM: neither pulled forward nor pushed back much


def flow_order(tracks: list[Track], bpm_of, start: float | None, recent: list[float],
               strength: str = "balanced", order_weight: float = 1.0) -> list[Track]:
    """Reorder (recommended) ``tracks`` so neighbours have a similar beat, starting from the song
    playing now (``start`` BPM). After a few similar songs the target drifts faster or slower,
    turning back at the edges of what the candidates offer. The original order (relevance)
    still counts, so this nudges rather than sorts (``order_weight`` < 1 when the order says less,
    e.g. a playlist's). ``bpm_of(track)`` → (bpm, confidence) | None."""
    if len(tracks) < 2:
        return list(tracks)
    tol, weight, run_len = STRENGTH.get(strength, STRENGTH["balanced"])
    known = [b[0] for t in tracks if (b := bpm_of(t)) and b[1] >= 0.2]
    if not known:
        return list(tracks)
    if start is None:
        start = known[0]          # the playing song's beat is unknown: begin from the first one that's known
    lo, hi = min(known), max(known)
    # move towards where more candidates are, so the drift has somewhere to go
    direction = 1 if sum(1 for b in known if b > start) >= sum(1 for b in known if b < start) else -1
    prev = start
    run = sum(1 for b in recent[-run_len:] if tempo_distance(b, start) <= tol)
    n = len(tracks)
    rank = {id(t): i for i, t in enumerate(tracks)}
    left = list(tracks)
    out: list[Track] = []
    while left:
        target = prev
        if run >= run_len:                                 # enough of this tempo: lean onwards
            target = prev + direction * tol * 0.9
            if target > hi or target < lo:
                direction = -direction
                target = prev + direction * tol * 0.9

        def score(t: Track) -> float:
            # relevance counts for less the stronger the flow
            base = (1 - rank[id(t)] / n) * (1 - 0.6 * weight) * order_weight
            info = bpm_of(t)
            if not info or info[1] < 0.2:
                return base + weight * NEUTRAL
            d = tempo_distance(target, info[0])
            # mostly "within the window", plus a wide pull so that, with nothing close, the nearest wins
            close = 0.8 * math.exp(-((d / (tol * 1.3)) ** 2)) + 0.35 * math.exp(-((d / (tol * 4)) ** 2))
            return base + weight * (NEUTRAL + (close - NEUTRAL) * min(1.0, info[1] / 0.6))

        best = max(left, key=score)
        left.remove(best)
        out.append(best)
        info = bpm_of(best)
        if info and info[1] >= 0.2:
            run = run + 1 if tempo_distance(prev, info[0]) <= tol else 1
            prev = info[0]
    return out


# ---- worker process --------------------------------------------------------------------
# Resolving a stream (yt-dlp) and decoding run in an idle-priority process of their own
# (:mod:`aria.core.workers`), so the interface never competes with them.

_pool = workers.IdleWorker()


def analyze_in_worker(track: dict):
    """Runs in the worker process."""
    import importlib
    importlib.import_module("aria.core.player")      # points python-vlc at the bundled VLC
    from aria import providers
    return analyze(Track.from_dict(track), providers.stream)


def shutdown_worker() -> None:
    _pool.shutdown()


# ---- cache + background analysis ------------------------------------------------------

class TempoLibrary(QObject):
    """BPM cache plus a one-at-a-time background analyser (never on the UI thread)."""

    updated = Signal(str)            # track key

    def __init__(self, stream_fn=None, in_process: bool = True):
        """``in_process``: analyse in the worker process (the app); False runs ``stream_fn`` in a thread."""
        super().__init__()
        self.stream_fn = stream_fn
        self.in_process = in_process
        self._file = paths.CACHE_DIR / "tempo.json"
        data = read_json(self._file, {})
        now = time.time()
        # results last a year (or until the analysis changes); failures are retried after a day
        self.data: dict[str, dict] = {
            k: v for k, v in data.get("tracks", {}).items()
            if v.get("v") == ANALYSIS_VERSION
            and now - v.get("at", 0) < (RETRY_FAILED if v.get("src") == "error" else CACHE_DAYS * 86400)}
        self._saver = DebouncedSaver(self._file, lambda: {"tracks": self.data}, 2000)
        self._pending: list[Track] = []
        self._busy = False
        self._urgent: set[str] = set()
        self._working: str | None = None
        self._last = 0.0
        self._gap = QTimer(self)
        self._gap.setSingleShot(True)
        self._gap.timeout.connect(self._next)

    def rekey(self, old: str, new: str) -> None:
        """A song changed source: its measured tempo stays with it."""
        if old in self.data and new not in self.data:
            self.data[new] = self.data.pop(old)
            self._saver.schedule()

    def get(self, track: Track | None) -> tuple[float, float] | None:
        if track is None:
            return None
        e = self.data.get(track.key)
        if not e or e.get("bpm") is None:
            return None
        return e["bpm"], e.get("conf", 0.0)

    def request(self, tracks: list[Track], front: bool = False, urgent: bool = False) -> set[str]:
        """Analyse these when there's time (``front``: before anything else waiting; ``urgent``: the
        user is waiting for them, so no breather in between). Returns the keys still to be measured."""
        fresh = [t for t in tracks if t and t.key not in self.data and (t.source != LOCAL or t.local_path)]
        keys = {t.key for t in self._pending}
        if front or urgent:
            # move them to the front, even if they were already waiting further back
            wanted = {t.key for t in fresh}
            self._pending = [t for t in fresh] + [t for t in self._pending if t.key not in wanted]
        else:
            self._pending.extend(t for t in fresh if t.key not in keys)
        if urgent:
            self._urgent |= {t.key for t in fresh}
        del self._pending[300:]
        waiting = {t.key for t in self._pending} | ({self._working} if self._working else set())
        self._next()
        return {t.key for t in tracks if t and t.key in waiting}

    def _next(self) -> None:
        if self._busy or not self._pending:
            return
        wait = GAP - (time.monotonic() - self._last)
        if wait > 0 and self._pending[0].key not in self._urgent:
            # a breather between songs, unless someone is waiting: the work never comes in bursts
            if not self._gap.isActive():
                self._gap.start(int(wait * 1000))
            return
        self._busy = True
        t = self._pending.pop(0)
        self._working = t.key
        self._urgent.discard(t.key)

        def done(found):
            self._busy = False
            self._working = None
            self._last = time.monotonic()
            entry = {"v": ANALYSIS_VERSION, "at": time.time()}
            if found:
                entry.update(bpm=found[0], conf=found[1], src=found[2])
            else:
                entry.update(bpm=None, conf=0.0, src="none")       # remembered, so it isn't retried every time
            self.data[t.key] = entry
            self._saver.schedule()
            self.updated.emit(t.key)
            self._next()

        def failed(exc):
            self._busy = False
            self._working = None
            self._last = time.monotonic()
            log.info("No BPM for %s: %s", t.title, exc)
            self.data[t.key] = {"v": ANALYSIS_VERSION, "at": time.time(), "bpm": None, "conf": 0.0, "src": "error"}
            self._saver.schedule()
            self.updated.emit(t.key)          # finished, if without a result (progress moves on)
            self._next()

        if self.in_process:
            work = lambda: _pool.pool().submit(analyze_in_worker, t.to_dict()).result(timeout=120)  # noqa: E731
        else:
            work = lambda: analyze(t, self.stream_fn)  # noqa: E731
        tasks.run(work, done, failed)

    def flush(self) -> None:
        self._saver.flush()

    def shutdown(self) -> None:
        self._pending.clear()
        self.flush()
        shutdown_worker()
