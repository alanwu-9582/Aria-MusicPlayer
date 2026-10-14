"""Forced Alignment: timing for plain lyrics from a speech model the user chose to download.

Off unless the user turns it on and downloads a model (they live in ``data/models``, one
folder each, and can be deleted from Settings). Only songs whose whole recording is on disk
are aligned — nothing is streamed or downloaded for it — and the work runs in the
idle-priority background worker, one song at a time.

How: the model (Whisper, run by faster-whisper on the CPU) listens to the song and writes
down what it hears, word by word with times. The known lyrics are then matched against that
transcript character by character (scripts folded, so 後 / 后 or spacing don't matter); each
line starts where its first matched characters were heard, and lines the model missed are
placed between their neighbours. Singing is harder to hear than speech, so the result is
only used when enough of the lyrics were matched — otherwise the plain lyrics stay.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from aria import paths
from aria.core import workers


@dataclass(frozen=True)
class SpeechModel:
    id: str
    name: str
    repo: str
    size_mb: int
    note: str


MODELS = [
    SpeechModel("tiny", "Tiny", "Systran/faster-whisper-tiny", 78, "Fastest · rough"),
    SpeechModel("base", "Base", "Systran/faster-whisper-base", 148, "Fast"),
    SpeechModel("small", "Small", "Systran/faster-whisper-small", 486, "Balanced"),
    SpeechModel("medium", "Medium", "Systran/faster-whisper-medium", 1531, "Accurate · slow"),
    SpeechModel("large-v3-turbo", "Large v3 Turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1622,
                "Most accurate"),
]
BY_ID = {m.id: m for m in MODELS}
MIN_COVERAGE = 0.40              # share of the lyrics the model must have heard
CHAR_TIME = 0.25                 # seconds a sung character takes (to back up to a line's start)
LAG = 0.7                        # word starts come out a little early: lines go this much later
SKIP = {".gitattributes", "README.md"}


# ---- the engine and the models on disk ------------------------------------------------

def engine_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def models_dir():
    """data/models (ARIA_MODELS_DIR may point a second copy, or a test run, at shared models)."""
    shared = os.environ.get("ARIA_MODELS_DIR")
    return Path(shared) if shared else paths.DATA_DIR / "models"


def model_path(model_id: str):
    return models_dir() / model_id


def installed(model_id: str) -> bool:
    d = model_path(model_id)
    return (d / "model.bin").exists() and (d / "config.json").exists() and not any(d.glob("*.part"))


def disk_size(model_id: str) -> int:
    d = model_path(model_id)
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0


def delete(model_id: str) -> None:
    shutil.rmtree(model_path(model_id), ignore_errors=True)


def download(model_id: str, progress=None, cancel: threading.Event | None = None) -> None:
    """Blocking: fetch the model's files (each to .part first). ``progress(done, total)`` in bytes."""
    from aria.providers import http
    m = BY_ID[model_id]
    r = http.get(f"https://huggingface.co/api/models/{m.repo}/tree/main", timeout=20)
    files = [(f["path"], int(f.get("size") or 0)) for f in r.json() if f.get("type") == "file" and f["path"] not in SKIP]
    total = sum(s for _p, s in files) or m.size_mb * 1_000_000
    d = model_path(model_id)
    d.mkdir(parents=True, exist_ok=True)
    done = 0
    for name, size in files:
        target = d / name
        if target.exists() and (not size or target.stat().st_size == size):
            done += size
            continue
        part = target.with_name(target.name + ".part")
        with http.session().get(f"https://huggingface.co/{m.repo}/resolve/main/{name}", stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(part, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("cancelled")
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        os.replace(part, target)
    if progress:
        progress(total, total)


class ModelManager(QObject):
    """Downloads that keep going while Settings is closed; one at a time."""

    progress = Signal(str, int, int)      # model, MB done, MB total
    finished = Signal(str, str)           # model, "" or what went wrong ("cancelled" too)

    def __init__(self):
        super().__init__()
        self.active: str | None = None
        self._cancel = threading.Event()

    def start(self, model_id: str) -> bool:
        if self.active or model_id not in BY_ID:
            return False
        from aria.core import tasks
        self.active = model_id
        self._cancel.clear()
        relay = tasks.Progress(lambda d, t: self.progress.emit(model_id, d, t))
        self._relay = relay                      # (kept alive while the download runs)
        # MB (as the sizes elsewhere show them) across the thread boundary: bytes past 2 GB don't fit an int signal
        report = lambda d, t: relay.emit(d >> 20, max(1, t >> 20))  # noqa: E731

        def done(_r):
            self.active = None
            self.finished.emit(model_id, "")

        def failed(exc):
            self.active = None
            if isinstance(exc, InterruptedError):
                for f in model_path(model_id).glob("*.part"):
                    f.unlink(missing_ok=True)
            self.finished.emit(model_id, "cancelled" if isinstance(exc, InterruptedError) else str(exc))

        tasks.run(lambda: download(model_id, report, self._cancel), done, failed, lane="bulk")
        return True

    def cancel(self) -> None:
        self._cancel.set()


# ---- matching the transcript to the lyrics ------------------------------------------------

_KEEP = re.compile(r"[\w぀-ヿ㐀-鿿가-힯]")


def _chars(text: str) -> str:
    """Comparable characters: folded (case, width, traditional/simplified), katakana as hiragana,
    no spaces or punctuation."""
    from aria.core import textnorm
    out = []
    for ch in textnorm.fold(text):
        if not _KEEP.match(ch) or ch == "_":
            continue
        if "ァ" <= ch <= "ヶ":
            ch = chr(ord(ch) - 0x60)
        out.append(ch)
    return "".join(out)


def _align(a: str, b: str) -> list[int]:
    """For every character of ``a`` (the lyrics), the character of ``b`` (the transcript) it lines
    up with, or -1. Global for the lyrics, free at both ends of the transcript (talk or noise
    before and after the singing costs nothing); a wrong character costs less than skipping."""
    n, m = len(a), len(b)
    prev = [0] * (m + 1)
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ai = a[i - 1]
        cur = [-i] + [0] * m
        row = trace[i]
        row[0] = 1
        for j in range(1, m + 1):
            d = prev[j - 1] + (2 if ai == b[j - 1] else -1)
            u = prev[j] - 1
            left = cur[j - 1] - 1
            if d >= u and d >= left:
                cur[j] = d
            elif u >= left:
                cur[j], row[j] = u, 1
            else:
                cur[j], row[j] = left, 2
        prev = cur
    j = max(range(m + 1), key=lambda k: prev[k])
    out = [-1] * n
    i = n
    while i > 0 and j > 0:
        step = trace[i][j]
        if step == 0:
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif step == 1:
            i -= 1
        else:
            j -= 1
    return out


_CHUNK = re.compile(r"[A-Za-z0-9'’\-]+\s*|\s+|.\s*", re.DOTALL)


def word_chunks(line: str) -> list[str]:
    """A line in the pieces a fill moves through: a Latin word (with its space), or one CJK
    character (with any space after it). Joined, they are the line exactly."""
    return _CHUNK.findall(line)


def match_lines(lines: list[str], words: list[tuple[float, float, str]],
                with_words: bool = False):
    """Line start times for ``lines`` ("" = stanza break, skipped) from timed ``words``, with the
    share of lyric characters the model heard exactly; None when that is too little to trust.
    ``with_words``: also each sung line's estimated word times, [[start, piece], …] (see
    :func:`word_chunks`) — the times of the characters the model heard, the rest in between."""
    sung = [ln for ln in lines if ln]
    if len(sung) < 4 or not words:
        return None
    t_chars, t_times = [], []                       # the transcript, a time for every character
    for start, end, text in words:
        cs = _chars(text)
        for j, ch in enumerate(cs):
            t_chars.append(ch)
            t_times.append(start + (end - start) * j / max(1, len(cs)))
    l_chars, owner = [], []
    for i, ln in enumerate(sung):
        for ch in _chars(ln):
            l_chars.append(ch)
            owner.append(i)
    if len(l_chars) < 10 or not t_chars:
        return None
    a, b = "".join(l_chars), "".join(t_chars)
    where = _align(a, b)
    exact = sum(1 for k, j in enumerate(where) if j >= 0 and a[k] == b[j])
    coverage = exact / len(a)
    if coverage < MIN_COVERAGE:
        return None
    first = {}
    for k, li in enumerate(owner):
        first.setdefault(li, k)
    times: list[float | None] = [None] * len(sung)
    for li in range(len(sung)):
        ks = [k for k in range(first.get(li, 0), len(owner)) if owner[k] == li]
        hits = [(k - first[li], t_times[where[k]]) for k in ks if where[k] >= 0 and a[k] == b[where[k]]]
        if len(hits) >= max(1, len(ks) * 0.25):     # enough of the line was really heard
            pos, t = hits[0]
            times[li] = max(0.0, t - pos * CHAR_TIME + LAG)    # back up to the line's first character
    known = [i for i, t in enumerate(times) if t is not None]
    if len(known) < max(3, len(sung) * 0.5):
        return None
    for i in range(len(sung)):                      # missed lines: between their neighbours
        if times[i] is None:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            if prev is not None and nxt is not None:
                times[i] = times[prev] + (times[nxt] - times[prev]) * (i - prev) / (nxt - prev)
            elif prev is not None:
                times[i] = times[prev] + 3.0 * (i - prev)
            else:
                times[i] = max(0.0, times[nxt] - 3.0 * (nxt - i))
    out: list[float] = []
    for t in times:                                 # in order, never two lines at once
        out.append(max(t, out[-1] + 0.3) if out else t)
    if not with_words:
        return out, coverage
    char_time = [t_times[where[k]] + LAG if where[k] >= 0 else None for k in range(len(a))]
    per_line = []
    for li, ln in enumerate(sung):
        start = out[li]
        end = out[li + 1] if li + 1 < len(out) else start + max(2.0, len(_chars(ln)) * CHAR_TIME)
        k0 = first.get(li, 0)
        pieces, at = [], k0
        stamps: list[float | None] = []
        for piece in word_chunks(ln):
            n = len(_chars(piece))
            hit = next((char_time[k] for k in range(at, at + n) if k < len(char_time) and char_time[k] is not None), None)
            stamps.append(hit if n else None)
            pieces.append(piece)
            at += n
        # fill the gaps, keep them in order and inside the line
        span = [i for i, t in enumerate(stamps) if t is not None and start - 0.5 <= t < end]
        for i in range(len(stamps)):
            if i in span:
                continue
            before = max((j for j in span if j < i), default=None)
            after = min((j for j in span if j > i), default=None)
            lo = stamps[before] if before is not None else start
            hi = stamps[after] if after is not None else end
            lo_i = before if before is not None else -1
            hi_i = after if after is not None else len(stamps)
            stamps[i] = lo + (hi - lo) * (i - lo_i) / max(1, hi_i - lo_i)
        timed, last = [], start
        for t, piece in zip(stamps, pieces):
            t = min(max(t, last, start), end - 0.05)
            timed.append([round(float(t), 2), piece])
            last = t
        if timed:
            timed[0][0] = round(float(start), 2)         # the fill starts with the line
        per_line.append(timed)
    return out, coverage, per_line


def timed_lines(lines: list[str], times: list[float], words: list | None = None):
    """Put the line starts back among the stanza breaks (a break shares the next line's time).
    With ``words`` (per sung line) also returns the words per line, [] for the breaks."""
    out, per, k, brk = [], [], 0, False
    for ln in lines:
        if not ln:
            brk = True
            continue
        if brk and out:
            out.append((times[k], ""))
            per.append([])
        out.append((times[k], ln))
        per.append(words[k] if words else [])
        k += 1
        brk = False
    return (out, per) if words is not None else out


def file_signature(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


# ---- running it (in the worker process) -------------------------------------------------

_loaded: dict[str, object] = {}
_pool = workers.IdleWorker()


def worker():
    """One idle-priority process of its own: the model stays loaded between songs."""
    return _pool.pool()


def shutdown_worker() -> None:
    """Stop it (a model it loaded is released, so its files can be deleted)."""
    _pool.shutdown()


WHISPER_LANG = {"ja": "ja", "zh": "zh", "ko": "ko", "en": "en"}


def _decode(path: str):
    """The whole song as 16 kHz mono float samples (what Whisper listens to)."""
    import importlib
    import tempfile
    importlib.import_module("aria.core.player")       # points python-vlc at the bundled VLC
    from aria.core import tempo
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        if not tempo.decode_excerpt(path, wav, whole=True, rate=16000, timeout=180):
            return None
        samples, _sr = tempo.read_wav(wav)
        return None if samples is None else samples / 32768.0
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def align_file(path: str, lines: list[str], model_id: str, language: str | None = None) -> dict | None:
    """Blocking, meant for the worker: {"lines", "confidence", "sig", "model"} or None."""
    if not os.path.exists(path) or not installed(model_id) or not engine_available():
        return None
    from faster_whisper import WhisperModel
    model = _loaded.get(model_id)
    if model is None:
        _loaded.clear()                                   # one model in memory at a time
        model = WhisperModel(str(model_path(model_id)), device="cpu", compute_type="int8",
                             cpu_threads=max(1, (os.cpu_count() or 2) // 2))
        _loaded[model_id] = model
    sung = [ln for ln in lines if ln]
    audio = _decode(path)                               # through VLC, like everything else Aria plays
    if audio is None:
        return None
    segments, _info = model.transcribe(
        audio, language=language, word_timestamps=True, vad_filter=False,     # speech detection drops singing
        condition_on_previous_text=False, beam_size=1,
        initial_prompt=" ".join(sung[:4])[:200])          # the words to listen for
    words = [(w.start, w.end, w.word) for seg in segments for w in (seg.words or [])]
    found = match_lines(lines, words, with_words=True)
    if found is None:
        return None
    times, coverage, per_line = found
    timed, per = timed_lines(lines, times, per_line)
    return {"lines": timed, "word_times": per, "confidence": round(coverage, 3),   # estimated, not sung data
            "sig": file_signature(path), "model": model_id}
