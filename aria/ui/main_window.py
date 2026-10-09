"""Main window (§6.1): sidebar | pages over player bar and status bar; tray and mini player."""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QPoint, Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLineEdit, QMainWindow, QMenu, QStackedWidget,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from aria import paths
from aria.core.models import SOURCE_LABELS
from aria.core.player import LOADING, PAUSED, PLAYING
from aria.ui import icons
from aria.ui.actions import TrackActions
from aria.ui.mini import MiniPlayer, Tray
from aria.ui.pages.library import LibraryPage
from aria.ui.pages.now_playing import NowPlayingPage
from aria.ui.pages.playlist import PlaylistPage
from aria.ui.pages.search import SearchPage
from aria.ui.player_bar import PlayerBar
from aria.ui.status_bar import StatusBar
from aria.ui.theme import theme
from aria.ui.widgets.dialogs import prompt
from aria.ui.widgets.sidebar import Sidebar
from aria.ui.widgets.toast import Toast
from aria.ui.widgets.tracklist import TrackListView

log = logging.getLogger(__name__)

GROUPS = [
    ("Music", [("music", "Now Playing"), ("search", "Search"), ("library", "Library"), ("disc", "Shelf"),
               ("orbit", "Constellation")]),
    ("Tools", [("terminal", "Console"), ("settings", "Settings")]),
]
PAGE_COUNT = sum(len(items) for _t, items in GROUPS)
NOW, SEARCH, LIBRARY, SHELF, SKY, CONSOLE, SETTINGS = range(PAGE_COUNT)
PLAYLIST = PAGE_COUNT                       # shared page for whichever playlist is open
STATE_TEXT = {PLAYING: "Playing", PAUSED: "Paused", LOADING: "Loading…"}
APPEARANCES = ("system", "light", "dark")


class MainWindow(QMainWindow):
    def __init__(self, settings, playback, library, downloader, playlists, shelf, log_bus):
        super().__init__()
        self.settings = settings
        self.playback = playback
        self.library = library
        self.downloader = downloader
        self.playlists = playlists
        self.shelf = shelf
        self.log_bus = log_bus
        self._quitting = False
        self._tray_hint_shown = False
        self.setWindowTitle("Aria")
        self.setMinimumSize(1000, 660)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar(GROUPS)
        outer.addWidget(self.sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        outer.addLayout(main, 1)

        self.toast_widget = Toast(central)
        self.actions = TrackActions(playback, library, downloader, playlists, self.toast)
        TrackListView.warm_hook = playback.warm

        self.player_bar = PlayerBar(playback, library)

        # Pages used right away are built now; the rest on first visit (faster start-up).
        self.pages = QStackedWidget()
        self.now_page = NowPlayingPage(playback, library, self.actions, settings)
        self.search_page = SearchPage(library, self.actions, settings)
        self.library_page = LibraryPage(library, downloader, self.actions, self.toast)
        self.playlist_page = PlaylistPage(playlists, self.actions, self.toast)
        self._built: dict[int, QWidget] = {NOW: self.now_page, SEARCH: self.search_page,
                                           LIBRARY: self.library_page, PLAYLIST: self.playlist_page}
        for i in range(PAGE_COUNT + 1):
            self.pages.addWidget(self._built.get(i) or QWidget())
        main.addWidget(self.pages, 1)
        main.addWidget(self.player_bar)
        self.status = StatusBar()
        main.addWidget(self.status)

        app_icon = QIcon(str(paths.ASSETS_DIR / "icon.ico"))
        self.sidebar.set_logo(icons.app_logo(24, self.devicePixelRatioF()))
        self.mini = MiniPlayer(playback, settings)
        self.mini.expand.connect(self.bring_back)
        self.tray = Tray(self, playback, app_icon) if QSystemTrayIcon.isSystemTrayAvailable() else None
        if self.tray:
            self.tray.show()

        self._wire()
        self._shortcuts()
        self._restore()

    # ---- lazy pages ------------------------------------------------------------

    def page(self, index: int):
        """The page at ``index``, built the first time it's needed."""
        w = self._built.get(index)
        if w is not None:
            return w
        if index == SHELF:
            from aria.ui.pages.shelf import ShelfPage
            w = ShelfPage(self.shelf, self.playlists, self.actions, self.toast, self.settings)
        elif index == SKY:
            from aria.ui.pages.constellation import ConstellationPage
            w = ConstellationPage(self.playback, self.library, self.actions)
        elif index == CONSOLE:
            from aria.ui.commands import Commands
            from aria.ui.pages.console import ConsolePage
            w = ConsolePage(self.log_bus, Commands(self))
        elif index == SETTINGS:
            from aria.ui.pages.settings import SettingsPage
            w = SettingsPage(self.settings, self)
        placeholder = self.pages.widget(index)
        self.pages.insertWidget(index, w)
        self.pages.removeWidget(placeholder)
        placeholder.deleteLater()
        self._built[index] = w
        if index == SHELF:
            key = self.playback.current.key if self.playback.current else None
            w.detail.list.set_playing(key)
        return w

    @property
    def shelf_page(self):
        return self.page(SHELF)

    @property
    def sky_page(self):
        return self.page(SKY)

    @property
    def console_page(self):
        return self.page(CONSOLE)

    @property
    def settings_page(self):
        return self.page(SETTINGS)

    # ---- setup ---------------------------------------------------------------

    def _wire(self) -> None:
        pb = self.playback
        self.sidebar.page_selected.connect(self.go)
        self.sidebar.playlist_selected.connect(self.open_playlist)
        self.sidebar.playlist_context.connect(self._playlist_menu)
        self.sidebar.new_playlist.connect(self.new_playlist)
        self.sidebar.collapse_toggled.connect(lambda c: self.settings.__setitem__("sidebar_collapsed", c))
        self.sidebar.appearance_clicked.connect(self._cycle_appearance)
        self.playlists.changed.connect(self._sync_playlists)
        pb.notice.connect(lambda kind, text: self.toast(kind, text))
        pb.state_changed.connect(lambda _s: self._update_status())
        pb.current_changed.connect(self._on_current)
        pb.queue_changed.connect(self._update_status)
        pb.source_changed.connect(lambda _l: self._update_status())
        self.player_bar.auto_btn.toggled.connect(lambda _on: self._update_status())
        self.player_bar.mini_btn.clicked.connect(self.show_mini)
        self.player_bar.playlist_btn.clicked.connect(
            lambda: self._current_playlist_menu(self.player_bar.playlist_btn))
        self.now_page.playlist_btn.clicked.connect(lambda: self._current_playlist_menu(self.now_page.playlist_btn))
        self.now_page.art.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.now_page.art.customContextMenuRequested.connect(
            lambda pos: self.playback.current and self.actions.menu(self, [self.playback.current]).exec(
                self.now_page.art.mapToGlobal(pos)))
        self.downloader.progress.connect(lambda lbl, f: self.status.set_job(f"Downloading {lbl}", f))
        self.downloader.finished.connect(self.library.set_local)
        self.downloader.finished.connect(self.playlists.set_local)
        self.downloader.finished.connect(pb.local_available)
        self.downloader.idle.connect(self._downloads_done)

    def _shortcuts(self) -> None:
        def sc(seq, fn, any_focus=False):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn if any_focus else lambda: self._unless_typing(fn))

        for i in range(PAGE_COUNT):
            sc(f"Ctrl+{i + 1}", lambda n=i: self.go(n), True)
        sc("Space", self.playback.toggle)
        sc("Ctrl+Right", self.playback.next, True)
        sc("Ctrl+Left", self.playback.previous, True)
        sc("Ctrl+F", lambda: (self.go(SEARCH), self.search_page.focus()), True)
        sc("Ctrl+L", lambda: (self.go(SEARCH), self.search_page.focus()), True)
        sc("Ctrl+O", lambda: (self.go(LIBRARY), self.library_page.add_files()), True)
        sc("Ctrl+N", self.new_playlist, True)
        sc("Ctrl+D", self._save_current, True)
        sc("Ctrl+P", lambda: self._current_playlist_menu(self.player_bar.playlist_btn), True)
        sc("Ctrl+Up", lambda: self._nudge_volume(5), True)
        sc("Ctrl+Down", lambda: self._nudge_volume(-5), True)
        sc("Ctrl+M", self.player_bar._toggle_mute, True)
        sc("Ctrl+Shift+M", self.show_mini, True)
        sc("Ctrl+,", lambda: self.go(SETTINGS), True)
        sc("Ctrl+Q", self.quit_app, True)

    def _unless_typing(self, fn) -> None:
        if not isinstance(QApplication.focusWidget(), QLineEdit):
            fn()

    def _restore(self) -> None:
        geo = self.settings["geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        else:
            self.resize(1240, 780)
        self._sync_playlists()
        self.sidebar.set_collapsed(self.settings["sidebar_collapsed"])
        self.sidebar.set_appearance(self.settings["appearance"])
        self.go(min(self.settings["page"], PAGE_COUNT - 1))
        self._on_current(self.playback.current)

    # ---- navigation ----------------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(PAGE_COUNT - 1, index))
        page = self.page(index)
        self.pages.setCurrentWidget(page)
        self.sidebar.select(index)
        self.settings["page"] = index
        page.on_shown()

    def open_playlist(self, pid: str) -> None:
        self.playlist_page.show_playlist(pid)
        self.pages.setCurrentWidget(self.playlist_page)
        self.sidebar.select_playlist(pid)

    def new_playlist(self) -> None:
        name = prompt(self, "New Playlist", "Playlist name", "Create")
        if name:
            p = self.playlists.create(name)
            self.open_playlist(p.id)

    def _sync_playlists(self) -> None:
        self.sidebar.set_playlists([(p.id, p.name) for p in self.playlists.items])
        on_playlist = self.pages.currentWidget() is self.playlist_page
        if on_playlist and not self.playlists.get(self.playlist_page.pid or ""):
            self.go(NOW)
        elif on_playlist and self.playlist_page.pid:
            self.sidebar.select_playlist(self.playlist_page.pid)

    def _playlist_menu(self, pid: str, pos) -> None:
        p = self.playlists.get(pid)
        if not p:
            return
        m = QMenu(self)
        c = theme.color("label")
        m.addAction(icons.icon("play", c, 16), "Play", lambda: self.actions.play(p.tracks))
        m.addAction(icons.icon("disc", c, 16), "Put on Shelf", lambda: self.shelf_page.put_playlist(pid))
        m.addAction(icons.icon("pencil", c, 16), "Rename…", lambda: (self.open_playlist(pid), self.playlist_page.rename()))
        m.addSeparator()
        m.addAction(icons.icon("trash", c, 16), "Delete Playlist…", lambda: self.delete_playlist(pid))
        m.exec(pos)

    def delete_playlist(self, pid: str) -> None:
        p = self.playlists.get(pid)
        if p:
            self.actions.remove_with_files(self, list(p.tracks), f"Delete “{p.name}”?",
                                           "Songs stay in your Library and queue.",
                                           lambda: self.playlists.delete(pid))

    # ---- behaviour -----------------------------------------------------------

    def toast(self, kind: str, text: str) -> None:
        self.toast_widget.show_message(text, kind)

    def set_appearance(self, appearance: str) -> None:
        self.settings["appearance"] = appearance
        theme.set_appearance(appearance)
        self.sidebar.set_appearance(appearance)
        if SETTINGS in self._built:
            self.settings_page.sync_appearance(appearance)

    def _cycle_appearance(self) -> None:
        cur = self.settings["appearance"]
        self.set_appearance(APPEARANCES[(APPEARANCES.index(cur) + 1) % len(APPEARANCES)])

    def settings_changed(self, key: str) -> None:
        if key in ("crossfade", "crossfade_secs"):
            self.playback.apply_settings()
        elif key == "lyrics_enhanced" and self.settings["lyrics_enhanced"]:
            if self.now_page.lyrics.state == "none":
                self.now_page._load_lyrics(force=True)
        elif key == "smart_artwork":
            self.now_page.art.set_track(self.playback.current)   # re-reads the cover colour
            self.mini._update_hue()
            if not self.settings["smart_artwork"]:
                self.now_page.set_hue(None)
                if SHELF in self._built:
                    self.shelf_page.set_hue(None)

    def _current_playlist_menu(self, anchor) -> None:
        t = self.playback.current
        if not t:
            return
        m = self.actions.playlist_menu(self, [t])
        top = anchor.mapToGlobal(QPoint(0, 0))
        middle = self.mapToGlobal(QPoint(0, self.height() // 2)).y()
        # Open away from the window edge the button sits on (upwards from the player bar).
        m.exec(top - QPoint(0, m.sizeHint().height() + 4) if top.y() > middle
               else top + QPoint(0, anchor.height() + 4))

    def _save_current(self) -> None:
        if self.playback.current:
            self.actions.toggle_saved(self.playback.current)

    def _nudge_volume(self, delta: int) -> None:
        v = max(0, min(100, self.playback.player.volume + delta))
        self.player_bar.volume.set_value(v)
        self.player_bar._set_volume(v)

    def _on_current(self, track) -> None:
        key = track.key if track else None
        self.library_page.set_playing(key)
        self.search_page.results.set_playing(key)
        self.playlist_page.set_playing(key)
        if SHELF in self._built:
            self.shelf_page.detail.list.set_playing(key)
        self.setWindowTitle(f"{track.title} — Aria" if track else "Aria")
        self._update_status()

    def _update_status(self) -> None:
        pb = self.playback
        t = pb.current
        state = STATE_TEXT.get(pb.state, "")
        source = ("Local file" if pb.playing_local else SOURCE_LABELS.get(t.source, "")) if t else ""
        n = len(pb.queue)
        self.status.set_parts(state if t else "", source, f"{n:,} in queue",
                              "Autoplay" if pb.autoplay else "",
                              "Crossfade" if self.settings["crossfade"] else "")

    def _downloads_done(self, ok: int, failed: int) -> None:
        self.status.set_job(None)
        if failed:
            self.toast("warning", f"{ok} downloaded, {failed} failed")
        else:
            self.toast("success", f"Downloaded {ok} song{'s' if ok != 1 else ''}")

    # ---- window / tray -------------------------------------------------------

    def show_mini(self) -> None:
        self.mini.show_near(self)

    def bring_back(self) -> None:
        self.mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.mini.close()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast_widget.isVisible():
            self.toast_widget.reposition()

    def closeEvent(self, e):
        self.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        if not self._quitting and self.tray and self.settings["close_to_tray"]:
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage("Aria is still playing", "Click the tray icon to bring it back; right-click › Quit to exit.",
                                      QSystemTrayIcon.MessageIcon.Information, 2600)
            return
        self.quit_app()
