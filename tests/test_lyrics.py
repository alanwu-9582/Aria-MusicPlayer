from aria.core.lyrics import extract_lyrics, parse_lrc


def test_parse_lrc_multiple_tags():
    lines = parse_lrc("[00:12.30]hello\n[00:05.00][00:20.00]chorus\n")
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
    found = extract_lyrics(DESC)
    assert found and not found.synced
    words = [s for _t, s in found.lines if s]
    assert words[0] == "窗外的麻雀 在電線桿上多嘴"
    assert "雨下整夜 我的愛溢出就像雨水" in words
    assert not any("http" in w or "訂閱" in w for w in words)


def test_timed_comment_becomes_synced():
    text = "\n".join(f"{i // 60}:{i % 60:02d} line number {n}" for n, i in enumerate(range(10, 200, 15)))
    found = extract_lyrics(text)
    assert found and found.synced and found.lines[0] == (10, "line number 0")


def test_no_lyrics_in_promo_text():
    assert extract_lyrics("Follow me on instagram https://x\nNew album out now\n#music #pop") is None
