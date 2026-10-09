"""Track actions shared by every list: play, queue, save, download, open, copy."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMenu, QWidget

from aria.core.models import LOCAL, Track
from aria.ui import icons
from aria.ui.theme import theme


class TrackActions:
    def __init__(self, playback, library, downloader, toast):
        self.pb = playback
        self.library = library
        self.downloader = downloader
        self.toast = toast

    def play(self, tracks: list[Track]) -> None:
        if not tracks:
            return
        self.pb.play_now(tracks[0])
        if len(tracks) > 1:
            self.pb.enqueue(tracks[1:], play_next=True)

    def play_next(self, tracks: list[Track]) -> None:
        if tracks:
            self.pb.enqueue(tracks, play_next=True)
            self.toast("success", "將在下一首播放" if len(tracks) == 1 else f"{len(tracks)} 首將接著播放")

    def enqueue(self, tracks: list[Track]) -> None:
        if tracks:
            self.pb.enqueue(tracks)
            self.toast("success", "已加入佇列" if len(tracks) == 1 else f"已加入 {len(tracks)} 首")

    def save(self, tracks: list[Track]) -> None:
        added = self.library.add(tracks)
        self.toast("success", f"已收藏 {added} 首" if added else "已在收藏中")

    def toggle_saved(self, track: Track) -> None:
        saved = self.library.toggle(track)
        self.toast("success", "已收藏" if saved else "已取消收藏")

    def download(self, tracks: list[Track]) -> None:
        self.library.add(tracks)   # downloads live in the library
        n = self.downloader.add([self.library.get(t.key) or t for t in tracks])
        self.toast("info", f"開始下載 {n} 首" if n else "已下載過")

    def open_source(self, track: Track) -> None:
        if track.source == LOCAL:
            QDesktopServices.openUrl(QUrl.fromLocalFile(track.local_path))
        elif track.url:
            QDesktopServices.openUrl(QUrl(track.url))

    def copy_link(self, tracks: list[Track]) -> None:
        QGuiApplication.clipboard().setText("\n".join(t.url for t in tracks if t.url))
        self.toast("success", "已複製連結")

    def menu(self, parent: QWidget, tracks: list[Track], extra: list[tuple[str, str, object]] | None = None) -> QMenu:
        """Context menu; ``extra`` = [(icon, text, callback)] appended after a separator."""
        m = QMenu(parent)
        c = theme.color("label")

        def add(icon, text, fn):
            m.addAction(icons.icon(icon, c, 16), text, fn)

        add("play", "立即播放", lambda: self.play(tracks))
        add("play-next", "下一首播放", lambda: self.play_next(tracks))
        add("queue-add", "加入佇列", lambda: self.enqueue(tracks))
        m.addSeparator()
        if not all(t.key in self.library for t in tracks):
            add("heart", "收藏", lambda: self.save(tracks))
        if any(t.source != LOCAL and not t.local_path for t in tracks):
            add("download", "下載", lambda: self.download(tracks))
        if len(tracks) == 1:
            add("external", "開啟來源", lambda: self.open_source(tracks[0]))
        if any(t.url and t.source != LOCAL for t in tracks):
            add("link", "複製連結", lambda: self.copy_link(tracks))
        if extra:
            m.addSeparator()
            for icon, text, fn in extra:
                add(icon, text, fn)
        return m
