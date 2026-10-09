import json

import pytest
from PySide6.QtCore import QCoreApplication

from aria import paths
from aria.core.models import BILIBILI, YOUTUBE


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(paths, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(paths, "AUDIO_DIR", tmp_path / "audio")
    from aria.core.library import Library
    return Library()


def test_import_v1_playlist_and_export_roundtrip(lib, tmp_path):
    v1 = {"saved": [
        {"title": "Song A", "author": "A", "watch_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
         "url": "x", "thumbnail_url": "t", "saved_dist": None},
        {"title": "Song B", "author": "B", "watch_url": "https://www.bilibili.com/video/BV1d4411N7zD",
         "url": "x", "thumbnail_url": "t", "saved_dist": "assets/audio/missing.webm"},
    ]}
    src = tmp_path / "old.json"
    src.write_text(json.dumps(v1), encoding="utf-8")
    assert lib.import_file(str(src)) == 2
    assert [t.source for t in lib.tracks] == [YOUTUBE, BILIBILI]
    assert lib.tracks[0].id == "dQw4w9WgXcQ"
    assert lib.tracks[1].local_path == ""          # missing file is dropped
    assert lib.import_file(str(src)) == 0           # no duplicates

    out = tmp_path / "out.json"
    assert lib.export_file(str(out)) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 2 and len(data["tracks"]) == 2


def test_toggle_and_remove(lib):
    from aria.core.models import Track
    t = Track(source=YOUTUBE, id="abc", title="x")
    assert lib.toggle(t) is True and t.key in lib
    assert lib.toggle(t) is False and t.key not in lib
