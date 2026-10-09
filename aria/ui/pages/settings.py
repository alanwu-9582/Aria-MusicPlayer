"""Settings (§4.11 group boxes, §6.4 field layout). Changes apply immediately."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QGridLayout, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from aria import paths
from aria.core.balance import DEFAULT_EXCLUDED
from aria.ui.pages.base import Page
from aria.ui.widgets.controls import Button, CheckBox, IconButton, Toggle, hbox, label
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

        # ---- audio: Volume Balance
        self.balance_apps: AppList | None = None
        bal = getattr(window, "balance", None)
        if bal is not None and bal.available:
            audio = Group("Audio")
            self.balance_mode = Segmented(["All Apps Except Excluded", "Only Chosen Apps"])
            self.balance_mode.set_index(1 if settings["balance_mode"] == "only" else 0)
            audio.field("Volume Balance turns down", self.balance_mode)
            self.balance_apps = AppList(settings, bal)
            self.apps_title = label("", "Secondary")
            box = QVBoxLayout()
            box.setSpacing(5)
            box.addWidget(self.apps_title)
            box.addWidget(self.balance_apps)
            audio.grid.addLayout(box, audio._row, 0, 1, 2)
            audio._row += 1
            self.balance_mode.changed.connect(self._set_balance_mode)
            self._set_balance_mode(self.balance_mode.index(), save=False)
            col.addWidget(audio)

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
        self.motion = self._toggle("Reduce Motion", "reduce_motion", "Hover effects change instantly")
        look.pair(self.smart, self.motion)
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
        self.cache_btn = Button("Clear Cache", icon="trash",
                                tooltip="Covers, lyrics and stream data; your music stays")
        self.cache_btn.clicked.connect(window.clear_cache)
        self.cache_size = label("", "Secondary")
        data.pair(self.cache_btn, self.cache_size)
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

    def _set_balance_mode(self, i: int, save: bool = True) -> None:
        if save:
            self._set("balance_mode", "only" if i == 1 else "exclude")
        self.apps_title.setText("Chosen Apps" if i == 1 else "Excluded Apps")
        self.apps_title.setToolTip("" if i == 1 else "Call apps (Discord, Teams, Zoom…) are excluded unless unticked")
        self.balance_apps.rebuild()

    def update_cache_size(self) -> None:
        from aria.core import cache, tasks
        self.cache_size.setText("…")
        tasks.run(cache.size, lambda n: self.cache_size.setText(f"Cache: {cache.human(n)}"),
                  lambda _e: self.cache_size.setText(""))

    def on_shown(self) -> None:
        self.update_cache_size()
        if self.balance_apps is not None:
            self.balance_apps.rebuild(rescan=True)

    def sync_appearance(self, appearance: str) -> None:
        self.appearance.set_index(APPEARANCES.index(appearance))


class AppList(QWidget):
    """Apps for Volume Balance: ticked = excluded (or, in "only" mode, managed).
    Lists the apps making sound right now plus every app already named."""

    def __init__(self, settings, balance):
        super().__init__()
        self.settings = settings
        self.balance = balance
        self._seen: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(14)
        self.rows.setVerticalSpacing(0)
        lay.addLayout(self.rows)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Add an app, e.g. zoom.exe")
        self.field.returnPressed.connect(self._add)
        add = Button("Add", icon="plus")
        add.clicked.connect(self._add)
        scan = IconButton("refresh", "Find Apps Playing Sound", tone="secondary")
        scan.clicked.connect(lambda: self.rebuild(rescan=True))
        lay.addSpacing(6)
        lay.addLayout(hbox(self.field, add, scan))

    def _key(self) -> str:
        return "balance_only" if self.settings["balance_mode"] == "only" else "balance_excluded"

    def _chosen(self) -> list[str]:
        return self.balance.excluded() if self._key() == "balance_excluded" else list(self.settings["balance_only"])

    def rebuild(self, rescan: bool = False) -> None:
        if rescan or not self._seen:
            self._seen = self.balance.apps()
        chosen = self._chosen()
        # Apps making sound now, plus any the user named; built-in call apps that
        # aren't running stay excluded without crowding the list.
        named = {c.lower() for c in chosen} - set(DEFAULT_EXCLUDED)
        names = sorted(set(self._seen) | named)
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, name in enumerate(names):
            box = CheckBox(name.removesuffix(".exe"))
            box.setToolTip(name)
            box.setChecked(name in {c.lower() for c in chosen})
            box.toggled.connect(lambda on, n=name: self._toggle(n, on))
            self.rows.addWidget(box, i // 2, i % 2)

    def _toggle(self, name: str, on: bool) -> None:
        chosen = [c.lower() for c in self._chosen()]
        if on and name not in chosen:
            chosen.append(name)
        elif not on and name in chosen:
            chosen.remove(name)
        self.settings[self._key()] = chosen
        self.balance.set_value(self.balance.value)       # re-apply with the new list

    def _add(self) -> None:
        name = self.field.text().strip().lower()
        if not name:
            return
        if not name.endswith(".exe"):
            name += ".exe"
        self.field.clear()
        self._toggle(name, True)
        self.rebuild()
