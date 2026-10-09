"""Now playing: artwork canvas on the left, queue / recommendations / history on the right (§6.3)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QSplitter, QStackedWidget, QVBoxLayout, QWidget

from aria.core import lyrics as lyrics_mod
from aria.core import tasks
from aria.core.player import LOADING
from aria.ui.pages.base import TintedPage
from aria.ui.player_bar import ElidedLabel
from aria.ui.widgets.artwork import Artwork
from aria.ui.widgets.controls import Button, IconButton, hbox, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.lyrics_view import LyricsView
from aria.ui.widgets.tracklist import RowAction, TrackListView

PANEL_MIN = 380


class NowPlayingPage(TintedPage):
    def __init__(self, playback, library, actions, settings):
        super().__init__("Now Playing", settings)
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
        self.art = Artwork(tint=self.tint)
        self.art.hue_changed.connect(self.set_hue)
        lv.addWidget(self.art, 1)
        self.song = ElidedLabel("Title2")
        self.artist = ElidedLabel("Secondary")
        self.open_btn = Button("Open Source", "borderless", icon="external")
        self.open_btn.clicked.connect(lambda: self.pb.current and self.actions.open_source(self.pb.current))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(self.song)
        info.addWidget(self.artist)
        self.source = Segmented(["Stream", "Local"], compact=True)
        self.source.changed.connect(lambda i: self.pb.switch_source(i == 1))
        self.playlist_btn = IconButton("list", "Add to Playlist", tone="secondary")
        row = hbox(info, self.source, self.playlist_btn, self.open_btn, spacing=12)
        row.setStretch(0, 1)
        lv.addLayout(row)
        split.addWidget(left)

        # ---- right: tabs + lists
        right = QWidget()
        right.setMinimumWidth(PANEL_MIN)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        self.tabs = Segmented(["Queue", "For You", "History", "Lyrics"])
        rv.addWidget(self.tabs)

        self.count = label("", "Caption")
        self.shuffle_btn = IconButton("shuffle", "Shuffle Queue", tone="secondary")
        self.clear_btn = IconButton("trash", "Clear Queue", tone="secondary")
        self.refresh_btn = IconButton("refresh", "Refresh Recommendations", tone="secondary")
        self.add_recs_btn = IconButton("queue-add", "Add All to Queue", tone="secondary")
        self.clear_hist_btn = IconButton("trash", "Clear History", tone="secondary")
        self.lyrics_btn = IconButton("refresh", "Search Lyrics Again", tone="secondary")
        rv.addLayout(hbox(self.count, None, self.shuffle_btn, self.clear_btn, self.refresh_btn,
                          self.add_recs_btn, self.clear_hist_btn, self.lyrics_btn, spacing=4))

        self.stack = QStackedWidget()
        self.queue_list = TrackListView("queue", "Your queue is empty", "Add songs from Search or your Library.", reorderable=True)
        self.recs_list = TrackListView("sparkles", "No recommendations yet", "Play a song to see related music.")
        self.hist_list = TrackListView("history", "No history yet", "")
        self.lyrics = LyricsView()
        for v in (self.queue_list, self.recs_list, self.hist_list, self.lyrics):
            self.stack.addWidget(v)
        rv.addWidget(self.stack, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 420])

        self._lyrics_for: str | None = None
        self._lyrics_ticket = tasks.Latest()
        self._lyrics_timer = QTimer(self)
        self._lyrics_timer.setSingleShot(True)
        self._lyrics_timer.setInterval(600)
        self._lyrics_timer.timeout.connect(self._load_lyrics)
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
        self.lyrics_btn.clicked.connect(lambda: self._load_lyrics(force=True))
        self.lyrics.seek.connect(pb.seek)
        pb.position_changed.connect(lambda pos, _l: self.lyrics.set_position(pos))

        saved = lambda t: t.key in self.library  # noqa: E731
        self.queue_list.actions = [RowAction("close", "Remove from Queue (Delete)", lambda r: pb.remove([r]))]
        self.queue_list.activated_row.connect(pb.play_queue_item)
        self.queue_list.delete_pressed.connect(pb.remove)
        self.queue_list.moved.connect(pb.move)
        self.queue_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [pb.queue[r] for r in rows],
                                     [("close", "Remove from Queue", lambda: pb.remove(rows))]).exec(pos))

        self.recs_list.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: pb.accept_recommendation(r)),
            RowAction("heart", "Save", lambda r: a.toggle_saved(pb.recommendations[r]), saved),
        ]
        self.recs_list.activated_row.connect(lambda r: pb.accept_recommendation(r, play=True))
        self.recs_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [pb.recommendations[r] for r in rows]).exec(pos))

        self.hist_list.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: a.enqueue([self.hist_list.tracks()[r]])),
            RowAction("heart", "Save", lambda r: a.toggle_saved(self.hist_list.tracks()[r]), saved),
        ]
        self.hist_list.activated_row.connect(lambda r: pb.play_now(self.hist_list.tracks()[r].copy()))
        self.hist_list.context_requested.connect(
            lambda rows, pos: a.menu(self, [self.hist_list.tracks()[r] for r in rows]).exec(pos))

        pb.current_changed.connect(self._on_current)
        pb.source_changed.connect(lambda _l: self._sync_source())
        pb.queue_changed.connect(self._refresh_queue)
        pb.recommendations_changed.connect(self._refresh_recs)
        pb.history_changed.connect(self._refresh_history)
        pb.recommending.connect(lambda _b: self._update_toolbar())
        pb.state_changed.connect(lambda s: self.art.set_loading(s == LOADING))
        self.library.changed.connect(self.recs_list.viewport().update)
        self.library.changed.connect(self.hist_list.viewport().update)

    def _sync_source(self) -> None:
        t = self.pb.current
        self.source.setVisible(bool(t and t.local_path and t.source != "local"))
        self.source.set_index(1 if self.pb.playing_local else 0)

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
        busy = tab == 1 and self.pb.recs_busy
        if tab == 3:
            lyr = self.lyrics.lyrics
            self.count.setText(f"{lyr.source} · {'Synced' if lyr.synced else 'Plain'}" if lyr else "")
        else:
            n = [len(self.pb.queue), len(self.pb.recommendations), len(self.pb.history)][tab]
            self.count.setText("Finding…" if busy else f"{n:,} song{'s' if n != 1 else ''}")
        for btn, on_tab in ((self.shuffle_btn, 0), (self.clear_btn, 0), (self.refresh_btn, 1),
                            (self.add_recs_btn, 1), (self.clear_hist_btn, 2), (self.lyrics_btn, 3)):
            btn.setVisible(tab == on_tab)
        self.shuffle_btn.setEnabled(len(self.pb.queue) > 1)
        self.clear_btn.setEnabled(bool(self.pb.queue))
        self.refresh_btn.setEnabled(not busy and (self.pb.current is not None or bool(self.pb.history)))
        self.add_recs_btn.setEnabled(bool(self.pb.recommendations))
        self.clear_hist_btn.setEnabled(bool(self.pb.history))
        self.lyrics_btn.setEnabled(self.pb.current is not None and self.lyrics.state != "loading")

    # ---- lyrics --------------------------------------------------------------

    def _load_lyrics(self, force: bool = False) -> None:
        t = self.pb.current
        if not t:
            self.lyrics.set_state("empty")
            self._update_toolbar()
            return
        if not force and self._lyrics_for == t.key:
            return
        self._lyrics_for = t.key
        ticket = self._lyrics_ticket.next()
        self.lyrics.set_state("loading")
        self._update_toolbar()
        enhanced = bool(self.settings["lyrics_enhanced"])

        def done(found):
            if self._lyrics_ticket.is_current(ticket):
                self.lyrics.set_state("ready" if found else "none", found)
                self.lyrics.set_position(self.pb.player.position())
                self._update_toolbar()

        def failed(_e):
            if self._lyrics_ticket.is_current(ticket):
                self.lyrics.set_state("none")
                self._update_toolbar()

        tasks.run(lambda: lyrics_mod.fetch(t, enhanced, self.pb.recommender, force=force), done, failed)

    def _on_current(self, t) -> None:
        self.art.set_track(t)
        self.song.set_full_text(t.title if t else "")
        self.artist.set_full_text((f"{t.artist} · {t.source_label}" if t.artist else t.source_label) if t else "")
        self.open_btn.setVisible(t is not None)
        self.playlist_btn.setVisible(t is not None)
        self._sync_source()
        key = t.key if t else None
        for v in (self.queue_list, self.recs_list, self.hist_list):
            v.set_playing(key)
        if key != self._lyrics_for:
            self.lyrics.set_state("loading" if t else "empty")
            self._lyrics_timer.start()          # wait out quick skips
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
