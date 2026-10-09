from aria.core.textnorm import core_title, is_variant, same_song


def test_core_title_cjk_brackets():
    assert core_title("周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou") == "晴天 sunny day"


def test_core_title_dash_artist():
    assert core_title("Rick Astley - Never Gonna Give You Up (Official Music Video)", "Rick Astley") == "never gonna give you up"


def test_core_title_angle_brackets():
    assert core_title("范逸臣 Van Fan〈走開〉動態歌詞影片 Lyric Video", "Mandarin Echoes") == "走开"   # folded to simplified


def test_same_song_cover_by_other_artist():
    assert same_song("周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou",
                     "晴天 - 鄧紫棋 cover (Live)", "G.E.M.")
    assert same_song("晴天 周杰伦 (歌词版)", "GM Lyric", "周杰倫 晴天 無損音樂FLAC 歌詞LYRICS 純享", "MusicDelta")
    assert same_song("Never Gonna Give You Up", "Rick Astley",
                     "Rick Astley - Never Gonna Give You Up (Piano Cover)", "Some Pianist")


def test_different_songs_are_related_not_same():
    assert not same_song("周杰倫 Jay Chou【晴天 Sunny Day】", "周杰倫 Jay Chou",
                         "周杰倫 Jay Chou【七里香 Orange Jasmine】-Official Music Video", "周杰倫 Jay Chou")
    assert not same_song("Never Gonna Give You Up", "Rick Astley",
                         "Rick Astley - Together Forever (Official Music Video)", "Rick Astley")
    assert not same_song("周杰倫 Jay Chou【晴天 Sunny Day】", "周杰倫 Jay Chou",
                         "五月天 Mayday【I LOVE YOU無望 I LoveYou Hopeless】Official Music Video", "滾石唱片 ROCK RECORDS")


def test_variants():
    assert is_variant("晴天 (cover by someone)")
    assert is_variant("【4K修复】周杰伦 - 晴天 钢琴教程")
    assert is_variant("Song Name (Live at Wembley)")
    assert not is_variant("周杰倫 Jay Chou【七里香 Orange Jasmine】-Official Music Video")


def test_bilibili_tag_brackets_are_not_song_names():
    bili = "【4K修复】周杰伦 - 晴天MV 2160P修复版"
    assert same_song(bili, "zyl2012_音乐无限", "周杰倫 Jay Chou【晴天 Sunny Day】", "周杰倫 Jay Chou")
    assert not same_song(bili, "zyl2012_音乐无限", "周杰倫 Jay Chou【七里香 Orange Jasmine】", "周杰倫 Jay Chou")
    assert core_title("【Hi-Res无损音质】｜《晴天》- 周杰伦", "VV音乐局") == "晴天"


def test_traditional_and_simplified_are_the_same_song():
    assert same_song("蒲公英的约定 周杰伦 (歌词版)", "GM Lyric",
                     "周杰倫 Jay Chou【蒲公英的約定 A Dandelion's Promise】- Lyric Video", "杰威爾音樂")


def test_artist_of():
    from aria.core.textnorm import artist_of
    assert artist_of("周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou") == "周杰倫 Jay Chou"
    assert artist_of("五月天 Mayday【溫柔 Tenderness】Official Music Video", "滾石唱片 ROCK RECORDS") == "五月天 Mayday"
    assert artist_of("Rick Astley - Together Forever (Official HD Video)", "Rick Astley") == "Rick Astley"
    assert artist_of("Don Omar - Danza Kuduro ft. Lucenzo", "Don Omar VEVO") == "Don Omar"
    assert artist_of("夜に駆ける", "YOASOBI - Topic") == "YOASOBI"


def test_display_title_keeps_case():
    from aria.core.textnorm import display_title, is_compilation
    assert display_title("Drake - Hotline Bling", "Drake") == "Hotline Bling"
    assert display_title("周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou") == "晴天 Sunny Day"
    assert display_title("ヨルシカ - 花に亡霊（OFFICIAL VIDEO）", "ヨルシカ") == "花に亡霊"
    assert is_compilation("Drake 2025 mix best collection") and not is_compilation("Hotline Bling", 267)
