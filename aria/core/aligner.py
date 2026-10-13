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

from PySide6.QtCore import QObject, Signal

from aria import paths


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
    return paths.DATA_DIR / "models"


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
        self._relay = relay
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

        tasks.run(lambda: download(model_id, report, self._cancel), done, failed)
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


def match_lines(lines: list[str], words: list[tuple[float, float, str]]) -> tuple[list[float], float] | None:
    """Line start times for ``lines`` ("" = stanza break, skipped) from timed ``words``, with the
    share of lyric characters the model heard exactly; None when that is too little to trust."""
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
    return out, coverage


def timed_lines(lines: list[str], times: list[float]) -> list[tuple[float, str]]:
    """Put the line starts back among the stanza breaks (a break shares the next line's time)."""
    out, k, brk = [], 0, False
    for ln in lines:
        if not ln:
            brk = True
            continue
        if brk and out:
            out.append((times[k], ""))
        out.append((times[k], ln))
        k += 1
        brk = False
    return out


def file_signature(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


# ---- running it (in the worker process) -------------------------------------------------

_loaded: dict[str, object] = {}
_executor = None


def worker():
    """One idle-priority process of its own: the model stays loaded between songs."""
    global _executor
    if _executor is None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from aria.core import tempo
        _executor = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                                        initializer=tempo._worker_init)
    return _executor


def shutdown_worker() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


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
    found = match_lines(lines, words)
    if found is None:
        return None
    times, coverage = found
    return {"lines": timed_lines(lines, times), "confidence": round(coverage, 3),
            "sig": file_signature(path), "model": model_id}
