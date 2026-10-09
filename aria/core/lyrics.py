"""Lyrics: LRCLIB first; with "enhanced" on, fall back to lyrics people paste
into video descriptions and comments.

Everything here is blocking; run it in a background task.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from aria import paths
from aria.core import textnorm
from aria.core.models import BILIBILI, SPOTIFY, YOUTUBE, Track

LRCLIB = "https://lrclib.net/api"
MISS_TTL = 3 * 24 * 3600          # don't re-ask for songs that had no lyrics for a few days


@dataclass
class Lyrics:
    lines: list[tuple[float | None, str]] = field(default_factory=list)
    synced: bool = False
    source: str = ""                  # LRCLIB | Description | Comments

    def to_dict(self) -> dict:
        return {"lines": self.lines, "synced": self.synced, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> Lyrics:
        return cls([(t, s) for t, s in d.get("lines", [])], d.get("synced", False), d.get("source", ""))

    def index_at(self, seconds: float) -> int:
        """Line being sung at ``seconds`` (synced only), -1 before the first."""
        idx = -1
        for i, (t, _s) in enumerate(self.lines):
            if t is None or t > seconds + 0.15:
                break
            idx = i
        return idx


# ---- parsing -----------------------------------------------------------------

_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_TIMED = re.compile(r"^\s*\(?(\d{1,2}):(\d{2})\)?\s+(.+)$")          # "1:23 lyric" in comments


def parse_lrc(text: str) -> list[tuple[float, str]]:
    out = []
    for raw in text.splitlines():
        tags = _LRC_TAG.findall(raw)
        words = _LRC_TAG.sub("", raw).strip()
        for m, s in tags:
            out.append((int(m) * 60 + float(s.replace(":", ".")), words))
    out.sort(key=lambda x: x[0])
    return out


def _plain(text: str) -> list[tuple[None, str]]:
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in text.strip().splitlines()]
    # collapse runs of blank lines into one stanza break
    out: list[tuple[None, str]] = []
    for ln in lines:
        if not ln.strip() and (not out or not out[-1][1]):
            continue
        out.append((None, ln.strip()))
    return out


def _valid_synced(lines) -> bool:
    words = [s for _t, s in lines if s]
    return len(words) >= 6 and len(set(words)) >= 4


def _valid_plain(text: str) -> bool:
    words = [ln for ln in text.splitlines() if ln.strip()]
    return len(words) >= 6


# ---- lyric-looking text inside descriptions / comments ----------------------

_NOT_LYRIC = re.compile(
    r"https?://|www\.|@\w|#\w|©|℗|訂閱|订阅|subscribe|instagram|facebook|twitter|tiktok|spotify|apple music|"
    r"itunes|kkbox|streaming|download|官方|official|channel|頻道|频道|製作|制作|producer|mixed by|mastered|"
    r"arranged|編曲|编曲|作詞|作词|作曲|詞[:：]|词[:：]|曲[:：]|lyrics?\s*[:：]|歌詞[:：]|歌词[:：]|director|導演|"
    r"発売|ticket|影片|視頻|视频|專輯|专辑|new album|\d{4}[./-]\d{1,2}",
    re.IGNORECASE)


def _lyric_like(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60 or _NOT_LYRIC.search(s):
        return False
    letters = sum(ch.isalpha() for ch in s)
    return letters >= max(2, len(s) * 0.5)


def extract_lyrics(text: str) -> Lyrics | None:
    """Find the longest run of lyric-looking lines; timed lines become synced lyrics."""
    if not text:
        return None
    lines = text.replace("\r", "").split("\n")

    timed = []
    for ln in lines:
        m = _TIMED.match(ln)
        if m and _lyric_like(m.group(3)):
            timed.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip()))
    if len(timed) >= 8 and all(a[0] <= b[0] for a, b in zip(timed, timed[1:])):
        return Lyrics(timed, True)

    best: tuple[int, int, int] = (0, 0, 0)          # lyric lines, start, end
    start, good, bad = None, 0, 0
    for i, ln in enumerate(lines + ["\x00"]):
        if not ln.strip():
            continue                                 # stanza breaks don't end a run
        if ln != "\x00" and _lyric_like(ln):
            if start is None:
                start, good, bad = i, 0, 0
            good += 1
            continue
        bad += 1
        if start is not None and (ln == "\x00" or bad > max(1, good // 12)):
            if good > best[0]:
                best = (good, start, i)
            start, good, bad = None, 0, 0
    if best[0] < 8:
        return None
    block = [ln for ln in lines[best[1]:best[2]] if not ln.strip() or _lyric_like(ln)]
    return Lyrics(_plain("\n".join(block)), False)


# ---- sources -----------------------------------------------------------------

def _names(track: Track) -> tuple[str, str]:
    if track.source == SPOTIFY:
        return track.title, track.artist.split(",")[0].strip()
    artist = textnorm.artist_of(track.title, track.artist) or track.artist
    return textnorm.display_title(track.title, artist), artist


def _from_lrclib(http, track: Track) -> Lyrics | None:
    title, artist = _names(track)
    candidates = []
    try:
        params = {"track_name": title, "artist_name": artist}
        if track.duration:
            params["duration"] = int(round(track.duration))
        r = http.session().get(f"{LRCLIB}/get", params=params, timeout=8)
        if r.status_code == 200:
            candidates.append(r.json())
    except Exception:
        pass
    try:
        r = http.get(f"{LRCLIB}/search", params={"q": f"{title} {artist}".strip()}, timeout=8)
        candidates.extend(r.json()[:15])
    except Exception:
        pass

    def score(c: dict) -> float:
        s = 0.0
        if track.duration and c.get("duration"):
            s -= min(abs(track.duration - c["duration"]), 60) / 4
        if c.get("syncedLyrics"):
            s += 3
        if textnorm.same_song(c.get("trackName", ""), c.get("artistName", ""), title, artist):
            s += 5
        return s

    for c in sorted(candidates, key=score, reverse=True):
        if c.get("instrumental"):
            continue
        if track.duration and c.get("duration") and abs(track.duration - c["duration"]) > 20:
            continue
        if not textnorm.same_song(c.get("trackName", ""), c.get("artistName", ""), title, artist):
            continue
        synced = parse_lrc(c.get("syncedLyrics") or "")
        if _valid_synced(synced):
            return Lyrics(synced, True, "LRCLIB")
        plain = c.get("plainLyrics") or ""
        if _valid_plain(plain):
            return Lyrics(_plain(plain), False, "LRCLIB")
    return None


def _from_video_text(ytdlp, bilibili, recommender, track: Track) -> Lyrics | None:
    """Enhanced: scan the description, then the top comments, for pasted lyrics."""
    if track.source == BILIBILI:
        try:
            desc = bilibili.description(track)
        except Exception:
            desc = ""
        found = extract_lyrics(desc)
        if found:
            found.source = "Description"
        return found
    vid = track.id if track.source == YOUTUBE else (recommender.youtube_seed(track) if recommender else None)
    if not vid:
        return None
    opts = {"getcomments": True, "skip_download": True,
            "extractor_args": {"youtube": {"max_comments": ["40", "all", "0", "0"], "comment_sort": ["top"]}}}
    with ytdlp._ydl(**opts) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}
    found = extract_lyrics(info.get("description") or "")
    if found:
        found.source = "Description"
        return found
    for c in sorted(info.get("comments") or [], key=lambda c: -(c.get("like_count") or 0)):
        found = extract_lyrics(c.get("text") or "")
        if found:
            found.source = "Comments"
            return found
    return None


# ---- cache + entry point --------------------------------------------------------

def _cache_path(track: Track):
    return paths.CACHE_DIR / "lyrics" / (hashlib.sha1(track.key.encode()).hexdigest() + ".json")


def fetch(track: Track, enhanced: bool, recommender=None, force: bool = False) -> Lyrics | None:
    from aria.providers import bilibili, http, ytdlp     # keep this module importable without network deps

    path = _cache_path(track)
    if not force and path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("lines"):
                return Lyrics.from_dict(d)
            # a cached miss: retry later, or now if enhanced was turned on since
            if time.time() - d.get("time", 0) < MISS_TTL and (d.get("enhanced") or not enhanced):
                return None
        except (OSError, ValueError):
            pass

    found = _from_lrclib(http, track)
    if found is None and enhanced:
        try:
            found = _from_video_text(ytdlp, bilibili, recommender, track)
        except Exception:
            found = None

    path.parent.mkdir(parents=True, exist_ok=True)
    data = found.to_dict() if found else {"lines": [], "time": time.time(), "enhanced": enhanced}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return found
