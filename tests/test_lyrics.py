from aria.core.lyrics import check_timeline, extract_lyrics, parse_lrc_full, parse_subtitles, version_tags


def test_parse_lrc_multiple_tags():
    lines = parse_lrc_full("[00:12.30]hello\n[00:05.00][00:20.00]chorus\n").lines
    assert [t for t, _ in lines] == [5.0, 12.3, 20.0]


DESC = """七里香
詞:方文山  曲:周杰倫
iTunes: https://itunes.apple.com/tw/album/x

窗外的麻雀 在電線桿上多嘴
妳說這一句 很有夏天的感覺
手中的鉛筆 在紙上來來回回
我用幾行字形容妳是我的誰
秋刀魚的滋味 貓跟妳都想了解
初戀的香味就這樣被我們尋回
那溫暖的陽光 像剛摘的鮮豔草莓
妳說妳捨不得吃掉這一種感覺

雨下整夜 我的愛溢出就像雨水
訂閱 → https://youtube.com/xxx
"""


def test_extract_lyrics_from_description():
    found = extract_lyrics(DESC, title="周杰倫 七里香", artist="周杰倫")
    assert found and not found.lyrics.synced
    words = [s for _t, s in found.lyrics.lines if s]
    assert words[0] == "窗外的麻雀 在電線桿上多嘴"
    assert "雨下整夜 我的愛溢出就像雨水" in words
    assert not any("http" in w or "訂閱" in w or "詞:" in w for w in words)


def test_timed_comment_becomes_synced():
    text = "\n".join(f"{i // 60}:{i % 60:02d} line number {n}" for n, i in enumerate(range(10, 200, 15)))
    found = extract_lyrics(text, duration=210)
    assert found and found.lyrics.synced and found.lyrics.lines[0] == (10, "line number 0")


def test_chapter_lists_are_not_synced_lyrics():
    chapters = "0:00 Intro\n0:35 Verse 1\n1:10 Pre-Chorus\n1:30 Chorus\n2:05 Verse 2\n2:40 Chorus\n3:10 Bridge\n3:40 Outro"
    assert "chapter list" in check_timeline([(int(t.split(":")[0]) * 60 + int(t.split(":")[1].split()[0]),
                                              t.split(" ", 1)[1]) for t in chapters.splitlines()], 240)
    found = extract_lyrics(chapters, duration=240)
    assert found is None or not found.lyrics.synced
    sparse = [(i * 40.0, f"part {i} words here") for i in range(7)]
    assert "too sparse" in check_timeline(sparse, 300)


def test_no_lyrics_in_promo_text():
    assert extract_lyrics("Follow me on instagram https://x\nNew album out now\n#music #pop") is None


def test_a_repeated_chorus_is_still_lyrics():
    chorus = ["君の声が聞こえる", "夜の向こうに", "君の声が聞こえる", "夜の向こうに"]
    text = "\n".join(["ずっと待っていた", "この街の灯り"] + chorus + ["", "もう一度だけ"] + chorus)
    found = extract_lyrics(text, title="ヨルシカ 声", artist="ヨルシカ")
    assert found is not None and sum(1 for _t, s in found.lyrics.lines if s == "君の声が聞こえる") == 4


def test_translations_lose_to_the_sung_language():
    jp = "\n".join(["ずっと待っていた", "この街の灯り", "君の声が聞こえる", "夜の向こうに", "もう一度だけ", "歌ってほしい",
                    "忘れないで", "朝が来るまで"])
    en = "\n".join(["I waited all along", "The lights of this town", "I can hear your voice", "Beyond the night",
                    "Just one more time", "I want you to sing", "Do not forget", "Until the morning comes"])
    a = extract_lyrics(jp, title="ヨルシカ - 声", artist="ヨルシカ")
    b = extract_lyrics(en, title="ヨルシカ - 声", artist="ヨルシカ")
    assert a is not None and (b is None or a.score > b.score)


def test_subtitles_parse_and_fold_rolling_cues():
    vtt = """WEBVTT

00:00:12.500 --> 00:00:15.000
<c>君の声が</c>聞こえる

00:00:15.000 --> 00:00:17.000
君の声が聞こえる

1
00:00:17,000 --> 00:00:20,000
♪ 夜の向こうに ♪
"""
    assert parse_subtitles(vtt) == [(12.5, "君の声が聞こえる"), (17.0, "夜の向こうに")]


def test_versions_are_different_recordings():
    assert version_tags("Pretender (Live at Budokan)") == {"live"}
    assert version_tags("Pretender") == set()
    assert version_tags("夜に駆ける - Acoustic Ver.") == {"acoustic"}
    assert version_tags("Official Music Video") == set()


def test_artist_and_song_are_told_apart_in_mixed_titles():
    from aria.core import identity
    a = identity.parse("胡夏 Xia Hu - Those Bygone Years 那些年", "HuXia胡夏")
    b = identity.parse("米津玄師  Kenshi Yonezu  - Lemon", "Kenshi Yonezu  米津玄師")
    assert (a.title, a.artist) == ("Those Bygone Years 那些年", "胡夏 Xia Hu")
    assert (b.title, b.artist) == ("Lemon", "米津玄師")


def test_no_one_letter_searches_and_each_script_is_tried():
    from aria.core.lyrics import title_variants
    from aria.core.models import Track
    t = Track(source="youtube", id="a", title="胡夏 Xia Hu - Those Bygone Years 那些年", artist="HuXia胡夏")
    v = title_variants(t)
    assert "那些年" in v and "Those Bygone Years" in v and not any(len(x) < 3 for x in v)


def test_lyrics_in_a_script_the_song_cannot_have_are_refused():
    from aria.core.lyrics import lang_ok
    from aria.core.models import Track
    jp = ["さめきった街に別れを告げ", "荒れくるう刺激に身をさらせ"]
    kr = ["손끝이 또 닿았네", "순간 온몸이 저려"]
    assert lang_ok(Track(source="spotify", id="a", title="火星人", artist="Yorushika"), jp)    # kanji titles can be Japanese
    assert not lang_ok(Track(source="youtube", id="b", title="那些年", artist="胡夏"), kr)
    assert not lang_ok(Track(source="youtube", id="c", title="ヨルシカ - 晴る", artist="ヨルシカ"), ["又回到最初的起点", "记忆中你青涩的脸"])


def test_lrc_files_beside_downloads(tmp_path):
    from aria.core import health, lyrics
    from aria.core.models import Track
    song = tmp_path / "Song [abcdefghijk].m4a"
    song.write_bytes(b"x")
    t = Track(source="youtube", id="abcdefghijk", title="Song", artist="Me", local_path=str(song))
    lyr = lyrics.Lyrics([(1.0, "夜に駆ける"), (5.0, "Next")], True, "LRCLIB",
                        trans=["Racing into the night", ""], words=[[[1.0, "夜に"], [1.6, "駆ける"]], []])
    written = lyrics.write_beside(t, lyr)
    back = lyrics.from_local_file(t)
    assert written and back.source == "LRCLIB" and back.trans[0] == "Racing into the night"
    assert back.words[0] == [[1.0, "夜に"], [1.6, "駆ける"]]
    (tmp_path / "Mine [zzzzzzzzzzz].m4a").write_bytes(b"x")
    own = tmp_path / "Mine [zzzzzzzzzzz].lrc"
    own.write_text("[00:01.00]my own", encoding="utf-8")
    mine = Track(source="youtube", id="zzzzzzzzzzz", title="Mine", local_path=str(tmp_path / "Mine [zzzzzzzzzzz].m4a"))
    assert lyrics.write_beside(mine, lyr, replace=True) is None          # never over the listener's own file
    (tmp_path / "Gone [yyyyyyyyyyy].lrc").write_text("[00:01.00]x", encoding="utf-8")
    lonely = [i for i in health.scan([t, mine], [], tmp_path) if i.kind == health.LYRICS_ONLY]
    assert [i.title for i in lonely] == ["Gone"]
    lyrics.remove_sidecars(str(song))
    assert not (tmp_path / "Song [abcdefghijk].lrc").exists()


