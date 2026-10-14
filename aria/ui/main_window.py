"""Main window (§6.1): sidebar | pages over player bar and status bar; tray, mini player
and the Quick Actions palette (Ctrl+K)."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QHBoxLayout,
                               QLineEdit, QMainWindow, QPlainTextEdit, QStackedWidget, QSystemTrayIcon,
                               QTextEdit, QVBoxLayout, QWidget)

from aria import paths
from aria.core import smart
from aria.core.models import LOCAL, SOURCE_LABELS, Track, format_duration
from aria.core.player import LOADING, PAUSED, PLAYING
from aria.ui import icons
from aria.ui.actions import TrackActions
from aria.ui.mini import MiniPlayer, Tray
from aria.ui.pages.library import LibraryPage
from aria.ui.pages.now_playing import NowPlayingPage
from aria.ui.pages.search import SearchPage
from aria.ui.player_bar import PlayerBar
from aria.ui.quick import Command, QuickActions
from aria.ui.status_bar import StatusBar
from aria.ui.theme import theme
from aria.ui.widgets.sidebar import Sidebar
from aria.ui.widgets.toast import Toast
from aria.ui.widgets.tracklist import TrackListView

log = logging.getLogger(__name__)

GROUPS = [
    ("Music", [("music", "Now Playing", "primary"), ("search", "Search"), ("library", "Library"), ("disc", "Shelf")]),
    ("Tools", [("terminal", "Console"), ("settings", "Settings")]),
]
PAGE_COUNT = sum(len(items) for _t, items in GROUPS)
NOW, SEARCH, LIBRARY, SHELF, CONSOLE, SETTINGS = range(PAGE_COUNT)
STATE_TEXT = {PLAYING: "Playing", PAUSED: "Paused", LOADING: "Loading…"}
APPEARANCES = ("system", "light", "dark")


class MainWindow(QMainWindow):
    def __init__(self, settings, playback, library, downloader, playlists, shelf, log_bus,
                 listening=None, balance=None, devices=None):
        super().__init__()
        self.listening = listening
        self.balance = balance
        self.devices = devices
        theme.reduce_motion = bool(settings["reduce_motion"])
        self.settings = settings
        self.playback = playback
        self.library = library
        self.downloader = downloader
        self.playlists = playlists
        self.shelf = shelf
        self.log_bus = log_bus
        self._quitting = False
        self._tray_hint_shown = False
        self._focus_before_mini = None
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
        from aria.core.aligner import ModelManager
        self.models = ModelManager()                 # speech model downloads (Forced Alignment)
        from aria.core.switch import SourceSwitcher
        self.switcher = SourceSwitcher(library, playlists, playback)
        self.actions.switcher = self.switcher
        self.actions.edit_info = self.edit_song_info
        from aria.ui.widgets.lyrics_view import LyricsView
        LyricsView.set_extras(settings["lyrics_translation"])
        self._learn_artists()
        library.changed.connect(self._learn_artists)
        from aria.core.activity import Activity
        self.activity = Activity(playback, library, downloader, playlists, devices, balance)
        log_bus.limit = settings["console_clear_after"]
        TrackListView.warm_hook = playback.warm

        self.player_bar = PlayerBar(playback, library, balance)

        # Pages used right away are built now; the rest on first visit (faster start-up).
        self.pages = QStackedWidget()
        self.now_page = NowPlayingPage(playback, library, self.actions, settings, listening)
        self.search_page = SearchPage(library, self.actions, settings)
        self.library_page = LibraryPage(library, downloader, self.actions, self.toast, settings)
        self._built: dict[int, QWidget] = {NOW: self.now_page, SEARCH: self.search_page, LIBRARY: self.library_page}
        for i in range(PAGE_COUNT):
            self.pages.addWidget(self._built.get(i) or QWidget())
        main.addWidget(self.pages, 1)
        main.addWidget(self.player_bar)
        self.status = StatusBar()
        main.addWidget(self.status)

        app_icon = QIcon(str(paths.ASSETS_DIR / "icon.ico"))
        self.sidebar.set_logo(icons.app_logo(24, self.devicePixelRatioF()))
        self.mini = MiniPlayer(playback, settings)
        self.mini.expand.connect(self.bring_back)
        self.mini.style_changed.connect(self._mini_style_changed)
        self.tray = Tray(self, playback, app_icon) if QSystemTrayIcon.isSystemTrayAvailable() else None
        if self.tray:
            self.tray.show()

        self.quick = QuickActions(self)
        self.immersive = None                       # built the first time it's opened
        # The status bar counts a listening timer down while one runs.
        self._status_tick = QTimer(self)
        self._status_tick.setInterval(1000)
        self._status_tick.timeout.connect(self._update_status)

        # Automatic Library Snapshots: shortly after start, then hourly checks.
        self._snap_timer = QTimer(self)
        self._snap_timer.setInterval(3_600_000)
        self._snap_timer.timeout.connect(self._auto_snapshot)
        self._snap_timer.start()
        QTimer.singleShot(20_000, self._auto_snapshot)

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
            w = ShelfPage(self.shelf, self.playlists, self.actions, self.toast, self.settings, self.smart_tracks,
                          self.listening)
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
    def settings_page(self):
        return self.page(SETTINGS)

    # ---- setup ---------------------------------------------------------------

    def _wire(self) -> None:
        pb = self.playback
        self.sidebar.page_selected.connect(self.go)
        self.sidebar.collapse_toggled.connect(lambda c: self.settings.__setitem__("sidebar_collapsed", c))
        self.sidebar.appearance_clicked.connect(self._cycle_appearance)
        self.sidebar.quit_clicked.connect(self.quit_app)
        self.sidebar.tracks_to_queue.connect(self.actions.enqueue)
        # every playlist and Smart Collection stands on the shelf (also before the Shelf page is built)
        self.playlists.changed.connect(lambda: self.shelf.sync_playlists(self.playlists))
        self.shelf.sync_playlists(self.playlists)
        pb.notice.connect(lambda kind, text: self.toast(kind, text))
        pb.state_changed.connect(lambda _s: self._update_status())
        pb.current_changed.connect(self._on_current)
        pb.queue_changed.connect(self._update_status)
        pb.source_changed.connect(lambda _l: self._update_status())
        pb.mode_changed.connect(self._on_mode)
        pb.arrange_progress.connect(self._on_measuring)
        self.player_bar.auto_btn.toggled.connect(lambda _on: self._update_status())
        self.player_bar.mini_btn.clicked.connect(self.show_mini)
        self.player_bar.playlist_btn.clicked.connect(
            lambda: self._current_playlist_menu(self.player_bar.playlist_btn))
        self.now_page.playlist_btn.clicked.connect(lambda: self._current_playlist_menu(self.now_page.playlist_btn))
        self.now_page.immersive_btn.clicked.connect(self.toggle_immersive)
        self.now_page.art.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.now_page.art.customContextMenuRequested.connect(
            lambda pos: self.playback.current and self.actions.menu(self, [self.playback.current]).exec(
                self.now_page.art.mapToGlobal(pos)))
        self.downloader.progress.connect(lambda text, f: self.status.set_job(text, f))
        self.downloader.stopped.connect(lambda why: (self.status.set_job(None), self.toast("warning", why)))
        QTimer.singleShot(8000, self.downloader.resume)        # downloads left unfinished last time
        # a finished download with line-synced lyrics: its words get timed too (Word-by-Word Everything)
        self.downloader.after = lambda t, lyr: self.now_page.words.request(t, lyr, t.local_path)
        self.downloader.finished.connect(self.library.set_local)
        self.downloader.finished.connect(self.playlists.set_local)
        self.downloader.finished.connect(pb.local_available)
        self.downloader.idle.connect(self._downloads_done)
        self.switcher.progress.connect(self._on_switching)
        self.switcher.finished.connect(self._switched)
        self.switcher.switched.connect(self._replace_in_lists)
        if self.devices is not None:
            self.devices.switched.connect(lambda text: self.toast("info", text))
        if self.balance is not None:
            self.balance.changed.connect(self.player_bar.on_balance_changed)
        if pb.tempo is not None:
            pb.tempo.updated.connect(lambda key: pb.current and pb.current.key == key and self.now_page.show_bpm())
        pb.file_deleted.connect(lambda key: self.library.set_local(key, ""))
        pb.file_deleted.connect(lambda key: self.playlists.set_local(key, ""))

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
        sc("Right", lambda: self._arrow(Qt.Key.Key_Right, 5), True)
        sc("Left", lambda: self._arrow(Qt.Key.Key_Left, -5), True)
        QApplication.instance().installEventFilter(self)
        sc("Ctrl+F", lambda: (self.go(SEARCH), self.search_page.focus()), True)
        sc("Ctrl+L", lambda: (self.go(SEARCH), self.search_page.focus()), True)
        sc("Ctrl+O", lambda: (self.go(LIBRARY), self.library_page.add_files()), True)
        sc("Ctrl+N", self.new_playlist, True)
        sc("Ctrl+K", self.open_quick, True)
        sc("F11", self.toggle_immersive, True)
        sc("Ctrl+D", self._save_current, True)
        sc("Ctrl+P", lambda: self._current_playlist_menu(self.player_bar.playlist_btn), True)
        sc("Ctrl+Up", lambda: self._nudge_volume(5), True)
        sc("Ctrl+Down", lambda: self._nudge_volume(-5), True)
        sc("Ctrl+M", self.player_bar._toggle_mute, True)
        sc("Ctrl+Shift+M", self.show_mini, True)
        sc("Ctrl+,", lambda: self.go(SETTINGS), True)
        sc("Ctrl+Q", self.quit_app, True)

    def _arrow(self, key, step: int) -> None:
        """←/→ always seek — except while typing in text that has a caret to move."""
        fw = QApplication.focusWidget()
        typing = (isinstance(fw, QLineEdit) and fw.text()) or isinstance(fw, (QTextEdit, QPlainTextEdit, QAbstractSpinBox))
        if typing:
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(fw, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
            return
        self.playback.seek_by(step)

    def eventFilter(self, obj, e):
        # An empty text field has nothing to move through, so ←/→ seek there too
        # (text fields claim the arrows before shortcuts see them).
        if (e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                and not e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                and isinstance(obj, QLineEdit) and not obj.text() and obj.window() is self):
            self.playback.seek_by(5 if e.key() == Qt.Key.Key_Right else -5)
            return True
        return super().eventFilter(obj, e)

    def _unless_typing(self, fn) -> None:
        if not isinstance(QApplication.focusWidget(), QLineEdit):
            fn()

    def _restore(self) -> None:
        geo = self.settings["geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        else:
            self.resize(1240, 780)
        self.sidebar.set_collapsed(self.settings["sidebar_collapsed"])
        self.sidebar.set_appearance(self.settings["appearance"])
        self.go(min(self.settings["page"], PAGE_COUNT - 1))
        self._on_current(self.playback.current)

    # ---- navigation ----------------------------------------------------------

    def go(self, index: int) -> None:
        if self.immersive is not None and self.immersive.isVisible():
            self.immersive.close()                     # going to a page (e.g. from Ctrl+K) leaves Immersive Mode
        index = max(0, min(PAGE_COUNT - 1, index))
        page = self.page(index)
        self.pages.setCurrentWidget(page)
        self.sidebar.select(index)
        self.settings["page"] = index
        page.on_shown()

    def open_playlist(self, pid: str) -> None:
        """A playlist or a Smart Collection: they open on the Shelf, like an album."""
        self.go(SHELF)
        self.shelf_page.open_list(pid)

    def new_playlist(self) -> None:
        self.go(SHELF)
        self.shelf_page.new_playlist()

    def new_smart(self, preset: int | None = None) -> None:
        """A Smart Collection from a preset, or (None) from the rule editor."""
        self.go(SHELF)
        self.shelf_page.new_smart(preset)

    def smart_tracks(self, sc) -> list:
        """A Smart Collection's songs right now (from the library, and what's been played)."""
        played = self.listening.played_tracks() if self.listening is not None else []
        stats = self.listening.stats if self.listening is not None else {}
        return smart.evaluate(sc, self.library.tracks, played, stats)

    # ---- behaviour -----------------------------------------------------------

    def toast(self, kind: str, text: str) -> None:
        self.toast_widget.show_message(text, kind)
        # problems also go to the Console (confirmations of the listener's own actions don't)
        if kind in ("danger", "warning"):
            logging.getLogger("notice").log(logging.ERROR if kind == "danger" else logging.WARNING, "%s", text)

    def set_appearance(self, appearance: str) -> None:
        self.settings["appearance"] = appearance
        theme.set_appearance(appearance)
        self.sidebar.set_appearance(appearance)
        if SETTINGS in self._built:
            self.settings_page.sync_appearance(appearance)

    def _cycle_appearance(self) -> None:
        cur = self.settings["appearance"]
        self.set_appearance(APPEARANCES[(APPEARANCES.index(cur) + 1) % len(APPEARANCES)])

    def _mini_style_changed(self, style: str) -> None:
        """Picked from the mini player's own menu: Settings shows the same choice."""
        if SETTINGS in self._built:
            from aria.ui.mini import STYLES
            self.settings_page.mini_style.set_index(STYLES.index(style))

    def _learn_artists(self) -> None:
        """Artists in the library are evidence when reading a video title (which side is the singer)."""
        from aria.core import identity
        names = []
        for t in self.library.tracks:
            if t.source == "spotify":
                names += [x.strip() for x in t.artist.split(",")]
            elif t.artist and not identity.generic_channel(identity.clean_uploader(t.artist)):
                names.append(identity.clean_uploader(t.artist))
        identity.remember_artists(names)

    def edit_song_info(self, track) -> None:
        """Edit Song Info…: the corrected name shows everywhere, and lyrics are looked up again."""
        from aria.ui.widgets.song_info import edit_song_info
        if not edit_song_info(self, track):
            return
        from aria.ui.widgets.tracklist import TrackListView
        for view in self.findChildren(TrackListView):       # every list shows the new name
            view.viewport().update()
        if self.library_page.rack_mode:
            self.library_page._sync_rack()
        cur = self.playback.current
        if cur is not None and cur.key == track.key:
            self.now_page._on_current(cur)
            self.now_page._load_lyrics(force=True)
            self.player_bar.set_track(cur)
            if self.immersive is not None:
                self.immersive._arrange()
        self.toast("success", "Song info saved")

    def settings_changed(self, key: str) -> None:
        value = self.settings[key]
        if key == "lyrics_translation":
            from aria.ui.widgets.lyrics_view import LyricsView
            LyricsView.set_extras(self.settings["lyrics_translation"])
        if key == "lyrics_words_all" and value:
            self.now_page.refine_words()                                 # the playing song's words now
        if key == "collage_centre" and self.immersive is not None:
            self.immersive.collage.set_centre(int(value))
            self.immersive._arrange()
        if key == "console_clear_after":
            self.log_bus.limit = value
        if key in ("crossfade", "crossfade_secs"):
            self.playback.apply_settings()
        elif key in ("tempo_flow", "tempo_strength", "tempo_scope"):
            pb = self.playback
            if self.settings["tempo_flow"] and pb.tempo is not None:
                pb.tempo.request(([pb.current] if pb.current else []) + pb.recommendations, front=True)
            pb.apply_tempo()
            self.now_page.show_bpm()
        elif key == "reduce_motion":
            theme.reduce_motion = bool(self.settings["reduce_motion"])
        elif key == "balance_mode" and self.balance is not None:
            self.balance.set_value(self.balance.value)
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

    # ---- collection health & backups ------------------------------------------------

    def check_health(self) -> None:
        from aria.ui.widgets.health_dialog import HealthDialog
        HealthDialog(self).exec()

    def flush_all(self) -> None:
        """Write every pending change now (before a snapshot)."""
        for store in (self.settings, self.library, self.playlists, self.shelf, self.playback):
            store.flush()
        if self.listening is not None:
            self.listening.save_now()

    def snapshot_now(self, reason: str = "manual", announce: bool = False):
        from aria.core import snapshot
        self.flush_all()
        try:
            path = snapshot.create(reason)
        except OSError as exc:
            self.toast("danger", "Couldn’t save a snapshot")
            log.error("Snapshot failed: %s", exc)
            return None
        if announce:
            self.toast("success", "Snapshot saved")
        return path

    def _auto_snapshot(self) -> None:
        from aria.core import snapshot
        if snapshot.due(self.settings["snapshot_every"]):
            self.snapshot_now("auto")

    def restore_snapshot(self, path) -> None:
        from aria.core import snapshot
        from aria.ui.widgets.dialogs import confirm
        snap = snapshot.read(path)
        if snap is None:
            self.toast("danger", "That isn’t an Aria backup")
            return
        if not confirm(self, "Restore this snapshot?",
                       f"{snap.label}. Aria saves the current state first, then restarts with the snapshot.",
                       "Restore & Restart", danger=True):
            return
        self._restart_into(path, "before-restore")

    def _restart_into(self, path, reason: str) -> None:
        """Snapshot what's here, write the backup's files and start Aria again without saving over them."""
        import subprocess
        import sys

        from aria.core import snapshot
        if self.snapshot_now(reason) is None:
            return
        try:
            snapshot.restore(path)
        except Exception as exc:
            log.error("Restore failed: %s", exc)
            self.toast("danger", "Couldn’t restore that backup")
            return
        if self.balance is not None:
            self.balance.shutdown()
        self.playback.player.release()
        if self.tray:
            self.tray.hide()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        cmd = [sys.executable] if paths.FROZEN else [sys.executable, "-m", "aria"]
        subprocess.Popen(cmd, cwd=str(paths.APP_DIR), creationflags=flags, close_fds=True)
        os._exit(0)                 # skip the usual save on exit: it would write the old data back

    def export_backup(self) -> None:
        import time

        from PySide6.QtWidgets import QFileDialog

        from aria.core import snapshot
        from aria.ui.widgets.dialogs import confirm
        size = sum(p.stat().st_size for p in paths.AUDIO_DIR.glob("*") if p.is_file()) if paths.AUDIO_DIR.exists() else 0
        title = "Export a full backup?"
        text = ("Library, playlists, shelf, history, sessions and settings in one file — "
                "restore it here or on another computer.")
        if size:
            ok, audio = confirm(self, title, text, "Choose File…",
                                option=f"Include downloaded songs ({size / 1_048_576:.0f} MB)")
        else:
            ok, audio = confirm(self, title, text, "Choose File…"), False
        if not ok:
            return
        name = f"Aria Backup {time.strftime('%Y-%m-%d')}.zip"
        dest, _ = QFileDialog.getSaveFileName(self, "Export Backup", name, "Aria Backup (*.zip)")
        if not dest:
            return
        self.flush_all()
        from aria.core import tasks
        from pathlib import Path
        self.status.set_job("Exporting backup")
        tasks.run(lambda: snapshot.create("manual", Path(dest), include_audio=audio),
                  lambda _p: (self.status.set_job(None), self.toast("success", "Backup exported")),
                  lambda e: (self.status.set_job(None), self.toast("danger", "Couldn’t export the backup")), lane="bulk")

    def import_backup(self) -> None:
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from aria.core import snapshot
        from aria.ui.widgets.dialogs import confirm
        src, _ = QFileDialog.getOpenFileName(self, "Import Backup", "", "Aria Backup (*.zip)")
        if not src:
            return
        snap = snapshot.read(Path(src))
        if snap is None:
            self.toast("danger", "That isn’t an Aria backup")
            return
        if confirm(self, "Replace your data with this backup?",
                   f"{snap.label}. What's here now is saved as a snapshot first; Aria then restarts.",
                   "Import & Restart", danger=True):
            self._restart_into(Path(src), "import")

    def clear_cache(self) -> None:
        """Ask, then empty data/cache in the background (library and downloads stay)."""
        from aria.core import cache, tasks
        from aria.ui.widgets.dialogs import confirm
        if not confirm(self, "Clear the cache?",
                       "Covers, lyrics and stream data are fetched again when needed. "
                       "Your library, playlists and downloads stay.", "Clear Cache"):
            return

        def done(freed: int):
            self.toast("success", f"Cache cleared · {cache.human(freed)} freed")
            if SETTINGS in self._built:
                self.settings_page.update_cache_size()

        tasks.run(cache.clear, done, lambda e: self.toast("danger", "Couldn’t clear the cache"))

    def free_space(self) -> None:
        """Pick downloaded songs to delete; they stay saved and in playlists, and stream when played."""
        from aria.core import cache
        from aria.ui.widgets.free_space_dialog import FreeSpaceDialog
        if self.downloader.busy:
            self.toast("info", "Wait for the downloads to finish first")
            return
        seen: dict[str, Track] = {}
        for t in self.library.tracks + [t for p in self.playlists.items for t in p.tracks]:
            if t.key not in seen and self.actions.local_path_of(t):
                seen[t.key] = t
        if not seen:
            self.toast("info", "No downloaded songs to delete")
            return
        dlg = FreeSpaceDialog(self, [(t, self.actions.local_path_of(t)) for t in seen.values()])
        if not dlg.exec() or not dlg.selected():
            return
        chosen = dlg.selected()
        size = 0
        for t in chosen:
            try:
                size += os.path.getsize(self.actions.local_path_of(t))
            except OSError:
                pass
        deleted, kept, later = self.actions.delete_downloads(chosen)
        parts = [f"{cache.human(size)} freed" if deleted else "Nothing deleted"]
        if later:
            parts.append("the playing song goes when it ends")
        if kept:
            parts.append(f"{kept} couldn’t be deleted")
        self.toast("success" if deleted else "info", " · ".join(parts))
        if SETTINGS in self._built:
            self.settings_page.update_cache_size()

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
        self.player_bar.set_volume(self.playback.player.volume + delta)

    # ---- immersive mode --------------------------------------------------------

    def toggle_immersive(self) -> None:
        """Full-screen listening (F11). The music carries on either way."""
        if self.immersive is not None and self.immersive.isVisible():
            self.immersive.close()
            return
        if self.immersive is None:
            from aria.ui.immersive import ImmersiveView
            self.immersive = ImmersiveView(self.playback, self.settings, self.now_page.lyrics, self.actions)
            self.immersive.closed.connect(self._immersive_closed)
            self.immersive.quick_requested.connect(self.open_quick)
        self.now_page._load_lyrics()               # make sure lyrics are on their way
        self.immersive.enter(self.screen())

    def _immersive_closed(self) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()

    # ---- quick actions -------------------------------------------------------

    def open_quick(self) -> None:
        """Ctrl+K — over Immersive Mode too, when that's what's showing."""
        if self.quick.isVisible():
            self.quick.close_palette()
            return
        imm = self.immersive if self.immersive is not None and self.immersive.isVisible() else None
        self.quick.open(imm)

    def _show_sessions(self) -> None:
        self.go(NOW)
        self.now_page.tabs.set_index(2, emit=True)
        self.now_page._show_history_mode(1)

    def quick_commands(self) -> list[Command]:
        """Everything Ctrl+K can do. The first few are offered before anything is typed."""
        pb, a, np = self.playback, self.actions, self.now_page
        playing = lambda: pb.current is not None  # noqa: E731
        downloadable = lambda: playing() and pb.current.source != LOCAL and not a.local_path_of(pb.current)  # noqa: E731
        has_file = lambda: playing() and pb.current.source != LOCAL and bool(a.local_path_of(pb.current))  # noqa: E731

        def discovery(v):
            np._set_dial(v)
            self.toast("info", {0: "Recommending familiar music", 50: "Recommendations balanced",
                                100: "Recommending the unexpected"}[v])

        cmds = [
            Command("Play / Pause", "play", pb.toggle, "Space", "resume stop"),
            Command("Next Song", "next", pb.next, "Ctrl+→", "skip"),
            Command("Add to Playlist", "list", lambda: self._current_playlist_menu(self.player_bar.playlist_btn),
                    "Ctrl+P", "playlist current", playing),
            Command("Download Current Track", "download", lambda: a.download([pb.current]), "",
                    "save offline file", downloadable),
            Command("Switch Source", "refresh",
                    lambda: pb.switch_source(not pb.playing_local), "", "stream local downloaded file", has_file),
            Command("Open Shelf", "disc", lambda: self.go(SHELF), "Ctrl+4", "albums records"),
            Command("Previous Song", "previous", pb.previous, "Ctrl+←", "back"),
            Command("Save to Library", "heart", self._save_current, "Ctrl+D", "like favourite", playing),
        ]
        for minutes in (25, 45, 60):
            cmds.append(Command(f"Listen for {minutes} Minutes", "timer",
                                lambda s=minutes * 60: np.start_timer(s), "", "timer time fitted playlist"))
        cmds += [
            Command("Stop Timer", "close", pb.cancel_timer, "", "timer", lambda: pb.timer is not None),
            Command("Leave Album Mode", "close", pb.end_album, "", "album", lambda: pb.album is not None),
            Command("Discovery: Familiar", "heart", lambda: discovery(0), "", "recommend dial known"),
            Command("Discovery: Balanced", "sparkles", lambda: discovery(50), "", "recommend dial"),
            Command("Discovery: Unexpected", "compass", lambda: discovery(100), "", "recommend dial new explore"),
            Command("Refresh Recommendations", "refresh", lambda: pb.refresh_recommendations(force=True), "",
                    "for you"),
            Command("Immersive Mode", "expand", self.toggle_immersive, "F11", "full screen fullscreen lyrics"),
            Command("Show Lyrics", "music", lambda: (self.go(NOW), np.tabs.set_index(3, emit=True)), "", "words"),
            Command("Listening Sessions", "clock", self._show_sessions, "", "history session"),
            Command("Shuffle Queue", "shuffle", pb.shuffle, "", "random", lambda: len(pb.queue) > 1),
            Command("Clear Queue", "trash", pb.clear, "", "empty", lambda: bool(pb.queue)),
            Command("Autoplay On / Off", "sparkles", self.player_bar.auto_btn.toggle, "", "recommend radio"),
            Command("Repeat Mode", "repeat", self.player_bar.repeat_btn.click, "", "loop"),
            Command("Mini Player", "mini", self.show_mini, "Ctrl+Shift+M", "small window"),
            Command("Volume Balance", "scale", self.player_bar.balance_btn.toggle, "", "other apps mix",
                    lambda: bool(self.balance and self.balance.available)),
            Command("New Playlist", "plus", self.new_playlist, "Ctrl+N", "create"),
            Command("New Smart Collection", "smart", lambda: self.new_smart(None), "", "rules auto filter"),
            Command("Now Playing", "music", lambda: self.go(NOW), "Ctrl+1"),
            Command("Search", "search", lambda: (self.go(SEARCH), self.search_page.focus()), "Ctrl+F", "find"),
            Command("Open Library", "library", lambda: self.go(LIBRARY), "Ctrl+3", "saved"),
            Command("Open Settings", "settings", lambda: self.go(SETTINGS), "Ctrl+,", "preferences options"),
            Command("Open Console", "terminal", lambda: self.go(CONSOLE), "Ctrl+5", "log commands"),
            Command("Clear Cache", "trash", self.clear_cache, "", "delete covers lyrics"),
            Command("Free Up Space", "trash", self.free_space, "", "delete downloads downloaded files storage disk"),
            Command("Check Library", "check-circle", self.check_health, "", "collection health duplicates missing"),
            Command("Back Up Now", "export", lambda: self.snapshot_now("manual", announce=True), "",
                    "snapshot backup save"),
            Command("Open Downloads Folder", "folder",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.AUDIO_DIR))), "", "files"),
            Command("Change Appearance", "moon", self._cycle_appearance, "", "theme dark light"),
            Command("Quit Aria", "power", self.quit_app, "Ctrl+Q", "exit close"),
        ]
        return cmds

    def _on_current(self, track) -> None:
        key = track.key if track else None
        self.library_page.set_playing(key)
        self.search_page.results.set_playing(key)
        if SHELF in self._built:
            self.shelf_page.detail.list.set_playing(key)
        self.setWindowTitle(f"{track.title} — Aria" if track else "Aria")
        self._update_status()

    def _on_measuring(self, done: int, total: int) -> None:
        """Arrange by Tempo: a progress bar in the status bar while BPMs are measured."""
        if total and done < total:
            self.status.set_job(f"Measuring tempo {done}/{total}", done / total)
        elif not self.downloader.busy:
            self.status.set_job(None)
            if total:
                self.toast("success", "Queue arranged by tempo")

    def _on_mode(self) -> None:
        if self.playback.timer is not None:
            self._status_tick.start()
        else:
            self._status_tick.stop()
        self._update_status()

    def _update_status(self) -> None:
        pb = self.playback
        t = pb.current
        state = STATE_TEXT.get(pb.state, "")
        source = ("Local file" if pb.playing_local else SOURCE_LABELS.get(t.source, "")) if t else ""
        n = len(pb.queue)
        if pb.timer is not None:
            mode = f"Timer {format_duration(pb.timer_remaining())} left"
        elif pb.album is not None:
            mode = "Album · gapless"
        else:
            mode = " · ".join(x for x in ("Autoplay" if pb.autoplay else "",
                                          "Crossfade" if self.settings["crossfade"] else "") if x)
        self.status.set_parts(state if t else "", source, f"{n:,} in queue", mode)

    def _on_switching(self, done: int, total: int, title: str) -> None:
        if total > 1:
            self.status.set_job(f"Switching source {done + 1}/{total}", done / total)
        else:
            self.status.set_job("Switching source", None)

    def _replace_in_lists(self, old_key: str, new) -> None:
        """Any list still showing the old version (search results, an open album…) shows the new one."""
        from aria.ui.widgets.tracklist import TrackListView
        for view in self.findChildren(TrackListView):
            tracks = view.tracks()
            hit = False
            for i, t in enumerate(tracks):
                if t.key == old_key:
                    tracks[i] = new.copy()
                    hit = True
            if hit:
                view.model_.refresh_all()

    def _switched(self, switched: int, missing: list, target: str) -> None:
        if not self.downloader.busy:
            self.status.set_job(None)
        name = "Spotify" if target == "spotify" else "YouTube"
        total = switched + len(missing)
        if not switched:
            self.toast("warning", f"No match on {name}" if total == 1 else f"None of {total} songs found on {name}")
        elif missing:
            self.toast("warning", f"Switched {switched} of {total} to {name} · {len(missing)} not found")
        else:
            self.toast("success", f"Switched to {name}" if total == 1 else f"Switched {total} songs to {name}")

    def _downloads_done(self, ok: int, failed: int) -> None:
        self.status.set_job(None)
        if failed:
            self.toast("warning", f"{ok} downloaded, {failed} failed")
        else:
            self.toast("success", f"Downloaded {ok} song{'s' if ok != 1 else ''}")

    # ---- window / tray -------------------------------------------------------

    def show_mini(self) -> None:
        # what had the focus, so coming back carries on from there
        self._focus_before_mini = QApplication.focusWidget() if self.isActiveWindow() else self._focus_before_mini
        self.mini.show_near(self)

    def bring_back(self) -> None:
        """Back from the mini player or the tray: the window as it was (maximized stays maximized),
        the same page and the same control in focus; playback never paused for any of it."""
        self.mini.hide()
        if self.isMinimized() or not self.isVisible():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
        self.raise_()
        self.activateWindow()
        w, self._focus_before_mini = self._focus_before_mini, None
        try:
            if w is not None and w.window() is self and w.isVisible():
                w.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            pass                                    # that control is gone meanwhile

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
        if self.quick.isVisible() and self.quick.parentWidget() is self.centralWidget():
            self.quick.setGeometry(self.centralWidget().rect())

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
