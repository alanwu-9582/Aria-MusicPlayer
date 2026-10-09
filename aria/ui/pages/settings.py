"""Settings (§4.11 group boxes, §6.4 field layout). Changes apply immediately."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QGridLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from aria import paths
from aria.ui.pages.base import Page
from aria.ui.widgets.controls import Button, Toggle, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import reserve_scrollbar

CROSSFADE_SECS = [3, 6, 9, 12]
APPEARANCES = ["system", "light", "dark"]


class Group(QFrame):
    """Group box: content background, 1 px border, radius 10, headline title only."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("Group")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        lay.addWidget(label(title, "Headline"))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        lay.addLayout(self.grid)
        self._row = 0

    def pair(self, a: QWidget, b: QWidget | None = None) -> None:
        """Two half-width controls on one row (or one spanning the row)."""
        for w in (a, b):
            if w is not None:
                w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if b is None:
            self.grid.addWidget(a, self._row, 0, 1, 2)
        else:
            self.grid.addWidget(a, self._row, 0)
            self.grid.addWidget(b, self._row, 1)
        self._row += 1

    def field(self, title: str, control: QWidget) -> None:
        box = QVBoxLayout()
        box.setSpacing(5)
        box.addWidget(label(title, "Secondary"))
        box.addWidget(control)
        self.grid.addLayout(box, self._row, 0, 1, 2)
        self._row += 1


class SettingsPage(Page):
    def __init__(self, settings, window):
        super().__init__("Settings")
        self.settings = settings
        self.w = window

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        reserve_scrollbar(scroll)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 12, 0)
        col.setSpacing(12)
        scroll.setWidget(body)
        self.root.addWidget(scroll, 1)

        # ---- playback
        play = Group("Playback")
        self.crossfade = self._toggle("Crossfade", "crossfade", "Blend the end of a song into the next")
        self.soft = self._toggle("Soft Resume", "soft_resume", "After a long pause, fade back in")
        play.pair(self.crossfade, self.soft)
        self.auto = Toggle("Autoplay", "Keep playing related songs when the queue ends")
        self.auto.setChecked(window.playback.autoplay)
        self.auto.toggled.connect(window.player_bar.auto_btn.setChecked)
        window.player_bar.auto_btn.toggled.connect(self.auto.setChecked)
        self.local = self._toggle("Switch to Downloads", "auto_local", "Play the file once a download finishes")
        play.pair(self.auto, self.local)
        self.fade_len = Segmented([f"{s} s" for s in CROSSFADE_SECS])
        cur = settings["crossfade_secs"]
        self.fade_len.set_index(CROSSFADE_SECS.index(cur) if cur in CROSSFADE_SECS else 1)
        self.fade_len.changed.connect(self._set_fade_len)
        play.field("Crossfade Length", self.fade_len)
        col.addWidget(play)

        # ---- lyrics
        lyr = Group("Lyrics")
        self.lyrics_enh = self._toggle("Deep Lyrics Search", "lyrics_enhanced",
                                       "Also look in video descriptions and comments")
        lyr.pair(self.lyrics_enh)
        col.addWidget(lyr)

        # ---- appearance
        look = Group("Appearance")
        self.appearance = Segmented(["System", "Light", "Dark"])
        self.appearance.set_index(APPEARANCES.index(settings["appearance"]))
        self.appearance.changed.connect(lambda i: window.set_appearance(APPEARANCES[i]))
        look.field("Theme", self.appearance)
        self.smart = self._toggle("Smart Artwork", "smart_artwork", "Tint the background from the cover")
        look.pair(self.smart)
        col.addWidget(look)

        # ---- window
        win = Group("Window")
        self.tray = self._toggle("Keep Playing in Tray", "close_to_tray", "Closing the window keeps the music going")
        mini = Button("Mini Player", icon="mini")
        mini.clicked.connect(window.show_mini)
        win.pair(self.tray, mini)
        col.addWidget(win)

        # ---- data
        data = Group("Data")
        downloads = Button("Downloads Folder", icon="folder")
        downloads.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.AUDIO_DIR))))
        folder = Button("Data Folder", icon="folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.DATA_DIR))))
        data.pair(downloads, folder)
        col.addWidget(data)
        col.addStretch(1)
        body.setMaximumWidth(620)

        self.crossfade.toggled.connect(lambda on: self.fade_len.setEnabled(on))
        self.fade_len.setEnabled(self.crossfade.isChecked())

    def _toggle(self, text: str, key: str, tip: str) -> Toggle:
        t = Toggle(text, tip)
        t.setChecked(bool(self.settings[key]))
        t.toggled.connect(lambda on: self._set(key, on))
        return t

    def _set(self, key: str, value) -> None:
        self.settings[key] = value
        self.w.settings_changed(key)

    def _set_fade_len(self, i: int) -> None:
        self._set("crossfade_secs", CROSSFADE_SECS[i])

    def sync_appearance(self, appearance: str) -> None:
        self.appearance.set_index(APPEARANCES.index(appearance))
