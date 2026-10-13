"""Song identity, language evidence and lyric rows (the Lyrics & Metadata Refinement spec)."""

import pytest


@pytest.fixture(autouse=True)
def _known():
    from aria.core import identity
    identity.remember_artists(["YOASOBI", "milet", "RADWIMPS", "Aimer"])


@pytest.mark.parametrize("raw, uploader, title, artist, franchise, role", [
    ("YOASOBI - アイドル (Official Music Video)", "YOASOBI", "アイドル", "YOASOBI", "", ""),
    ("アイドル - YOASOBI", "", "アイドル", "YOASOBI", "", ""),
    ("【推しの子】OP アイドル / YOASOBI", "Anime Music Channel", "アイドル", "YOASOBI", "推しの子", "OP"),
    ("《葬送のフリーレン》ED Anytime Anywhere - milet", "Anime Music Channel", "Anytime Anywhere", "milet", "葬送のフリーレン", "ED"),
    ("Your Name OST - Sparkle - RADWIMPS", "", "Sparkle", "RADWIMPS", "Your Name", "OST"),
    ("Aimer「残響散歌」Music Video", "Aimer Official YouTube Channel", "残響散歌", "Aimer", "", ""),
    ("TVアニメ「呪術廻戦」第2期 OP主題歌「青のすみか」/ キタニタツヤ", "TOHO animation", "青のすみか", "キタニタツヤ", "呪術廻戦", "OP"),
])
def test_identity_fixtures(raw, uploader, title, artist, franchise, role):
    from aria.core import identity
    d = identity.parse(raw, uploader)
    assert (d.title, d.artist, d.franchise, d.role) == (title, artist, franchise, role)


def test_identity_versions_uploaders_and_ambiguity():
    from aria.core import identity
    assert identity.parse("Artist / Title [TV size]").version == "TV size"
    cover = identity.parse("Lemon / 米津玄師 (Cover by Hana)", "Hana Ch.")
    assert (cover.title, cover.artist, cover.version.lower(), cover.performer) == ("Lemon", "米津玄師", "cover", "Hana")
    assert identity.generic_channel("Anime Music Channel") and not identity.generic_channel("YOASOBI")
    assert identity.parse("夜に駆ける", "Anime Music Channel").artist == ""          # a reposter isn't the singer
    vague = identity.parse("作品名 - 角色名 - 曲名")
    assert vague.ambiguous and not vague.sure("artist")


def test_overrides_win_survive_and_reset(tmp_path):
    from aria.core import identity
    from aria.core.models import Track
    identity._overrides = identity.Overrides(tmp_path / "o.json")
    t = Track(source="youtube", id="x1", title="作品名 - 角色名 - 曲名", artist="Some Channel")
    identity.overrides().set(t.key, {"title": "曲名", "artist": "歌手", "language": "ja"})
    d = identity.of(t)
    assert (d.title, d.artist, d.language, d.provenance["title"]) == ("曲名", "歌手", "ja", "user")
    identity.overrides().move(t.key, "spotify:abc")                 # a source switch keeps the fix
    assert identity.Overrides(tmp_path / "o.json").get("spotify:abc")["title"] == "曲名"
    identity.overrides().reset("spotify:abc")
    assert identity.overrides().get("spotify:abc") == {}
    identity._overrides = None


@pytest.mark.parametrize("text, lang, romanized", [
    ("夜に駆ける", "ja", ""), ("残響散歌", "", ""), ("約束", "", ""),
    ("Kimi no Na wa", "ja", "romaji"), ("Yoru ni Kakeru", "ja", "romaji"), ("Wo Xiang Ni", "zh", "pinyin"),
    ("Ai", "", ""), ("Love", "", ""), ("我們的歌 你的心", "zh", ""), ("사랑해", "ko", ""),
    ("I love you baby tonight", "en", ""),
])
def test_language_evidence_and_abstention(text, lang, romanized):
    from aria.core import langid
    g = langid.detect(text)
    assert (g.lang, g.romanized) == (lang, romanized)


def test_mixed_japanese_english():
    from aria.core import langid
    g = langid.detect("君と dancing tonight with you")
    assert g.lang == "ja" and g.also == "en"


def test_lrc_rows_words_offset():
    from aria.core.lyrics import parse_lrc_full
    lyr = parse_lrc_full("[offset:+500]\n[00:10.00]<00:10.00>夜に<00:10.60>駆ける\n[00:10.00]yoru ni kakeru\n"
                         "[00:10.00]Racing into the night\n[00:20.00][00:40.00]Chorus\n[00:31.00]Next")
    assert lyr.lines[0] == (9.5, "夜に駆ける")
    assert lyr.roman[0] == "yoru ni kakeru" and lyr.trans[0] == "Racing into the night"
    assert lyr.words[0] == [[9.5, "夜に"], [10.1, "駆ける"]] and lyr.word_timed
    assert [t for t, _s in lyr.lines] == [9.5, 19.5, 30.5, 39.5]     # a chorus sung twice keeps both times
    from aria.core.lyrics import Lyrics
    back = Lyrics.from_dict(lyr.to_dict())
    assert back.roman == lyr.roman and back.trans == lyr.trans and back.words == lyr.words


def test_plain_alternating_translation_is_paired():
    from aria.core.lyrics import Lyrics, _plain, pair_rows
    text = "\n".join(["夜に駆ける君の手を", "在夜裡奔跑握住你的手", "離さないでいてね", "不要放開我好嗎",
                      "沈むように溶けてゆくように", "像是沉沒又像是融化", "二人だけの空が広がる", "只屬於我們的天空展開"])
    lyr = pair_rows(Lyrics(_plain(text), False), "ja")
    assert len(lyr.lines) == 4 and lyr.trans[0] == "在夜裡奔跑握住你的手"


def test_romanized_lyrics_never_beat_the_original(monkeypatch):
    from aria.core import lyrics
    from aria.core.models import Track
    t = Track(source="youtube", id="r1", title="YOASOBI - 夜に駆ける", artist="Ayase / YOASOBI", duration=260)
    romaji = "\n".join(f"[00:{10 + 4 * i:02d}.00]shizumu you ni tokete yuku you ni futari dake no sora ga hirogaru"
                       for i in range(10))
    original = "\n".join(["沈むように溶けてゆくように", "二人だけの空が広がる夜に"] * 5)
    cands = [{"id": 1, "trackName": "夜に駆ける", "artistName": "YOASOBI", "duration": 260, "syncedLyrics": romaji},
             {"id": 2, "trackName": "夜に駆ける", "artistName": "YOASOBI", "duration": 261, "plainLyrics": original}]
    monkeypatch.setattr(lyrics, "_lrclib_candidates", lambda *a: cands)
    synced, plain = lyrics._from_lrclib(None, t)
    assert synced is None and plain is not None and not plain.romanized and plain.lang == "ja"


def test_ttml_and_local_files(tmp_path):
    from aria.core import lyrics
    from aria.core.models import Track
    ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
            '<p begin="00:01.000" end="00:03.000"><span begin="00:01.000">Hello </span><span begin="00:02.000">world</span>'
            '<span role="x-translation">你好世界</span></p>'
            '<p begin="00:04.000" end="00:05.000">Bye</p></div></body></tt>')
    lyr = lyrics.parse_ttml(ttml)
    assert lyr.lines == [(1.0, "Hello world"), (4.0, "Bye")] and lyr.trans[0] == "你好世界" and lyr.words[0][1][0] == 2.0
    song = tmp_path / "song.m4a"
    song.write_bytes(b"x")
    (tmp_path / "song.lrc").write_text("[00:01.00]one\n[00:02.00]two\n[00:03.00]three", encoding="utf-8")
    found = lyrics.from_local_file(Track(source="youtube", id="l1", title="x", local_path=str(song)))
    assert found.source == "Local File" and found.synced and len(found.lines) == 3


def test_short_romaji_and_kanji_titles_are_not_chinese(tmp_path):
    from aria.core import identity, langid, smart
    from aria.core.models import Track
    for w in ("Yakimochi", "Yorushika", "Mai Kuraki", "Kenshi Yonezu"):
        g = langid.detect(w)
        assert (g.lang, g.romanized) == ("ja", "romaji"), w
    for w in ("Kaiju", "Lemon", "Tokyo", "Ai", "Aimer"):
        assert langid.detect(w).lang == "", w                     # could be anything: abstain
    identity._overrides = identity.Overrides(tmp_path / "o.json")
    identity.lyrics_lookup = lambda key: ("", 0.0)
    identity._lyrics_lang.clear()
    yaki = Track(source="youtube", id="y1", title="Yakimochi", artist="高橋優")
    mars = Track(source="spotify", id="m1", title="火星人", artist="Yorushika")
    for t in (yaki, mars):
        assert identity.of(t).language == "ja"
        zh = smart.SmartCollection("z", [{"field": "language", "value": "zh"}])
        assert smart.evaluate(zh, [t], [], {}) == []                # kanji alone never makes it Chinese
    identity._overrides = None
