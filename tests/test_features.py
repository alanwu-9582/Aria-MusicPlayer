import random
import time

import pytest
from PySide6.QtCore import QCoreApplication

from aria import paths
from aria.core import smart, textnorm, timefit
from aria.core.listening import Stat, finished_listening
from aria.core.models import YOUTUBE, Track


def yt(i, title="", artist="", duration=0.0, **kw):
    return Track(source=YOUTUBE, id=i, title=title or f"song {i}", artist=artist, duration=duration, **kw)


# ---- originality ------------------------------------------------------------------

def test_derivative_uploads_are_told_apart_from_covers():
    assert textnorm.is_derivative("【動態歌詞】周杰倫 晴天")
    assert textnorm.is_derivative("Idol (Nightcore)")
    assert textnorm.is_derivative("晴天 Remix")
    assert textnorm.is_derivative("アイドル (AI Cover)")
    assert not textnorm.is_derivative("YOASOBI「アイドル」 Official Lyric Video")   # the artist's own
    assert not textnorm.is_derivative("Mad World")                                    # not a "MAD" video
    assert not textnorm.is_derivative("アイドル / YOASOBI (cover)")                    # covers are fine
    assert textnorm.is_performance("アイドル / YOASOBI (cover)")


def test_originals_outrank_covers():
    official = textnorm.originality("ヨルシカ - 晴る (Official Music Video)", "ヨルシカ n-buna Official")
    cover = textnorm.originality("晴る / ヨルシカ (cover)", "someone")
    assert official > 0 > cover


def test_recommendations_drop_derivatives_but_keep_covers_of_other_songs():
    from aria.core.recommend import pick_related
    played = [yt("a", "周杰倫【晴天】Official MV", "周杰倫")]
    cands = [yt("b", "七里香 (動態歌詞)", "Lyrics TW"),
             yt("c", "五月天【倔強】cover by 某人", "某人"),
             yt("d", "五月天 Mayday【溫柔】Official Music Video", "滾石唱片")]
    assert [t.id for t in pick_related(cands, played, 5)] == ["c", "d"]


def test_discovery_dial_moves_familiar_and_new_artists():
    from aria.core.recommend import rank_by_taste
    seed = yt("s", "A - seed", "A")
    radio = [yt(str(i), f"Artist{i} - song", f"Artist{i}") for i in range(10)]
    library = [yt("x", "Artist9 - saved", "Artist9")]
    familiar = [t.id for t in rank_by_taste(radio, [radio], [seed], [], library, discovery=0.0)]
    unexpected = [t.id for t in rank_by_taste(radio, [radio], [seed], [], library, discovery=1.0)]
    assert familiar.index("9") < unexpected.index("9")
    assert unexpected[-1] == "9"


# ---- time-fitted playlist ---------------------------------------------------------

def test_timefit_lands_close_without_going_over():
    rng = random.Random(4)
    songs = [yt(str(i), duration=rng.randint(150, 330)) for i in range(60)]
    for target in (25 * 60, 31 * 60 + 25, 60 * 60):
        got = timefit.fit(songs, target, rng=random.Random(1))
        total = timefit.playing_time(got)
        assert total <= target and target - total <= 5


def test_timefit_counts_crossfade_overlap_and_keeps_queue_order():
    songs = [yt(str(i), duration=200) for i in range(10)]
    got = timefit.fit(songs, 600 + 0, overlap=6, keep_order=True)
    assert timefit.playing_time(got, 6) <= 600
    assert [t.id for t in got] == sorted((t.id for t in got), key=int)


def test_parse_duration():
    assert timefit.parse_duration("31:25") == 1885
    assert timefit.parse_duration("45") == 2700
    assert timefit.parse_duration("1:05:00") == 3900
    assert timefit.parse_duration("abc") is None
    assert timefit.parse_duration("0:30") is None          # under a minute


# ---- smart collections ------------------------------------------------------------

def test_smart_collection_rules():
    now = time.time()
    lib = [yt("j", "ヨルシカ - 晴る", duration=250, added_at=now - 3 * 86400, local_path=__file__),
           yt("z", "周杰倫 - 晴天", duration=269, added_at=now - 60 * 86400),
           yt("e", "Coldplay - Yellow", duration=266, added_at=now - 2 * 86400)]
    stats = {"youtube:e": Stat(plays=3, finished=2, last=now)}
    recent = smart.SmartCollection("r", [{"field": "added_within", "value": 30}])
    assert [t.id for t in smart.evaluate(recent, lib, [], stats)] == ["j", "e"]
    unfinished = smart.SmartCollection("u", [{"field": "not_finished", "value": True}])
    assert [t.id for t in smart.evaluate(unfinished, lib, [], stats)] == ["j", "z"]
    jp = smart.SmartCollection("d", [{"field": "downloaded", "value": True}, {"field": "language", "value": "ja"}])
    assert [t.id for t in smart.evaluate(jp, lib, [], stats)] == ["j"]
    either = smart.SmartCollection("o", [{"field": "language", "value": "zh"}, {"field": "played_at_least", "value": 2}],
                                   match="any")
    assert [t.id for t in smart.evaluate(either, lib, [], stats)] == ["z", "e"]


def test_finished_listening():
    assert finished_listening(heard=200, furthest=245, length=250)
    assert not finished_listening(heard=40, furthest=245, length=250)     # skipped to the end
    assert not finished_listening(heard=200, furthest=180, length=250)    # stopped early


# ---- listening sessions -----------------------------------------------------------

@pytest.fixture()
def listening_env(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(paths, "LISTENING_FILE", tmp_path / "listening.json")
    from PySide6.QtCore import QObject, Signal

    class FakePlayback(QObject):
        current_changed = Signal(object)
        position_changed = Signal(float, float)
        state_changed = Signal(str)
        state = "playing"

    class FakeLibrary(QObject):
        added = Signal(list)

    return FakePlayback(), FakeLibrary()


def test_sessions_record_order_saves_and_split_on_gaps(listening_env):
    from aria.core import listening as mod
    pb, lib = listening_env
    ls = mod.Listening(pb, lib)
    pb.current_changed.emit(yt("1", duration=200))
    pb.current_changed.emit(yt("2", duration=200))
    lib.added.emit(["youtube:2"])
    s = ls.current
    assert [t.id for t in s.tracks] == ["1", "2"] and s.saved == ["youtube:2"]
    assert ls.stats["youtube:1"].plays == 1
    s.listened = 600
    ls.last_active -= mod.SESSION_GAP + 1          # a long quiet gap…
    pb.current_changed.emit(yt("3", duration=200))  # …so this starts a new session
    assert len(ls.sessions) == 1 and ls.sessions[0].card.endswith("— 2 tracks")
    assert [t.id for t in ls.current.tracks] == ["3"]
