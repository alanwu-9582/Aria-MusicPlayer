"""One playlist: play, reorder, rename, delete."""

from __future__ import annotations

import random

from aria.core.models import format_duration
from aria.ui.pages.base import Page
from aria.ui.widgets.controls import Button, label
from aria.ui.widgets.dialogs import prompt
from aria.ui.widgets.tracklist import RowAction, TrackListView


class PlaylistPage(Page):
    def __init__(self, playlists, actions, toast):
        super().__init__("Playlist")
        self.playlists = playlists
        self.actions = actions
        self.toast = toast
        self.pid: str | None = None

        self.rename_btn = Button("Rename…", "borderless", icon="pencil")
        self.delete_btn = Button("Delete…", "danger", icon="trash")
        self.header.addWidget(self.rename_btn)
        self.header.addWidget(self.delete_btn)
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

        self.list = TrackListView("list", "This playlist is empty", "Right-click any song › Add to Playlist.", reorderable=True)
        self.list.actions = [RowAction("close", "Remove from Playlist (Delete)", lambda r: self._remove([r]))]
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:]))
        self.list.delete_pressed.connect(self._remove)
        self.list.moved.connect(lambda s, d: self.pid and playlists.move(self.pid, s, d))
        self.list.context_requested.connect(
            lambda rows, pos: actions.menu(self, [self.list.tracks()[r] for r in rows],
                                           [("close", "Remove from Playlist", lambda: self._remove(rows))]).exec(pos))
        self.root.addWidget(self.list, 1)

        self.play_btn.clicked.connect(lambda: actions.play(self.list.selected_tracks() or self.list.tracks()))
        self.shuffle_btn.clicked.connect(self._shuffle)
        self.queue_btn.clicked.connect(lambda: actions.enqueue(self.list.selected_tracks() or self.list.tracks()))
        playlists.playlist_changed.connect(lambda pid: pid == self.pid and self.refresh())
        playlists.changed.connect(self.refresh)

    def show_playlist(self, pid: str) -> None:
        self.pid = pid
        self.refresh()
        self.list.scrollToTop()

    def refresh(self) -> None:
        p = self.playlists.get(self.pid) if self.pid else None
        if not p:
            return
        self.title.setText(p.name)
        self.list.set_tracks(p.tracks)
        total = sum(t.duration for t in p.tracks)
        self.count.setText(f"{len(p.tracks):,} song{'s' if len(p.tracks) != 1 else ''} · {format_duration(total)}" if p.tracks else "")
        for b in (self.play_btn, self.shuffle_btn, self.queue_btn):
            b.setEnabled(bool(p.tracks))

    def set_playing(self, key) -> None:
        self.list.set_playing(key)

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

    def delete(self) -> None:
        p = self.playlists.get(self.pid) if self.pid else None
        if p:
            self.window().delete_playlist(p.id)

