"""Settings (§4.11 group boxes, §6.4 field layout). Changes apply immediately.

The groups sit on one scrolling page; an outline on the left jumps to a group
and follows along as you scroll."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QSizePolicy, QVBoxLayout, QWidget)

from aria import paths
from aria.core.balance import DEFAULT_EXCLUDED
from aria.ui.pages.base import Page
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import Button, IconButton, MenuButton, Toggle, hbox, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import plain_scroll

CONSOLE_CLEAR = [200, 500, 1000, 0]      # messages; 0 = never

CROSSFADE_SECS = [3, 6, 9, 12]
APPEARANCES = ["system", "light", "dark"]
TAP_SYNC = ["off", "on", "smooth"]                 # Double-Click to Sync
SNAPSHOT_EVERY = ["off", "daily", "weekly"]


class Group(QFrame):
    """Group box: content background, 1 px border, radius 10, headline title only."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("Group")
        self.title = title
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

    def field(self, title: str, control) -> None:
        box = QVBoxLayout()
        box.setSpacing(5)
        box.addWidget(label(title, "Secondary"))
        if isinstance(control, QWidget):
            box.addWidget(control)
        else:
            box.addLayout(control)
        self.grid.addLayout(box, self._row, 0, 1, 2)
        self._row += 1


class OutlineItem(QAbstractButton):
    """One entry of the settings outline: text, with an accent mark when it's the section in view."""

    def __init__(self, text: str):
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._hover = False
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(font("callout", 600)).horizontalAdvance(self.text()) + 28, 30)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover and not self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("fill"))
            p.drawRoundedRect(r, 7, 7)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(2, 8, 3, r.height() - 16), 1, 1)
        p.setFont(font("callout", 600 if self.isChecked() else 400))
        p.setPen(theme.color("label" if self.isChecked() else "secondary"))
        p.drawText(r.adjusted(14, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class SettingsPage(Page):
    def __init__(self, settings, window):
        super().__init__("Settings")
        self.settings = settings
        self.w = window
        self.groups: list[Group] = []

        row = QHBoxLayout()
        row.setSpacing(20)
        self.outline = QVBoxLayout()
        self.outline.setSpacing(2)
        self.outline.setContentsMargins(0, 0, 0, 0)
        outline_box = QWidget()
        outline_box.setFixedWidth(150)
        outline_box.setLayout(self.outline)
        row.addWidget(outline_box, 0, Qt.AlignmentFlag.AlignTop)
        self.scroll = scroll = plain_scroll()
        body = QWidget()
        col = self.col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 12, 0)
        col.setSpacing(12)
        scroll.setWidget(body)
        row.addWidget(scroll, 1)
        self.root.addLayout(row, 1)

        # ---- playback
        play = self._group("Playback")
        self.crossfade = self._toggle("Crossfade", "crossfade", "Blend the end of a song into the next")
        self.soft = self._toggle("Soft Resume", "soft_resume", "After a long pause, fade back in")
        play.pair(self.crossfade, self.soft)
        self.auto = Toggle("Autoplay", "Keep playing related songs when the queue ends")
        self.auto.setChecked(window.playback.autoplay)
        self.auto.toggled.connect(window.player_bar.auto_btn.setChecked)
        window.player_bar.auto_btn.toggled.connect(self.auto.setChecked)
        self.local = self._toggle("Switch to Downloads", "auto_local", "Play the file once a download finishes")
        play.pair(self.auto, self.local)
        self.resume = self._toggle("Resume Where I Left Off", "resume_on_start",
                                   "When Aria starts, continue the song it was closed on, from that moment")
        play.pair(self.resume)
        self.fade_len = Segmented([f"{s} s" for s in CROSSFADE_SECS])
        cur = settings["crossfade_secs"]
        self.fade_len.set_index(CROSSFADE_SECS.index(cur) if cur in CROSSFADE_SECS else 1)
        self.fade_len.changed.connect(self._set_fade_len)
        play.field("Crossfade Length", self.fade_len)
        self.tempo = self._toggle("Tempo Flow", "tempo_flow",
                                  "Recommended songs with a similar beat play one after another; the tempo drifts")
        self.tempo_strength = Segmented(["Gentle", "Balanced", "Strong"])
        strengths = ["gentle", "balanced", "strong"]
        cur = settings["tempo_strength"]
        self.tempo_strength.set_index(strengths.index(cur) if cur in strengths else 1)
        self.tempo_strength.changed.connect(lambda i: self._set("tempo_strength", strengths[i]))
        play.pair(self.tempo)
        play.field("Flow Strength", self.tempo_strength)
        scopes = ["recommendations", "playlists"]
        self.tempo_scope = Segmented(["Recommendations Only", "Include Playlists"])
        self.tempo_scope.set_index(scopes.index(settings["tempo_scope"]) if settings["tempo_scope"] in scopes else 0)
        self.tempo_scope.changed.connect(lambda i: self._set("tempo_scope", scopes[i]))
        play.field("Tempo Flow Scope", self.tempo_scope)
        for w in (self.tempo_strength, self.tempo_scope):
            self.tempo.toggled.connect(w.setEnabled)
            w.setEnabled(self.tempo.isChecked())

        # ---- audio: output device + per-device memory + Volume Balance
        audio = self._group("Audio")
        self.devices = getattr(window, "devices", None)
        if self.devices is not None:
            self.device_pick = MenuButton(["System Default"])
            self.device_pick.changed.connect(self._pick_device)
            audio.field("Output", self.device_pick)
            self.dev_profiles = self._toggle("Volume per Output", "device_profiles_on",
                                             "Each output keeps its own volume and balance")
            self.dev_fallback = self._toggle("Fall Back to Default", "device_fallback",
                                             "If the chosen output goes away, play on the default one")
            audio.pair(self.dev_profiles, self.dev_fallback)
            self.devices.devices_changed.connect(self._sync_devices)
            self._sync_devices()
        self.balance_apps: AppList | None = None
        bal = getattr(window, "balance", None)
        if bal is not None and bal.available:
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

        # ---- library: Collection Health
        lib = self._group("Library")
        check = Button("Check Library…", icon="check-circle",
                       tooltip="Stray downloads, duplicates, missing files and dead links")
        check.clicked.connect(window.check_health)
        lib.pair(check, label("Duplicates, missing files, stray downloads", "Caption"))

        # ---- backup: Library Snapshot
        backup = self._group("Backup")
        self.snap_every = Segmented(["Off", "Daily", "Weekly"])
        every = settings["snapshot_every"]
        self.snap_every.set_index(SNAPSHOT_EVERY.index(every) if every in SNAPSHOT_EVERY else 2)
        self.snap_every.changed.connect(lambda i: self._set("snapshot_every", SNAPSHOT_EVERY[i]))
        backup.field("Automatic Snapshots", self.snap_every)
        self.snap_list = QVBoxLayout()
        self.snap_list.setSpacing(0)
        backup.field("Snapshots", self.snap_list)
        now_btn = Button("Back Up Now", icon="plus")
        now_btn.clicked.connect(lambda: (window.snapshot_now("manual", announce=True), self.refresh_snapshots()))
        export_btn = Button("Export…", icon="export", tooltip="Everything in one file — to move Aria to another computer")
        export_btn.clicked.connect(window.export_backup)
        import_btn = Button("Import…", icon="import", tooltip="Restore from an exported backup")
        import_btn.clicked.connect(window.import_backup)
        backup.grid.addLayout(hbox(now_btn, export_btn, import_btn, None), backup._row, 0, 1, 2)
        backup._row += 1

        # ---- lyrics
        lyr = self._group("Lyrics")
        self.lyrics_enh = self._toggle("Deep Lyrics Search", "lyrics_enhanced",
                                       "Also look in video descriptions and comments")
        self.lyrics_align = self._toggle("Forced Alignment", "lyrics_align",
                                         "Downloaded songs with only plain lyrics: a speech model listens and times "
                                         "each line (shown as Synced · Aligned; plain lyrics stay when it can't tell)")
        lyr.pair(self.lyrics_enh, self.lyrics_align)
        self.align_streams = self._toggle("Align Streamed Songs", "align_streams",
                                          "Streamed songs without synced lyrics: download a temporary copy to align "
                                          "while it plays; the copy is deleted when the song ends")
        self.lyrics_align.toggled.connect(lambda _on: self._sync_align())
        lyr.pair(self.align_streams)
        self.lyrics_trans = self._toggle("Lyrics Translation", "lyrics_translation",
                                         "Show a line's translation under it, when the lyrics have one")
        self.words_all = self._toggle("Word-by-Word Everything", "lyrics_words_all",
                                      "Line-synced lyrics too: the song is analysed (a streamed one through a "
                                      "temporary copy) so every word is filled as it's sung")
        lyr.pair(self.lyrics_trans, self.words_all)
        self.tap_sync = Segmented(["Off", "On", "Smoothed"], compact=True)
        self.tap_sync.setToolTip("Double-click a line as it starts and the timing shifts to match. "
                                 "Smoothed: from the second time on, half the average of the earlier ones "
                                 "and half the latest")
        mode = settings["lyrics_tap_sync"]
        self.tap_sync.set_index(TAP_SYNC.index(mode) if mode in TAP_SYNC else 0)
        self.tap_sync.changed.connect(lambda i: self._set("lyrics_tap_sync", TAP_SYNC[i]))
        lyr.field("Double-Click to Sync", self.tap_sync)
        from aria.ui.widgets.dialogs import confirm
        from aria.ui.widgets.models_panel import ModelsPanel
        self.models = ModelsPanel(settings, window.models, confirm, window.toast, self._sync_align)
        lyr.field("Speech Models", self.models)
        self._sync_align()

        # ---- appearance
        look = self._group("Appearance")
        self.appearance = Segmented(["System", "Light", "Dark"])
        self.appearance.set_index(APPEARANCES.index(settings["appearance"]))
        self.appearance.changed.connect(lambda i: window.set_appearance(APPEARANCES[i]))
        look.field("Theme", self.appearance)
        self.smart = self._toggle("Smart Artwork", "smart_artwork", "Tint the background from the cover")
        self.motion = self._toggle("Reduce Motion", "reduce_motion", "Things change in place instead of moving")
        look.pair(self.smart, self.motion)
        self.centre = Segmented(["2 × 2", "3 × 3", "4 × 4"], compact=True)
        self.centre.set_index(max(0, min(2, int(settings["collage_centre"] or 2) - 2)))
        self.centre.changed.connect(lambda i: self._set("collage_centre", i + 2))
        look.field("Collage Now Playing", self.centre)

        # ---- window
        win = self._group("Window")
        self.tray = self._toggle("Keep Playing in Tray", "close_to_tray", "Closing the window keeps the music going")
        from aria.ui.mini import STYLE_NAMES, STYLES
        self.mini_style = Segmented([STYLE_NAMES[s] for s in STYLES])
        self.mini_style.set_index(STYLES.index(settings["mini_style"]) if settings["mini_style"] in STYLES else 0)
        self.mini_style.changed.connect(lambda i: window.mini.set_style(STYLES[i]))
        mini = Button("Mini Player", icon="mini")
        mini.clicked.connect(window.show_mini)
        win.pair(self.tray, mini)
        win.field("Mini Player Style", self.mini_style)

        # ---- data
        data = self._group("Data")
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
        self.free_btn = Button("Free Up Space", icon="trash",
                               tooltip="Delete downloaded songs; they stay saved and stream instead")
        self.free_btn.clicked.connect(window.free_space)
        self.downloads_size = label("", "Secondary")
        data.pair(self.free_btn, self.downloads_size)

        # ---- console
        con = self._group("Console")
        self.console_clear = Segmented(["200", "500", "1000", "Never"])
        n = settings["console_clear_after"]
        self.console_clear.set_index(CONSOLE_CLEAR.index(n) if n in CONSOLE_CLEAR else 2)
        self.console_clear.changed.connect(lambda i: self._set("console_clear_after", CONSOLE_CLEAR[i]))
        con.field("Clear After", self.console_clear)

        col.addStretch(1)
        body.setMaximumWidth(620)
        self.outline.addStretch(1)
        scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._follow())
        self.crossfade.toggled.connect(lambda on: self.fade_len.setEnabled(on))
        self.fade_len.setEnabled(self.crossfade.isChecked())
        QTimer.singleShot(0, self._follow)

    # ---- outline ---------------------------------------------------------------

    def _group(self, title: str) -> Group:
        g = Group(title)
        self.col.addWidget(g)
        self.groups.append(g)
        item = OutlineItem(title)
        item.clicked.connect(lambda _c=False, g=g: self.jump(g.title))
        g.outline_item = item
        self.outline.addWidget(item)
        return g

    def jump(self, title: str) -> None:
        """Scroll so the group starts at the top (as far as the page allows)."""
        g = next((g for g in self.groups if g.title == title), None)
        if g is not None:
            self.scroll.verticalScrollBar().setValue(g.y())
            self._mark(g)

    def _follow(self) -> None:
        bar = self.scroll.verticalScrollBar()
        y = bar.value() + 40
        current = self.groups[0]
        for g in self.groups:
            if g.y() <= y:
                current = g
        if bar.value() >= bar.maximum() - 2 and bar.maximum() > 0:
            # at the very bottom the last groups can't reach the top: pick the one in view
            visible = [g for g in self.groups if g.y() + g.height() / 2 >= bar.value()]
            current = visible[0] if visible and current not in visible else current
        self._mark(current)

    def _mark(self, group: Group) -> None:
        for g in self.groups:
            g.outline_item.setChecked(g is group)

    # ---- helpers -----------------------------------------------------------------

    def _toggle(self, text: str, key: str, tip: str) -> Toggle:
        t = Toggle(text, tip)
        t.setChecked(bool(self.settings[key]))
        t.toggled.connect(lambda on: self._set(key, on))
        return t

    def _sync_align(self) -> None:
        """Forced Alignment can be turned on once there's an engine and a model to run."""
        from aria.core import aligner
        ready = aligner.engine_available() and aligner.installed(self.settings["align_model"])
        self.lyrics_align.setEnabled(ready)
        if not ready and self.lyrics_align.isChecked():
            self.lyrics_align.setChecked(False)
        if hasattr(self, "align_streams"):
            self.align_streams.setEnabled(ready and self.lyrics_align.isChecked())
        if hasattr(self, "words_all"):
            self.words_all.setEnabled(ready)                      # it needs a speech model too

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

    # ---- audio outputs -----------------------------------------------------------

    def _sync_devices(self) -> None:
        outs = self.devices.outputs()
        self._device_ids = [""] + [d for d, _n in outs]
        self.device_pick.items = ["System Default"] + [self.devices.name_of(d) for d, _n in outs]
        chosen = self.settings["output_device"]
        if chosen and chosen not in self._device_ids:            # chosen but unplugged: still show it
            self._device_ids.append(chosen)
            self.device_pick.items.append(f"{self.devices.name_of(chosen)} (disconnected)")
        self.device_pick.set_index(self._device_ids.index(chosen) if chosen in self._device_ids else 0)
        self.device_pick.update()

    def _pick_device(self, i: int) -> None:
        self.devices.choose(self._device_ids[i])

    # ---- backups -------------------------------------------------------------------

    def refresh_snapshots(self) -> None:
        from aria.core import snapshot
        while self.snap_list.count():
            item = self.snap_list.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        snaps = snapshot.snapshots()[:5]
        if not snaps:
            self.snap_list.addWidget(label("No snapshots yet", "Caption"))
        for s in snaps:
            restore = Button("Restore…", "borderless")
            restore.clicked.connect(lambda _c=False, p=s.path: self.w.restore_snapshot(p))
            self.snap_list.addLayout(hbox(label(s.label), None, restore))

    # ---- page ----------------------------------------------------------------------

    def update_cache_size(self) -> None:
        from aria.core import cache, tasks
        self.cache_size.setText("…")
        tasks.run(cache.size, lambda n: self.cache_size.setText(f"Cache: {cache.human(n)}"),
                  lambda _e: self.cache_size.setText(""))
        tasks.run(cache.downloads_size, lambda n: self.downloads_size.setText(f"Downloads: {cache.human(n)}"),
                  lambda _e: self.downloads_size.setText(""))

    def on_shown(self) -> None:
        self.update_cache_size()
        self.refresh_snapshots()
        if self.devices is not None:
            self.devices.refresh()
            self._sync_devices()
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
            box = Toggle(name.removesuffix(".exe"), name)
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
