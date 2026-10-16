"""Double-Click to Sync: the timing shift worked out from double-clicked lines."""

import pytest

from aria.core import lyrics


def test_on_uses_only_the_latest_click():
    assert lyrics.tap_offset("on", [], 1.2) == 1.2
    assert lyrics.tap_offset("on", [0.4, 3.0], -0.6) == -0.6


def test_smoothed_halves_the_earlier_average_and_the_latest():
    assert lyrics.tap_offset("smooth", [], 1.2) == 1.2                    # the first click counts in full
    assert lyrics.tap_offset("smooth", [1.0], 2.0) == pytest.approx(1.5)
    assert lyrics.tap_offset("smooth", [1.0, 2.0, 3.0], 0.0) == pytest.approx(1.0)   # 0.5 · 2.0 + 0.5 · 0


def test_clicks_are_remembered_per_song(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics.paths, "DATA_DIR", tmp_path)
    prefs = lyrics.Prefs()
    for i in range(lyrics.MAX_TAPS + 5):
        prefs.add_tap("a", i / 10)
    prefs.add_tap("b", -0.333)
    again = lyrics.Prefs()                                                 # read back from the file
    assert len(again.taps("a")) == lyrics.MAX_TAPS and again.taps("a")[-1] == pytest.approx(2.4)
    assert again.taps("b") == [-0.33]
    again.clear_taps("a")
    assert lyrics.Prefs().taps("a") == []


def test_removed_choices_dont_linger(tmp_path, monkeypatch):
    import json
    from aria import paths
    from aria.core import storage
    monkeypatch.setattr(lyrics.paths, "DATA_DIR", tmp_path)
    (tmp_path / "lyrics_prefs.json").write_text(json.dumps({"a": {"offset": 1.5, "no_align": True}, "b": {"no_align": True}}))
    assert lyrics.Prefs().data == {"a": {"offset": 1.5}}
    assert json.loads((tmp_path / "lyrics_prefs.json").read_text()) == {"a": {"offset": 1.5}}   # gone from the file too
    monkeypatch.setattr(paths, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"volume": 30, "lyrics_align": True}))
    s = storage.Settings()
    assert s["volume"] == 30 and "lyrics_align" not in s.values
    s.flush()
    assert "lyrics_align" not in json.loads((tmp_path / "settings.json").read_text())
