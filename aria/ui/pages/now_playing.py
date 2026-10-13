"""Now playing: artwork canvas on the left, queue / recommendations / history / lyrics on the right (§6.3).

New tools live inside these tabs rather than on pages of their own:
- Queue: the listening timer (Time-Fitted Playlist) and album mode status.
- For You: the Discovery dial (familiar ↔ unexpected).
- History: songs, or listening sessions as cards.
- Lyrics: click a line to play from there.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLineEdit, QMenu, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from aria.core import identity
from aria.core import lyrics as lyrics_mod
from aria.core import tasks, timefit
from aria.core.models import format_duration
from aria.core.player import LOADING
from aria.ui import icons
from aria.ui.pages.base import TintedPage
from aria.ui.player_bar import ElidedLabel
from aria.ui.theme import theme
from aria.ui.widgets.artwork import Artwork
from aria.ui.widgets.controls import Button, IconButton, add_item, hbox, label, menu_below
from aria.ui.widgets.dialogs import confirm, prompt
from aria.ui.widgets.lyrics_view import LyricsView
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.sessions import SessionListView, span
from aria.ui.widgets.slider import Slider
from aria.ui.widgets.tracklist import RowAction, TrackListView

lyr_log = logging.getLogger("lyrics")

PANEL_MIN = 380
QUEUE, FOR_YOU, HISTORY, LYRICS = range(4)
TIMER_PRESETS = (25, 45, 60)
MAX_OFFSET = 999.0                 # seconds the lyrics can be shifted either way


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
        self.open_btn = IconButton("external", "Open Source", tone="secondary")
        self.open_btn.clicked.connect(lambda: self.pb.current and self.actions.open_source(self.pb.current))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(self.song)
        info.addWidget(self.artist)
        self.source = Segmented(["Stream", "Local"], compact=True)
        self.source.changed.connect(lambda i: self.pb.switch_source(i == 1))
        self.playlist_btn = IconButton("list", "Add to Playlist", tone="secondary")
        self.immersive_btn = IconButton("expand", "Immersive Mode (F11)", tone="secondary")
        row = hbox(info, self.source, self.playlist_btn, self.immersive_btn, self.open_btn, spacing=12)
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
        self.undo_btn = IconButton("undo", "Undo Arrange", tone="secondary")
        self.shuffle_btn = IconButton("shuffle", "Shuffle Queue", tone="secondary")
        self.familiar_btn = IconButton("heart", "Familiar", size=24, icon_size=14, tone="secondary", tip=True)
        self.dial = Slider(100, 50, step=10, name="Discovery")
        self.dial.setFixedWidth(96)
        self.dial.setToolTip("Discovery: familiar ↔ unexpected")
        self.dial.set_value(float(settings["discovery"]) * 100)
        self.unexpected_btn = IconButton("compass", "Unexpected", size=24, icon_size=14, tone="secondary")
        self.refresh_btn = IconButton("refresh", "Refresh Recommendations", tone="secondary")
        self.play_session_btn = IconButton("play", "Play This Session", tone="secondary")
        self.save_session_btn = IconButton("list", "Save as Playlist", tone="secondary")
        self.earlier_btn = IconButton("minus", "Lyrics Earlier (0.5 s)", tone="secondary")
        self.offset_edit = QLineEdit()                 # the timing shift, typed in seconds (any amount)
        self.offset_edit.setFixedWidth(74)
        self.offset_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.offset_edit.setToolTip("Lyrics timing in seconds: type an amount, Enter to apply")
        self.offset_edit.setFrame(False)               # just the number: no box around it
        self.offset_edit.setObjectName("PlainField")
        self.offset_edit.setStyleSheet("QLineEdit#PlainField { border: none; background: transparent; padding: 0; }")
        self.later_btn = IconButton("plus", "Lyrics Later (0.5 s)", tone="secondary")
        self.more_btn = IconButton("more", "More", tone="secondary")
        self.plain_btn = Button("Use Plain", "borderless")
        self.align_btn = IconButton("waveform", "Align Again", tone="secondary")
        rv.addLayout(hbox(self.hist_mode, self.back_btn, self.count, self.mode_end_btn, None,
                          self.familiar_btn, self.dial, self.unexpected_btn, 6,
                          self.undo_btn, self.shuffle_btn, self.refresh_btn,
                          self.play_session_btn, self.save_session_btn,
                          self.plain_btn, self.align_btn, self.earlier_btn, self.offset_edit, self.later_btn, self.more_btn,
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
        self.lyric_prefs = lyrics_mod.prefs()
        self._plain_lyrics: lyrics_mod.Lyrics | None = None       # what an alignment was made from
        self._aligning: str | None = None                         # song being aligned right now
        self._align_note = "Aligning…"
        self._temp_audio: dict[str, str] = {}                     # throwaway copies made to align streamed songs → song
        self._temp_busy: str | None = None                        # the one being aligned right now
        self._clear_temp_audio(everything=True)                   # left over from last time
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
        self.undo_btn.clicked.connect(pb.undo_arrange)
        self.mode_end_btn.clicked.connect(self._end_mode)
        self.refresh_btn.clicked.connect(lambda: pb.refresh_recommendations(force=True))
        self.dial.committed.connect(lambda v: pb.set_discovery(v / 100))
        self.familiar_btn.clicked.connect(lambda: self._set_dial(0))
        self.unexpected_btn.clicked.connect(lambda: self._set_dial(100))
        self.hist_mode.changed.connect(self._show_history_mode)
        self.back_btn.clicked.connect(lambda: self._show_history_mode(1))
        self.play_session_btn.clicked.connect(lambda: self._play_session(self._session_id))
        self.save_session_btn.clicked.connect(lambda: self._save_session(self._session_id))
        self.earlier_btn.clicked.connect(lambda: self._nudge_offset(-0.5))
        self.later_btn.clicked.connect(lambda: self._nudge_offset(0.5))
        self.offset_edit.editingFinished.connect(self._typed_offset)
        self.more_btn.clicked.connect(self._more)
        self.plain_btn.clicked.connect(self._use_plain)
        self.align_btn.clicked.connect(self._align_again)
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
        pb.arrange_progress.connect(lambda _d, _t: self._update_toolbar())
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

    def _more(self) -> None:
        """The tab's less-used actions."""
        pb, m = self.pb, QMenu(self)
        tab = self.tabs.index()
        if tab == QUEUE:
            self._fill_timer_menu(m.addMenu(icons.icon("timer", theme.color("label"), 16), "Listen for a Set Time"))
            add_item(m, "waveform", "Arrange by Tempo", self._arrange_by_tempo, len(pb.queue) > 1 and pb.tempo is not None)
            m.addSeparator()
            add_item(m, "trash", "Clear Queue", pb.clear, bool(pb.queue))
        elif tab == FOR_YOU:
            add_item(m, "queue-add", "Add All to Queue", self._add_all_recs, bool(pb.recommendations))
        elif tab == HISTORY:
            add_item(m, "trash", "Clear History", pb.clear_history, bool(pb.history))
        else:
            add_item(m, "refresh", "Search Lyrics Again", lambda: self._load_lyrics(force=True),
                     pb.current is not None and self.lyrics.state != "loading")
            lyr = self.lyrics.lyrics
            add_item(m, "undo", "Reset Timing", lambda: self._nudge_offset(None), bool(lyr and lyr.offset))
            m.addSeparator()
            add_item(m, "list", "Choose Lyrics…", self._choose_lyrics, pb.current is not None)
            add_item(m, "pencil", "Edit Song Info…", lambda: self.actions.edit_info and self.actions.edit_info(pb.current),
                     pb.current is not None)
        menu_below(self.more_btn, m)

    def _fill_timer_menu(self, m: QMenu) -> None:
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

    def _arrange_by_tempo(self) -> None:
        known = self.pb.arrange_by_tempo()
        total = len(self.pb.queue)
        if known >= total:
            self.actions.toast("success", "Queue arranged by tempo")
        else:
            self.actions.toast("info", f"Arranged by tempo · {total - known} song{'s' if total - known != 1 else ''} still being measured")

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
            text = lyr.label if lyr else ""
            if lyr is not None and not lyr.synced and self.pb.current and self._aligning == self.pb.current.key:
                text += f" · {self._align_note}"
        elif tab == QUEUE and pb.measuring[1]:
            done, total = pb.measuring
            text = f"Measuring tempo · {done} / {total}"
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
        self.mode_end_btn.set_tip(end_tip)

        shown = {
            self.shuffle_btn: tab == QUEUE, self.undo_btn: tab == QUEUE and pb.can_undo_arrange,
            self.familiar_btn: tab == FOR_YOU, self.dial: tab == FOR_YOU, self.unexpected_btn: tab == FOR_YOU,
            self.refresh_btn: tab == FOR_YOU,
            self.hist_mode: tab == HISTORY and hist_page != 2, self.back_btn: tab == HISTORY and hist_page == 2,
            self.play_session_btn: tab == HISTORY and hist_page == 2,
            self.save_session_btn: tab == HISTORY and hist_page == 2,
            self.more_btn: not (tab == HISTORY and hist_page != 0),
        }
        lyr = self.lyrics.lyrics if self.lyrics.state == "ready" else None
        timed = tab == LYRICS and lyr is not None and lyr.synced
        shown.update({self.earlier_btn: timed, self.later_btn: timed, self.offset_edit: timed,
                      self.plain_btn: timed and lyr.sync == lyrics_mod.ALIGNED})
        alignable = tab == LYRICS and lyr is not None and (lyr.sync == lyrics_mod.ALIGNED or not lyr.synced) \
            and self._plain_lyrics is not None and self._can_align(self.pb.current)
        shown[self.align_btn] = alignable
        self.align_btn.set_tip("Align Again" if lyr is not None and lyr.synced else "Align")
        self.align_btn.setEnabled(self._aligning is None)
        if lyr is not None and not self.offset_edit.hasFocus():
            self.offset_edit.setText(f"{lyr.offset:+.1f} s")      # shown even at +0.0 s
        for w, on in shown.items():
            w.setVisible(on)
        self.shuffle_btn.setEnabled(len(pb.queue) > 1)
        self.refresh_btn.setEnabled(not busy and (pb.current is not None or bool(pb.history)))


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
            lyr_log.info("Lyrics · “%s” · %s", t.title, found.label if found is not None else "none found")
            if self._lyrics_ticket.is_current(ticket):
                if found is not None:
                    found.offset = self.lyric_prefs.offset(t.key)
                self.lyrics.set_state("ready" if found else "none", found)
                self.lyrics.set_position(self.pb.player.position())
                self._update_toolbar()
                if found is not None and not found.synced:
                    self._maybe_align(t, found, ticket)

        def failed(_e):
            if self._lyrics_ticket.is_current(ticket):
                self.lyrics.set_state("none")
                self._update_toolbar()

        tasks.run(lambda: lyrics_mod.fetch(t, enhanced, self.pb.recommender, force=force), done, failed)

    def _choose_lyrics(self) -> None:
        t = self.pb.current
        if t is None:
            return
        from aria.ui.widgets.song_info import choose_lyrics
        res = choose_lyrics(self, t, bool(self.lyric_prefs.choice(t.key)))
        if not res:
            return
        kind, c = res
        lyrics_mod.choose(t, c if kind == "apply" else None)
        self._load_lyrics(force=True)

    def _nudge_offset(self, step: float | None) -> None:
        """Shift the timing for this song (None = back to as found); remembered per song."""
        lyr, t = self.lyrics.lyrics, self.pb.current
        if lyr is None or t is None:
            return
        self._apply_offset(0.0 if step is None else lyr.offset + step)

    def _typed_offset(self) -> None:
        text = self.offset_edit.text().replace("s", "").replace("秒", "").replace(",", ".").strip()
        try:
            value = float(text)
        except ValueError:
            lyr = self.lyrics.lyrics                   # not a number: show the current shift again
            self.offset_edit.setText(f"{lyr.offset:+.1f} s" if lyr else "")
            return
        self._apply_offset(value)

    def _apply_offset(self, seconds: float) -> None:
        lyr, t = self.lyrics.lyrics, self.pb.current
        if lyr is None or t is None:
            return
        lyr.offset = max(-MAX_OFFSET, min(MAX_OFFSET, round(seconds, 1)))
        self.lyric_prefs.set_offset(t.key, lyr.offset)
        self.offset_edit.setText(f"{lyr.offset:+.1f} s")
        self.lyrics.set_position(self.pb.player.position())
        self.lyrics.changed.emit()                      # Immersive Mode shows the same lyrics
        self._update_toolbar()

    def _can_align(self, t) -> bool:
        """Forced Alignment is on and ready, and this song is on disk (or streamed songs may be
        downloaded for it)."""
        import os
        from aria.core import aligner
        if t is None or not self.settings["lyrics_align"] or not aligner.installed(self.settings["align_model"]):
            return False
        on_disk = bool(t.local_path and os.path.exists(t.local_path))
        return (on_disk or (bool(self.settings["align_streams"]) and t.source != "local")) and aligner.engine_available()

    def _maybe_align(self, t, plain, ticket, again: bool = False) -> None:
        """Plain lyrics: time them with the speech model (when turned on) — from the file on disk,
        or from a temporary download of a streamed song (when that's turned on too)."""
        import os
        self._plain_lyrics = plain
        if not again:
            known = lyrics_mod.cached_alignment(t, plain)
            if known is not None and not self.lyric_prefs.align_off(t.key) and self.settings["lyrics_align"]:
                self._show_aligned(known, ticket)
                return
            if self.lyric_prefs.align_off(t.key):
                return
        if not self._can_align(t):
            return
        copy = next((p for p, k in self._temp_audio.items() if k == t.key and os.path.exists(p)), None)
        if t.local_path and os.path.exists(t.local_path):
            self._run_align(t, plain, ticket, t.local_path)
        elif copy:
            self._run_align(t, plain, ticket, copy, temp=True)      # downloaded already for this song
        else:
            self._align_from_download(t, plain, ticket)

    def _align_again(self) -> None:
        """Time this song's lyrics afresh (also after Use Plain)."""
        t, plain = self.pb.current, self._plain_lyrics
        if t is None or plain is None or self._aligning is not None:
            return
        self.lyric_prefs.set_align_off(t.key, False)
        self.lyrics.set_state("ready", plain)              # the words untimed while it listens again
        self._maybe_align(t, plain, self._lyrics_ticket.current(), again=True)
        self._update_toolbar()

    def _run_align(self, t, plain, ticket, path: str, temp: bool = False) -> None:
        from aria.core import aligner
        words = [s for _t, s in plain.lines]
        from aria.core import langid
        lang = aligner.WHISPER_LANG.get(langid.detect(" ".join(w for w in words[:24] if w)).lang)   # unknown: let it listen
        model = self.settings["align_model"]
        self._aligning, self._align_note = t.key, "Aligning…"
        self._temp_busy = path if temp else None
        self._update_toolbar()

        def work():
            # its own idle-priority process: playback and the window never wait for it
            return aligner.worker().submit(aligner.align_file, path, words, model, lang).result(timeout=1800)

        def finish(result):
            if self._aligning == t.key:
                self._aligning = None
            if temp:
                self._temp_busy = None
                if result:
                    result = dict(result, temp=True)          # made from a copy that's about to go
            lyr_log.info("Aligned · “%s” · %d%% of lines", t.title, round(100 * result.get("confidence", 0)))                  if result else lyr_log.warning("Couldn’t align “%s”", t.title)
            if result:
                aligned = lyrics_mod.save_alignment(t, plain, result)
                if self._lyrics_ticket.is_current(ticket):
                    self._show_aligned(aligned, ticket)
            self._update_toolbar()
            self._clear_temp_audio()

        tasks.run(work, finish, lambda e: finish(None))

    def _align_from_download(self, t, plain, ticket) -> None:
        """A streamed song: download a temporary copy while it plays, align that, and delete the
        copy once the song is over."""
        from aria import paths, providers
        self._aligning, self._align_note = t.key, "Downloading to align…"
        self._update_toolbar()
        folder = paths.CACHE_DIR / "align_audio"

        def done(path):
            self._temp_audio[path] = t.key
            still = self.pb.current is not None and self.pb.current.key == t.key
            if not still:                                    # the song is over already: nothing to time now
                if self._aligning == t.key:
                    self._aligning = None
                self._update_toolbar()
                self._clear_temp_audio()
                return
            self._run_align(t, plain, ticket, path, temp=True)

        def failed(e):
            lyr_log.warning("Couldn’t download “%s” to align: %s", t.title, e)
            if self._aligning == t.key:
                self._aligning = None
            self._update_toolbar()

        lyr_log.info("Downloading “%s” to align its lyrics", t.title)
        tasks.run(lambda: providers.download_to(t, folder), done, failed)

    def _clear_temp_audio(self, everything: bool = False) -> None:
        """Delete the temporary copies of songs that are over (the playing song keeps its copy until
        it ends). ``everything``: the whole folder (at start)."""
        import os
        import shutil
        from aria import paths
        if everything:
            shutil.rmtree(paths.CACHE_DIR / "align_audio", ignore_errors=True)
            return
        playing = self.pb.current.key if self.pb.current else None
        for path, key in list(self._temp_audio.items()):
            if path == self._temp_busy or key == playing:
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                continue                                     # still open somewhere: next time
            self._temp_audio.pop(path, None)

    def _show_aligned(self, aligned, ticket) -> None:
        t = self.pb.current
        if not self._lyrics_ticket.is_current(ticket) or t is None:
            return
        aligned.offset = self.lyric_prefs.offset(t.key)
        self.lyrics.set_state("ready", aligned)
        self.lyrics.set_position(self.pb.player.position())
        self._update_toolbar()

    def _use_plain(self) -> None:
        """This song's aligned timing is wrong: keep its plain lyrics from now on."""
        t = self.pb.current
        if t is None or self._plain_lyrics is None:
            return
        self.lyric_prefs.set_align_off(t.key, True)
        self.lyrics.set_state("ready", self._plain_lyrics)
        self._update_toolbar()

    # ---- updates -------------------------------------------------------------

    def show_bpm(self) -> None:
        """The playing song's line, with its BPM as quiet extra detail when Tempo Flow knows it."""
        t = self.pb.current
        if not t:
            self.artist.set_full_text("")
            return
        bits = [identity.display(t)[1], t.source_label]
        info = self.pb.tempo.get(t) if self.pb.tempo is not None and self.settings["tempo_flow"] else None
        if info and info[1] >= 0.35:
            bits.append(f"{round(info[0])} BPM")
        self.artist.set_full_text(" · ".join(b for b in bits if b))

    def _on_current(self, t) -> None:
        self.art.set_track(t)
        self.song.set_full_text(identity.display(t)[0] if t else "")
        self.show_bpm()
        self.open_btn.setVisible(t is not None)
        self.playlist_btn.setVisible(t is not None)
        self._sync_source()
        key = t.key if t else None
        for v in (self.queue_list, self.recs_list, self.hist_list, self.session_list):
            v.set_playing(key)
        if self._temp_audio:
            self._clear_temp_audio()                      # the song it was made for is over
        if key != self._lyrics_for:
            self._plain_lyrics = None
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
