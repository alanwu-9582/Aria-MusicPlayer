import pytest
from PySide6.QtCore import QCoreApplication

from aria import paths
from aria.core.models import YOUTUBE, Track


@pytest.fixture()
def lists(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(paths, "PLAYLISTS_FILE", tmp_path / "playlists.json")
    monkeypatch.setattr(paths, "SHELF_FILE", tmp_path / "shelf.json")
    from aria.core import lists as mod
    return mod


def t(i):
    return Track(source=YOUTUBE, id=i, title=f"song {i}")


def test_playlist_add_dedupes_and_moves(lists):
    pls = lists.Playlists()
    p = pls.create("  週末  ")
    assert p.name == "週末"
    assert pls.add(p.id, [t("a"), t("b"), t("a")]) == 2
    assert pls.add(p.id, [t("b"), t("c")]) == 1
    pls.move(p.id, 2, 0)
    assert [x.id for x in p.tracks] == ["c", "a", "b"]
    pls.remove(p.id, [1])
    assert [x.id for x in p.tracks] == ["c", "b"]
    pls.flush()
    again = lists.Playlists()
    assert [x.id for x in again.get(p.id).tracks] == ["c", "b"]


def test_shelf_keeps_user_order(lists):
    shelf = lists.Shelf()
    a = lists.Album("A", url="u1", tracks=[t("1")])
    b = lists.Album("B", url="u2")
    assert shelf.add(a) and shelf.add(b)
    assert not shelf.add(lists.Album("A again", url="u1"))
    assert [x.title for x in shelf.albums] == ["B", "A"]
    shelf.move(0, 1)
    shelf.flush()
    assert [x.title for x in lists.Shelf().albums] == ["A", "B"]
