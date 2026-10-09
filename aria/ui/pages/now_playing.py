"""Now playing: artwork canvas on the left, queue / recommendations / history / lyrics on the right (§6.3).

New tools live inside these tabs rather than on pages of their own:
- Queue: the listening timer (Time-Fitted Playlist) and album mode status.
- For You: the Discovery dial (familiar ↔ unexpected).
- History: songs, or listening sessions as cards.
- Lyrics: click a line to play from there.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QMenu, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from aria.core import lyrics as lyrics_mod
from aria.core import tasks, timefit
from aria.core.models import format_duration
from aria.core.player import LOADING
from aria.ui import icons
from aria.ui.pages.base import TintedPage
from aria.ui.player_bar import ElidedLabel
from aria.ui.theme import theme
from aria.ui.widgets.artwork import Artwork
from aria.ui.widgets.controls import Button, IconButton, hbox, label
from aria.ui.widgets.dialogs import confirm, prompt
from aria.ui.widgets.lyrics_view import LyricsView
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.sessions import SessionListView, span
from aria.ui.widgets.slider import Slider
from aria.ui.widgets.tracklist import RowAction, TrackListView

PANEL_MIN = 380
QUEUE, FOR_YOU, HISTORY, LYRICS = range(4)
TIMER_PRESETS = (25, 45, 60)


class NowPlayingPage(TintedPage):
    def __init__(self, playback, library, actions, settings, listening=None):
        super().__init__("Now Playing", settings)
        self.pb = playback
        self.library = library
        self.actions = actions
        self.settings = settings
        self.listening = listening
        self._session_id: str | None = None

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

        # toolbar (one row; each tab shows its own tools)
        self.count = label("", "Caption")
        self.mode_end_btn = IconButton("close", "", size=22, icon_size=13, tone="secondary")
        self.hist_mode = Segmented(["Songs", "Sessions"], compact=True)
        self.back_btn = IconButton("arrow-left", "All Sessions", tone="secondary")
        self.timer_btn = IconButton("timer", "Listen for a Set Time", tone="secondary")
        self.shuffle_btn = IconButton("shuffle", "Shuffle Queue", tone="secondary")
        self.clear_btn = IconButton("trash", "Clear Queue", tone="secondary")
        self.familiar_btn = IconButton("heart", "Familiar", size=24, icon_size=14, tone="secondary")
        self.dial = Slider(100, 50, step=10, name="Discovery")
        self.dial.setFixedWidth(96)
        self.dial.setToolTip("Discovery: familiar ↔ unexpected")
        self.dial.set_value(float(settings["discovery"]) * 100)
        self.unexpected_btn = IconButton("compass", "Unexpected", size=24, icon_size=14, tone="secondary")
        self.refresh_btn = IconButton("refresh", "Refresh Recommendations", tone="secondary")
        self.add_recs_btn = IconButton("queue-add", "Add All to Queue", tone="secondary")
        self.clear_hist_btn = IconButton("trash", "Clear History", tone="secondary")
        self.play_session_btn = IconButton("play", "Play This Session", tone="secondary")
        self.save_session_btn = IconButton("list", "Save as Playlist", tone="secondary")
        self.lyrics_btn = IconButton("refresh", "Search Lyrics Again", tone="secondary")
        rv.addLayout(hbox(self.hist_mode, self.back_btn, self.count, self.mode_end_btn, None,
                          self.familiar_btn, self.dial, self.unexpected_btn, 6,
                          self.timer_btn, self.shuffle_btn, self.clear_btn, self.refresh_btn, self.add_recs_btn,
                          self.clear_hist_btn, self.play_session_btn, self.save_session_btn, self.lyrics_btn,
                          spacing=4))

        self.stack = QStackedWidget()
        self.queue_list = TrackListView("queue", "Your queue is empty", "Add songs from Search or your Library.", reorderable=True)
        self.recs_list = TrackListView("sparkles", "No recommendations yet", "Play a song to see related music.")
        self.hist_list = TrackListView("history", "No history yet", "")
        self.sessions = SessionListView()
        self.session_list = TrackListView("clock", "Empty session", "")
        self.hist_stack = QStackedWidget()
        for v in (self.hist_list, self.sessions, self.session_list):
            self.hist_stack.addWidget(v)
        self.lyrics = LyricsView()
        for v in (self.queue_list, self.recs_list, self.hist_stack, self.lyrics):
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
        self._countdown = QTimer(self)               # the listening timer's remaining time
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._update_toolbar)
        self._sessions_dirty = True
        self._wire()
        self.tabs.set_index(settings["panel_tab"])
        self._show_tab(settings["panel_tab"])
        self._on_current(self.pb.current)
        self._refresh_queue()
        self._refresh_recs()
        self._refresh_history()
        self._on_mode()

    def _wire(self) -> None:
        pb, a = self.pb, self.actions
        self.tabs.changed.connect(self._show_tab)
        self.shuffle_btn.clicked.connect(pb.shuffle)
        self.clear_btn.clicked.connect(pb.clear)
        self.timer_btn.clicked.connect(self._timer_menu)
        self.mode_end_btn.clicked.connect(self._end_mode)
        self.refresh_btn.clicked.connect(lambda: pb.refresh_recommendations(force=True))
        self.add_recs_btn.clicked.connect(self._add_all_recs)
        self.dial.committed.connect(lambda v: pb.set_discovery(v / 100))
        self.familiar_btn.clicked.connect(lambda: self._set_dial(0))
        self.unexpected_btn.clicked.connect(lambda: self._set_dial(100))
        self.clear_hist_btn.clicked.connect(pb.clear_history)
        self.hist_mode.changed.connect(self._show_history_mode)
        self.back_btn.clicked.connect(lambda: self._show_history_mode(1))
        self.play_session_btn.clicked.connect(lambda: self._play_session(self._session_id))
        self.save_session_btn.clicked.connect(lambda: self._save_session(self._session_id))
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

        for view in (self.hist_list, self.session_list):
            view.actions = [
                RowAction("queue-add", "Add to Queue", lambda r, v=view: a.enqueue([v.tracks()[r]])),
                RowAction("heart", "Save", lambda r, v=view: a.toggle_saved(v.tracks()[r]), saved),
            ]
            view.activated_row.connect(lambda r, v=view: pb.play_now(v.tracks()[r].copy()))
            view.context_requested.connect(
                lambda rows, pos, v=view: a.menu(self, [v.tracks()[r] for r in rows]).exec(pos))
        self.sessions.opened.connect(self._open_session)
        self.sessions.context_requested.connect(self._session_menu)

        pb.current_changed.connect(self._on_current)
        pb.source_changed.connect(lambda _l: self._sync_source())
        pb.queue_changed.connect(self._refresh_queue)
        pb.recommendations_changed.connect(self._refresh_recs)
        pb.history_changed.connect(self._refresh_history)
        pb.recommending.connect(lambda _b: self._update_toolbar())
        pb.mode_changed.connect(self._on_mode)
        pb.state_changed.connect(lambda s: self.art.set_loading(s == LOADING))
        self.library.changed.connect(self.recs_list.viewport().update)
        self.library.changed.connect(self.hist_list.viewport().update)
        self.library.changed.connect(self.session_list.viewport().update)
        if self.listening is not None:
            self.listening.changed.connect(self._sessions_changed)

    # ---- queue: album mode + listening timer ----------------------------------

    def _on_mode(self) -> None:
        if self.pb.timer is not None:
            self._countdown.start()
        else:
            self._countdown.stop()
        self._update_toolbar()

    def _end_mode(self) -> None:
        if self.pb.timer is not None:
            self.pb.cancel_timer()
        elif self.pb.album is not None:
            self.pb.end_album()

    def _timer_menu(self) -> None:
        m = QMenu(self)
        c = theme.color("label")
        for minutes in TIMER_PRESETS:
            m.addAction(icons.icon("timer", c, 16), f"{minutes} min", lambda s=minutes * 60: self.start_timer(s))
        m.addAction("Custom…", self._custom_timer)
        m.addSeparator()
        src = self.settings["timer_source"]
        for key, text in (("library", "From Library"), ("queue", "From Queue")):
            act = m.addAction(text, lambda k=key: self.settings.__setitem__("timer_source", k))
            if key == src:
                act.setIcon(icons.icon("check", theme.color("accent"), 16))
        if self.pb.timer is not None:
            m.addSeparator()
            m.addAction(icons.icon("close", c, 16), "Stop Timer", self.pb.cancel_timer)
        m.exec(self.timer_btn.mapToGlobal(QPoint(0, self.timer_btn.height() + 4)))

    def _custom_timer(self) -> None:
        text = prompt(self, "Listen For", "Minutes, or minutes:seconds (31:25)", "Start")
        if text is None:
            return
        secs = timefit.parse_duration(text)
        if secs is None:
            self.actions.toast("warning", "Try a length like 30 or 31:25")
            return
        self.start_timer(secs)

    def start_timer(self, seconds: float) -> None:
        """Fill ``seconds`` with songs from the library (or the queue) and start."""
        from_queue = self.settings["timer_source"] == "queue"
        pool = list(self.pb.queue) if from_queue else list(self.library.tracks)
        overlap = float(self.settings["crossfade_secs"]) if self.pb.settings["crossfade"] else 0.0
        chosen = timefit.fit(pool, seconds, overlap, keep_order=from_queue)
        if not chosen and from_queue:              # empty queue: recommendations alone
            chosen = timefit.fit([t for t in self.pb.recommendations if t.duration], seconds, overlap)
        if not chosen:
            self.actions.toast("warning", "Not enough songs with a known length")
            return
        total = timefit.playing_time(chosen, overlap)
        short = seconds - total >= 60
        self.pb.start_timer(chosen, seconds, top_up=from_queue and short)
        if from_queue and short:
            self.actions.toast("info", f"{len(chosen)} songs from the queue · topping up with recommendations")
        elif total < seconds * 0.9:
            where = "queue" if from_queue else "Library"
            self.actions.toast("warning", f"Only {format_duration(total)} of music in your {where}")
        else:
            self.actions.toast("success", f"{len(chosen)} songs · {format_duration(total)}")
        self.tabs.set_index(QUEUE, emit=True)

    # ---- for you: discovery ------------------------------------------------------

    def _set_dial(self, v: float) -> None:
        self.dial.set_value(v)
        self.pb.set_discovery(v / 100)

    # ---- history: songs and sessions ------------------------------------------

    def _show_history_mode(self, i: int) -> None:
        self.hist_mode.set_index(i)
        self._session_id = None
        if i == 1 and self._sessions_dirty:
            self._refresh_sessions()
        self.hist_stack.setCurrentIndex(i)
        self._update_toolbar()

    def _sessions_changed(self) -> None:
        self._sessions_dirty = True
        if self.isVisible() and self.tabs.index() == HISTORY and self.hist_stack.currentIndex() == 1:
            self._refresh_sessions()
        elif self._session_id and self.listening and self.listening.current \
                and self._session_id == self.listening.current.id:
            self._open_session(self._session_id)       # the live session grew

    def _refresh_sessions(self) -> None:
        if self.listening is None:
            return
        self._sessions_dirty = False
        live = self.listening.current.id if self.listening.current else None
        self.sessions.set_sessions(self.listening.all_sessions(), live)

    def _open_session(self, sid: str) -> None:
        s = self.listening.get(sid) if self.listening else None
        if not s:
            return
        self._session_id = sid
        live = self.listening.current is s
        self.session_list.set_tracks(s.tracks)
        self.session_list.setToolTip("")
        self._session_caption = f"{s.title} · {span(s, live)}"
        self.hist_stack.setCurrentIndex(2)
        self._update_toolbar()

    def _session_menu(self, sid: str, pos) -> None:
        s = self.listening.get(sid) if self.listening else None
        if not s:
            return
        m = QMenu(self)
        c = theme.color("label")
        m.addAction(icons.icon("play", c, 16), "Play", lambda: self._play_session(sid))
        m.addAction(icons.icon("queue-add", c, 16), "Add to Queue", lambda: self.actions.enqueue(s.tracks))
        m.addAction(icons.icon("list", c, 16), "Save as Playlist…", lambda: self._save_session(sid))
        if self.listening.current is not s:
            m.addSeparator()
            m.addAction(icons.icon("trash", c, 16), "Remove Session", lambda: self._delete_session(sid))
        m.exec(pos)

    def _play_session(self, sid: str | None) -> None:
        s = self.listening.get(sid) if self.listening and sid else None
        if s:
            self.actions.play(s.tracks)

    def _save_session(self, sid: str | None) -> None:
        s = self.listening.get(sid) if self.listening and sid else None
        if s:
            name = prompt(self, "Save as Playlist", "Playlist name", "Save", s.title)
            if name:
                p = self.actions.playlists.create(name, s.tracks)
                self.actions.toast("success", f"Saved “{p.name}”")

    def _delete_session(self, sid: str) -> None:
        if confirm(self, "Remove this session?", "Its songs stay in your history and library.", "Remove", danger=True):
            self.listening.delete(sid)

    # ---- toolbar ---------------------------------------------------------------

    def _add_all_recs(self) -> None:
        if self.pb.recommendations:
            self.actions.enqueue(list(self.pb.recommendations))
            self.pb.recommendations = []
            self.pb.recommendations_changed.emit()

    def _sync_source(self) -> None:
        t = self.pb.current
        self.source.setVisible(bool(t and t.local_path and t.source != "local"))
        self.source.set_index(1 if self.pb.playing_local else 0)

    def _show_tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.settings["panel_tab"] = i
        if i == HISTORY and self.hist_stack.currentIndex() == 1 and self._sessions_dirty:
            self._refresh_sessions()
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        pb = self.pb
        tab = self.tabs.index()
        busy = tab == FOR_YOU and pb.recs_busy
        hist_page = self.hist_stack.currentIndex()
        end_tip = ""
        if tab == LYRICS:
            lyr = self.lyrics.lyrics
            text = f"{lyr.source} · {'Synced' if lyr.synced else 'Plain'}" if lyr else ""
        elif tab == QUEUE and pb.timer is not None:
            text = f"Timer · {format_duration(pb.timer_remaining())} left"
            end_tip = "Stop Timer"
        elif tab == QUEUE and pb.album is not None:
            text = f"Album · {pb.album['title']}"
            end_tip = "Leave Album Mode"
        elif tab == HISTORY and hist_page == 2:
            text = getattr(self, "_session_caption", "")
        elif tab == HISTORY and hist_page == 1:
            n = len(self.listening.all_sessions()) if self.listening else 0
            text = f"{n:,} session{'s' if n != 1 else ''}"
        else:
            n = [len(pb.queue), len(pb.recommendations), len(pb.history)][tab]
            text = "Finding…" if busy else f"{n:,} song{'s' if n != 1 else ''}"
        self.count.setText(text)
        self.mode_end_btn.setVisible(bool(end_tip))
        self.mode_end_btn.setToolTip(end_tip)

        shown = {
            self.timer_btn: tab == QUEUE, self.shuffle_btn: tab == QUEUE, self.clear_btn: tab == QUEUE,
            self.familiar_btn: tab == FOR_YOU, self.dial: tab == FOR_YOU, self.unexpected_btn: tab == FOR_YOU,
            self.refresh_btn: tab == FOR_YOU, self.add_recs_btn: tab == FOR_YOU,
            self.hist_mode: tab == HISTORY and hist_page != 2, self.back_btn: tab == HISTORY and hist_page == 2,
            self.clear_hist_btn: tab == HISTORY and hist_page == 0,
            self.play_session_btn: tab == HISTORY and hist_page == 2,
            self.save_session_btn: tab == HISTORY and hist_page == 2,
            self.lyrics_btn: tab == LYRICS,
        }
        for w, on in shown.items():
            w.setVisible(on)
        self.timer_btn.setChecked(pb.timer is not None)
        self.shuffle_btn.setEnabled(len(pb.queue) > 1)
        self.clear_btn.setEnabled(bool(pb.queue))
        self.refresh_btn.setEnabled(not busy and (pb.current is not None or bool(pb.history)))
        self.add_recs_btn.setEnabled(bool(pb.recommendations))
        self.clear_hist_btn.setEnabled(bool(pb.history))
        self.lyrics_btn.setEnabled(pb.current is not None and self.lyrics.state != "loading")

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

    # ---- updates -------------------------------------------------------------

    def _on_current(self, t) -> None:
        self.art.set_track(t)
        self.song.set_full_text(t.title if t else "")
        self.artist.set_full_text((f"{t.artist} · {t.source_label}" if t.artist else t.source_label) if t else "")
        self.open_btn.setVisible(t is not None)
        self.playlist_btn.setVisible(t is not None)
        self._sync_source()
        key = t.key if t else None
        for v in (self.queue_list, self.recs_list, self.hist_list, self.session_list):
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
