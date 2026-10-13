"""Lyrics: gather candidates, check them, keep the best — a wrong timeline is worse than right plain text.

Where candidates come from, best first:

  P0  LRCLIB — an exact lookup plus a few searches (title / artist variants)
  P1  the video's own uploaded subtitles (YouTube; never speech-recognised captions), when
      they turn out to be the lyrics in the song's language rather than a translation or narration
  P2  LRCLIB plain text; with Deep Lyrics Search, lyrics pasted into the description and comments

Every candidate is scored on how well it matches the song (title, artist, length and version —
Live / Acoustic / Remix are different recordings), and timelines are validated (in order, dense
enough, within the song, not a chapter list such as "0:00 Intro / 1:15 Verse"). Description and
comment text is cut into blocks and scored instead of rejected by keyword: credits, links and
promotion are filtered out line by line, a repeated chorus is normal, likes count only a little.

Lyrics must be in the song's own language and script: romanized lyrics (romaji, pinyin) and
translations never win over original-script ones — original plain text beats a romanized
timeline (it can be aligned). Translation and romanization rows found with the original (an LRC
line repeated at the same time, or alternate lines of plain text) are kept beside each line, and
word times (enhanced LRC, TTML) are kept per word; the view may show or hide them. A lyric file
next to a downloaded song (.lrc / .vtt / .srt / .ttml) comes first. The listener can pick other
lyrics (Choose Lyrics) — remembered per song until reset — and corrected song info (Edit Song
Info, :mod:`aria.core.identity`) re-checks what was found under the old name.

Results are cached per song with the algorithm version, so a better algorithm re-checks weak
results while good synced lyrics are never fetched twice. Timing plain lyrics with a speech
model (Forced Alignment) lives in :mod:`aria.core.aligner`.

Everything here is blocking; run it in a background task.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field

from aria import paths
from aria.core import identity, langid, textnorm
from aria.core.models import BILIBILI, SPOTIFY, YOUTUBE, Track

LRCLIB = "https://lrclib.net/api"
ALGO = 4                          # bump when choosing / validating changes: weaker cached results get re-checked
MISS_TTL = 3 * 24 * 3600          # don't re-ask for songs that had no lyrics for a few days
MAX_SEARCHES = 5                  # LRCLIB searches per song, on top of the exact lookup
MAX_COMMENTS = 40

SYNCED, ALIGNED, PLAIN = "synced", "aligned", "plain"


@dataclass
class Lyrics:
    lines: list[tuple[float | None, str]] = field(default_factory=list)
    synced: bool = False              # lines carry times (found synced, or aligned here)
    source: str = ""                  # LRCLIB | Subtitles | Description | Comments | Local File | …
    sync: str = ""                    # synced | aligned | plain
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)
    offset: float = 0.0               # the listener's own shift (s, + = later); not cached here
    trans: list[str] = field(default_factory=list)      # per line: its translation ("" when none)
    roman: list[str] = field(default_factory=list)      # per line: its romanization ("" when none)
    words: list[list] = field(default_factory=list)     # per line: [[start, text], …] when timed word by word
    lang: str = ""                    # language of the original lines (ja | zh | ko | en | "")
    romanized: bool = False           # the lines themselves are romaji / pinyin (no original script found)

    def __post_init__(self):
        if not self.sync:
            self.sync = SYNCED if self.synced else PLAIN
        n = len(self.lines)
        self.trans = (list(self.trans) + [""] * n)[:n]
        self.roman = (list(self.roman) + [""] * n)[:n]
        self.words = (list(self.words) + [[]] * n)[:n]

    @property
    def word_timed(self) -> bool:
        """Timed word by word: as found (Word Synced), or estimated by Forced Alignment."""
        return self.sync in (SYNCED, ALIGNED) and any(self.words)

    @property
    def label(self) -> str:
        """The quiet status line: what kind of lyrics these are, and from where."""
        tail = " · Romanized" if self.romanized else ""
        if self.sync == ALIGNED:
            return ("Estimated Sync · Word by Word" if self.word_timed else "Estimated Sync") + tail
        if self.sync == SYNCED:
            kind = "Word Synced" if self.word_timed else "Line Synced"
            return (f"{kind} · {self.source}" if self.source else kind) + tail
        return "Plain Lyrics" + tail

    def to_dict(self) -> dict:
        d = {"lines": self.lines, "synced": self.synced, "source": self.source, "sync": self.sync,
             "confidence": round(self.confidence, 3), "issues": self.issues, "lang": self.lang,
             "romanized": self.romanized}
        if any(self.trans):
            d["trans"] = self.trans
        if any(self.roman):
            d["roman"] = self.roman
        if any(self.words):
            d["words"] = self.words
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Lyrics:
        return cls([(t, s) for t, s in d.get("lines", [])], d.get("synced", False), d.get("source", ""),
                   d.get("sync", ""), d.get("confidence", 0.0), list(d.get("issues", [])),
                   trans=d.get("trans", []), roman=d.get("roman", []), words=d.get("words", []),
                   lang=d.get("lang", ""), romanized=d.get("romanized", False))

    def index_at(self, seconds: float) -> int:
        """Line being sung at ``seconds`` (synced only), -1 before the first."""
        idx = -1
        for i, (t, _s) in enumerate(self.lines):
            if t is None or t > seconds + 0.15:
                break
            idx = i
        return idx


def classify(lyr: Lyrics) -> Lyrics:
    """Note the language of the original lines, and whether they are romanized."""
    g = langid.detect_lines([s for _t, s in lyr.lines if s and _line_kind(s) == "lyric"])
    lyr.lang = g.lang
    lyr.romanized = bool(g.romanized)
    return lyr


# ---- parsing -----------------------------------------------------------------

_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_TIMED = re.compile(r"^\s*[\[(]?(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?[\])]?\s*[-–—|:]?\s+(.+)$")   # "1:23 lyric"
_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


_WORD_TAG = re.compile(r"<(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)>")     # enhanced LRC: a time per word
_OFFSET_TAG = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE | re.MULTILINE)


def _secs(m: str, s: str) -> float:
    return int(m) * 60 + float(s.replace(":", "."))


def parse_lrc_full(text: str) -> Lyrics:
    """LRC with everything kept apart: a line may carry several times (a chorus sung twice);
    per-word times (``<mm:ss.xx>``) become the line's words; the file's own [offset:] (ms, + =
    sooner) is applied; a second line at the same time is the first one's romanization (Latin
    spelling of it) or its translation — never a line of its own."""
    shift = 0.0
    if m := _OFFSET_TAG.search(text):
        shift = int(m.group(1)) / 1000
    rows: list[tuple[float, str, list]] = []
    for raw in text.splitlines():
        tags = _LRC_TAG.findall(raw)
        if not tags:
            continue
        body = _LRC_TAG.sub("", raw)
        words: list = []
        marks = list(_WORD_TAG.finditer(body))
        if marks:
            for k, wm in enumerate(marks):
                end = marks[k + 1].start() if k + 1 < len(marks) else len(body)
                chunk = body[wm.end():end]
                if chunk.strip():
                    words.append([round(max(0.0, _secs(*wm.groups()) - shift), 3), chunk])
        plain = re.sub(r"[ \t]{2,}", " ", _WORD_TAG.sub("", body)).strip()
        for mm, ss in tags:
            rows.append((max(0.0, _secs(mm, ss) - shift), plain, words))
    rows.sort(key=lambda x: x[0])                      # stable: an original stays above its translation
    lines, trans, roman, wordl = [], [], [], []
    for t, plain, words in rows:
        if lines and abs(t - lines[-1][0]) < 0.02 and plain and lines[-1][1]:
            if plain == lines[-1][1]:
                continue
            if langid.is_romanization_of(plain, lines[-1][1]):
                roman[-1] = roman[-1] or plain
            else:
                trans[-1] = trans[-1] or plain
            continue
        lines.append((t, plain))
        trans.append("")
        roman.append("")
        wordl.append(words if words and "".join(w[1] for w in words).strip() == plain else
                     ([[w[0], w[1]] for w in words] if words else []))
    return Lyrics(lines, True, trans=trans, roman=roman, words=wordl)


def pair_rows(lyr: Lyrics, lang: str = "") -> Lyrics:
    """Plain text that alternates original and translation (or romaji) lines: fold each extra
    line under the one it belongs to. Needs a clear pattern — about half the lines in pairs —
    and an original language to tell which is which (``lang``, or the most common script)."""
    lines = [s for _t, s in lyr.lines]
    if len(lines) < 6 or any(lyr.trans) or any(lyr.roman):
        return lyr

    def kind(s: str) -> str:
        g = langid.detect(s)
        return g.lang if g.lang in ("ja", "ko") else ("zh" if g.script == "han" else
                                                      ("latin" if g.script == "latin" else g.lang or g.script))
    kinds = [kind(s) if s else "" for s in lines]
    seen = [k for k in kinds if k]
    if not seen:
        return lyr
    main = lang if lang in seen else max(set(seen), key=seen.count)
    pairs = sum(1 for a, b in zip(kinds, kinds[1:]) if a == main and b and b != main)
    if pairs * 2 < len(seen) * 0.8 or (main == "latin" and lang != "en"):
        return lyr
    out, trans, roman = [], [], []
    prev = ""
    for (t, s), k in zip(lyr.lines, kinds):
        if out and k and k != main and prev == main:
            if langid.is_romanization_of(s, out[-1][1]):
                roman[-1] = roman[-1] or s
            else:
                trans[-1] = trans[-1] or s
            prev = k
            continue
        out.append((t, s))
        trans.append("")
        roman.append("")
        prev = k
    return Lyrics(out, lyr.synced, lyr.source, lyr.sync, lyr.confidence, lyr.issues, trans=trans, roman=roman)


def _ttml_time(v: str) -> float | None:
    v = (v or "").strip()
    try:
        if v.endswith("ms"):
            return float(v[:-2]) / 1000
        if v.endswith("s"):
            return float(v[:-1])
        parts = [float(x) for x in v.replace(",", ".").split(":")]
        sec = 0.0
        for x in parts:
            sec = sec * 60 + x
        return sec
    except ValueError:
        return None


def parse_ttml(text: str) -> Lyrics | None:
    """TTML (Apple-style timed lyrics): <p begin> lines, <span begin> words, translation spans."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    lines, trans, words = [], [], []
    for p in root.iter():
        if not p.tag.endswith("}p") and p.tag != "p":
            continue
        start = _ttml_time(p.get("begin", ""))
        if start is None:
            continue
        ws, tr = [], ""
        for sp in p.iter():
            if sp is p or not (sp.tag.endswith("}span") or sp.tag == "span"):
                continue
            role = next((v for k, v in sp.attrib.items() if k.endswith("role")), "")
            if "translation" in role:
                tr = "".join(sp.itertext()).strip()
                continue
            b = _ttml_time(sp.get("begin", ""))
            if b is not None and (sp.text or "").strip():
                ws.append([b, sp.text + (sp.tail or "")])
        text_ = "".join(w[1] for w in ws).strip() if ws else "".join(p.itertext()).replace(tr, "").strip()
        lines.append((start, re.sub(r"\s+", " ", text_)))
        trans.append(tr)
        words.append(ws)
    if not lines:
        return None
    order = sorted(range(len(lines)), key=lambda k: lines[k][0])
    return Lyrics([lines[k] for k in order], True, trans=[trans[k] for k in order], words=[words[k] for k in order])


def from_local_file(track: Track) -> Lyrics | None:
    """A lyric file next to the downloaded song (same name): .lrc, .ttml, .vtt or .srt."""
    import os
    path = track.local_path
    if not path or not os.path.exists(path):
        return None
    stem = os.path.splitext(path)[0]
    for ext in (".lrc", ".ttml", ".vtt", ".srt", ".txt"):
        f = stem + ext
        if not os.path.exists(f):
            continue
        try:
            raw = open(f, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        if ext == ".lrc":
            lyr = parse_lrc_full(raw)
        elif ext == ".ttml":
            lyr = parse_ttml(raw)
        elif ext == ".txt":
            lyr = Lyrics(_plain(raw), False) if _valid_plain(raw) else None
        else:
            timed = parse_subtitles(raw)
            lyr = Lyrics(timed, True) if timed else None
        if lyr is not None and lyr.lines:
            mine = _ARIA_TAG.search(raw) if ext == ".lrc" else None
            lyr.source, lyr.confidence = (mine.group(1).strip() if mine else "Local File"), 1.0
            return classify(lyr)
    return None


# ---- lyric files beside downloaded songs ------------------------------------------------------

LYRIC_FILES = (".lrc", ".ttml")
_ARIA_TAG = re.compile(r"^\[re:\s*Aria\s*·\s*([^\]]*)\]", re.MULTILINE)


def sidecars(audio_path: str) -> list[str]:
    """The lyric files (.lrc / .ttml) that belong to a downloaded song (same name)."""
    import os
    stem = os.path.splitext(audio_path)[0]
    return [stem + ext for ext in LYRIC_FILES if os.path.exists(stem + ext)]


def remove_sidecars(audio_path: str) -> None:
    """The song's file went: its lyric files go too."""
    import os
    for f in sidecars(audio_path):
        try:
            os.remove(f)
        except OSError:
            pass


def _ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return bool(_ARIA_TAG.search(fh.read(400)))
    except OSError:
        return False


def _stamp(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def to_lrc(lyr: Lyrics, title: str = "", artist: str = "") -> str:
    """LRC text: word times as <mm:ss.xx>, a translation / romanization as a second line at the same time."""
    out = [f"[ti:{title}]" if title else "", f"[ar:{artist}]" if artist else "", f"[re:Aria · {lyr.source or 'LRCLIB'}]"]
    for i, (t, text) in enumerate(lyr.lines):
        if t is None:
            continue
        tag = f"[{_stamp(t)}]"
        words = lyr.words[i] if i < len(lyr.words) else []
        out.append(tag + ("".join(f"<{_stamp(w)}>{piece}" for w, piece in words) if words else text))
        for extra in (lyr.trans[i] if i < len(lyr.trans) else "", lyr.roman[i] if i < len(lyr.roman) else ""):
            if extra:
                out.append(tag + extra)
    return "\n".join(x for x in out if x) + "\n"


def write_beside(track: Track, lyr: Lyrics | None, replace: bool = False) -> str | None:
    """Keep found synced lyrics as a .lrc next to the downloaded song (never over the
    listener's own lyric file; ``replace`` only rewrites one Aria wrote)."""
    import os
    path = track.local_path
    if not path or not os.path.exists(path) or lyr is None or lyr.sync != SYNCED or lyr.source == "Local File":
        return None
    target = os.path.splitext(path)[0] + ".lrc"
    have = sidecars(path)
    if have and not (replace and all(_ours(f) for f in have)):
        return None
    ident = identity.of(track)
    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(to_lrc(lyr, ident.title, ident.artist))
    except OSError:
        return None
    return target


def parse_subtitles(text: str) -> list[tuple[float, str]]:
    """WebVTT / SRT cues → (start, text), markup removed, rolling duplicates folded."""
    out: list[tuple[float, str]] = []
    lines = text.replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        m = _CUE.search(lines[i])
        if not m:
            i += 1
            continue
        h, mi, s, ms = m.group(1), m.group(2), m.group(3), m.group(4)
        start = int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000
        i += 1
        words = []
        while i < len(lines) and lines[i].strip():
            words.append(re.sub(r"<[^>]+>|\{[^}]+\}", "", lines[i]).strip())
            i += 1
        text_ = " ".join(w for w in words if w).strip()
        text_ = re.sub(r"^[♪♫\s]+|[♪♫\s]+$", "", text_)
        if text_ and not (out and out[-1][1] == text_):
            out.append((start, text_))
    return out


def _plain(text: str) -> list[tuple[None, str]]:
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.strip().splitlines()]
    out: list[tuple[None, str]] = []
    for ln in lines:                             # runs of blank lines become one stanza break
        if not ln and (not out or not out[-1][1]):
            continue
        out.append((None, ln))
    while out and not out[-1][1]:
        out.pop()
    return out


def _valid_plain(text: str) -> bool:
    return sum(1 for ln in text.splitlines() if ln.strip()) >= 6


# ---- matching ------------------------------------------------------------------

_VERSION = re.compile(r"(?<![a-z])(live|acoustic|unplugged|remix|rmx|instrumental|inst\.?|karaoke|off ?vocal|"
                      r"sped ?up|slowed|nightcore|demo)(?![a-z])|現場|现场|演唱會|演唱会|伴奏|純音樂|纯音乐|"
                      r"ライブ|アコースティック|リミックス|インスト", re.IGNORECASE)


def version_tags(title: str) -> set[str]:
    """What makes a recording a different one (Live, Acoustic, Remix…), not just noise."""
    tags = set()
    for m in _VERSION.finditer(title):
        t = m.group(0).lower()
        tags.add({"rmx": "remix", "inst": "instrumental", "inst.": "instrumental", "現場": "live", "现场": "live",
                  "演唱會": "live", "演唱会": "live", "ライブ": "live", "unplugged": "acoustic",
                  "アコースティック": "acoustic", "リミックス": "remix", "インスト": "instrumental",
                  "伴奏": "instrumental", "純音樂": "instrumental", "纯音乐": "instrumental",
                  "off vocal": "instrumental", "offvocal": "instrumental"}.get(t, t.replace(" ", "")))
    return tags


def _names(track: Track) -> tuple[str, str]:
    """(song, artist) to search for: the listener's corrections, else the best reading of the title."""
    ident = identity.of(track)
    return ident.title, ident.artist or identity.clean_uploader(track.artist)


def title_variants(track: Track) -> list[str]:
    """Ways the song may be named: the cleaned title, the part before any bracket (a film or
    drama name in 《》 is not the song), the core title."""
    title, artist = _names(track)
    out = [title]
    if track.source != SPOTIFY:
        head = re.split(r"\s*[(\[（【《「]", track.title, maxsplit=1)[0].strip(" -|｜")
        if head:
            out.append(identity.parse(head, track.artist).title)
        out.append(textnorm.core_title(track.title, artist))
    # "Those Bygone Years 那些年": each script on its own too (that's how lyric sites often list it)
    out += _scripts(title)[1:]
    seen, res = set(), []
    for v in out:
        k = textnorm.fold(v or "").replace(" ", "")
        if res and len(k) < (2 if any(ord(ch) > 0x2E80 for ch in k) else 3):
            continue                                     # an extra "x" would find any song with an x in it
        if k not in seen:
            seen.add(k)
            res.append(v)
    return res


# lyrics in a script the song can't be sung in. A title in kanji alone can be Chinese or
# Japanese (火星人, 米津玄師), so only kana or Hangul in the title decides; kanji rules out Korean.
_WRONG_SCRIPT = {"zh": {"ko"}, "ko": {"ja", "zh"}, "ja": {"ko", "zh"}}


def song_language(track: Track) -> tuple[str, float]:
    """(language, confidence) of the song itself: the listener's choice, else its title and
    artist. Kanji alone answers "ja or zh" — reported as ("han", …) so Korean is still ruled out."""
    ident = identity.of(track)
    if ident.language_source == "user":
        return ident.language, 1.0
    g = langid.detect(f"{ident.title} {ident.artist}")
    if g.lang:
        return g.lang, g.confidence
    return ("han", 0.5) if g.script in ("han", "mixed") and re.search(r"[\u4e00-\u9fff]", ident.title) else ("", 0.0)


def lang_ok(track: Track, words: list[str]) -> bool:
    """Whether lyrics in this language can belong to this song: not in a script the song can't
    be sung in (Korean lyrics for a kanji title), not a translation (English lyrics for a song
    sure to be Japanese). Romanized lyrics pass here — they are ranked below originals instead."""
    song, sure = song_language(track)
    g = langid.detect(" ".join(w for w in words[:24] if w))
    if g.romanized or not g.lang:
        return True
    if song == "han":
        return g.lang not in ("ko",)
    if g.lang in _WRONG_SCRIPT.get(song, set()) and (g.confidence >= 0.6 or sure >= 0.9):
        return False
    return not (song in ("ja", "ko", "zh") and sure >= 0.9 and g.lang == "en" and g.confidence >= 0.8 and not g.also)


def _overlap(lines_a: list[str], lines_b: list[str]) -> float:
    """How much of ``a``'s text appears in ``b`` (folded characters, so scripts and spacing don't matter)."""
    def grams(lines):
        text = textnorm.fold("".join(lines)).replace(" ", "")
        return {text[i:i + 2] for i in range(len(text) - 1)}
    a, b = grams(lines_a), grams(lines_b)
    return len(a & b) / len(a) if a else 0.0


# ---- timelines ---------------------------------------------------------------------

_CHAPTER = re.compile(r"^(intro|verse|pre-?chorus|chorus|hook|bridge|interlude|instrumental|solo|outro|ending|"
                      r"opening|break|drop|refrain|part|track|chapter)\b|^\d+[.)]\s|前奏|主歌|副歌|間奏|间奏|尾奏|"
                      r"イントロ|[ABCＡＢＣ]メロ|サビ|アウトロ|間奏", re.IGNORECASE)


def check_timeline(timed: list[tuple[float, str]], duration: float = 0.0) -> list[str]:
    """Problems that make a timeline untrustworthy ([] = fine to show as synced)."""
    issues = []
    words = [s for _t, s in timed if s]
    if len(words) < 6 or len(set(words)) < 4:
        return ["too few lines"]
    times = [t for t, s in timed if s]
    backwards = sum(1 for a, b in zip(times, times[1:]) if b < a - 0.01)
    if backwards > max(1, len(times) * 0.05):
        issues.append("out of order")
    if sum(1 for w in words if _CHAPTER.search(w.strip())) >= max(2, len(words) * 0.3):
        issues.append("chapter list")
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b >= a)
    if gaps:
        median = gaps[len(gaps) // 2]
        if median > 20:
            issues.append("too sparse")                   # a few marks across the song: chapters
        if median < 0.4:
            issues.append("too dense")
    if duration:
        if times[-1] > duration + 8:
            issues.append("beyond the song")
        if times[-1] - times[0] < duration * 0.35:
            issues.append("covers too little")
    return issues


# ---- lyrics inside descriptions / comments -------------------------------------------

_URL = re.compile(r"https?://|www\.|\.com\b|\.tv\b|\b[\w.-]+@[\w-]+\.\w+")
_HANDLE = re.compile(r"(^|\s)[@#][\w぀-ヿ一-鿿]+")
_CREDIT = re.compile(r"^\s*[\[(【]?\s*(作詞|作词|作曲|編曲|编曲|詞|词|曲|唄|歌|vocal|vo\.|music|lyrics?|words|"
                     r"composer|composed|arranged|arrangement|producer|produced|mix(ed)?|master(ed|ing)?|"
                     r"guitar|bass|drums|piano|keyboard|illust(ration)?|movie|video|director|監督|導演|导演|"
                     r"編集|映像|イラスト|動画|作画|演奏|chorus|engineer|label|artist)\s*(by)?\s*[:：/／|]",
                     re.IGNORECASE)
_PROMO = re.compile(r"subscribe|訂閱|订阅|チャンネル登録|follow|instagram|facebook|twitter|tiktok|weibo|微博|"
                    r"spotify|apple music|itunes|kkbox|line music|streaming|download|配信|発売|發行|发行|"
                    r"release|ticket|門票|门票|tour|巡演|concert|new album|專輯|专辑|official (music )?video|"
                    r"\bmv\b|m/v|©|℗|all rights|版權|版权|\d{4}[./-]\d{1,2}[./-]\d{1,2}", re.IGNORECASE)


def _line_kind(line: str) -> str:
    """noise | credit | promo | prose | lyric | other — each line judged on its own."""
    s = line.strip()
    if not s:
        return "blank"
    if _URL.search(s) or (_HANDLE.search(s) and len(s) < 40):
        return "noise"
    if _CREDIT.search(s):
        return "credit"
    if _PROMO.search(s):
        return "promo"
    wide = sum(1 for ch in s if ord(ch) > 0x2E80)
    if len(s) > (48 if wide > len(s) * 0.5 else 90):
        return "prose"                               # a paragraph, not a sung line
    # sentences, not sung lines: full stops, or several commas in one line
    if wide > len(s) * 0.5:
        if s.endswith("。") or len(re.findall(r"[，。；：]", s)) >= 2 or (len(s) > 22 and "，" in s):
            return "prose"
    elif len(s) > 50 and len(re.findall(r"[.,;:!?]", s)) >= 3:
        return "prose"
    letters = sum(ch.isalpha() for ch in s)
    return "lyric" if letters >= max(2, len(s) * 0.5) else "other"


@dataclass
class _Candidate:
    lyrics: Lyrics
    score: float


def _blocks(lines: list[str]) -> list[list[str]]:
    """Runs of lyric lines; single blank lines (stanzas) stay inside, anything else ends a run."""
    out, cur, blanks = [], [], 0
    for ln in lines + ["\x00"]:
        kind = "end" if ln == "\x00" else _line_kind(ln)
        if kind == "lyric":
            if blanks and cur:
                cur.append("")
            cur.append(ln.strip())
            blanks = 0
        elif kind == "blank":
            blanks += 1
            if blanks >= 3 and cur:                  # a wide gap: a new section of the text
                out.append(cur)
                cur, blanks = [], 0
        else:
            if cur:
                out.append(cur)
            cur, blanks = [], 0
    return out


def extract_lyrics(text: str, duration: float = 0.0, title: str = "", artist: str = "",
                   reference: list[str] | None = None) -> _Candidate | None:
    """The most credible lyrics in a description or comment, scored (None when nothing convinces)."""
    if not text:
        return None
    lines = text.replace("\r", "").split("\n")
    lang = langid.script_language(f"{title} {artist}") if title else ""

    def lang_fit(words: list[str]) -> float:
        if not lang:
            return 0.0
        mine = langid.script_language(" ".join(words[:12]))
        return 1.0 if mine == lang else (-2.0 if lang != "en" and mine == "en" else -0.5)

    best: _Candidate | None = None
    # timed lines: only as synced lyrics when the timeline holds up
    timed = []
    for ln in lines:
        m = _TIMED.match(ln)
        if m and _line_kind(m.group(4)) == "lyric":
            timed.append((int(m.group(1)) * 60 + int(m.group(2)) + int((m.group(3) or "0").ljust(3, "0")) / 1000,
                          m.group(4).strip()))
    if timed:
        issues = check_timeline(timed, duration)
        if not issues:
            words = [s for _t, s in timed]
            score = 6 + min(len(words), 40) / 8 + lang_fit(words)
            if reference:
                score += 4 * _overlap(words, reference)
            best = _Candidate(Lyrics(timed, True, confidence=min(1.0, score / 12)), score)
    for block in _blocks(lines):
        words = [w for w in block if w]
        if len(words) < 6:
            continue
        lengths = [len(w) for w in words]
        avg = sum(lengths) / len(lengths)
        score = 1.0 + min(len(words), 40) / 6 + lang_fit(words)
        score -= 1.5 if avg < 3 else 0                 # emoji / one-word reactions
        if reference:
            ov = _overlap(words, reference)
            score += 5 * ov - (2 if ov < 0.15 else 0)
        if title and textnorm.fold(textnorm.core_title(title, artist)) in textnorm.fold(" ".join(words[:2])):
            score += 0.3                                # starts with the song's name: often a lyric post
        if len(words) < 8 and not reference:
            score -= 1
        if best is None or score > best.score:
            best = _Candidate(Lyrics(_plain("\n".join(block)), False, confidence=min(1.0, score / 10)), score)
    return best if best is not None and best.score >= 2.0 else None


# ---- sources -----------------------------------------------------------------

def _scripts(text: str) -> list[str]:
    """``text``, plus its CJK part and its Latin part when it has both ("胡夏 Xia Hu" → 胡夏, Xia Hu)."""
    cjk = " ".join(langid.cjk_runs(text))
    latin = langid.without_cjk(text).strip(" -|｜/")
    latin = re.sub(r"\s+", " ", latin)
    out = [text]
    if cjk and re.search(r"[A-Za-z]{2}", latin):
        out += [cjk, latin]
    return out


def _lrclib_candidates(http, track: Track, titles: list[str], artist: str) -> list[dict]:
    seen: dict = {}

    def keep(items):
        for c in items or []:
            if isinstance(c, dict) and c.get("id") not in seen:
                seen[c.get("id")] = c
    title = titles[0]
    try:
        params = {"track_name": title, "artist_name": artist}
        if track.duration:
            params["duration"] = int(round(track.duration))
        r = http.session().get(f"{LRCLIB}/get", params=params, timeout=8)
        if r.status_code == 200:
            keep([r.json()])
    except Exception:
        pass
    # lyric sites list a song under one script: pair titles and artists written alike first
    artists = _scripts(artist) if artist else [""]
    pairs = sorted(((v, a) for v in titles for a in artists),
                   key=lambda va: (langid.has_cjk(va[0]) != langid.has_cjk(va[1]), titles.index(va[0]), artists.index(va[1])))
    searches = [{"track_name": title, "artist_name": artist}] + [{"q": f"{v} {a}".strip()} for v, a in pairs]
    # the CJK names alone ("下雨天Xia Yu Tian" → 下雨天), in simplified characters
    cjk_title = next((v for v in titles if langid.has_cjk(v) and not re.search(r"[A-Za-z]{2}", v)), title)
    cjk_artist = next((a for a in artists if langid.has_cjk(a) and not re.search(r"[A-Za-z]{2}", a)), artist)
    simp = textnorm.simplified(f"{cjk_title} {cjk_artist}".strip())
    if simp != f"{title} {artist}".strip():
        searches.insert(1, {"q": simp})                 # traditional names, simplified listings (and the reverse)
    searches.insert(min(len(searches), MAX_SEARCHES - 1), {"q": titles[0]})   # the uploader may be a channel
    for params in searches[:MAX_SEARCHES]:
        if any(c.get("syncedLyrics") and _lrclib_fits(track, c, titles, artist, synced=True)
               and not langid.detect(c["syncedLyrics"][:600]).romanized for c in seen.values()):
            break                                       # a good synced match in the original script: enough
        try:
            r = http.session().get(f"{LRCLIB}/search", params=params, timeout=8)
            if r.status_code == 200:
                keep(r.json()[:15])
        except Exception:
            pass
    return list(seen.values())


def _lrclib_fits(track: Track, c: dict, titles: list[str], artist: str, synced: bool) -> bool:
    if c.get("instrumental"):
        return False
    if not any(textnorm.same_song(c.get("trackName", ""), c.get("artistName", ""), t, artist) for t in titles):
        return False
    if version_tags(c.get("trackName", "") + " " + (c.get("albumName") or "")) != version_tags(track.title):
        return False                                    # a live take's timing isn't the studio one's
    if track.duration and c.get("duration"):
        # a timeline needs the same edit; plain words survive a longer intro
        if abs(track.duration - c["duration"]) > (12 if synced else 40):
            return False
    return True


def _from_lrclib(http, track: Track) -> tuple[Lyrics | None, Lyrics | None]:
    """(best synced, best plain) from LRCLIB."""
    titles, artist = title_variants(track), _names(track)[1]
    cands = _lrclib_candidates(http, track, titles, artist)

    def closeness(c):
        return -abs(track.duration - c["duration"]) if track.duration and c.get("duration") else -5

    synced_c: list[Lyrics] = []
    plain_c: list[Lyrics] = []
    lang = song_language(track)[0]
    for c in sorted(cands, key=closeness, reverse=True):
        if c.get("syncedLyrics") and _lrclib_fits(track, c, titles, artist, True):
            lyr = parse_lrc_full(c["syncedLyrics"])
            if not check_timeline(lyr.lines, track.duration) and lang_ok(track, [s for _t, s in lyr.lines]):
                lyr.source, lyr.confidence = "LRCLIB", 0.95
                synced_c.append(classify(lyr))
        if _valid_plain(c.get("plainLyrics") or "") and _lrclib_fits(track, c, titles, artist, False) \
                and lang_ok(track, c["plainLyrics"].splitlines()):
            lyr = pair_rows(Lyrics(_plain(c["plainLyrics"]), False, "LRCLIB", confidence=0.8), lang)
            plain_c.append(classify(lyr))
    # the song's own script first: original plain text beats a romanized timeline (it can be aligned)
    synced = next((x for x in synced_c if not x.romanized), None)
    plain = next((x for x in plain_c if not x.romanized), None)
    if synced is None and plain is None:
        synced = synced_c[0] if synced_c else None
        plain = plain_c[0] if plain_c else None
    return synced, plain


_SUB_LANGS = {"ja": ("ja", "ja-JP"), "ko": ("ko", "ko-KR"), "en": ("en", "en-US", "en-GB"),
              "zh": ("zh-Hant", "zh-TW", "zh-HK", "zh", "zh-Hans", "zh-CN")}


def _from_subtitles(http, info: dict, track: Track, reference: list[str] | None) -> Lyrics | None:
    """The uploader's own subtitles, when they are the song's lyrics with a believable timeline."""
    subs = info.get("subtitles") or {}                    # uploaded only: automatic captions are speech guesses
    if not subs:
        return None
    lang = song_language(track)[0]
    lang = "" if lang == "han" else lang
    order = [c for c in _SUB_LANGS.get(lang, ()) if c in subs] + [c for c in subs if c not in _SUB_LANGS.get(lang, ())]
    for code in order[:2]:
        fmt = next((f for f in subs[code] if f.get("ext") in ("vtt", "srt")), None)
        if not fmt or not fmt.get("url"):
            continue
        try:
            r = http.session().get(fmt["url"], timeout=8)
            timed = parse_subtitles(r.text)
        except Exception:
            continue
        if check_timeline(timed, track.duration):
            continue
        words = [s for _t, s in timed]
        if sum(1 for w in words if _line_kind(w) == "lyric") < len(words) * 0.7:
            continue                                     # narration, credits, sound cues
        g = langid.detect(" ".join(words[:20]))
        if (lang not in ("en", "") and g.lang == "en" and not g.romanized) or g.romanized or not lang_ok(track, words):
            continue                                     # a translation or romanization, not what's sung
        if reference and _overlap(words, reference) < 0.25:
            continue                                     # doesn't read like the known lyrics
        return Lyrics(timed, True, "Subtitles", confidence=0.85)
    return None


def _video_info(ytdlp, recommender, track: Track, comments: bool) -> dict:
    vid = track.id if track.source == YOUTUBE else \
        (track.match_id or (recommender.youtube_seed(track) if recommender else None))
    if not vid:
        return {}
    opts = {"skip_download": True}
    if comments:
        opts.update(getcomments=True,
                    extractor_args={"youtube": {"max_comments": [str(MAX_COMMENTS), "all", "0", "0"],
                                                "comment_sort": ["top"]}})
    with ytdlp._ydl(**opts) as y:
        return y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}


def _from_video_text(texts: list[tuple[str, str, int]], track: Track, reference: list[str] | None) -> Lyrics | None:
    """``texts``: (source, text, likes). The best-scoring block across them, or None."""
    title, artist = _names(track)
    best: tuple[float, Lyrics] | None = None
    for source, text, likes in texts:
        cand = extract_lyrics(text, track.duration, title, artist, reference)
        if cand is None or not lang_ok(track, [s for _t, s in cand.lyrics.lines]):
            continue
        score = cand.score + (0.5 if source == "Description" else 0.0) + 0.15 * math.log10(1 + max(0, likes))
        if best is None or score > best[0]:
            cand.lyrics.source = source
            best = (score, cand.lyrics)
    return best[1] if best else None


def find(track: Track, enhanced: bool, recommender=None) -> Lyrics | None:
    """Gather and compare candidates; blocking. Requests are bounded (≤ 5 LRCLIB, 1 video page,
    ≤ 2 subtitle files)."""
    from aria.providers import bilibili, http, ytdlp     # keep this module importable without network deps

    local = from_local_file(track)
    if local is not None:
        return local                                     # the listener's own file beside the song
    synced, plain = _from_lrclib(http, track)
    if synced is not None:
        return synced                                    # the best kind there is: stop here
    reference = [s for _t, s in plain.lines if s] if plain else None
    video_ok = track.source in (YOUTUBE, SPOTIFY) or (track.source == BILIBILI and enhanced)
    if not video_ok:
        return plain
    try:
        if track.source == BILIBILI:
            texts = [("Description", bilibili.description(track), 0)]
            info = {}
        else:
            info = _video_info(ytdlp, recommender, track, comments=enhanced)
            texts = [("Description", info.get("description") or "", 0)] if enhanced else []
            texts += [("Comments", c.get("text") or "", c.get("like_count") or 0)
                      for c in (info.get("comments") or [])[:MAX_COMMENTS]]
    except Exception:
        return plain
    subs = _from_subtitles(http, info, track, reference) if info else None
    if subs is not None:
        return subs
    if enhanced:
        found = _from_video_text(texts, track, reference)
        if found is not None and (found.synced or plain is None):
            return found                                 # a validated timeline beats plain text; else LRCLIB's words
    return plain


# ---- the listener's per-song choices -------------------------------------------------

class Prefs:
    """Sync offset and "no alignment" per song, kept with the user's data (not the cache)."""

    def __init__(self):
        self.path = paths.DATA_DIR / "lyrics_prefs.json"
        try:
            self.data: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def offset(self, key: str) -> float:
        return float(self.data.get(key, {}).get("offset", 0.0))

    def set_offset(self, key: str, seconds: float) -> None:
        self._set(key, "offset", round(seconds, 1) or None)

    def align_off(self, key: str) -> bool:
        return bool(self.data.get(key, {}).get("no_align"))

    def set_align_off(self, key: str, off: bool) -> None:
        self._set(key, "no_align", True if off else None)

    def choice(self, key: str) -> dict | None:
        """Lyrics the listener picked for this song (Choose Lyrics), e.g. {"lrclib": 123}."""
        return self.data.get(key, {}).get("choice")

    def set_choice(self, key: str, choice: dict | None) -> None:
        self._set(key, "choice", choice)

    def _set(self, key: str, name: str, value) -> None:
        entry = self.data.setdefault(key, {})
        if value is None:
            entry.pop(name, None)
        else:
            entry[name] = value
        if not entry:
            self.data.pop(key, None)
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


_prefs: Prefs | None = None


def prefs() -> Prefs:
    """The one per-song lyric preferences store (several writers would overwrite each other)."""
    global _prefs
    if _prefs is None:
        _prefs = Prefs()
    return _prefs


def cached_alignment(track: Track, plain: Lyrics) -> Lyrics | None:
    """An alignment made earlier for these very words — and, when the song is on disk, for this
    very file (a streamed song keeps the one made from its temporary download)."""
    import os
    from aria.core import aligner
    d = read_cache(track) or {}
    al = d.get("aligned")
    if not al or al.get("words") != [s for _t, s in plain.lines if s]:
        return None
    path = track.local_path
    if path and os.path.exists(path) and al.get("sig") != aligner.file_signature(path) and not al.get("temp"):
        return None
    return _aligned(plain, al["lines"], al.get("confidence", 0.0), al.get("word_times"))


def _aligned(plain: Lyrics, lines, confidence: float, word_times=None) -> Lyrics:
    lyr = Lyrics([(t, s) for t, s in lines], True, plain.source, ALIGNED, confidence, words=word_times or [])
    if len(lines) == len(plain.lines):
        lyr.trans, lyr.roman = list(plain.trans), list(plain.roman)
    lyr.lang, lyr.romanized = plain.lang, plain.romanized
    return lyr


def save_alignment(track: Track, plain: Lyrics, result: dict) -> Lyrics:
    d = read_cache(track) or {}
    d["aligned"] = dict(result, words=[s for _t, s in plain.lines if s])
    write_cache(track, d)
    return _aligned(plain, result["lines"], result.get("confidence", 0.0), result.get("word_times"))


# ---- cache + entry point --------------------------------------------------------

def _cache_path(track: Track):
    return _cache_path_of(track.key)


def _cache_path_of(key: str):
    return paths.CACHE_DIR / "lyrics" / (hashlib.sha1(key.encode()).hexdigest() + ".json")


def _cached_language(key: str) -> tuple[str, float]:
    """For :mod:`identity`: the language of lyrics found earlier (no network)."""
    try:
        d = json.loads(_cache_path_of(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", 0.0
    lang = d.get("lang") or ""
    if not lang and d.get("lines"):
        g = langid.detect_lines([s for _t, s in d["lines"][:40] if s])
        lang = g.lang if g.confidence >= 0.6 else ""
    return (lang, 0.6 if d.get("romanized") else 0.9) if lang else ("", 0.0)


identity.lyrics_lookup = _cached_language


def read_cache(track: Track) -> dict | None:
    try:
        return json.loads(_cache_path(track).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_cache(track: Track, data: dict) -> None:
    path = _cache_path(track)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def search_lrclib(query: str, track: Track | None = None, limit: int = 20) -> list[dict]:
    """Choose Lyrics: LRCLIB results for ``query``, each with what the listener needs to pick —
    the kind (Word Synced / Line Synced / Plain), length against the song's, language — and the
    lyrics themselves for the preview (parsed here, off the interface). Blocking."""
    from aria.providers import http
    try:
        r = http.session().get(f"{LRCLIB}/search", params={"q": query}, timeout=8)
        items = r.json()[:limit] if r.status_code == 200 else []
    except Exception:
        items = []
    out = []
    for c in items:
        if not isinstance(c, dict) or c.get("instrumental"):
            continue
        lyr = lyrics_of(c)
        kind = "Word Synced" if lyr.word_timed else ("Line Synced" if lyr.synced else "Plain")
        out.append({"id": c.get("id"), "title": c.get("trackName", ""), "artist": c.get("artistName", ""),
                    "album": c.get("albumName") or "", "duration": c.get("duration") or 0,
                    "synced": lyr.synced, "kind": kind, "lang": lyr.lang, "romanized": lyr.romanized,
                    "lyrics": lyr, "preview": [s for _t, s in lyr.lines if s][:4],
                    "diff": abs(track.duration - c["duration"]) if track and track.duration and c.get("duration") else None,
                    "raw": c})
    return out


def lyrics_of(c: dict) -> Lyrics:
    """A Choose Lyrics result as lyrics."""
    raw = c.get("raw", c)
    if raw.get("syncedLyrics"):
        lyr = parse_lrc_full(raw["syncedLyrics"])
        lyr.confidence = 1.0
    else:
        lyr = Lyrics(_plain(raw.get("plainLyrics") or ""), False, confidence=1.0)
    lyr.source = "LRCLIB"
    return classify(lyr)


def choose(track: Track, c: dict | None) -> Lyrics | None:
    """Keep the listener's pick for this song (None = back to automatic)."""
    store = prefs()
    d = read_cache(track) or {}
    if c is None:
        store.set_choice(track.key, None)
        d.pop("chosen", None)
        d.pop("v", None)                                  # look again
        write_cache(track, d)
        if track.local_path:
            for f in sidecars(track.local_path):          # the file Aria wrote for the old pick goes too
                if _ours(f):
                    try:
                        import os
                        os.remove(f)
                    except OSError:
                        pass
        return None
    lyr = lyrics_of(c)
    store.set_choice(track.key, {"lrclib": c.get("id")})
    d["chosen"] = dict(lyr.to_dict(), id=c.get("id"))
    write_cache(track, d)
    write_beside(track, lyr, replace=True)
    return lyr


def _chosen(track: Track, choice: dict, d: dict | None) -> Lyrics | None:
    if d and d.get("chosen") and d["chosen"].get("id") == choice.get("lrclib"):
        return Lyrics.from_dict(d["chosen"])
    from aria.providers import http
    try:
        r = http.session().get(f"{LRCLIB}/get/{choice.get('lrclib')}", timeout=8)
        if r.status_code == 200:
            lyr = lyrics_of(r.json())
            d = d or {}
            d["chosen"] = dict(lyr.to_dict(), id=choice.get("lrclib"))
            write_cache(track, d)
            return lyr
    except Exception:
        pass
    return None


def fetch(track: Track, enhanced: bool, recommender=None, force: bool = False) -> Lyrics | None:
    found = _fetch(track, enhanced, recommender, force)
    if found is not None and found.lang:
        identity.note_lyrics_language(track.key, found.lang, 0.9 if not found.romanized else 0.6)
    if found is not None and track.local_path:
        write_beside(track, found)                        # a downloaded song keeps its lyrics with it
    return found


def _fetch(track: Track, enhanced: bool, recommender=None, force: bool = False) -> Lyrics | None:
    d = read_cache(track)
    choice = prefs().choice(track.key)
    if choice:
        picked = _chosen(track, choice, d)
        if picked is not None:
            return picked
    d = None if force else d
    if d is not None and d.get("ident", "") != identity.signature(track):
        d = None                                          # the song was renamed: what was found may be wrong
    if d is not None:
        good_synced = d.get("lines") and (d.get("sync") or ("synced" if d.get("synced") else "")) == SYNCED \
            and d.get("source") in ("LRCLIB", "Subtitles", "Local File") and not d.get("romanized") \
            and lang_ok(track, [s for _t, s in d["lines"]]) \
            and not langid.detect_lines([s for _t, s in d["lines"][:30]]).romanized
        current = d.get("v") == ALGO
        if good_synced or (current and d.get("lines")):
            return Lyrics.from_dict(d)
        if current and not d.get("lines") and time.time() - d.get("time", 0) < MISS_TTL \
                and (d.get("enhanced") or not enhanced):
            return None                                   # a recent miss (retried now if Deep Search was turned on)
    found = find(track, enhanced, recommender)
    data = {"v": ALGO, "time": time.time(), "enhanced": enhanced, "ident": identity.signature(track)}
    data.update(found.to_dict() if found else {"lines": []})
    old = read_cache(track) or {}
    for k in ("aligned", "chosen"):
        if old.get(k):
            data[k] = old[k]                              # an alignment / a pick made earlier stays
    write_cache(track, data)
    return found
