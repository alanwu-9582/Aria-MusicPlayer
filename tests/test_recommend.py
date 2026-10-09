from aria.core.models import YOUTUBE, Track
from aria.core.recommend import pick_related


def yt(i, title, artist):
    return Track(source=YOUTUBE, id=i, title=title, artist=artist)


def test_pick_related_drops_same_song_and_keeps_variety():
    played = [yt("a", "周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou")]
    cands = [
        yt("a", "周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video", "周杰倫 Jay Chou"),  # itself
        yt("b", "晴天 - 鄧紫棋 (cover)", "G.E.M."),                                           # cover
        yt("c", "晴天 周杰伦 (歌词版)", "GM Lyric"),                                         # re-upload
        yt("d", "周杰倫 Jay Chou【七里香 Orange Jasmine】-Official Music Video", "周杰倫 Jay Chou"),
        yt("e", "周杰倫 Jay Chou【擱淺 Step Aside】-Official Music Video", "周杰倫 Jay Chou"),
        yt("f", "周杰倫 Jay Chou【安靜 Silence】Official MV", "周杰倫 Jay Chou"),
        yt("g", "五月天 Mayday【I LOVE YOU無望】Official Music Video", "滾石唱片 ROCK RECORDS"),
        yt("h", "七里香 歌詞版", "Lyrics TW"),                                               # dup of d
    ]
    got = [t.id for t in pick_related(cands, played, 3)]
    assert got == ["d", "g", "e"]       # no artist twice in a row when avoidable


def test_spread_keeps_all_items():
    from aria.core.recommend import _spread
    ts = [yt(str(i), f"song {i}", "A" if i < 3 else "B") for i in range(5)]
    out = [t.id for t in _spread(ts)]
    assert sorted(out) == ["0", "1", "2", "3", "4"]
    assert out[:4] == ["0", "3", "1", "4"]
