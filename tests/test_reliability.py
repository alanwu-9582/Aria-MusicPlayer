"""Streaming, download and lyric-pipeline reliability: error kinds, retries, cooldowns, shared
resolutions, verified downloads, word timing, tidy titles."""

import threading
import time

import pytest

from aria.core.models import YOUTUBE, Stream, Track
from aria.providers import errors


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}


class _HTTPError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"HTTP {status}")
        self.response = _Resp(status, headers)


def test_failures_are_classified_and_messages_are_safe():
    k = errors.ErrorKind
    assert errors.classify(_HTTPError(429, {"Retry-After": "30"})).retry_after_seconds == 30
    assert errors.classify(_HTTPError(429)).kind == k.RATE_LIMITED
    assert errors.classify(_HTTPError(403)).kind == k.URL_EXPIRED
    assert errors.classify(_HTTPError(401)).kind == k.AUTH_REQUIRED
    assert errors.classify(_HTTPError(503)).retryable
    assert errors.classify(TimeoutError("read timed out")).kind == k.TIMEOUT
    assert errors.classify(Exception("ERROR: [youtube] x: Video unavailable")).kind == k.NOT_FOUND
    assert errors.classify(Exception("The uploader has not made this video available in your country")).kind \
        == k.GEO_RESTRICTED
    assert errors.classify(Exception("Sign in to confirm you’re not a bot")).kind == k.AUTH_REQUIRED
    assert errors.classify(Exception("Unable to extract nsig function")).kind == k.EXTRACTOR_OUTDATED
    assert not errors.classify(Exception("Video unavailable")).retryable
    assert errors.redact("GET https://rr1.googlevideo.com/videoplayback?sig=SECRET&x=1 failed") == \
        "GET https://rr1.googlevideo.com/videoplayback?… failed"


def test_retry_is_bounded_and_only_for_passing_trouble():
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("connection reset by peer")
        return "ok"
    assert errors.retry(flaky, "youtube", sleep=waits.append) == "ok"
    assert len(calls) == 3 and len(waits) == 2 and all(0 <= w <= 4 for w in waits)

    calls.clear()

    def gone():
        calls.append(1)
        raise Exception("Video unavailable")
    with pytest.raises(errors.ProviderError) as e:
        errors.retry(gone, "youtube", sleep=lambda _s: None)
    assert len(calls) == 1 and e.value.failure.kind == errors.ErrorKind.NOT_FOUND

    calls.clear()
    with pytest.raises(errors.ProviderError):
        errors.retry(lambda: calls.append(1) or (_ for _ in ()).throw(ConnectionError("down")), "youtube",
                     attempts=3, sleep=lambda _s: None)
    assert len(calls) == 3                                   # never more than the budget


def test_cooldowns_are_per_provider():
    now = [100.0]
    c = errors.Cooldowns(clock=lambda: now[0])
    c.note(errors.classify(_HTTPError(429, {"Retry-After": "20"}), "youtube"))
    assert 19 < c.cooling("youtube") <= 20 and c.cooling("bilibili") == 0
    now[0] += 21
    assert c.cooling("youtube") == 0


def test_one_resolution_for_the_same_song_at_once(monkeypatch):
    from aria import providers
    calls = []

    class Slow:
        source = YOUTUBE

        def stream(self, track):
            calls.append(track.key)
            time.sleep(0.3)
            return Stream(url="https://example/a", expires_at=time.time() + 3600)
    monkeypatch.setitem(providers.BY_SOURCE, YOUTUBE, Slow())
    monkeypatch.setattr(providers, "_streams", {})
    t = Track(source=YOUTUBE, id="abcdefghijk", title="x")
    out = []
    threads = [threading.Thread(target=lambda: out.append(providers.stream(t))) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert calls == [t.key] and len(out) == 3 and {s.url for s in out} == {"https://example/a"}
    providers.forget_stream(t)
    assert providers.cached_stream(t) is None


def test_background_work_leaves_a_cooling_provider_alone(monkeypatch):
    from aria import providers

    class Never:
        source = YOUTUBE

        def stream(self, track):
            raise AssertionError("must not be asked")
    monkeypatch.setitem(providers.BY_SOURCE, YOUTUBE, Never())
    monkeypatch.setattr(providers, "_streams", {})
    monkeypatch.setattr(errors, "cooldowns", errors.Cooldowns())
    errors.cooldowns.note(errors.ProviderFailure(errors.ErrorKind.RATE_LIMITED, YOUTUBE, False, 60))
    with pytest.raises(errors.ProviderError) as e:
        providers.stream(Track(source=YOUTUBE, id="abcdefghijk", title="x"), background=True)
    assert e.value.failure.kind == errors.ErrorKind.RATE_LIMITED


def _mp4(path, declared_extra=0, size=200_000):
    """A file shaped like an M4A: ftyp box, then an mdat box claiming ``declared_extra`` more bytes."""
    ftyp = (24).to_bytes(4, "big") + b"ftypM4A " + b"\0" * 12
    body = b"\0" * (size - len(ftyp) - 8)
    mdat = (len(body) + 8 + declared_extra).to_bytes(4, "big") + b"mdat" + body
    path.write_bytes(ftyp + mdat)
    return str(path)


def test_downloaded_files_are_checked(tmp_path):
    from aria.core import media_check
    assert media_check.verify(_mp4(tmp_path / "ok.m4a"), 0, probe=False) == (True, "")
    assert not media_check.verify(_mp4(tmp_path / "cut.m4a", declared_extra=500_000), 0, probe=False)[0]
    page = tmp_path / "page.m4a"
    page.write_bytes(b"<!DOCTYPE html><html>" + b" " * 60_000)
    assert "web page" in media_check.verify(str(page), 0, probe=False)[1]
    tiny = tmp_path / "tiny.m4a"
    tiny.write_bytes(b"\0" * 100)
    assert not media_check.verify(str(tiny), 0, probe=False)[0]
    assert not media_check.verify(_mp4(tmp_path / "short.m4a"), 240, probe=False)[0]   # 200 kB for 4 minutes


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.instance() or QCoreApplication([])
    from aria import paths
    monkeypatch.setattr(paths, "AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(paths, "DOWNLOADS_FILE", tmp_path / "downloads.json")
    from aria.core.library import Downloader
    return Downloader()


def test_downloads_are_published_only_once_verified(downloader, tmp_path, monkeypatch):
    from aria import paths, providers
    from aria.core import media_check
    t = Track(source=YOUTUBE, id="abcdefghijk", title="Song", duration=0)

    def fake(track, folder, progress=None):
        progress(0.5, 1_000_000)
        return _mp4(folder / "Song [abcdefghijk].m4a")
    monkeypatch.setattr(providers, "download_to", fake)
    monkeypatch.setattr(media_check, "probe_seconds", lambda _p, timeout=6.0: 0.0)
    seen = []
    final = downloader._fetch(t, lambda f, s=None: seen.append(f), type("V", (), {"emit": lambda *_: None})())
    assert final == str(paths.AUDIO_DIR / "Song [abcdefghijk].m4a") and seen == [0.5]
    assert not list((paths.AUDIO_DIR / ".incoming").iterdir())          # nothing left in staging

    def broken(track, folder, progress=None):
        p = folder / "Bad [bbbbbbbbbbb].m4a"
        p.write_bytes(b"<html>error</html>" + b" " * 60_000)
        return str(p)
    monkeypatch.setattr(providers, "download_to", broken)
    with pytest.raises(errors.ProviderError):
        downloader._fetch(Track(source=YOUTUBE, id="bbbbbbbbbbb", title="Bad"), lambda *a: None,
                          type("V", (), {"emit": lambda *_: None})())
    assert not (paths.AUDIO_DIR / "Bad [bbbbbbbbbbb].m4a").exists()       # never offered as offline audio


def test_one_job_per_song_and_unfinished_downloads_continue(downloader, monkeypatch):
    from aria.core.library import Downloader
    monkeypatch.setattr(Downloader, "_next", lambda self: setattr(self, "_busy", True))
    a, b = Track(source=YOUTUBE, id="aaaaaaaaaaa", title="A"), Track(source=YOUTUBE, id="bbbbbbbbbbb", title="B")
    assert downloader.add([a, b, a]) == 2 and downloader.add([b]) == 0
    downloader._saver.flush()
    again = Downloader()
    monkeypatch.setattr(again, "_next", lambda: None)
    assert again.resume() == 2 and [t.id for t in again._pending] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_titles_never_keep_empty_or_half_brackets():
    from aria.core import identity
    assert identity.tidy("Song ()") == "Song"
    assert identity.tidy("Song )") == "Song"
    assert identity.tidy("( Song") == "Song"
    assert identity.tidy("Song (from \" \")") == "Song"
    assert identity.tidy("(G)I-DLE") == "(G)I-DLE"
    assert identity.tidy("米津玄師  Kenshi Yonezu") == "米津玄師  Kenshi Yonezu"
    live = identity.parse("Artist - Song (Live at Budokan)", "x")
    assert live.title == "Song" and live.version == "Live at Budokan"
    assert identity.version_tag(live.version) == "Live"


def test_line_synced_lyrics_keep_their_lines_and_gain_words():
    from aria.core import lyrics
    synced = lyrics.Lyrics([(10.0, "夜に駆ける"), (14.0, ""), (16.0, "沈むように")], True, "LRCLIB")
    # the alignment heard the lines a little early, with its own spacing
    result = {"lines": [(9.0, "夜に駆ける"), (12.0, ""), (13.0, "沈むように")],
              "word_times": [[[9.0, "夜に"], [10.0, "駆ける"]], [], [[13.0, "沈む"], [14.0, "ように"]]]}
    merged = lyrics.merge_words(synced, result)
    assert merged.lines == synced.lines and merged.words_estimated and merged.word_timed
    assert merged.words[0][0][0] == 10.0 and merged.words[2][0][0] == 16.0      # each line starts on its own time
    assert all(a[0] <= b[0] for a, b in zip(merged.words[0], merged.words[0][1:]))
    assert merged.words[0][-1][0] < 16.0                                           # never into the next line
    assert merged.label == "Line Synced · Estimated Words · LRCLIB"
    back = lyrics.parse_lrc_full(lyrics.to_lrc(merged))
    assert back.words[0] and lyrics.ESTIMATED in lyrics.to_lrc(merged)


class _FakeHTTP:
    """LRCLIB stand-in: answers by query."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def session(self):
        return self

    def get(self, url, params=None, timeout=None):
        self.asked.append(dict(params or {}))
        params = params or {}
        key = params.get("q") or (f"t:{params['track_name']}" if "track_name" in params and "artist_name" not in params
                                  else "")
        found = [] if url.endswith("/get") else self.routes.get(key, [])
        return type("R", (), {"status_code": 200 if found or "/search" in url else 404,
                              "json": lambda self_: found})()


def _lrc(words: bool) -> str:
    lines = ["窓の外", "雨が降る", "君を待つ", "夜が明ける", "もう一度", "歌ってほしい"] * 2

    def at(t):
        return f"{int(t // 60):02d}:{t % 60:05.2f}"
    return "\n".join(f"[{at(10 + 15 * i)}]" + (f"<{at(10 + 15 * i)}>{s[:2]}<{at(11 + 15 * i)}>{s[2:]}" if words else s)
                     for i, s in enumerate(lines))


def test_lrclib_word_timing_wins_and_timelines_are_looked_for_before_plain_text(monkeypatch):
    from aria.core import lyrics
    t = Track(source=YOUTUBE, id="lrc00000001", title="ヨルシカ - 雨", artist="ヨルシカ", duration=200)
    entry = {"trackName": "雨", "artistName": "ヨルシカ", "duration": 200}
    line = dict(entry, id=1, syncedLyrics=_lrc(False))
    word = dict(entry, id=2, syncedLyrics=_lrc(True))
    plain = dict(entry, id=3, plainLyrics="窓の外\n雨が降る\n君を待つ\n夜が明ける\nもう一度\n歌ってほしい")
    synced, _plain = lyrics._from_lrclib(_FakeHTTP({"雨 ヨルシカ": [line, word]}), t)
    assert synced.word_timed and not synced.words_estimated
    # only plain text in the first searches; a timeline only under the song's name alone
    http = _FakeHTTP({"雨 ヨルシカ": [plain], "t:雨": [line]})
    monkeypatch.setattr(lyrics, "MAX_SEARCHES", 1)
    synced, _plain = lyrics._from_lrclib(http, t)
    assert synced is not None and synced.synced                       # found by the extra search
    assert {"track_name": "雨"} in http.asked


def test_collage_centre_spans_the_chosen_cells():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from aria.ui.collage import layout
    for n in (2, 3, 4):
        tiles, centre = layout(1600, 900, 40, n)
        cell = (tiles[0].rect.width() + 4) / tiles[0].cols
        assert round((centre.width() + 4) / cell) == n
