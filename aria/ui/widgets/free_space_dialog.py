"""Free Up Space: pick which downloaded songs to delete. The songs stay where they are
(library, playlists, queue) and stream from then on; only the files go."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from aria.core import cache
from aria.core.models import Track
from aria.ui.player_bar import ElidedLabel
from aria.ui.widgets.controls import Button, CheckBox, hbox, label
from aria.ui.widgets.tracklist import plain_scroll


class FileRow(QFrame):
    toggled = Signal()

    def __init__(self, track: Track, size: int):
        super().__init__()
        self.track, self.size = track, size
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 8, 2)
        lay.setSpacing(10)
        self.check = CheckBox("")
        self.check.setFixedWidth(20)
        self.check.toggled.connect(lambda _on: self.toggled.emit())
        lay.addWidget(self.check)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = ElidedLabel()
        title.set_full_text(track.title)
        sub = ElidedLabel("Caption")
        sub.set_full_text(" · ".join(x for x in (track.artist, track.source_label) if x))
        col.addWidget(title)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        lay.addWidget(label(cache.human(size), "Secondary"))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.check.toggle()                 # the whole row is the checkbox


class FreeSpaceDialog(QDialog):
    def __init__(self, parent, files: list[tuple[Track, str]]):
        """``files``: (song, its downloaded file)."""
        super().__init__(parent)
        self.setWindowTitle(" ")
        self.resize(620, 600)
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(10)
        col.addWidget(label("Free Up Space", "Title2"))
        col.addWidget(label("Delete downloaded files. The songs stay in your Library and playlists and stream "
                            "when played.", "Secondary"))

        self.all = CheckBox("Select All")
        self.all.clicked.connect(self._select_all)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(200)
        self.filter.textChanged.connect(self._filter)
        self.summary = label("", "Secondary")
        col.addLayout(hbox(self.all, None, self.summary, self.filter))

        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        self.rows: list[FileRow] = []
        sized = []
        for t, path in files:
            try:
                sized.append((t, os.path.getsize(path)))
            except OSError:
                sized.append((t, 0))
        for t, size in sorted(sized, key=lambda x: -x[1]):          # the biggest first
            r = FileRow(t, size)
            r.toggled.connect(self._update)
            rows.addWidget(r)
            self.rows.append(r)
        rows.addStretch(1)
        scroll = plain_scroll()
        scroll.setWidget(body)
        col.addWidget(scroll, 1)

        cancel = Button("Cancel")
        cancel.clicked.connect(self.reject)
        self.delete_btn = Button("Delete", "destructive")
        self.delete_btn.clicked.connect(self.accept)
        col.addLayout(hbox(None, cancel, self.delete_btn))
        self._update()

    def selected(self) -> list[Track]:
        return [r.track for r in self.rows if r.check.isChecked()]

    def _select_all(self) -> None:
        on = self.all.isChecked()
        for r in self.rows:
            if r.isVisible() or not on:
                r.check.blockSignals(True)
                r.check.setChecked(on and r.isVisible())
                r.check.blockSignals(False)
        self._update()

    def _filter(self, text: str) -> None:
        q = text.strip().casefold()
        for r in self.rows:
            r.setVisible(not q or q in r.track.title.casefold() or q in r.track.artist.casefold())

    def _update(self) -> None:
        chosen = [r for r in self.rows if r.check.isChecked()]
        total = sum(r.size for r in self.rows)
        size = sum(r.size for r in chosen)
        n = len(self.rows)
        self.summary.setText(f"{n} song{'s' if n != 1 else ''} · {cache.human(total)}")
        self.delete_btn.setEnabled(bool(chosen))
        self.delete_btn.setText(f"Delete {len(chosen)} · {cache.human(size)}" if chosen else "Delete")
        self.all.blockSignals(True)
        self.all.setChecked(bool(chosen) and len(chosen) == n)
        self.all.blockSignals(False)
