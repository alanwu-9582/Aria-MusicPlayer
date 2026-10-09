"""Library: saved tracks, downloads, playlist import / export, local files.
Shown as a list (for managing) or a Disc Rack (for browsing); both views share
the same filtered songs, selection and actions."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLineEdit, QStackedWidget

from aria import paths
from aria.core.library import AUDIO_EXTS, local_track
from aria.core.models import LOCAL
from aria.ui import icons
from aria.ui.pages.base import Page
from aria.ui.theme import theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import Button, IconButton, label, vline
from aria.ui.widgets.rack import RackPanel, RackView, track_items
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView

log = logging.getLogger(__name__)


class LibraryPage(Page):
    def __init__(self, library, downloader, actions, toast, settings=None):
        super().__init__("Library")
        self.settings = settings
        self.library = library
        self.downloader = downloader
        self.actions = actions
        self.toast = toast

        self.open_btn = Button("Add Files…", "borderless", icon="plus")
        self.folder_btn = Button("Downloads", "borderless", icon="folder")
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.AUDIO_DIR))))
        self.import_btn = Button("Import…", "borderless", icon="import")
        self.export_btn = Button("Export…", "borderless", icon="export")
        for b in (self.folder_btn, self.open_btn, self.import_btn, self.export_btn):
            self.header.addWidget(b)
        self.open_btn.clicked.connect(self.add_files)
        self.import_btn.clicked.connect(self.import_playlist)
        self.export_btn.clicked.connect(self.export_playlist)

        bar = self.toolbar()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(220)
        self._filter_icon = QAction(self.filter)
        self.filter.addAction(self._filter_icon, QLineEdit.ActionPosition.LeadingPosition)
        self.filter.textChanged.connect(self._refresh)
        self.play_btn = Button("Play", "primary", icon="play")
        self.queue_btn = Button("Add to Queue", icon="queue-add")
        self.download_btn = Button("Download", icon="download")
        self.remove_btn = Button("Remove", "danger", icon="trash")
        self.count = label("", "Caption")
        self.view_toggle = Segmented(["List", "Disc Rack"], compact=True)
        for w in (self.filter, vline(), self.play_btn, self.queue_btn, self.download_btn, vline(), self.remove_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.count)
        bar.addWidget(self.view_toggle)

        self.list = TrackListView("library", "Your library is empty", "Save songs with the heart in Search.")
        self.list.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.list.tracks()[r]])),
            RowAction("download", "Download", lambda r: actions.download([self.list.tracks()[r]]),
                      lambda t: bool(t.local_path)),
        ]
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:]))
        self.list.delete_pressed.connect(lambda _rows: self.remove())
        self.list.context_requested.connect(self._menu)
        self.list.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())

        # Disc Rack: the same songs as single CDs on shelves.
        self.rack = RackView()
        self.rack_panel = RackPanel(self.rack, ("library", "Your library is empty", "Save songs with the heart in Search."))
        self.rack.activated.connect(lambda i: actions.play(self.list.tracks()[i:]))
        self.rack.context.connect(lambda i, pos: self._menu([i], pos))
        self.rack.selection_changed.connect(self._rack_selected)
        strip = self.rack_panel.detail
        for icon, tip, fn in (("play", "Play", lambda: actions.play(self._targets())),
                              ("queue-add", "Add to Queue", lambda: actions.enqueue(self._targets())),
                              ("download", "Download", lambda: actions.download(self._targets()))):
            b = IconButton(icon, tip, tone="secondary")
            b.clicked.connect(fn)
            strip.buttons.addWidget(b)
        self._rack_dirty = True
        thumbs_mod.instance().ready.connect(lambda _k: self.rack.isVisible() and self.rack.refresh_covers())

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.rack_panel)
        self.root.addWidget(self.stack, 1)
        self.view_toggle.changed.connect(self._set_view)

        self.play_btn.clicked.connect(lambda: actions.play(self._targets()))
        self.queue_btn.clicked.connect(lambda: actions.enqueue(self._targets()))
        self.download_btn.clicked.connect(lambda: actions.download(self._targets()))
        self.remove_btn.clicked.connect(self.remove)

        library.changed.connect(self._refresh)
        library.track_updated.connect(self.list.model_.refresh_key)
        theme.changed.connect(self._retint)
        self._retint()
        self._refresh()
        start = 1 if settings is not None and settings["library_view"] == "rack" else 0
        self.view_toggle.set_index(start)
        self._set_view(start, save=False)

    def _retint(self) -> None:
        self._filter_icon.setIcon(icons.icon("search", theme.color("tertiary"), 15))

    def set_playing(self, key) -> None:
        self.list.set_playing(key)
        self.rack.set_playing(key)

    # ---- views ---------------------------------------------------------------

    @property
    def rack_mode(self) -> bool:
        return self.stack.currentIndex() == 1

    def _set_view(self, i: int, save: bool = True) -> None:
        """List ↔ Disc Rack, carrying the selection across."""
        if save and self.settings is not None:
            self.settings["library_view"] = "rack" if i == 1 else "list"
        if i == 1:
            sel = self.list.selected_tracks()
            self._sync_rack(sel[0].key if sel else None)
        else:
            j = self.rack.selected
            if 0 <= j < len(self.list.tracks()):
                self.list.clearSelection()
                self.list.setCurrentIndex(self.list.model_.index(j))
        self.stack.setCurrentIndex(i)
        self._update_buttons()

    def _sync_rack(self, keep: str | None = None) -> None:
        if keep is None and 0 <= self.rack.selected < len(self.rack.items):
            keep = self.rack.items[self.rack.selected].key
        self.rack.set_items(track_items(self.list.tracks()), keep)
        self._rack_dirty = False
        self._rack_selected(self.rack.selected)

    def _rack_selected(self, i: int) -> None:
        strip = self.rack_panel.detail
        tracks = self.list.tracks()
        if not (0 <= i < len(tracks)):
            strip.hide()
        else:
            t = tracks[i]
            from aria.core.models import format_duration
            bits = [t.artist, format_duration(t.duration) if t.duration else "", t.source_label,
                    "Downloaded" if t.local_path else ""]
            strip.show_item(self.rack.items[i].cover(), t.title, " · ".join(b for b in bits if b))
            strip.show()
        self._update_buttons()

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
        if self.rack_mode:
            self._sync_rack()
        else:
            self._rack_dirty = True
        total = len(self.library)
        self.count.setText(f"{len(tracks):,} of {total:,} songs" if q else f"{total:,} song{'s' if total != 1 else ''}")
        self._update_buttons()

    def _selection(self):
        if self.rack_mode:
            i = self.rack.selected
            return [self.list.tracks()[i]] if 0 <= i < len(self.list.tracks()) else []
        return self.list.selected_tracks()

    def _targets(self):
        return self._selection() or self.list.tracks()

    def _update_buttons(self) -> None:
        has_any = bool(self.list.tracks())
        sel = self._selection()
        self.play_btn.setEnabled(has_any)
        self.queue_btn.setEnabled(has_any)
        self.download_btn.setEnabled(any(t.source != LOCAL and not t.local_path for t in (sel or self.list.tracks())))
        self.remove_btn.setEnabled(bool(sel))
        self.export_btn.setEnabled(bool(self.library.tracks))

    def _menu(self, rows, pos) -> None:
        tracks = [self.list.tracks()[r] for r in rows]
        self.actions.menu(self, tracks, [("trash", "Remove from Library", self.remove)]).exec(pos)

    # ---- commands ------------------------------------------------------------

    def remove(self) -> None:
        tracks = self._selection()
        if not tracks:
            return
        name = f"“{tracks[0].title}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        keys = [t.key for t in tracks]
        self.actions.remove_with_files(self, tracks, f"Remove {name} from Library?",
                                       "Your queue and history stay as they are.",
                                       lambda: self.library.remove(keys, delete_files=False))

    def add_files(self) -> None:
        pattern = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add Music Files", "", f"Audio ({pattern})")
        if files:
            n = self.library.add([local_track(f) for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS])
            self.toast("success", f"Added {n} song{'s' if n != 1 else ''}")

    def import_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Playlist", "", "Playlist (*.json)")
        if not path:
            return
        try:
            n = self.library.import_file(path)
        except Exception as exc:
            log.error("Import failed: %s", exc)
            self.toast("danger", "Couldn’t read that file")
            return
        log.info("Imported %d songs (%s)", n, os.path.basename(path))
        self.toast("success", f"Imported {n} song{'s' if n != 1 else ''}")

    def export_playlist(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Library", "Aria Library.json", "Playlist (*.json)")
        if not path:
            return
        try:
            n = self.library.export_file(path)
        except OSError as exc:
            log.error("Export failed: %s", exc)
            self.toast("danger", "Couldn’t write the file")
            return
        log.info("Exported %d songs to %s", n, path)
        self.toast("success", f"Exported {n} song{'s' if n != 1 else ''}")
