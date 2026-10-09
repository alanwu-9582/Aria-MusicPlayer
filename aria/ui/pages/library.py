"""Library: saved tracks, downloads, playlist import / export, local files."""

from __future__ import annotations

import logging
import os

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QLineEdit

from aria.core.library import AUDIO_EXTS, local_track
from aria.core.models import LOCAL
from aria.ui import icons
from aria.ui.pages.base import Page
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, label, vline
from aria.ui.widgets.dialogs import confirm
from aria.ui.widgets.tracklist import RowAction, TrackListView

log = logging.getLogger(__name__)


class LibraryPage(Page):
    def __init__(self, library, downloader, actions, toast):
        super().__init__("收藏")
        self.library = library
        self.downloader = downloader
        self.actions = actions
        self.toast = toast

        self.open_btn = Button("加入檔案…", "borderless", icon="folder", tooltip="加入電腦裡的音樂檔（Ctrl+O）")
        self.import_btn = Button("匯入…", "borderless", icon="import", tooltip="匯入歌單 JSON（支援 v1 格式）")
        self.export_btn = Button("匯出…", "borderless", icon="export", tooltip="把收藏匯出成歌單 JSON")
        for b in (self.open_btn, self.import_btn, self.export_btn):
            self.header.addWidget(b)
        self.open_btn.clicked.connect(self.add_files)
        self.import_btn.clicked.connect(self.import_playlist)
        self.export_btn.clicked.connect(self.export_playlist)

        bar = self.toolbar()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("篩選")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(220)
        self._filter_icon = QAction(self.filter)
        self.filter.addAction(self._filter_icon, QLineEdit.ActionPosition.LeadingPosition)
        self.filter.textChanged.connect(self._refresh)
        self.play_btn = Button("播放", "primary", icon="play", tooltip="播放選取的歌；未選取時播放全部")
        self.queue_btn = Button("加入佇列", icon="queue-add", tooltip="未選取時加入全部")
        self.download_btn = Button("下載", icon="download", tooltip="下載後可離線播放")
        self.remove_btn = Button("移除", "danger", icon="trash", tooltip="從收藏移除（Delete）")
        self.count = label("", "Caption")
        for w in (self.filter, vline(), self.play_btn, self.queue_btn, self.download_btn, vline(), self.remove_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.count)

        self.list = TrackListView("library", "還沒有收藏", "在搜尋結果按愛心收藏歌曲。")
        self.list.actions = [
            RowAction("queue-add", "加入佇列", lambda r: actions.enqueue([self.list.tracks()[r]])),
            RowAction("download", "下載", lambda r: actions.download([self.list.tracks()[r]]),
                      lambda t: bool(t.local_path)),
        ]
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:]))
        self.list.delete_pressed.connect(lambda _rows: self.remove())
        self.list.context_requested.connect(self._menu)
        self.list.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.root.addWidget(self.list, 1)

        self.play_btn.clicked.connect(lambda: actions.play(self._targets()))
        self.queue_btn.clicked.connect(lambda: actions.enqueue(self._targets()))
        self.download_btn.clicked.connect(lambda: actions.download(self._targets()))
        self.remove_btn.clicked.connect(self.remove)

        library.changed.connect(self._refresh)
        library.track_updated.connect(self.list.model_.refresh_key)
        theme.changed.connect(self._retint)
        self._retint()
        self._refresh()

    def _retint(self) -> None:
        self._filter_icon.setIcon(icons.icon("search", theme.color("tertiary"), 15))

    def set_playing(self, key) -> None:
        self.list.set_playing(key)

    # ---- list ----------------------------------------------------------------

    def _refresh(self) -> None:
        q = self.filter.text().strip().casefold()
        tracks = [t for t in self.library.tracks
                  if not q or q in t.title.casefold() or q in t.artist.casefold()]
        selected = {t.key for t in self.list.selected_tracks()}
        self.list.set_tracks(tracks)
        for i, t in enumerate(tracks):
            if t.key in selected:
                self.list.selectionModel().select(self.list.model_.index(i),
                                                  self.list.selectionModel().SelectionFlag.Select)
        total = len(self.library)
        self.count.setText(f"{len(tracks):,} / {total:,} 首" if q else f"{total:,} 首")
        self._update_buttons()

    def _targets(self):
        return self.list.selected_tracks() or self.list.tracks()

    def _update_buttons(self) -> None:
        has_any = bool(self.list.tracks())
        sel = self.list.selected_tracks()
        self.play_btn.setEnabled(has_any)
        self.queue_btn.setEnabled(has_any)
        self.download_btn.setEnabled(any(t.source != LOCAL and not t.local_path for t in (sel or self.list.tracks())))
        self.remove_btn.setEnabled(bool(sel))
        self.export_btn.setEnabled(bool(self.library.tracks))

    def _menu(self, rows, pos) -> None:
        tracks = [self.list.tracks()[r] for r in rows]
        self.actions.menu(self, tracks, [("trash", "從收藏移除", self.remove)]).exec(pos)

    # ---- commands ------------------------------------------------------------

    def remove(self) -> None:
        tracks = self.list.selected_tracks()
        if not tracks:
            return
        name = f"「{tracks[0].title}」" if len(tracks) == 1 else f"{len(tracks)} 首歌"
        downloaded = sum(1 for t in tracks if t.local_path and t.source != LOCAL)
        detail = "已下載的音檔也會一併刪除；佇列不受影響。" if downloaded else "不會影響佇列與播放紀錄。"
        if confirm(self, f"移除{name}？", detail, "移除", danger=True):
            self.library.remove([t.key for t in tracks])
            self.toast("success", "已移除")

    def add_files(self) -> None:
        pattern = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "加入音樂檔", "", f"音樂檔 ({pattern})")
        if files:
            n = self.library.add([local_track(f) for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS])
            self.toast("success", f"已加入 {n} 首")

    def import_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "匯入歌單", "", "歌單 (*.json)")
        if not path:
            return
        try:
            n = self.library.import_file(path)
        except Exception as exc:
            log.error("匯入失敗: %s", exc)
            self.toast("danger", "無法讀取歌單")
            return
        log.info("已匯入 %d 首（%s）", n, os.path.basename(path))
        self.toast("success", f"已匯入 {n} 首")

    def export_playlist(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "匯出歌單", "Aria 歌單.json", "歌單 (*.json)")
        if not path:
            return
        try:
            n = self.library.export_file(path)
        except OSError as exc:
            log.error("匯出失敗: %s", exc)
            self.toast("danger", "無法寫入檔案")
            return
        log.info("已匯出 %d 首到 %s", n, path)
        self.toast("success", f"已匯出 {n} 首")
