"""Library: saved tracks, downloads, playlist import / export, local files.
Shown as a list (for managing), a Disc Rack or a Collage of covers (for browsing); all three
share the same filtered songs, selection and actions. Switching keeps the chosen song and the
place: the song stays chosen (and in view), or the view opens where the last one was."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QPoint, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMenu, QStackedWidget, QWidget

from aria import paths
from aria.core.library import AUDIO_EXTS, local_track
from aria.core.models import LOCAL
from aria.ui import icons
from aria.ui.pages.base import Page
from aria.ui.theme import theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import Button, IconButton, add_item, hbox, label, menu_below
from aria.ui.widgets.cover_wall import CoverWall
from aria.ui.widgets.rack import RackPanel, RackView
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView

log = logging.getLogger(__name__)

VIEWS = ("list", "rack", "collage")


class LibraryPage(Page):
    def __init__(self, library, downloader, actions, toast, settings=None):
        super().__init__("Library")
        self.settings = settings
        self.library = library
        self.downloader = downloader
        self.actions = actions
        self.toast = toast

        bar = self.toolbar()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(220)
        self._filter_icon = QAction(self.filter)
        self.filter.addAction(self._filter_icon, QLineEdit.ActionPosition.LeadingPosition)
        self.filter.textChanged.connect(self._refresh)
        self.play_btn = Button("Play", "primary", icon="play")
        self.more_btn = IconButton("more", "More", tone="secondary")
        self.more_btn.clicked.connect(self._more)
        self.count = label("", "Caption")
        self.view_toggle = Segmented(["List", "Disc Rack", "Collage"], compact=True)
        for w in (self.filter, self.play_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.count)
        bar.addWidget(self.view_toggle)
        bar.addWidget(self.more_btn)

        self.list = TrackListView("library", "Your library is empty", "Save songs with the heart in Search.")
        self.list.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.list.tracks()[r]]), actions.queued),
            RowAction("download", "Download", lambda r: actions.download([self.list.tracks()[r]]),
                      lambda t: bool(t.local_path)),
        ]
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:r + 1]))
        self.list.drag_out = True
        self.list.delete_pressed.connect(lambda _rows: self.remove())
        self.list.context_requested.connect(self._menu)
        self.list.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())

        # Disc Rack: the same songs as single CDs on shelves; Collage: a wall of their covers.
        empty = ("library", "Your library is empty", "Save songs with the heart in Search.")
        self.rack = RackView()
        self.wall = CoverWall()
        self.rack_panel = RackPanel(self.rack, empty)
        self.wall_panel = RackPanel(self.wall, empty)
        for view, panel in ((self.rack, self.rack_panel), (self.wall, self.wall_panel)):
            view.activated.connect(lambda i: actions.play(self.list.tracks()[i:i + 1]))
            view.drag_tracks = lambda i: [self.list.tracks()[i]] if 0 <= i < len(self.list.tracks()) else []
            view.context.connect(lambda i, pos: self._menu([i], pos))
            view.selection_changed.connect(self._rack_selected)
            panel.add_buttons((("play", "Play", lambda: actions.play(self._targets())),
                               ("queue-add", "Add to Queue", lambda: actions.enqueue(self._targets())),
                               ("download", "Download", lambda: actions.download(self._targets()))))
        self.rack.reorderable = True
        self.rack.moved.connect(self._rack_moved)
        thumbs_mod.instance().ready.connect(lambda _k: self.rack_mode and self._board().refresh_covers())

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.rack_panel)
        self.stack.addWidget(self.wall_panel)
        self.root.addWidget(self.stack, 1)
        self.view_toggle.changed.connect(self._set_view)

        # appears only while songs from here are being dragged
        from aria.ui.widgets import dragdrop
        self.to_queue = dragdrop.DropZone("queue-add", "Add to Queue")
        self.to_queue.dropped.connect(actions.enqueue)
        self.to_trash = dragdrop.DropZone("trash", "Remove from Library", danger=True)
        self.to_trash.dropped.connect(self._remove_tracks)
        # laid over the foot of the view (never in the layout): nothing moves while songs are carried
        self.drop_bar = QWidget(self)
        self.drop_bar.setLayout(hbox(self.to_queue, self.to_trash, spacing=12))
        self.drop_bar.layout().setContentsMargins(0, 0, 0, 0)
        self.drop_bar.hide()
        dragdrop.bus.started.connect(self._show_drop_bar)
        dragdrop.bus.finished.connect(self.drop_bar.hide)

        self.play_btn.clicked.connect(lambda: actions.play(self._targets()))

        library.changed.connect(self._refresh)
        library.track_updated.connect(self.list.model_.refresh_key)
        # a download finished (or a file went): the rack's offline marks follow
        library.track_updated.connect(lambda _k: self.rack_mode and self._sync_rack())
        theme.changed.connect(self._retint)
        self._retint()
        self._refresh()
        start = VIEWS.index(settings["library_view"]) if settings is not None and settings["library_view"] in VIEWS else 0
        self.view_toggle.set_index(start)
        self._set_view(start, save=False)

    def _show_drop_bar(self, src) -> None:
        if src in (self.list, self.rack, self.wall):
            self._place_drop_bar()
            self.drop_bar.show()
            self.drop_bar.raise_()

    def _place_drop_bar(self) -> None:
        g = self.stack.geometry()
        self.drop_bar.setGeometry(g.left(), g.bottom() - 52 + 1, g.width(), 52)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.drop_bar.isVisible():
            self._place_drop_bar()

    def _retint(self) -> None:
        self._filter_icon.setIcon(icons.icon("search", theme.color("tertiary"), 15))

    def set_playing(self, key) -> None:
        self.list.set_playing(key)
        self.rack.set_playing(key)
        self.wall.set_playing(key)

    # ---- views ---------------------------------------------------------------

    @property
    def rack_mode(self) -> bool:
        """A board of covers is showing (Disc Rack or Collage), not the list."""
        return self.stack.currentIndex() != 0

    def _board(self):
        """The Disc Rack or the cover wall, whichever shows (or was last shown)."""
        return self.wall if self.stack.currentIndex() == 2 else self.rack

    def _panel(self):
        return self.wall_panel if self.stack.currentIndex() == 2 else self.rack_panel

    def _place(self) -> tuple[list[str], str | None]:
        """What to carry to the next view: the chosen songs, and the first song in sight."""
        tracks = self.list.tracks()
        if not self.rack_mode:
            chosen = [t.key for t in self.list.selected_tracks()]
            top = self.list.indexAt(QPoint(8, 8)).row()
        else:
            board = self._board()
            chosen = [tracks[board.selected].key] if 0 <= board.selected < len(tracks) else []
            top = board.index_at_y(self._panel().scroll.verticalScrollBar().value())
        return chosen, (tracks[top].key if 0 <= top < len(tracks) else None)

    def _set_view(self, i: int, save: bool = True) -> None:
        """List ↔ Disc Rack ↔ Collage: the chosen song stays chosen and in sight; with none, the new
        view opens where the old one was."""
        if save and self.settings is not None:
            self.settings["library_view"] = VIEWS[i]
        chosen, top = self._place()
        keys = [t.key for t in self.list.tracks()]
        self.stack.setCurrentIndex(i)
        if i == 0:
            self.list.clearSelection()
            for n, k in enumerate(chosen):
                if k in keys:
                    ix = self.list.model_.index(keys.index(k))
                    if n == 0:
                        self.list.setCurrentIndex(ix)
                    self.list.selectionModel().select(ix, self.list.selectionModel().SelectionFlag.Select)
            if chosen and chosen[0] in keys:
                self.list.scrollTo(self.list.model_.index(keys.index(chosen[0])))
            elif top in keys:
                self.list.scrollTo(self.list.model_.index(keys.index(top)), self.list.ScrollHint.PositionAtTop)
        else:
            board = self._board()
            self._sync_rack(chosen[0] if chosen else None)
            bar = self._panel().scroll.verticalScrollBar()

            def place():
                if board.selected >= 0:
                    board.ensure_visible(board.selected)
                elif top in keys:
                    bar.setValue(board.top_of(keys.index(top)))
            QTimer.singleShot(0, place)                 # once the board has its size
        self._update_buttons()

    def _rack_moved(self, src: int, dst: int) -> None:
        # only the whole, unfiltered library can be put in a new order
        if not self.filter.text().strip():
            self.library.move(src, dst)

    def _sync_rack(self, keep: str | None = None) -> None:
        self._panel().sync_tracks(self.list.tracks(), self.actions.local_path_of, keep)
        self._rack_selected(self._board().selected)

    def _rack_selected(self, i: int) -> None:
        self._panel().show_track(self.list.tracks(), i)
        self._update_buttons()

    # ---- list ----------------------------------------------------------------

    def _refresh(self) -> None:
        q = self.filter.text().strip().casefold()
        tracks = [t for t in self.library.tracks
                  if not q or q in t.title.casefold() or q in t.artist.casefold()]
        self.list.set_tracks(tracks)                 # (the chosen songs stay chosen)
        self.rack.reorderable = not q
        if self.rack_mode:
            self._sync_rack()
        total = len(self.library)
        self.count.setText(f"{len(tracks):,} of {total:,} songs" if q else f"{total:,} song{'s' if total != 1 else ''}")
        self._update_buttons()

    def _selection(self):
        if self.rack_mode:
            i = self._board().selected
            return [self.list.tracks()[i]] if 0 <= i < len(self.list.tracks()) else []
        return self.list.selected_tracks()

    def _targets(self):
        return self._selection() or self.list.tracks()

    def _update_buttons(self) -> None:
        has_any = bool(self.list.tracks())
        sel = self._selection()
        self.play_btn.setEnabled(has_any)
        self.play_btn.setText("Play Selected" if len(sel) > 1 else "Play")

    def _more(self) -> None:
        """The less-used actions: on the selection (or everything shown), then the library itself."""
        m = QMenu(self)
        sel, targets = self._selection(), self._targets()
        a = self.actions
        add_item(m, "queue-add", "Add to Queue", lambda: a.enqueue(targets), bool(targets))
        add_item(m, "download", "Download", lambda: a.download(targets),
                 any(t.source != LOCAL and not t.local_path for t in targets))
        add_item(m, "trash", "Remove from Library", self.remove, bool(sel))
        m.addSeparator()
        add_item(m, "plus", "Add Music Files…", self.add_files)
        add_item(m, "import", "Import Playlist…", self.import_playlist)
        add_item(m, "export", "Export Playlist…", self.export_playlist, bool(self.library.tracks))
        add_item(m, "folder", "Open Downloads Folder",
                 lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.AUDIO_DIR))))
        menu_below(self.more_btn, m)

    def _menu(self, rows, pos) -> None:
        tracks = [self.list.tracks()[r] for r in rows]
        self.actions.menu(self, tracks, [("trash", "Remove from Library", self.remove)]).exec(pos)

    # ---- commands ------------------------------------------------------------

    def _remove_tracks(self, tracks) -> None:
        """Dropped on Remove: gone at once (asked only about deleting downloaded files)."""
        keys = [t.key for t in tracks]
        name = f"“{tracks[0].title}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        self.actions.remove_with_files(self, tracks, f"Remove {name} from Library?",
                                       "Your queue and history stay as they are.",
                                       lambda: self.library.remove(keys, delete_files=False), confirm_always=False)

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
