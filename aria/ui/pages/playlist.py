"""One playlist: play, reorder, rename, delete. Also shows Smart Collections,
which fill themselves from rules and so are read-only here. Songs show as a
list or as a Disc Rack (same songs, selection and actions)."""

from __future__ import annotations

import random

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QStackedWidget

from aria.core import smart
from aria.core.models import format_duration
from aria.ui.pages.base import Page
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import Button, IconButton, label
from aria.ui.widgets.rack import RackPanel, RackView, track_items
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.dialogs import confirm, prompt
from aria.ui.widgets.smart_dialog import edit_smart
from aria.ui.widgets.tracklist import RowAction, TrackListView


class PlaylistPage(Page):
    def __init__(self, playlists, actions, toast, library=None, listening=None, settings=None):
        super().__init__("Playlist")
        self.settings = settings
        self.playlists = playlists
        self.actions = actions
        self.toast = toast
        self.library = library
        self.listening = listening
        self.pid: str | None = None
        self.smart_id: str | None = None

        self.rules_btn = Button("Edit Rules…", "borderless", icon="smart")
        self.rename_btn = Button("Rename…", "borderless", icon="pencil")
        self.delete_btn = Button("Delete…", "danger", icon="trash")
        self.header.addWidget(self.rules_btn)
        self.header.addWidget(self.rename_btn)
        self.header.addWidget(self.delete_btn)
        self.rules_btn.clicked.connect(self.edit_rules)
        self.rename_btn.clicked.connect(self.rename)
        self.delete_btn.clicked.connect(self.delete)

        bar = self.toolbar()
        self.play_btn = Button("Play", "primary", icon="play")
        self.shuffle_btn = Button("Shuffle", icon="shuffle")
        self.queue_btn = Button("Add to Queue", icon="queue-add")
        self.count = label("", "Caption")
        for w in (self.play_btn, self.shuffle_btn, self.queue_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.count)
        self.view_toggle = Segmented(["List", "Disc Rack"], compact=True)
        bar.addWidget(self.view_toggle)

        self.list = TrackListView("list", "This playlist is empty", "Right-click any song › Add to Playlist.", reorderable=True)
        self._own_actions = [RowAction("close", "Remove from Playlist (Delete)", lambda r: self._remove([r]))]
        saved = lambda t: self.library is not None and t.key in self.library  # noqa: E731
        self._smart_actions = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.list.tracks()[r]])),
            RowAction("heart", "Save", lambda r: actions.toggle_saved(self.list.tracks()[r]), saved),
        ]
        self.list.actions = self._own_actions
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:]))
        self.list.delete_pressed.connect(lambda rows: self.smart_id is None and self._remove(rows))
        self.list.moved.connect(lambda s, d: self.pid and playlists.move(self.pid, s, d))
        self.list.context_requested.connect(self._menu)

        self.rack = RackView()
        self.rack_panel = RackPanel(self.rack, ("list", "Nothing here yet", ""))
        self.rack.activated.connect(lambda i: actions.play(self.list.tracks()[i:]))
        self.rack.context.connect(lambda i, pos: self._menu([i], pos))
        self.rack.selection_changed.connect(self._rack_selected)
        strip = self.rack_panel.detail
        for icon, tip, fn in (("play", "Play from Here", lambda: self._rack_do(lambda ts, i: actions.play(ts[i:]))),
                              ("queue-add", "Add to Queue", lambda: self._rack_do(lambda ts, i: actions.enqueue([ts[i]])))):
            b = IconButton(icon, tip, tone="secondary")
            b.clicked.connect(fn)
            strip.buttons.addWidget(b)
        thumbs_mod.instance().ready.connect(lambda _k: self.rack.isVisible() and self.rack.refresh_covers())
        self.stack = QStackedWidget()
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.rack_panel)
        self.root.addWidget(self.stack, 1)
        self.view_toggle.changed.connect(self._set_view)
        if settings is not None and settings["playlist_view"] == "rack":
            self.view_toggle.set_index(1)
            self.stack.setCurrentIndex(1)

        self.play_btn.clicked.connect(lambda: actions.play(self.list.selected_tracks() or self.list.tracks()))
        self.shuffle_btn.clicked.connect(self._shuffle)
        self.queue_btn.clicked.connect(lambda: actions.enqueue(self.list.selected_tracks() or self.list.tracks()))
        playlists.playlist_changed.connect(lambda pid: pid in (self.pid, self.smart_id) and self.refresh())
        playlists.changed.connect(self.refresh)

        # Smart Collections follow the library and play stats (coalesced).
        self._smart_timer = QTimer(self)
        self._smart_timer.setSingleShot(True)
        self._smart_timer.setInterval(400)
        self._smart_timer.timeout.connect(lambda: self.smart_id and self.refresh())
        if library is not None:
            library.changed.connect(self._smart_timer.start)
            library.track_updated.connect(lambda _k: self._smart_timer.start())
        if listening is not None:
            listening.stats_changed.connect(self._smart_timer.start)

    # ---- showing -------------------------------------------------------------

    def show_playlist(self, pid: str) -> None:
        self.pid, self.smart_id = pid, None
        self.list.reorderable = True
        self.list.actions = self._own_actions
        self.list.empty = ("list", "This playlist is empty", "Right-click any song › Add to Playlist.")
        self.refresh()
        self.list.scrollToTop()
        self.rack_panel.scroll.verticalScrollBar().setValue(0)

    def show_smart(self, sid: str) -> None:
        self.pid, self.smart_id = None, sid
        self.list.reorderable = False
        self.list.actions = self._smart_actions
        self.list.empty = ("smart", "Nothing matches yet", "Songs appear here as they fit the rules.")
        self.refresh()
        self.list.scrollToTop()
        self.rack_panel.scroll.verticalScrollBar().setValue(0)

    def matches(self, sc: smart.SmartCollection) -> list:
        lib = self.library.tracks if self.library is not None else []
        played = self.listening.played_tracks() if self.listening is not None else []
        stats = self.listening.stats if self.listening is not None else {}
        return smart.evaluate(sc, lib, played, stats)

    def refresh(self) -> None:
        if self.smart_id:
            sc = self.playlists.get_smart(self.smart_id)
            if not sc:
                return
            tracks = self.matches(sc)
            self.title.setText(sc.name)
            rules = (" · " if sc.match == "all" else " or ").join(smart.describe(r) for r in sc.rules)
            caption = f"{len(tracks):,} song{'s' if len(tracks) != 1 else ''}"
            self.count.setText(f"{rules} — {caption}" if rules else caption)
        else:
            p = self.playlists.get(self.pid) if self.pid else None
            if not p:
                return
            tracks = p.tracks
            self.title.setText(p.name)
            total = sum(t.duration for t in tracks)
            self.count.setText(f"{len(tracks):,} song{'s' if len(tracks) != 1 else ''} · {format_duration(total)}"
                               if tracks else "")
        self.rules_btn.setVisible(self.smart_id is not None)
        self.rename_btn.setVisible(self.smart_id is None)
        self.list.set_tracks(tracks)
        if self.stack.currentIndex() == 1:
            self._sync_rack()
        for b in (self.play_btn, self.shuffle_btn, self.queue_btn):
            b.setEnabled(bool(tracks))

    def set_playing(self, key) -> None:
        self.list.set_playing(key)
        self.rack.set_playing(key)

    # ---- views ---------------------------------------------------------------

    def _set_view(self, i: int) -> None:
        if self.settings is not None:
            self.settings["playlist_view"] = "rack" if i == 1 else "list"
        if i == 1:
            sel = self.list.selected_tracks()
            self._sync_rack(sel[0].key if sel else None)
        elif 0 <= self.rack.selected < len(self.list.tracks()):
            self.list.clearSelection()
            self.list.setCurrentIndex(self.list.model_.index(self.rack.selected))
        self.stack.setCurrentIndex(i)

    def _sync_rack(self, keep: str | None = None) -> None:
        if keep is None and 0 <= self.rack.selected < len(self.rack.items):
            keep = self.rack.items[self.rack.selected].key
        self.rack.set_items(track_items(self.list.tracks()), keep)
        self._rack_selected(self.rack.selected)

    def _rack_selected(self, i: int) -> None:
        strip = self.rack_panel.detail
        tracks = self.list.tracks()
        if not (0 <= i < len(tracks)):
            strip.hide()
            return
        t = tracks[i]
        bits = [t.artist, format_duration(t.duration) if t.duration else "", t.source_label,
                f"{i + 1} of {len(tracks)}"]
        strip.show_item(self.rack.items[i].cover(), t.title, " · ".join(b for b in bits if b))
        strip.show()

    def _rack_do(self, fn) -> None:
        i = self.rack.selected
        if 0 <= i < len(self.list.tracks()):
            fn(self.list.tracks(), i)

    # ---- edits ---------------------------------------------------------------

    def _menu(self, rows, pos) -> None:
        tracks = [self.list.tracks()[r] for r in rows]
        extra = None if self.smart_id else [("close", "Remove from Playlist", lambda: self._remove(rows))]
        self.actions.menu(self, tracks, extra).exec(pos)

    def _remove(self, rows) -> None:
        if not self.pid:
            return
        tracks = [self.list.tracks()[r] for r in rows]
        name = f"“{tracks[0].title}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        pid = self.pid
        # Plain removals are instant; the dialog only appears to offer deleting downloads.
        self.actions.remove_with_files(self, tracks, f"Remove {name} from this playlist?",
                                       "They stay in your Library and queue.",
                                       lambda: self.playlists.remove(pid, rows), confirm_always=False)

    def _shuffle(self) -> None:
        tracks = list(self.list.tracks())
        random.shuffle(tracks)
        self.actions.play(tracks)

    def rename(self) -> None:
        p = self.playlists.get(self.pid) if self.pid else None
        if p:
            name = prompt(self, "Rename Playlist", "Playlist name", "Save", p.name)
            if name:
                self.playlists.rename(p.id, name)

    def edit_rules(self) -> None:
        sc = self.playlists.get_smart(self.smart_id) if self.smart_id else None
        if not sc:
            return
        edited = edit_smart(self, sc, lambda c: len(self.matches(c)))
        if edited:
            sc.name, sc.rules, sc.match, sc.scope = edited.name, edited.rules, edited.match, edited.scope
            self.playlists.update_smart(sc)

    def delete(self) -> None:
        if self.smart_id:
            sc = self.playlists.get_smart(self.smart_id)
            if sc and confirm(self, f"Delete “{sc.name}”?", "Only the collection goes; its songs stay where they are.",
                              "Delete", danger=True):
                self.playlists.delete_smart(sc.id)
            return
        p = self.playlists.get(self.pid) if self.pid else None
        if p:
            self.window().delete_playlist(p.id)
