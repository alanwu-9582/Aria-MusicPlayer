"""Main window (§6.1): sidebar | pages over player bar and status bar."""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from aria.core.models import SOURCE_LABELS
from aria.core.player import LOADING, PAUSED, PLAYING
from aria.ui.actions import TrackActions
from aria.ui.commands import Commands
from aria.ui.pages.console import ConsolePage
from aria.ui.pages.library import LibraryPage
from aria.ui.pages.now_playing import NowPlayingPage
from aria.ui.pages.search import SearchPage
from aria.ui.player_bar import PlayerBar
from aria.ui.status_bar import StatusBar
from aria.ui.theme import theme
from aria.ui.widgets.sidebar import Sidebar
from aria.ui.widgets.toast import Toast

log = logging.getLogger(__name__)

PAGES = [("music", "播放中"), ("search", "搜尋"), ("library", "收藏"), ("terminal", "主控台")]
STATE_TEXT = {PLAYING: "播放中", PAUSED: "已暫停", LOADING: "載入中…"}
APPEARANCES = ("system", "light", "dark")


class MainWindow(QMainWindow):
    def __init__(self, settings, playback, library, downloader, log_bus):
        super().__init__()
        self.settings = settings
        self.playback = playback
        self.library = library
        self.downloader = downloader
        self.setWindowTitle("Aria")
        self.setMinimumSize(980, 640)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar(PAGES)
        outer.addWidget(self.sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        outer.addLayout(main, 1)

        self.toast_widget = Toast(central)
        self.actions = TrackActions(playback, library, downloader, self.toast)

        self.pages = QStackedWidget()
        self.now_page = NowPlayingPage(playback, library, self.actions, settings)
        self.search_page = SearchPage(library, self.actions, settings)
        self.library_page = LibraryPage(library, downloader, self.actions, self.toast)
        self.console_page = ConsolePage(log_bus, Commands(self))
        for p in (self.now_page, self.search_page, self.library_page, self.console_page):
            self.pages.addWidget(p)
        main.addWidget(self.pages, 1)

        self.player_bar = PlayerBar(playback, library)
        main.addWidget(self.player_bar)
        self.status = StatusBar()
        main.addWidget(self.status)

        self._wire()
        self._shortcuts()
        self._restore()

    # ---- setup ---------------------------------------------------------------

    def _wire(self) -> None:
        pb = self.playback
        self.sidebar.page_selected.connect(self.go)
        self.sidebar.collapse_toggled.connect(lambda c: self.settings.__setitem__("sidebar_collapsed", c))
        self.sidebar.appearance_clicked.connect(self._cycle_appearance)
        pb.notice.connect(lambda kind, text: self.toast(kind, text))
        pb.state_changed.connect(lambda _s: self._update_status())
        pb.current_changed.connect(self._on_current)
        pb.queue_changed.connect(self._update_status)
        self.player_bar.auto_btn.toggled.connect(lambda _on: self._update_status())
        self.downloader.progress.connect(lambda lbl, f: self.status.set_job(f"下載中 {lbl}", f))
        self.downloader.finished.connect(self.library.set_local)
        self.downloader.idle.connect(self._downloads_done)

    def _shortcuts(self) -> None:
        def sc(seq, fn, any_focus=False):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn if any_focus else lambda: self._unless_typing(fn))

        for i in range(len(PAGES)):
            sc(f"Ctrl+{i + 1}", lambda n=i: self.go(n), True)
        sc("Space", self.playback.toggle)
        sc("Ctrl+Right", self.playback.next, True)
        sc("Ctrl+Left", self.playback.previous, True)
        sc("Ctrl+F", lambda: (self.go(1), self.search_page.focus()), True)
        sc("Ctrl+L", lambda: (self.go(1), self.search_page.focus()), True)
        sc("Ctrl+O", lambda: (self.go(2), self.library_page.add_files()), True)
        sc("Ctrl+D", self._save_current, True)
        sc("Ctrl+Up", lambda: self._nudge_volume(5), True)
        sc("Ctrl+Down", lambda: self._nudge_volume(-5), True)
        sc("Ctrl+M", self.player_bar._toggle_mute, True)

    def _unless_typing(self, fn) -> None:
        if not isinstance(QApplication.focusWidget(), QLineEdit):
            fn()

    def _restore(self) -> None:
        geo = self.settings["geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        else:
            self.resize(1200, 760)
        self.sidebar.set_collapsed(self.settings["sidebar_collapsed"])
        self.sidebar.set_appearance(self.settings["appearance"])
        self.go(self.settings["page"])
        self._on_current(self.playback.current)

    # ---- behaviour -----------------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(len(PAGES) - 1, index))
        self.pages.setCurrentIndex(index)
        self.sidebar.select(index)
        self.settings["page"] = index
        self.pages.currentWidget().on_shown()

    def toast(self, kind: str, text: str) -> None:
        self.toast_widget.show_message(text, kind)

    def set_appearance(self, appearance: str) -> None:
        self.settings["appearance"] = appearance
        theme.set_appearance(appearance)
        self.sidebar.set_appearance(appearance)

    def _cycle_appearance(self) -> None:
        cur = self.settings["appearance"]
        self.set_appearance(APPEARANCES[(APPEARANCES.index(cur) + 1) % len(APPEARANCES)])

    def _save_current(self) -> None:
        if self.playback.current:
            self.actions.toggle_saved(self.playback.current)

    def _nudge_volume(self, delta: int) -> None:
        v = max(0, min(100, self.playback.player.volume + delta))
        self.player_bar.volume.set_value(v)
        self.player_bar._set_volume(v)

    def _on_current(self, track) -> None:
        self.library_page.set_playing(track.key if track else None)
        self.search_page.results.set_playing(track.key if track else None)
        self.setWindowTitle(f"{track.title} — Aria" if track else "Aria")
        self._update_status()

    def _update_status(self) -> None:
        pb = self.playback
        t = pb.current
        state = STATE_TEXT.get(pb.state, "")
        self.status.set_parts(
            state if t else "",
            SOURCE_LABELS.get(t.source, "") if t else "",
            f"佇列 {len(pb.queue):,} 首",
            "自動推薦" if pb.autoplay else "",
        )

    def _downloads_done(self, ok: int, failed: int) -> None:
        self.status.set_job(None)
        if failed:
            self.toast("warning", f"{ok} 首完成，{failed} 首失敗")
        else:
            self.toast("success", f"已下載 {ok} 首")

    # ---- Qt events -----------------------------------------------------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast_widget.isVisible():
            self.toast_widget.reposition()

    def closeEvent(self, e):
        self.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        super().closeEvent(e)
