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
           yt("z", "周杰倫 - 說好的幸福呢", duration=269, added_at=now - 60 * 86400),
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


def test_clear_cache_keeps_everything_else(tmp_path, monkeypatch):
    from aria.core import cache
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "THUMB_DIR", tmp_path / "cache" / "thumbs")
    monkeypatch.setattr(paths, "AUDIO_DIR", tmp_path / "audio")
    paths.ensure_dirs()
    (paths.THUMB_DIR / "a.img").write_bytes(b"x" * 2048)
    (paths.CACHE_DIR / "lyrics").mkdir()
    (paths.CACHE_DIR / "lyrics" / "b.json").write_text("{}")
    song = paths.AUDIO_DIR / "song.m4a"
    song.write_bytes(b"music")
    assert cache.size() >= 2048
    assert cache.clear() >= 2048
    assert cache.size() == 0 and paths.THUMB_DIR.exists() and song.exists()
    assert cache.human(1536) == "1.5 KB"


# ---- collection health ------------------------------------------------------------

def test_health_finds_strays_missing_duplicates_and_extra_copies(tmp_path):
    from aria.core import health
    audio = tmp_path / "audio"
    audio.mkdir()
    kept = audio / "ヨルシカ - 晴る [abcdefghijk].m4a"
    kept.write_bytes(b"a" * 10)
    (audio / "ヨルシカ - 晴る [abcdefghijk].webm").write_bytes(b"b" * 10)     # same video twice
    (audio / "Stray Song [zyxwvutsrqp].m4a").write_bytes(b"c" * 10)            # not in the library
    lib = [yt("abcdefghijk", "ヨルシカ - 晴る (Official Video)", "ヨルシカ / n-buna Official", local_path=str(kept)),
           yt("xxxxxxxxxxx", "ヨルシカ - 晴る (Lyrics)", "ヨルシカ / n-buna Official"),             # same song again
           yt("missingfile", "Gone Song", "Somebody", local_path=str(audio / "gone.m4a"))]
    issues = health.scan(lib, [], audio)
    kinds = sorted(i.kind for i in issues)
    assert kinds == sorted([health.EXTRA_COPY, health.ORPHAN, health.MISSING, health.DUPLICATE])
    orphan = next(i for i in issues if i.kind == health.ORPHAN)
    assert orphan.title == "Stray Song" and orphan.paths == [str(audio / "Stray Song [zyxwvutsrqp].m4a")]
    dup = next(i for i in issues if i.kind == health.DUPLICATE)
    assert dup.keys[0] == "youtube:abcdefghijk"          # the downloaded one is kept
    extra = next(i for i in issues if i.kind == health.EXTRA_COPY)
    assert extra.paths == [str(audio / "ヨルシカ - 晴る [abcdefghijk].webm")]


def test_health_link_check_reports_failures():
    from aria.core import health

    def stream(t):
        if t.id == "dead":
            raise RuntimeError("Video unavailable")

    found = health.check_links([yt("ok"), yt("dead")], stream)
    assert [i.keys for i in found] == [["youtube:dead"]]


# ---- library snapshot -------------------------------------------------------------

def test_snapshot_round_trip_relinks_downloads(tmp_path, monkeypatch):
    import json

    from aria.core import snapshot
    for name, rel in (("DATA_DIR", ""), ("AUDIO_DIR", "audio"), ("LIBRARY_FILE", "library.json"),
                      ("PLAYLISTS_FILE", "playlists.json"), ("SHELF_FILE", "shelf.json"),
                      ("SESSION_FILE", "session.json"), ("LISTENING_FILE", "listening.json"),
                      ("SETTINGS_FILE", "settings.json")):
        monkeypatch.setattr(paths, name, tmp_path / rel if rel else tmp_path)
    paths.AUDIO_DIR.mkdir()
    song = paths.AUDIO_DIR / "A [abcdefghijk].m4a"
    song.write_bytes(b"x")
    track = {"source": "youtube", "id": "abcdefghijk", "title": "A", "local_path": "C:/elsewhere/audio/A [abcdefghijk].m4a"}
    paths.LIBRARY_FILE.write_text(json.dumps({"tracks": [track]}), encoding="utf-8")
    paths.PLAYLISTS_FILE.write_text(json.dumps({"playlists": [{"name": "P", "tracks": []}]}), encoding="utf-8")
    snap = snapshot.create("manual")
    info = snapshot.read(snap)
    assert info.songs == 1 and info.playlists == 1 and snapshot.snapshots()[0].path == snap
    paths.LIBRARY_FILE.write_text(json.dumps({"tracks": []}), encoding="utf-8")      # things change…
    snapshot.restore(snap)                                                            # …and go back
    restored = json.loads(paths.LIBRARY_FILE.read_text(encoding="utf-8"))["tracks"]
    assert restored[0]["id"] == "abcdefghijk" and restored[0]["local_path"] == str(song)
    assert not snapshot.due("off")


def test_balance_is_gentle_near_the_middle():
    from aria.core.balance import gain
    assert gain(0.05) == 1.0                 # dead zone
    assert gain(0.25) > 0.85                 # a small move is a small change
    assert gain(1.0) < 0.07                  # the ends still mean it


# ---- tempo flow -------------------------------------------------------------------

def _bpms(mapping):
    return lambda t: mapping.get(t.id)


def test_tempo_distance_treats_half_time_as_close():
    from aria.core.tempo import tempo_distance
    assert tempo_distance(70, 140) < 10 < tempo_distance(100, 125)


def test_flow_order_prefers_close_beats_but_moves_on():
    from aria.core.tempo import flow_order
    bpm = {"a": (92, 0.9), "b": (128, 0.9), "c": (96, 0.9), "d": (140, 0.9),
           "e": (102, 0.9), "f": (134, 0.9), "g": (112, 0.9), "h": (122, 0.9)}
    tracks = [yt(k) for k in "abcdefgh"]
    got = [t.id for t in flow_order(tracks, _bpms(bpm), start=90, recent=[], strength="strong")]
    assert got[:3] == ["a", "c", "e"]                     # the close ones first
    jumps = [abs(bpm[x][0] - bpm[y][0]) for x, y in zip(got, got[1:])]
    assert max(jumps) <= 20                              # no lurching across the range
    assert sorted(got) == sorted("abcdefgh")             # nothing lost


def test_flow_order_leaves_unknown_and_unsure_songs_in_relevance_order():
    from aria.core.tempo import flow_order
    tracks = [yt(k) for k in "xyz"]
    assert [t.id for t in flow_order(tracks, _bpms({}), start=120, recent=[])] == ["x", "y", "z"]
    unsure = {"x": (60, 0.05), "y": (200, 0.05), "z": (120, 0.05)}
    assert [t.id for t in flow_order(tracks, _bpms(unsure), start=120, recent=[])] == ["x", "y", "z"]


def test_estimate_finds_a_click_track_and_rejects_noise():
    import numpy as np

    from aria.core.tempo import estimate
    sr = 11025
    x = np.zeros(sr * 20)
    period = int(sr * 60 / 120)
    for k in range(0, x.size - 400, period):
        x[k:k + 300] += np.sin(np.arange(300) * 0.9) * np.exp(-np.arange(300) / 60)
    bpm, conf = estimate(x, sr)
    assert abs(bpm - 120) < 2 and conf > 0.5
    rng = np.random.default_rng(1)
    _bpm, noise_conf = estimate(rng.standard_normal(sr * 20), sr)
    assert noise_conf < 0.2


def test_flow_order_turns_back_gently_at_the_edge():
    from aria.core.tempo import flow_order
    bpm = {"p2": (92, 0.9), "p3": (140, 0.9), "p4": (96, 0.9), "p5": (128, 0.9),
           "p6": (102, 0.9), "p7": (134, 0.9), "p8": (112, 0.9)}
    tracks = [yt(k) for k in bpm]               # a playlist's own order
    got = [bpm[t.id][0] for t in flow_order(tracks, _bpms(bpm), start=120, recent=[], strength="strong",
                                            order_weight=0.15)]
    assert got[:3] == [128, 134, 140]
    assert max(abs(a - b) for a, b in zip(got, got[1:])) <= 30     # no 140 → 92 leap


def test_flow_order_without_a_starting_beat_starts_from_the_first_known():
    from aria.core.tempo import flow_order
    bpm = {"a": (100, 0.9), "b": (150, 0.9), "c": (104, 0.9), "d": (146, 0.9)}
    got = [t.id for t in flow_order([yt(k) for k in "abcd"], _bpms(bpm), start=None, recent=[], strength="strong")]
    assert got[:2] == ["a", "c"]                         # the close beats start together


# ---- switch source -----------------------------------------------------------------

def test_switch_source_reads_the_song_out_of_a_video_title():
    from aria.core.switch import _song
    assert _song(yt("a", "米津玄師  Kenshi Yonezu  - Lemon", "Kenshi Yonezu  米津玄師")) == ("Lemon", "米津玄師")
    assert _song(yt("b", "Official髭男dism - Pretender［Official Video］", "Official髭男dism"))[0] == "Pretender"
    assert _song(yt("c", "YOASOBI「アイドル」 Official Music Video", "YOASOBI"))[0] == "アイドル"


def test_switch_source_rejects_other_recordings():
    from aria.core.models import SPOTIFY
    from aria.core.switch import _score
    want = yt("a", "ヨルシカ - 櫂（OFFICIAL VIDEO）", "ヨルシカ", duration=238)
    sp = lambda title, d: Track(source=SPOTIFY, id="s", title=title, artist="Yorushika", duration=d)  # noqa: E731
    assert _score(want, "櫂", "ヨルシカ", sp("櫂", 238.0)) is not None
    assert _score(want, "櫂", "ヨルシカ", sp("櫂 - Live", 240.0)) is None        # another take
    assert _score(want, "櫂", "ヨルシカ", sp("櫂", 300.0)) is None               # another edit
    assert _score(want, "櫂", "ヨルシカ", sp("晴る", 238.0)) is None              # another song


def test_switch_source_keeps_the_place_and_the_download(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    from aria.core.library import Library
    from aria.core.lists import Playlists
    from aria.core.models import SPOTIFY
    monkeypatch.setattr(paths, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(paths, "PLAYLISTS_FILE", tmp_path / "playlists.json")
    lib = Library([yt("1"), yt("2", local_path="x.m4a"), yt("3")])
    lists = Playlists()
    p = lists.create("Mix", [yt("3"), yt("2")])
    new = Track(source=SPOTIFY, id="sp2", title="Two", match_id="2", local_path="x.m4a")
    lib.replace("youtube:2", new)
    lists.replace("youtube:2", new)
    assert [t.key for t in lib.tracks] == ["youtube:1", "spotify:sp2", "youtube:3"]
    assert lib.get("spotify:sp2").local_path == "x.m4a" and "youtube:2" not in lib
    assert [t.key for t in lists.get(p.id).tracks] == ["youtube:3", "spotify:sp2"]
    lib.replace("youtube:3", Track(source=SPOTIFY, id="sp2", title="Two"))   # already saved: merged
    assert [t.key for t in lib.tracks] == ["youtube:1", "spotify:sp2"]


def test_console_clears_itself_after_the_limit():
    QCoreApplication.instance() or QCoreApplication([])
    from aria import log as applog
    bus = applog.LogBus()
    bus.limit = 3
    cleared = []
    bus.cleared.connect(cleared.append)
    for i in range(7):
        bus.record.emit(applog.LogRecord(0, 20, "x", str(i)))
    assert cleared == [3, 3] and [r.message for r in bus.history] == ["6"]


def test_library_order_can_be_changed_by_dragging(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    from aria.core.library import Library
    monkeypatch.setattr(paths, "LIBRARY_FILE", tmp_path / "library.json")
    lib = Library([yt("1"), yt("2"), yt("3"), yt("4")])
    lib.move(0, 2)
    assert [t.id for t in lib.tracks] == ["2", "3", "1", "4"]
    lib.move(3, 0)
    assert [t.id for t in lib.tracks] == ["4", "2", "3", "1"]


def test_spotify_artist_links_are_recognised():
    from aria import providers
    assert providers.spotify.match("https://open.spotify.com/artist/4UK2Lzi6fBfUi9rpDt6cik")
    assert providers.spotify.match("https://open.spotify.com/intl-ja/artist/4UK2Lzi6fBfUi9rpDt6cik?si=x")


# ---- immersive collage ------------------------------------------------------------

def test_collage_is_a_mosaic_of_whole_cell_rectangles_around_the_middle_square():
    import random
    from PySide6.QtCore import QPointF
    from aria.ui import collage
    for w, h in [(1280, 800), (1600, 1000), (1920, 1080), (2560, 1440), (1366, 768), (1024, 768)]:
        tiles, centre = collage.layout(w, h, 40)
        assert abs(centre.width() - centre.height()) < 0.01
        assert abs(centre.center().x() - w / 2) < 3 and abs(centre.center().y() - h / 2) < 3
        assert 30 <= len(tiles) <= 44, (w, h, len(tiles))
        c = (tiles[0].rect.width() + collage.GAP) / tiles[0].cols
        for i, t in enumerate(tiles):
            # one unit for all: whole cells, simple ratios
            assert abs(t.rect.width() - (t.cols * c - collage.GAP)) < 0.01
            assert abs(t.rect.height() - (t.rows * c - collage.GAP)) < 0.01
            assert (t.cols, t.rows) in {(a, b) for a, b, _w in collage.SHAPES}
            assert not t.rect.intersects(centre)
            assert all(not t.rect.intersects(o.rect) for o in tiles[i + 1:])
        rng = random.Random(1)
        for _ in range(300):                                                  # edge to edge: no holes
            pt = QPointF(rng.uniform(0, w), rng.uniform(0, h))
            near = [r.adjusted(-collage.GAP, -collage.GAP, collage.GAP, collage.GAP) for r in [centre] + [t.rect for t in tiles]]
            assert any(r.contains(pt) for r in near)
    few, _c = collage.layout(1600, 1000, 8)                                   # few songs: fewer, larger squares
    assert len(few) <= 14


def test_collage_picks_queue_first_without_the_current_song_or_repeats():
    from aria.ui import collage
    cur = yt("c", "Song C", "A")
    queue = [yt("q1", "Song 1"), yt("c", "Song C", "A"), yt("q2", "Song 2"), yt("q1", "Song 1")]
    recs = [yt("r1", "Song 3"), yt("r2", "Song 2 (Official Video)"), yt("r3", "Song 4")]
    picked = collage.pick(queue, recs, cur, 10)
    assert [(t.id, src) for t, src in picked] == [("q1", "Queue"), ("q2", "Queue"), ("r1", "For You"), ("r3", "For You")]


def test_cover_wall_packs_every_song_without_gaps():
    from aria.ui.widgets.cover_wall import wall_layout
    keys = [f"youtube:{i:03d}" for i in range(120)]
    for cols in (2, 4, 7, 10):
        cells, rows = wall_layout(keys, cols)
        taken = {}
        for n, (x, y, w, h) in enumerate(cells):
            for i in range(w):
                for j in range(h):
                    assert x + i < cols and (x + i, y + j) not in taken
                    taken[(x + i, y + j)] = n
        # every cell is used, except a few at the very end
        assert all((x, y) in taken for y in range(rows - 2) for x in range(cols))
        assert wall_layout(keys, cols) == (cells, rows)          # the same wall each time
