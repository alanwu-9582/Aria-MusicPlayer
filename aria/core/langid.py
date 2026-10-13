"""Language of a title or of lyrics, from evidence — and "unknown" when there isn't enough.

Writing system and language are kept apart:

* kana is a strong Japanese sign, Hangul a Korean one;
* kanji alone is *not* Chinese: a Japanese title or lyric in kanji only (残響散歌, 約束) stays
  ambiguous unless forms only one of the two languages uses decide it (Japanese 駆 気 歩 戦 …,
  Chinese function words 的 了 你 们 這 …), or there is enough text (a whole Japanese lyric
  always has kana);
* Latin letters are not English: Japanese romaji (Yoru ni Kakeru) and Mandarin pinyin (Wo Xiang Ni)
  are told apart from English, and from each other, at the phrase level — a single shared word
  like "ai" or "ni" proves nothing.

``detect`` returns a :class:`Guess` with a confidence and the evidence it used; callers decide
how much to trust it. Nothing here guesses when the evidence is thin: short snippets abstain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

JA, ZH, KO, EN, UNKNOWN = "ja", "zh", "ko", "en", ""
NAMES = {JA: "Japanese", ZH: "Chinese", KO: "Korean", EN: "English", UNKNOWN: "Unknown"}

_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[a-zA-ZāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüĀÁǍÀĒÉĚÈĪÍǏÌŌÓǑÒŪÚǓÙ']+")

# kanji forms written only in Japanese (Chinese writes them differently, in both scripts)
_JA_ONLY = set("々〆駆気歩戦経図楽薬広黒様涙変転読売桜込畑働峠辻匂歳鉄円両単実帰総恵継隠騒戻渋雑拡塩"
               "斎浜沢県観覧覚悪圧囲栄営駅縁応価晩犠拠挙渓軽撃剣険検圏権顕験厳鉱済剤")
# words / forms only Chinese uses (simplified or traditional): function words and simplified shapes
_ZH_ONLY = set("的了嗎吗吧呢啊們们這这你妳她說说沒没麼么對对讓让给还过为个爱听见觉梦开关门长风飞云"
               "乐样语记谁怎哪呀啦咱俩别从总轻离")
_ZH_ONLY -= _JA_ONLY

# ---- romaji (Hepburn) and pinyin ----------------------------------------------------

_ROMAJI_SYL = re.compile(
    r"^(?:(?:kk|ss|tt|pp|cch)?(?:ky|gy|sh|j|ch|ny|hy|by|py|my|ry|ts|[kgszjtdnhbpmyrwf])?[aiueo]|n|-)+$")
_ROMAJI_WORDS = {"no", "wa", "ga", "wo", "ni", "de", "to", "mo", "ka", "yo", "ne", "e", "kara", "made", "dake",
                 "desu", "masu", "nai", "tai", "kimi", "boku", "watashi", "anata", "ore", "sora", "yume",
                 "kokoro", "ai", "koi", "hana", "yoru", "asa", "hikari", "namida", "sekai", "mirai", "kaze",
                 "shite", "suru", "naru", "iru", "aru", "kite", "itte", "dakara", "demo", "zutto", "mada",
                 "itsumo", "koko", "soko", "doko", "sayonara", "arigatou", "kakeru", "na", "nanka", "saa"}
_PINYIN_SYL = re.compile(
    r"^(?:(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?"
    r"(?:iang|iong|uang|ang|eng|ing|ong|ian|iao|uai|uan|üan|ai|ei|ao|ou|an|en|in|un|ün|ia|ie|iu|ua|uo|ui|üe|er|"
    r"a|o|e|i|u|ü|v))+$")
_PINYIN_WORDS = {"wo", "ni", "ta", "women", "nimen", "de", "le", "shi", "bu", "zai", "you", "mei", "hen", "xiang",
                 "ai", "shuo", "zhe", "na", "yi", "ge", "jiu", "dou", "hai", "ye", "gei", "rang", "xin", "qing",
                 "shui", "zhi", "dao", "guo", "hui", "neng", "yao", "kan", "ting", "wang", "xiao", "meng", "tian",
                 "shang", "xia", "yong", "yuan", "zhong", "ren", "sheng", "huo", "hao", "zou", "lai", "qu", "ba", "ma"}
_PINYIN_ONLY = re.compile(r"(?:^|[^a-z])(?:zh|x[iu]|q[iu]|[a-z]*(?:ang|eng|ong|iang|uang|iong)(?![a-z])|"
                          r"[a-z]*(?:ian|uan|iu|ui|ao|ou|ei)(?![a-z]))")
_ROMAJI_ONLY = re.compile(r"(?:tsu|shi|chi|fu|ky[aou]|ry[aou]|ny[aou]|ou(?![a-z])|masu|desu|tte|kk|ss|pp)")
_ENGLISH = {"the", "you", "and", "i", "a", "to", "me", "my", "in", "of", "it", "is", "on", "be", "all", "your",
            "we", "that", "for", "with", "love", "so", "this", "just", "can", "don't", "i'm", "it's", "know",
            "what", "when", "baby", "like", "oh", "never", "now", "are", "was", "but", "no", "yeah", "will",
            "heart", "night", "time", "feel", "way", "go", "say", "let", "want", "tonight", "dancing", "life"}


@dataclass
class Guess:
    lang: str = UNKNOWN                  # ja | zh | ko | en | "" (unknown)
    confidence: float = 0.0              # 0..1
    script: str = ""                     # kana | hangul | han | latin | mixed | ""
    romanized: str = ""                  # "romaji" | "pinyin" when Latin text spells a CJK language
    also: str = ""                       # a second language that is clearly there too (ja + en …)
    evidence: list[str] = field(default_factory=list)

    @property
    def sure(self) -> bool:
        return bool(self.lang) and self.confidence >= 0.6


def _short_romaji(words: list[str]) -> bool:
    """One or two words that can only be Japanese romaji: every word spells Japanese syllables,
    isn't English, and at least one either uses romaji-only spelling (chi, shi, tsu, ky …) or
    can't be read as pinyin at all ("Yakimochi", "Yorushika", "Mai Kuraki"). Words that could be
    either ("Kaiju", "Ai") prove nothing."""
    words = [w.lower() for w in words]
    if not words or any(w in _ENGLISH or w in _LOANWORDS or not _ROMAJI_SYL.match(w) for w in words) \
            or sum(map(len, words)) < 6:
        return False
    return any(len(w) >= 4 and (_ROMAJI_ONLY.search(w) or _JA_ONLY_SYL & set(_ROMAJI_PIECE.findall(w))) for w in words)


# syllables Japanese has and Mandarin doesn't (ki, ra, ko, to …): a word made with them isn't pinyin
_ROMAJI_PIECE = re.compile(r"(?:ky|gy|sh|ch|ny|hy|by|py|my|ry|ts|[kgszjtdnhbpmyrwf])?[aiueo]")
_JA_ONLY_SYL = {"ki", "gi", "hi", "ra", "ro", "so", "to", "no", "do", "ko", "go", "ho", "zo", "jo", "tsu", "shi",
                "chi", "ji", "ryo", "kyo", "ryu", "kyu", "nyo"}
_LOANWORDS = {"tokyo", "kyoto", "osaka", "karaoke", "tsunami", "emoji", "anime", "manga", "sushi", "kimono",
              "ninja", "samurai", "origami", "karate", "tofu", "tamagotchi", "pokemon", "nintendo", "toyota", "honda"}


def _latin(words: list[str]) -> Guess:
    """English, romaji or pinyin — from whole phrases, never from one shared word."""
    words = [w.lower().strip("'") for w in words if w.strip("'")]
    n = len(words)
    if n < 3:
        if _short_romaji(words):
            return Guess(JA, 0.6, "latin", "romaji", evidence=["romaji-only spelling"])
        return Guess(UNKNOWN, 0.0, "latin", evidence=["too few words"])
    text = " ".join(words)
    en = sum(w in _ENGLISH for w in words) / n
    ro_ok = sum(bool(_ROMAJI_SYL.match(w)) for w in words) / n
    py_ok = sum(bool(_PINYIN_SYL.match(w.replace("ü", "v"))) for w in words) / n
    ro_hits = sum(w in _ROMAJI_WORDS for w in words) / n
    py_hits = sum(w in _PINYIN_WORDS for w in words) / n
    ro_only = len(_ROMAJI_ONLY.findall(text))
    py_only = len(_PINYIN_ONLY.findall(text))
    ev = [f"en {en:.2f}", f"romaji {ro_ok:.2f}/{ro_hits:.2f}/{ro_only}", f"pinyin {py_ok:.2f}/{py_hits:.2f}/{py_only}"]
    if en >= 0.3 and en > ro_hits and en > py_hits:
        return Guess(EN, min(0.95, 0.5 + en), "latin", evidence=ev)
    ro = ro_ok * 0.5 + ro_hits + 0.1 * min(ro_only, 4) - (0.15 * min(py_only, 4))
    py = py_ok * 0.5 + py_hits + 0.1 * min(py_only, 4) - (0.15 * min(ro_only, 4))
    if ro_ok >= 0.8 and ro > py and ro >= 0.75:
        return Guess(JA, min(0.9, ro - 0.1), "latin", "romaji", evidence=ev)
    if py_ok >= 0.8 and py > ro and py >= 0.75:
        return Guess(ZH, min(0.9, py - 0.1), "latin", "pinyin", evidence=ev)
    if en >= 0.15:
        return Guess(EN, 0.4 + en, "latin", evidence=ev)
    return Guess(UNKNOWN, 0.2, "latin", evidence=ev)


def detect(text: str) -> Guess:
    """The language of ``text`` (a title, or lyric lines joined), with how sure and why."""
    text = text or ""
    kana = len(_KANA.findall(text))
    hangul = len(_HANGUL.findall(text))
    han_chars = _HAN.findall(text)
    han = len(han_chars)
    words = _LATIN_WORD.findall(text)
    latin = sum(len(w) for w in words)
    cjk = kana + hangul + han
    if cjk == 0:
        return _latin(words)
    script = "mixed" if latin > cjk else ("kana" if kana else "hangul" if hangul else "han")
    also = EN if sum(w.lower() in _ENGLISH for w in words) >= 2 else ""      # 君と dancing tonight: ja + en
    if hangul and hangul >= kana:
        return Guess(KO, min(0.98, 0.6 + hangul / max(1, cjk)), script, also=also, evidence=[f"hangul {hangul}"])
    if kana:
        share = kana / max(1, kana + han)
        conf = 0.95 if (kana >= 2 or share >= 0.2) else 0.7
        return Guess(JA, conf, script, also=also, evidence=[f"kana {kana}/{kana + han}"])
    # kanji only: decide by forms only one language writes, or by enough text
    ja = sum(ch in _JA_ONLY for ch in han_chars)
    zh = sum(ch in _ZH_ONLY for ch in han_chars)
    ev = [f"han {han}", f"ja-only {ja}", f"zh-only {zh}"]
    if ja > zh and ja >= 1:
        return Guess(JA, min(0.9, 0.55 + 0.15 * (ja - zh)), script, also=also, evidence=ev)
    if zh > ja and zh >= 1:
        return Guess(ZH, min(0.95, 0.5 + 0.1 * (zh - ja) + (0.2 if han >= 20 else 0)), script, also=also, evidence=ev)
    if han >= 40:
        # a whole lyric with no kana at all is Chinese (Japanese lyrics always use kana)
        return Guess(ZH, 0.8, script, also=also, evidence=ev + ["long, no kana"])
    return Guess(UNKNOWN, 0.3, script, also=also, evidence=ev + ["kanji alone: ja or zh"])


def detect_lines(lines: list[str], limit: int = 40) -> Guess:
    """Lyric lines (credits and empty lines should be left out by the caller)."""
    return detect(" ".join(x for x in lines[:limit] if x))


def is_romanization_of(line: str, original: str) -> bool:
    """``line`` spells ``original`` (a CJK line) in Latin letters — romaji or pinyin rows under it."""
    if not original or not _HAN.search(original) and not _KANA.search(original) and not _HANGUL.search(original):
        return False
    if _HAN.search(line) or _KANA.search(line) or _HANGUL.search(line):
        return False
    g = _latin(_LATIN_WORD.findall(line))
    return bool(g.romanized) or (g.lang == UNKNOWN and len(_LATIN_WORD.findall(line)) >= 2
                                 and sum(bool(_ROMAJI_SYL.match(w.lower()) or _PINYIN_SYL.match(w.lower()))
                                         for w in _LATIN_WORD.findall(line)) >= 0.8 * len(_LATIN_WORD.findall(line)))


def legacy(g: Guess) -> str:
    """ja | zh | ko | en for older callers: unknown CJK reads as ""."""
    if g.lang:
        return g.lang
    return EN if g.script == "latin" else ""
