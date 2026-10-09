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
    icon = paths.ASSETS_DIR / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    # Imported after QApplication exists (widgets/pixmaps need it).
    from aria.core import storage, tasks
    from aria.core.library import Downloader, Library
    from aria.core.playback import Playback
    from aria.ui.main_window import MainWindow
    from aria.ui.theme import theme

    settings = storage.Settings()
    theme.attach(app, settings["appearance"])

    legacy = storage.migrate_legacy()
    library = Library(legacy[0] if legacy else None)
    playback = Playback(settings, legacy[1] if legacy else None)
    downloader = Downloader()

    window = MainWindow(settings, playback, library, downloader, bus)
    window.show()
    log.info("Aria %s 已啟動 · 收藏 %d 首 · 佇列 %d 首", __version__, len(library), len(playback.queue))

    code = app.exec()

    playback.shutdown()
    settings.flush()
    library.flush()
    tasks.shutdown()
    logging.shutdown()
    # yt-dlp / requests worker threads may still be blocked on the network; don't wait for them.
    os._exit(code)
