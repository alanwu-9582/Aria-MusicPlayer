"""Title normalisation used to tell "the same song" apart from "a related song".

Video titles carry a lot of decoration: artist names, 【brackets】, (Official MV),
"lyrics", "cover by …".  We reduce a title to its *core* (the song name) and
compare cores, so a cover, live cut, remix or lyric upload of a song counts as
the same song no matter who performs it.
"""

from __future__ import annotations

import difflib
import html
import re
import unicodedata
from pathlib import Path

# Brackets that usually wrap the song name in CJK-style titles: 周杰倫【晴天】.
# 《》「」 are more reliable than 【】, which often holds tags like 【4K修復】.
_NAME_BRACKETS = (re.compile(r"[《「『〈](.+?)[》」』〉]"), re.compile(r"【(.+?)】"))
# Decorations wrapped in () [] ｜…
_DECOR_BRACKETS = re.compile(r"[\(\[（［｛{][^\)\]）］｝}]*[\)\]）］｝}]")
_SEPARATORS = re.compile(r"\s+[-–—~|｜/]\s+|\s*[｜|]\s*")

# Latin words are bounded by non-alphanumerics rather than \b, so "4K修复" still
# splits into "4k" + "修复".
_NOISE = re.compile(
    r"(?<![a-z0-9])(official|music|video|audio|lyrics?|lyric video|mv|m/v|hd|hq|4k|1080p|2160p|"
    r"visuali[sz]er|full|version|ver\.?|feat\.?|ft\.?|with|prod\.?|topic|hi-res|flac|"
    r"remaster(?:ed)?|explicit|clean|teaser|trailer|color coded)(?![a-z0-9])"
    r"|官方|完整版|高音質|高音质|無損|无损|動態歌詞|动态歌词|歌詞版|歌词版|歌詞|歌词|字幕版|字幕|中字|"
    r"純享|纯享|音樂|音乐|版本|修復版|修复版|修復|修复|音質|音质|循環|循环|補檔|补档|首播|中英",
    re.IGNORECASE,
)

# Markers of a different *performance* of a song (cover, live, remix…).
VARIANT_MARKERS = re.compile(
    r"(?<![a-z0-9])(cover|covered|live|remix|rmx|acoustic|instrumental|karaoke|inst\.?|"
    r"sped ?up|slowed|reverb|nightcore|8d|piano|guitar|violin|tutorial|dj|"
    r"mashup|medley|bootleg|edit|reaction|ai)(?![a-z0-9])"
    r"|翻唱|翻自|現場|现场|演唱會|演唱会|伴奏|純音樂|纯音乐|鋼琴|钢琴|吉他|"
    r"教學|教学|降調|降调|升調|升调|加速|減速|减速|混音|串燒|串烧|女聲|女声|男聲|男声|"
    r"抖音|彈唱|弹唱|改編|改编|重製|重制|纯享|純享|舞台|节目|節目|翻跳|合唱版",
    re.IGNORECASE,
)

_CJK = r"぀-ヿ㐀-䶿一-鿿가-힯"
_SEGMENTS = re.compile(rf"[{_CJK}]+|[a-z0-9']+(?:\s+[a-z0-9']+)*")


def _load_t2s() -> dict[int, str]:
    """Traditional → Simplified, one character at a time (from OpenCC TSCharacters.txt, Apache-2.0)."""
    raw = (Path(__file__).with_name("t2s.txt")).read_text(encoding="utf-8")
    return {ord(raw[i]): raw[i + 1] for i in range(0, len(raw) - 1, 2)}


_T2S = _load_t2s()


def _fold(text: str) -> str:
    """Case, width and script folding: 約定 / 约定 / ＹＯＵ / you compare equal."""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return text.casefold().translate(_T2S)


def fold(text: str) -> str:
    """Public folding for search: case, width and traditional/simplified script."""
    return _fold(text)


def _parts(title: str, names: list[str]) -> list[str]:
    """Cleaned title pieces that could be the song name (artist pieces removed)."""
    t = _fold(title)
    named = [m.strip() for rx in _NAME_BRACKETS for m in rx.findall(t) if m.strip()]
    for cand in named:
        # The first bracket that isn't pure noise is the song name.
        if _NOISE.sub("", cand).strip(" -_.,"):
            t = cand
            break
    t = _DECOR_BRACKETS.sub(" ", t)
    parts = [p.strip() for p in _SEPARATORS.split(t) if p.strip()]
    if len(parts) > 1:
        parts = [p for p in parts if not _is_artist(p, names)] or parts[-1:]
    out = []
    for p in parts:
        p = _NOISE.sub(" ", p)
        for name in names:
            if len(name) > 1 and p.replace(name, "").strip():
                p = p.replace(name, " ")
        p = re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", p)).strip()
        if p:
            out.append(p)
    return out


def core_title(title: str, artist: str = "") -> str:
    """Best guess at the song name inside a video title."""
    parts = _parts(title, _artist_tokens(artist))
    return parts[0] if parts else ""


def _artist_tokens(artist: str) -> list[str]:
    a = _fold(artist)
    a = re.sub(r"\s*-\s*topic$", "", a)
    a = re.sub(r"(official|channel|vevo|music|records?|唱片|官方頻道|官方频道)", " ", a)
    tokens = [x.strip() for x in re.split(r"[,&/、×x]|\s{2,}", a) if x.strip()]
    # Also split mixed CJK/latin names: "周杰倫 jay chou" → "周杰倫", "jay chou"
    out: list[str] = []
    for tok in tokens:
        out.append(tok)
        out.extend(s for s in _SEGMENTS.findall(tok) if s != tok)
    return out


def _is_artist(part: str, names: list[str]) -> bool:
    if not names:
        return False
    p = part.strip()
    return any(p == n or (len(n) > 1 and n in p and len(p) <= len(n) + 3) for n in names)


def _keys(core: str) -> set[str]:
    keys = set()
    for seg in _SEGMENTS.findall(core):
        compact = seg.replace(" ", "")
        if re.match(rf"[{_CJK}]", seg):
            if len(compact) >= 2:
                keys.add(compact)
        elif len(compact) >= 3:
            keys.add(compact)
    if not keys and core:
        keys.add(core.replace(" ", ""))
    return keys


def song_keys(title: str, artist: str = "") -> set[str]:
    """Distinctive pieces of the core title: CJK runs and latin phrases."""
    return _keys(core_title(title, artist))


def _cores_match(ca: str, cb: str) -> bool:
    if _keys(ca) & _keys(cb):
        return True
    ca, cb = ca.replace(" ", ""), cb.replace(" ", "")
    if not ca or not cb:
        return False
    if min(len(ca), len(cb)) >= 2 and (ca in cb or cb in ca):
        return True
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= 0.85


class Song:
    """A title reduced once, for many comparisons."""

    __slots__ = ("parts",)

    def __init__(self, title: str, artist: str = ""):
        self.parts = _parts(title, _artist_tokens(artist))

    def same(self, other: Song) -> bool:
        pa, pb = self.parts, other.parts
        # A piece shared by both titles while each also has its own name is most
        # likely the (unknown) artist: "周杰倫 - 晴天" vs "周杰倫 - 七里香".
        shared = set(pa) & set(pb)
        if shared and set(pa) - shared and set(pb) - shared:
            pa = [p for p in pa if p not in shared]
            pb = [p for p in pb if p not in shared]
        return any(_cores_match(x, y) for x in pa for y in pb)


def same_song(a_title: str, a_artist: str, b_title: str, b_artist: str) -> bool:
    """True when two titles are the same song, whoever performs it."""
    return Song(a_title, a_artist).same(Song(b_title, b_artist))


_CHANNEL_NOISE = re.compile(r"\s*(-\s*topic|vevo|official( channel)?|channel|youtube|music|官方頻道|官方频道|官方)\s*$",
                            re.IGNORECASE)


def clean_channel(channel: str) -> str:
    prev = None
    name = channel.strip()
    while prev != name:
        prev = name
        name = _CHANNEL_NOISE.sub("", name).strip(" -|/")
    return name or channel.strip()


def artist_of(title: str, channel: str = "") -> str:
    """Display name of the performer behind a video title / channel."""
    chan = clean_channel(channel)
    if chan and _fold(chan).replace(" ", "") in _fold(title).replace(" ", ""):
        return chan
    t = html.unescape(title).strip()
    m = re.match(r"^\s*([^【「『《〈\[\(]{1,40}?)\s*[【「『《〈]", t)
    if m and _NOISE.sub("", _fold(m.group(1))).strip():
        return m.group(1).strip(" -｜|")
    if " - " in t:
        left = t.split(" - ", 1)[0].strip()
        if 0 < len(left) <= 40 and not _DECOR_BRACKETS.fullmatch(left):
            return re.sub(r"^[【\[].*?[】\]]\s*", "", left).strip() or chan
    return chan


_COMPILATION = re.compile(r"(?<![a-z])(mix|playlist|compilation|best of|greatest hits|full album|megamix|"
                          r"nonstop|hours?|top \d+)(?![a-z])|合集|合輯|精選|串燒|串烧|歌單|歌单|小時|小时",
                          re.IGNORECASE)


def is_compilation(title: str, duration: float = 0) -> bool:
    """Mixes, playlists-as-videos and hour-long loops aren't songs."""
    return duration > 15 * 60 or bool(_COMPILATION.search(title))


def display_title(title: str, artist: str = "") -> str:
    """The song name for labels, keeping its original case and script."""
    t = html.unescape(title).strip()
    for rx in _NAME_BRACKETS:
        m = rx.search(t)
        if m and _NOISE.sub("", _fold(m.group(1))).strip(" -_.,"):
            return m.group(1).strip()
    t = _DECOR_BRACKETS.sub(" ", t)
    parts = [p.strip() for p in _SEPARATORS.split(t) if p.strip()]
    names = _artist_tokens(artist)
    keep = [p for p in parts if not _is_artist(_fold(p), names)] or parts
    out = keep[-1] if len(keep) > 1 and _is_artist(_fold(keep[0]), names) else keep[0]
    return re.sub(r"\s+", " ", out).strip() or title


def artist_key(name: str) -> str:
    """Grouping key for an artist name (case, width, script and spacing folded)."""
    return re.sub(r"[\s·・,，]+", "", _fold(name))


def is_variant(title: str) -> bool:
    return bool(VARIANT_MARKERS.search(_fold(title)))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _fold(a), _fold(b)).ratio()


# ---- originality -----------------------------------------------------------------
# Derivative uploads re-package someone else's recording (lyric/subtitle videos,
# sped-up or nightcore edits, fan videos, remixes, karaoke tracks…). A cover or a
# live performance is a new performance, so it doesn't count as derivative.

_DERIVATIVE = re.compile(
    r"(?<![a-z0-9])(lyrics?|lyric video|sub(?:s|bed)?|eng ?sub|vietsub|legendado|letra|romaji|"
    r"nightcore|sped ?up|speed ?up|slowed|reverb|8d|bass ?boosted|daycore|"
    r"amv|gmv|fan ?made|edit audio|remix|rmx|mashup|medley|bootleg|"
    r"karaoke|instrumental|inst\.?|off ?vocal|backing track|"
    r"tutorial|lesson|reaction|ai cover|ai version|tiktok version)(?![a-z0-9])"
    r"|歌詞|歌词|動態|动态|字幕|中字|中日|中英|日中|羅馬|罗马|拼音|歌詞付き|"
    r"加速|減速|减速|降調|降调|升調|升调|倍速|"
    r"二創|二创|手書き|手书|鬼畜|自製|自制|混剪|剪輯|剪辑|抖音|"
    r"混音|串燒|串烧|伴奏|純音樂|纯音乐|消音|教學|教学|反應|反应|ai翻唱|AI翻唱",
    re.IGNORECASE,
)
_PERFORMANCE = re.compile(
    r"(?<![a-z0-9])(cover|covered|live|acoustic|unplugged|session|piano|guitar|violin|"
    r"歌ってみた|弾いてみた)(?![a-z0-9])|翻唱|翻自|現場|现场|演唱會|演唱会|彈唱|弹唱",
    re.IGNORECASE,
)
_OFFICIAL_TITLE = re.compile(r"(?<![a-z])(official|mv|m/v|music video|audio)(?![a-z])|官方|公式", re.IGNORECASE)
_OFFICIAL_CHANNEL = re.compile(r"(-\s*topic|vevo)\s*$|(?<![a-z])official(?![a-z])|(records?|entertainment)\s*$|官方|公式",
                               re.IGNORECASE)


def is_derivative(title: str) -> bool:
    """Lyric/subtitle videos, speed edits, fan videos, remixes, karaoke… (an artist's own
    "Official Lyric Video" is still the original)."""
    t = _fold(title)
    m = _DERIVATIVE.search(t)
    if not m:
        return False
    lyric_only = all(re.fullmatch(r"lyrics?|lyric video|歌詞|歌词", x.group(0), re.IGNORECASE)
                     for x in _DERIVATIVE.finditer(t))
    return not (lyric_only and "official" in t)


def is_performance(title: str) -> bool:
    """A cover / live / acoustic performance (allowed, but ranked after originals)."""
    return bool(_PERFORMANCE.search(_fold(title)))


def originality(title: str, channel: str = "") -> float:
    """-1 … 1: how likely an upload is the artist's original recording."""
    if is_derivative(title):
        return -1.0
    score = 0.0
    if is_performance(title):
        score -= 0.5
    chan = _fold(channel).strip()
    if _OFFICIAL_CHANNEL.search(chan):
        score += 0.5
    if _OFFICIAL_TITLE.search(_fold(title)):
        score += 0.3
    who = artist_of(title, channel)
    if who and chan and artist_key(who) == artist_key(clean_channel(channel)):
        score += 0.2              # uploaded by the performer themselves
    return max(-1.0, min(1.0, score))


# ---- language --------------------------------------------------------------------

_KANA = re.compile(r"[぀-ヿㇰ-ㇿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
LANGUAGES = {"ja": "Japanese", "zh": "Chinese", "ko": "Korean", "en": "English / Other"}


def language_of(title: str, artist: str = "") -> str:
    """Rough language of a song from the scripts in its title and artist: ja | zh | ko | en."""
    text = f"{title} {artist}"
    if _KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _HAN.search(text):
        return "zh"
    return "en"
