"""Who sings what: the song behind a video title, with how sure each part is — and the
listener's own corrections, which always win.

A video title is read several ways before any is believed. Position alone decides nothing
("Artist - Title", "Title - Artist", "Title / Artist" all occur); the parts are weighed on
evidence: the uploader's name, artists already in the library, the separator's usual order,
a work-and-role prefix ("【推しの子】OP アイドル / YOASOBI" — the title follows the role). A
series, film or game ("franchise") with its role (OP / ED / OST …) is kept apart and is never
the artist or the title; versions (TV size, Live, Cover …) are kept as versions, so a TV-size cut
isn't taken for the full song; decorations (Official Music Video, MV, 4K …) are dropped. A
channel that only reposts (Anime Music Channel, … Lyrics) is not taken for the singer.

Every field carries a confidence; when the evidence is thin the reading stays conservative
(the cleaned raw title, no artist guess) and the listener can correct it — Edit Song Info —
which is kept per song in ``data/track_overrides.json``, survives source switches and restarts,
and can be reset. Raw titles and uploaders are never changed.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

from aria import paths
from aria.core import langid, textnorm

ROLE = re.compile(
    r"(?<![A-Za-z])(OP|ED|OST|opening(?: theme)?|ending(?: theme)?|insert song|theme song|main theme|"
    r"original soundtrack|soundtrack|主題歌|主题歌|主題曲|主题曲|片頭曲|片头曲|片尾曲|插曲|挿入歌|劇中歌|"
    r"オープニング(?:テーマ)?|エンディング(?:テーマ)?|イメージソング)(?:\s*\d{1,2})?(?![A-Za-z])", re.IGNORECASE)
VERSION = re.compile(
    r"(?<![A-Za-z])(TV\s*(?:size|ver(?:sion)?\.?|edit)|full\s*(?:size|ver(?:sion)?\.?)|short\s*ver(?:sion)?\.?|"
    r"live(?:\s+ver(?:sion)?\.?)?|acoustic(?:\s+ver(?:sion)?\.?)?|unplugged|remix|cover|instrumental|"
    r"off\s*vocal|inst\.?|remaster(?:ed)?(?:\s+\d{4})?|piano\s*ver(?:sion)?\.?|re-?record(?:ing|ed)?|"
    r"新録|再録|"
    r"歌ってみた|弾いてみた|翻唱|カバー|ライブ|現場|伴奏|纯音乐|純音樂)(?![A-Za-z])", re.IGNORECASE)
DECOR = re.compile(
    r"(?<![A-Za-z])(official\s*(?:music\s*)?(?:video|audio|mv|lyric\s*video|visualizer)?|music\s*video|"
    r"lyric(?:s)?\s*video|lyrics?|visualizer|audio|video|mv|m/v|pv|4k|hd|hq|1080p|60fps|"
    r"with\s*lyrics|歌詞|动态歌词|動態歌詞|中字|中日字幕|中文字幕|字幕|完整版|高音質|高音质|官方|公式|"
    r"ミュージックビデオ)(?![A-Za-z])", re.IGNORECASE)
_BRACKETS = re.compile(r"【([^】]*)】|《([^》]*)》|〈([^〉]*)〉|「([^」]*)」|『([^』]*)』|“([^”]*)”|"
                       r"\(([^)]*)\)|\[([^\]]*)\]|（([^）]*)）|［([^］]*)］")
_KINDS = "【《〈「『([（［"
_SEP = re.compile(r"\s+[-–—~]\s+|\s*[／/｜|]\s*|\s+-|-\s+|"
                  r"(?<=[)）\]】」』\u3040-\u30ff\u4e00-\u9fff])-(?=\S)|(?<=\S)-(?=[(（\[【「『\u3040-\u30ff\u4e00-\u9fff])")
_BY = re.compile(r"^(?:by|performed by|sung by|演唱|歌)\s*[:：]?\s+(.+)$", re.IGNORECASE)
_FEAT = re.compile(r"\s*(?:\(|（)?\b(?:feat\.?|ft\.?|featuring|with)\s+([^()（）\[\]]+)(?:\)|）)?", re.IGNORECASE)
_COVER_BY = re.compile(r"(?:cover(?:ed)?\s*by|歌ってみた|翻唱)[\s:：/]*([^()（）\[\]【】/|]+)?", re.IGNORECASE)
_GENERIC_CHANNEL = re.compile(
    r"(?<![A-Za-z])(anime|music|songs?|lyrics?|channel|records?|entertainment|label|official|topic|vevo|"
    r"nightcore|jpop|j-pop|kpop|cpop|playlist|radio|tv|hits|archive|fan|中文|字幕|歌詞|翻譯|翻译|频道|頻道|"
    r"チャンネル|音楽|音樂|音乐|唱片|netflix|disney|crunchyroll|muse|ani-?one|aniplex|toho|kadokawa|sony|avex|"
    r"universal|warner|emi|bilibili|iqiyi|愛奇藝|爱奇艺|taiwan|japan|asia|singapore|studios?|pictures)(?![A-Za-z])",
    re.IGNORECASE)

KNOWN_ARTISTS: set[str] = set()           # artist keys from the library: names seen before are evidence


@dataclass
class TrackIdentity:
    title: str = ""
    artist: str = ""
    album: str = ""
    franchise: str = ""                    # the series / film / game the song belongs to
    role: str = ""                         # OP / ED / OST / insert song … (never the artist)
    version: str = ""                      # tv size / live / cover / remix …
    performer: str = ""                    # who sings this upload, when it's a cover
    language: str = ""                     # ja | zh | ko | en | "" (unknown)
    language_confidence: float = 0.0
    language_source: str = ""              # user | lyrics | title | artist
    raw_title: str = ""
    uploader: str = ""
    confidence: dict = field(default_factory=dict)     # per field, 0..1
    provenance: dict = field(default_factory=dict)     # per field: user | provider | parsed | uploader | raw
    ambiguous: bool = False

    def sure(self, name: str, at: float = 0.6) -> bool:
        return self.confidence.get(name, 0.0) >= at


_KIND_WORDS = re.compile(r"(?<![A-Za-z])(TV\s*アニメ|アニメ|劇場版|映画|ドラマ|ゲーム|TV\s*動畫|動畫|动画|電視劇|电视剧|電影|电影|"
                         r"anime|movie|film|drama|game)(?![A-Za-z])", re.IGNORECASE)
_SEASON = re.compile(r"第\s*\d+\s*[期季部]|season\s*\d+|S\d+(?![A-Za-z])", re.IGNORECASE)


def _role(r: str) -> str:
    return r.upper() if len(r) <= 3 else r


def _clean(text: str) -> str:
    text = DECOR.sub(" ", text)
    text = re.sub(r"[ \t\u3000]{2,}", "\x00", text)          # two spaces part names ("米津玄師  Kenshi Yonezu")
    text = re.sub(r"\s+", " ", text).replace("\x00", "  ")
    return text.strip(" -–—~|｜/／:：,.")


def clean_uploader(uploader: str) -> str:
    """The uploader with "- Topic", "Official YouTube Channel" and the like taken off (repeatedly)."""
    name = re.split(r"\s+/\s+|\s*｜\s*", uploader or "")[0]
    while True:
        new = textnorm.clean_channel(name)
        new = re.sub(r"\s*(official|youtube)\s*$", "", new, flags=re.IGNORECASE).strip()
        if new == name or not new:
            return new or name
        name = new


def generic_channel(name: str) -> bool:
    """A reposting / compilation channel rather than a performer ("Anime Music Channel")."""
    words = re.findall(r"[A-Za-z]+|[^\sA-Za-z]+", name or "")
    if not words:
        return True
    generic = len(_GENERIC_CHANNEL.findall(name))
    return generic >= max(1, (len(words) + 1) // 2)


def _artist_evidence(part: str, uploader: str) -> float:
    """How much says ``part`` is the performer: the uploader's name, a library artist."""
    s = 0.0
    if uploader and not generic_channel(uploader):
        s += 0.7 * textnorm.names_channel(part, uploader)
    key = textnorm.artist_key(part)
    if key and key in KNOWN_ARTISTS:
        s += 0.5
    elif any(textnorm.artist_key(x) in KNOWN_ARTISTS for x in re.split(r"\s{2,}|\s*[,&×xX]\s+", part) if len(x) > 1):
        s += 0.35
    return s


def parse(raw: str, uploader: str = "", album: str = "") -> TrackIdentity:
    """Read a video / file title into a :class:`TrackIdentity` (see the module notes)."""
    raw = html.unescape(raw or "").strip()
    ident = TrackIdentity(raw_title=raw, uploader=uploader, album=album)
    up = clean_uploader(uploader)
    conf, prov = ident.confidence, ident.provenance
    work = raw
    quoted: list[str] = []
    title_after_role = False

    # 1. bracketed pieces: version, decoration, franchise (+ role), quoted title
    for m in list(_BRACKETS.finditer(raw)):
        kind = m.group(0)[0]
        inner = next(g for g in m.groups() if g is not None).strip()
        role_after = re.match(rf"\s*(?:(?:{_KIND_WORDS.pattern})\s*)?(?:(?:{_SEASON.pattern})\s*)?" + ROLE.pattern,
                              raw[m.end(): m.end() + 22], re.IGNORECASE)
        replace = " "
        cov = _COVER_BY.search(inner)
        if cov and cov.group(1) and kind in "([（［":
            ident.version, ident.performer = ident.version or "Cover", _clean(cov.group(1))
        elif VERSION.search(inner) and len(_clean(VERSION.sub(" ", inner))) <= 2:
            ident.version = ident.version or VERSION.search(inner).group(1).strip()
            cov = _COVER_BY.search(inner)
            if cov and cov.group(1):
                ident.performer = cov.group(1).strip()
        elif not _clean(inner):
            pass                                                    # (Official Music Video), 【MV】 …
        elif ROLE.search(inner) and kind in "([（［【":
            # (TVアニメ「X」OP主題歌) — the work is inside
            r = ROLE.search(inner)
            ident.role = ident.role or _role(r.group(1))
            q = re.search(r"[「『《](.+?)[」』》]", inner)
            ident.franchise = ident.franchise or (q.group(1) if q else _clean(ROLE.sub(" ", inner)))
        elif role_after and kind in "【《〈「『":
            ident.franchise = ident.franchise or inner
            ident.role = _role(role_after.group(role_after.lastindex or 1))
            # "【Work】OP Title / Artist": the title follows the role (unless it's quoted later)
            title_after_role = len(_KIND_WORDS.sub("", raw[: m.start()]).strip()) <= 2
            work = work.replace(role_after.group(0), " ", 1)
        elif kind == "【" and m.start() <= 1 and raw[m.end():].strip():
            replace = f" {inner} - "                               # "【imase】NIGHT DANCER": a name leads
        elif kind in "「『【《〈“":
            quoted.append(inner)
            replace = " | "
        else:
            continue
        work = work.replace(m.group(0), replace, 1)

    # 2. versions and roles outside brackets
    v = VERSION.search(work)
    if v and not ident.version:
        ident.version = v.group(1).strip()
        cov = _COVER_BY.search(work)
        if cov and cov.group(1):
            ident.performer = _clean(cov.group(1))
        work = work[: v.start()] + " " + work[v.end():]
    work = _COVER_BY.sub(" ", work) if ident.version.lower() in ("cover", "歌ってみた", "翻唱", "カバー") else work
    if ident.franchise:
        work = ROLE.sub(" ", work)                     # leftover "主題歌" after "OP": the role, not a name
    work = _clean(_SEASON.sub(" ", _KIND_WORDS.sub(" ", work)))
    work = re.sub(r"^[\s|]+|[\s|]+$", "", re.sub(r"(\s*\|\s*)+", " | ", work))

    parts = [p.strip() for p in _SEP.split(work) if p and _clean(p)]
    parts = [_clean(p) for p in parts]
    # a part naming a work with its role ("Your Name OST", "TVアニメ X ED") is the franchise
    keep = []
    for i, p in enumerate(parts):
        r = ROLE.search(p)
        if r and len(parts) > 1 and (r.end() >= len(p) - 2 or r.start() <= 1):
            name = _clean(ROLE.sub(" ", p))
            ident.role = ident.role or _role(r.group(1))
            if name:
                ident.franchise = ident.franchise or name
                title_after_role = title_after_role or i == 0
            continue
        if r and not ident.role and i == 0 and len(parts) == 1:
            ident.role = _role(r.group(1))
            p = _clean(ROLE.sub(" ", p))
        keep.append(p)
    parts = [p for p in keep if p]

    # a leading "TVアニメ X OP" without brackets inside a single part
    feat = ""
    if parts:
        fm = _FEAT.search(parts[-1]) or (_FEAT.search(parts[0]) if len(parts) > 1 else None)
        if fm:
            feat = fm.group(1).strip()

    def from_uploader() -> tuple[str, float, str]:
        if up and not generic_channel(up):
            return up, 0.6, "uploader"
        return "", 0.0, ""

    # "… by Artist" names the singer outright
    by = next((i for i, p in enumerate(parts) if _BY.match(p)), None)
    by_artist = _BY.match(parts[by]).group(1).strip() if by is not None else ""
    if by is not None:
        parts = [p for i, p in enumerate(parts) if i != by]
    title = artist = ""
    if quoted:
        title = quoted[-1] if ident.franchise and len(quoted) > 1 and quoted[0] == ident.franchise else quoted[0]
        conf["title"], prov["title"] = 0.85, "parsed"
        rest = [p for p in parts if p and textnorm.fold(p) != textnorm.fold(title)]
        # a leftover naming the uploader when the uploader is a platform (Netflix) isn't the singer
        rest = [p for p in rest if not (generic_channel(up) and textnorm.names_channel(p, up) >= 0.5)]
        if by_artist:
            artist, conf["artist"], prov["artist"] = by_artist, 0.9, "parsed"
            if rest and not ident.franchise:
                ident.franchise = rest[0]
        elif rest:
            artist = rest[0]
            conf["artist"], prov["artist"] = 0.75 + min(0.2, _artist_evidence(artist, up)), "parsed"
        else:
            artist, conf["artist"], prov["artist"] = from_uploader()
    elif by_artist and parts:
        title, artist = parts[-1], by_artist
        conf["title"], conf["artist"] = 0.75, 0.9
        prov["title"] = prov["artist"] = "parsed"
        if len(parts) > 1 and not ident.franchise:
            ident.franchise = parts[0]
    elif len(parts) == 1:
        title = parts[0]
        conf["title"], prov["title"] = 0.7, "parsed"
        artist, conf["artist"], prov["artist"] = from_uploader()
    elif len(parts) >= 2:
        a, b = parts[0], parts[1]
        ea, eb = _artist_evidence(a, up), _artist_evidence(b, up)
        sep = re.search(r"\s+[-–—~]\s+|\s*[／/｜|]\s*|\s+-|-\s+", work)
        slash = bool(sep and re.search(r"[／/｜|]", sep.group(0)))
        prior_left = 0.0 if title_after_role else (-0.1 if slash else 0.1)   # "Artist - Title" / "Title / Artist"
        sa, sb = ea + max(0.0, prior_left), eb + max(0.0, -prior_left) + (0.1 if title_after_role else 0.0)
        if len(parts) >= 3:
            # several dashes: the part the evidence names is the artist; the title stays conservative
            scores = [_artist_evidence(p, up) for p in parts]
            k = max(range(len(parts)), key=lambda i: scores[i])
            if scores[k] >= 0.35:
                artist = parts[k]
                rest = [p for i, p in enumerate(parts) if i != k]
                title = rest[-1] if k == 0 else rest[0]
                conf["artist"], conf["title"] = min(0.9, 0.5 + scores[k]), 0.55
            else:
                title, artist = _clean(work), ""
                conf["title"], conf["artist"] = 0.35, 0.0
                ident.ambiguous = True
            prov["title"] = prov["artist"] = "parsed"
        else:
            left_artist = sa >= sb
            artist, title = (a, b) if left_artist else (b, a)
            margin = abs(sa - sb)
            evidence = max(ea, eb)
            conf["artist"] = min(0.95, 0.35 + evidence + margin / 2)
            conf["title"] = min(0.95, 0.45 + evidence + margin / 2)
            prov["title"] = prov["artist"] = "parsed"
            ident.ambiguous = evidence < 0.3 and not title_after_role
    if feat and artist and textnorm.fold(feat) in textnorm.fold(title):
        title = _FEAT.sub("", title).strip()
    if artist:
        artist = re.split(r"\s{2,}", artist)[0].strip()           # "米津玄師  Kenshi Yonezu" → 米津玄師
        bare = re.sub(r"\s*[(（][^)）]*[)）]\s*", " ", artist).strip()
        if bare and bare != artist:
            artist = bare                                           # "南拳媽媽(Nan Quan Mama)": the alias in brackets goes
    ident.title = _clean(_FEAT.sub("", title)) if title else _clean(raw)
    ident.artist = artist
    if not ident.title:
        ident.title, conf["title"] = raw, 0.2
    for name in ("franchise", "role", "version", "performer"):
        if getattr(ident, name):
            conf.setdefault(name, 0.8)
            prov.setdefault(name, "parsed")
    return ident


# ---- the listener's corrections ------------------------------------------------------

FIELDS = ("title", "artist", "franchise", "version", "language")


class Overrides:
    """Edit Song Info: per song, the fields the listener set. Kept with the user's data."""

    def __init__(self, path=None):
        self.path = path or paths.DATA_DIR / "track_overrides.json"
        try:
            self.data: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> dict:
        return dict(self.data.get(key, {}))

    def set(self, key: str, fields: dict) -> None:
        clean = {k: str(v).strip() for k, v in fields.items() if k in FIELDS and str(v or "").strip()}
        if clean:
            self.data[key] = clean
        else:
            self.data.pop(key, None)
        self._save()

    def reset(self, key: str) -> None:
        if self.data.pop(key, None) is not None:
            self._save()

    def move(self, old: str, new: str) -> None:
        """A song switched source: its corrections go with it."""
        if old in self.data and old != new:
            self.data[new] = self.data.pop(old)
            self._save()

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


_overrides: Overrides | None = None
_lyrics_lang: dict[str, tuple[str, float]] = {}      # song key → language its lyrics were found in
lyrics_lookup = None                                  # key → (lang, confidence) from the lyrics cache (set by lyrics)


def overrides() -> Overrides:
    global _overrides
    if _overrides is None:
        _overrides = Overrides()
    return _overrides


def _lyrics_language(key: str) -> tuple[str, float]:
    """The language the song's lyrics were found in — this session, or from the lyrics cache."""
    if key not in _lyrics_lang and lyrics_lookup is not None:
        try:
            _lyrics_lang[key] = lyrics_lookup(key) or ("", 0.0)
        except Exception:
            _lyrics_lang[key] = ("", 0.0)
    return _lyrics_lang.get(key, ("", 0.0))


def note_lyrics_language(key: str, lang: str, confidence: float) -> None:
    """Lyrics were found: their language is the best evidence of the song's."""
    if lang:
        _lyrics_lang[key] = (lang, confidence)


def of(track) -> TrackIdentity:
    """The song behind ``track``: structured provider data as given, otherwise read from the
    title; then the listener's corrections; then the language from the best evidence there is
    (user > lyrics > title > artist)."""
    from aria.core.models import SPOTIFY
    if track.source == SPOTIFY:
        title = track.title
        ident = TrackIdentity(raw_title=track.title, uploader=track.artist, album=getattr(track, "album", "") or "")
        m = re.search(r"\s+-\s+([^-]*?(?:remaster|live|acoustic|remix|version|edit|mono|stereo)[^-]*)$", title, re.I)
        if m:
            ident.version, title = m.group(1).strip(), title[: m.start()].strip()
        ident.title, ident.artist = title, track.artist.split(",")[0].strip()
        ident.confidence.update(title=0.95, artist=0.95)
        ident.provenance.update(title="provider", artist="provider")
    else:
        ident = parse(track.title, track.artist)
    user = overrides().get(track.key)
    for name in FIELDS:
        if user.get(name):
            setattr(ident, name, user[name])
            ident.confidence[name], ident.provenance[name] = 1.0, "user"
    if user.get("language"):
        ident.language, ident.language_confidence, ident.language_source = user["language"], 1.0, "user"
    elif _lyrics_language(track.key)[0]:
        ident.language, ident.language_confidence = _lyrics_language(track.key)
        ident.language_source = "lyrics"
    else:
        gt = langid.detect(ident.title)
        t_conf = min(gt.confidence, 0.5) if gt.script == "latin" else gt.confidence   # an English title proves little
        ga = langid.detect(ident.artist or track.artist)
        # the artist's own name: kana / Hangul says a lot, a romaji name ("Yorushika") some
        a_conf = 0.75 if ga.script in ("kana", "hangul") and ga.lang else (0.6 if ga.romanized else 0.0)
        if gt.romanized:
            t_conf = max(t_conf, 0.6)                    # a romaji title ("Yakimochi") is Japanese evidence
        if a_conf > t_conf and ga.lang:
            ident.language, ident.language_confidence, ident.language_source = ga.lang, a_conf, "artist"
        elif gt.lang:
            ident.language, ident.language_confidence, ident.language_source = gt.lang, t_conf, "title"
    return ident


_shown: dict[tuple, tuple[str, str]] = {}


def display(track) -> tuple[str, str]:
    """(title, artist) for labels: the listener's corrections, else the confident reading, else
    the raw title — a low-confidence guess never replaces what the source says."""
    k = (track.key, track.title, track.artist, signature(track))
    hit = _shown.get(k)
    if hit is None:
        ident = of(track)
        title = ident.title if ident.sure("title", 0.5) else textnorm.display_title(track.title, track.artist) or track.title
        artist = ident.artist if ident.sure("artist", 0.5) and ident.artist else identity_artist(track)
        if len(_shown) > 500:
            _shown.clear()
        hit = _shown[k] = (title, artist)
    return hit


def identity_artist(track) -> str:
    return clean_uploader(track.artist) or track.artist


def remember_artists(names) -> None:
    for n in names:
        k = textnorm.artist_key(n or "")
        if len(k) >= 2:
            KNOWN_ARTISTS.add(k)


def signature(track) -> str:
    """Changes when the listener corrects the song: lyrics found for the old name are re-checked."""
    user = overrides().get(track.key)
    return json.dumps({k: user[k] for k in sorted(user) if k != "language"}, ensure_ascii=False) if user else ""
