"""Now playing: artwork canvas on the left, queue / recommendations / history on the right (§6.3)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QStackedWidget, QVBoxLayout, QWidget

from aria.core.player import LOADING
from aria.ui.pages.base import Page
from aria.ui.player_bar import ElidedLabel
from aria.ui.widgets.artwork import Artwork
from aria.ui.widgets.controls import Button, IconButton, hbox, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView

PANEL_MIN = 380


class NowPlayingPage(Page):
    def __init__(self, playback, library, actions, settings):
        super().__init__("播放中")
        self.pb = playback
        self.library = library
        self.actions = actions
        self.settings = settings

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(16)
        split.setChildrenCollapsible(False)
        self.root.addWidget(split, 1)

        # ---- left: canvas + caption rows
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)
        self.art = Artwork()
        lv.addWidget(self.art, 1)
        self.song = ElidedLabel("Title2")
        self.artist = ElidedLabel("Secondary")
        self.open_btn = Button("開啟來源", "borderless", icon="external", tooltip="在瀏覽器開啟原始頁面")
        self.open_btn.clicked.connect(lambda: self.pb.current and self.actions.open_source(self.pb.current))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(self.song)
        info.addWidget(self.artist)
        row = hbox(info, self.open_btn, spacing=12)
        row.setStretch(0, 1)
        lv.addLayout(row)
        split.addWidget(left)

        # ---- right: tabs + lists
        right = QWidget()
        right.setMinimumWidth(PANEL_MIN)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        self.tabs = Segmented(["佇列", "推薦", "紀錄"],
                              tooltips=["接下來要播放的歌（可拖曳排序）", "與目前歌曲相關的 YouTube 歌曲", "播放過的歌"])
        rv.addWidget(self.tabs)

        self.count = label("", "Caption")
        self.shuffle_btn = IconButton("shuffle", "打亂佇列", tone="secondary")
        self.clear_btn = IconButton("trash", "清空佇列", tone="secondary")
        self.refresh_btn = IconButton("refresh", "重新推薦", tone="secondary")
        self.add_recs_btn = IconButton("queue-add", "全部加入佇列", tone="secondary")
        self.clear_hist_btn = IconButton("trash", "清除紀錄", tone="secondary")
        rv.addLayout(hbox(self.count, None, self.shuffle_btn, self.clear_btn, self.refresh_btn,
                          self.add_recs_btn, self.clear_hist_btn, spacing=4))

        self.stack = QStackedWidget()
        self.queue_list = TrackListView("queue", "佇列是空的", "從搜尋或收藏加入歌曲。", reorderable=True)
        self.recs_list = TrackListView("sparkles", "還沒有推薦", "播放一首歌後會出現相關歌曲。")
        self.hist_list = TrackListView("history", "還沒有紀錄", "")
        for v in (self.queue_list, self.recs_list, self.hist_list):
            self.stack.addWidget(v)
        rv.addWidget(self.stack, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 420])

        self._wire()
        self.tabs.set_index(settings["panel_tab"])
        self._show_tab(settings["panel_tab"])
        self._on_current(self.pb.current)
        self._refresh_queue()
        self._refresh_recs()
        self._refresh_history()

    def _wire(self) -> None:
        pb, a = self.pb, self.actions
        self.tabs.changed.connect(self._show_tab)
        self.shuffle_btn.clicked.connect(pb.shuffle)
        self.clear_btn.clicked.connect(pb.clear)
        self.refresh_btn.clicked.connect(lambda: pb.refresh_recommendations(force=True))
        self.add_recs_btn.clicked.connect(self._add_all_recs)
        self.clear_hist_btn.clicked.connect(pb.clear_history)

        saved = lambda t: t.key in self.library  # noqa: E731
        self.queue_list.actions = [RowAction("close", "從佇列移除（Delete）", lambda r: pb.remove([r]))]
        self.queue_list.activated_row.connect(pb.play_queue_item)
        self.queue_list.delete_pressed.connect(pb.remove)
        self.queue_list.moved.connect(pb.move)
        self.queue_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [pb.queue[r] for r in rows],
                                     [("close", "從佇列移除", lambda: pb.remove(rows))]).exec(pos))

        self.recs_list.actions = [
            RowAction("queue-add", "加入佇列", lambda r: pb.accept_recommendation(r)),
            RowAction("heart", "收藏", lambda r: a.toggle_saved(pb.recommendations[r]), saved),
        ]
        self.recs_list.activated_row.connect(lambda r: pb.accept_recommendation(r, play=True))
        self.recs_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [pb.recommendations[r] for r in rows]).exec(pos))

        self.hist_list.actions = [
            RowAction("queue-add", "加入佇列", lambda r: a.enqueue([self.hist_list.tracks()[r]])),
            RowAction("heart", "收藏", lambda r: a.toggle_saved(self.hist_list.tracks()[r]), saved),
        ]
        self.hist_list.activated_row.connect(lambda r: pb.play_now(self.hist_list.tracks()[r].copy()))
        self.hist_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [self.hist_list.tracks()[r] for r in rows]).exec(pos))

        pb.current_changed.connect(self._on_current)
        pb.queue_changed.connect(self._refresh_queue)
        pb.recommendations_changed.connect(self._refresh_recs)
        pb.history_changed.connect(self._refresh_history)
        pb.recommending.connect(lambda _b: self._update_toolbar())
        pb.state_changed.connect(lambda s: self.art.set_loading(s == LOADING))
        self.library.changed.connect(self.recs_list.viewport().update)
        self.library.changed.connect(self.hist_list.viewport().update)

    def _add_all_recs(self) -> None:
        if self.pb.recommendations:
            self.actions.enqueue(list(self.pb.recommendations))
            self.pb.recommendations = []
            self.pb.recommendations_changed.emit()

    def _show_tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.settings["panel_tab"] = i
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        tab = self.tabs.index()
        n = [len(self.pb.queue), len(self.pb.recommendations), len(self.pb.history)][tab]
        busy = tab == 1 and self.pb.recs_busy
        self.count.setText("推薦中…" if busy else f"{n:,} 首")
        for btn, on_tab in ((self.shuffle_btn, 0), (self.clear_btn, 0), (self.refresh_btn, 1),
                            (self.add_recs_btn, 1), (self.clear_hist_btn, 2)):
            btn.setVisible(tab == on_tab)
        self.shuffle_btn.setEnabled(len(self.pb.queue) > 1)
        self.clear_btn.setEnabled(bool(self.pb.queue))
        self.refresh_btn.setEnabled(not busy and (self.pb.current is not None or bool(self.pb.history)))
        self.add_recs_btn.setEnabled(bool(self.pb.recommendations))
        self.clear_hist_btn.setEnabled(bool(self.pb.history))

    def _on_current(self, t) -> None:
        self.art.set_track(t)
        self.song.set_full_text(t.title if t else "")
        self.artist.set_full_text((f"{t.artist} · {t.source_label}" if t.artist else t.source_label) if t else "")
        self.open_btn.setVisible(t is not None)
        key = t.key if t else None
        for v in (self.queue_list, self.recs_list, self.hist_list):
            v.set_playing(key)
        self._update_toolbar()

    def _refresh_queue(self) -> None:
        self.queue_list.set_tracks(self.pb.queue)
        self._update_toolbar()

    def _refresh_recs(self) -> None:
        self.recs_list.set_tracks(self.pb.recommendations)
        self._update_toolbar()

    def _refresh_history(self) -> None:
        self.hist_list.set_tracks(self.pb.history[::-1])
        self._update_toolbar()
