"""Application bootstrap."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from aria import __version__, log as applog, paths


def main() -> int:
    paths.ensure_dirs()
    bus = applog.setup()
    log = logging.getLogger("aria")

    if sys.platform == "win32":
        # Own taskbar group + icon instead of python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Aria.MusicPlayer")
        except Exception:
            pass

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Aria")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)      # closing can mean "keep playing in the tray"
    icon = paths.ASSETS_DIR / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    # Light imports first, so the launch card appears before the heavy ones (yt-dlp, VLC).
    from aria.core import storage
    from aria.ui.splash import Splash
    from aria.ui.theme import theme

    settings = storage.Settings()
    theme.attach(app, settings["appearance"])
    splash = Splash()
    splash.start()

    splash.step("Loading the player…")
    from aria.core import tasks
    from aria.core.library import Downloader, Library
    from aria.core.lists import Playlists, Shelf
    from aria.core.playback import Playback

    splash.step("Reading your library…")
    legacy = storage.migrate_legacy()
    library = Library(legacy[0] if legacy else None)
    playback = Playback(settings, legacy[1] if legacy else None)
    downloader = Downloader()
    playlists = Playlists()
    shelf = Shelf()

    splash.step("Preparing the interface…")
    from aria.ui.main_window import MainWindow
    window = MainWindow(settings, playback, library, downloader, playlists, shelf, bus)
    splash.finish(window)
    log.info("Aria %s started · %d in library · %d in queue", __version__, len(library), len(playback.queue))

    code = app.exec()

    playback.shutdown()
    settings.flush()
    library.flush()
    playlists.flush()
    shelf.flush()
    tasks.shutdown()
    logging.shutdown()
    # yt-dlp / requests worker threads may still be blocked on the network; don't wait for them.
    os._exit(code)
