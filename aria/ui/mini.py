"""Mini player (always on top) and the system tray icon.

Two looks (Settings › Window › Mini Player, or right-click it): Solid — the page colour tinted by
the cover — and Liquid Glass — one flat translucent layer (about 63 % opaque) in the cover's
colour, so what's behind the window shows through; no gradients, no blur."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from aria.core.player import LOADING, PLAYING
from aria.ui import icons
from aria.ui.player_bar import Cover, ElidedLabel
from aria.ui.theme import theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.controls import IconButton
from aria.ui.widgets.slider import Slider


STYLES = ("solid", "glass")
STYLE_NAMES = {"solid": "Solid", "glass": "Liquid Glass"}


class MiniPlayer(QWidget):
    expand = Signal()
    style_changed = Signal(str)

    def __init__(self, playback, settings):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Aria")
        self.pb = playback
        self.settings = settings
        self.setFixedSize(380, 96)
        self._drag: QPoint | None = None
        self._pos = 0.0
        self._len = 0.0
        self.tint = tint_mod.TintAnimator(self)
        self.tint.changed.connect(self.update)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 10, 14)
        root.setSpacing(12)
        self.cover = Cover()
        self.cover.setFixedSize(64, 64)
        root.addWidget(self.cover)

        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = ElidedLabel("Headline")
        self.artist = ElidedLabel("Caption")
        col.addWidget(self.title)
        col.addWidget(self.artist)
        col.addStretch(1)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.prev = IconButton("previous", "Previous", icon_size=14)
        self.play = IconButton("play", "Play / Pause", icon_size=15, tone="on_accent")
        self.play.setObjectName("PlayButton")
        self.next = IconButton("next", "Next", icon_size=14)
        for b in (self.prev, self.play, self.next):
            controls.addWidget(b)
        controls.addStretch(1)
        # Volume, kept in step with the main player bar through Playback.volume_changed.
        self.mute = IconButton("volume", "Mute", size=24, icon_size=14, tone="secondary")
        self.volume = Slider(100, 70, step=5, name="Volume")
        self.volume.setFixedWidth(92)
        self.volume.set_value(playback.player.volume)
        controls.addWidget(self.mute)
        controls.addWidget(self.volume)
        col.addLayout(controls)
        root.addLayout(col, 1)

        side = QVBoxLayout()
        side.setSpacing(2)
        self.expand_btn = IconButton("expand", "Open Aria", size=22, icon_size=13, tone="secondary")
        self.close_btn = IconButton("close", "Close Mini Player", size=22, icon_size=13, tone="secondary")
        side.addWidget(self.close_btn)
        side.addWidget(self.expand_btn)
        side.addStretch(1)
        root.addLayout(side)

        self.prev.clicked.connect(playback.previous)
        self.play.clicked.connect(playback.toggle)
        self.next.clicked.connect(playback.next)
        self.expand_btn.clicked.connect(self.expand)
        self.close_btn.clicked.connect(self.hide)
        self._unmuted = playback.player.volume or 70
        self.volume.moved.connect(lambda v: playback.set_volume(int(v)))
        self.mute.clicked.connect(self._toggle_mute)
        playback.volume_changed.connect(self._on_volume)
        self._on_volume(playback.player.volume)
        playback.current_changed.connect(self._on_track)
        playback.state_changed.connect(self._on_state)
        playback.position_changed.connect(self._on_pos)
        thumbs_mod.instance().ready.connect(lambda _k: self._update_hue())
        theme.changed.connect(self.update)
        theme.changed.connect(self.apply_style)
        self._on_track(playback.current)
        self._on_state(playback.state)

    def _on_track(self, t) -> None:
        from aria.core import identity
        self.cover.set_track(t)
        name, who = identity.display(t) if t else ("Aria", "")
        self.title.set_full_text(name)
        self.artist.set_full_text(who)
        self._update_hue()

    # ---- style -----------------------------------------------------------------

    @property
    def glass(self) -> bool:
        return self.settings["mini_style"] == "glass"

    def set_style(self, style: str) -> None:
        if style in STYLES and style != self.settings["mini_style"]:
            self.settings["mini_style"] = style
            self.apply_style()
            self.style_changed.emit(style)

    def apply_style(self) -> None:
        self._text_colours()
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self.apply_style()

    def _text_colours(self) -> None:
        """On glass the quiet grey of the artist line would sink into what shows through: it takes
        the text colour, a little softened, instead."""
        if self.glass:
            ink = "rgba(255,255,255,205)" if theme.dark else "rgba(20,20,26,190)"
            self.artist.setStyleSheet(f"color: {ink};")
        else:
            self.artist.setStyleSheet("")

    def contextMenuEvent(self, e):
        m = QMenu(self)
        for style in STYLES:
            act = m.addAction(STYLE_NAMES[style], lambda s=style: self.set_style(s))
            act.setCheckable(True)
            act.setChecked(style == self.settings["mini_style"])
        m.exec(e.globalPos())

    def _update_hue(self) -> None:
        t = self.pb.current
        pm = thumbs_mod.instance().get(t.thumbnail) if t and t.thumbnail else None
        if pm is not None or not t:
            self.tint.set(tint_mod.hue_of(pm) if pm and not pm.isNull() and self.settings["smart_artwork"] else None)

    def _on_volume(self, v: int) -> None:
        if v:
            self._unmuted = v
        self.volume.set_value(v)
        self.mute.set_icon("mute" if v == 0 else "volume-low" if v < 50 else "volume")
        self.mute.set_tip("Unmute" if v == 0 else "Mute")

    def _toggle_mute(self) -> None:
        self.pb.set_volume(0 if self.pb.player.volume else (self._unmuted or 70))

    def _on_state(self, s: str) -> None:
        self.play.set_icon("pause" if s in (PLAYING, LOADING) else "play")

    def _on_pos(self, pos: float, length: float) -> None:
        self._pos, self._len = pos, length
        self.update()

    def show_near(self, anchor: QWidget | None = None) -> None:
        saved = self.settings["mini_pos"]
        if saved:
            x, y = (int(v) for v in saved.split(","))
            self.move(x, y)
        else:
            screen = (anchor.screen() if anchor else self.screen()).availableGeometry()
            self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)
        self.show()
        self.raise_()

    # ---- dragging ------------------------------------------------------------

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.settings["mini_pos"] = f"{self.x()},{self.y()}"

    def mouseDoubleClickEvent(self, e):
        self.expand.emit()

    def _glass_tint(self) -> QColor:
        """The glass: the cover's colour, livelier than the page tint, a little more opaque than half."""
        base = self.tint.color("page")
        if self.tint.hs is None:
            c = QColor(250, 250, 253) if not theme.dark else QColor(24, 24, 30)
        else:
            sat = min(0.45 if not theme.dark else 0.55, base.hslSaturationF() * 2.0 + 0.06)
            c = QColor.fromHslF(max(0.0, base.hslHueF()), sat, 0.86 if not theme.dark else 0.16)
        c.setAlpha(160)
        return c

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        if self.glass:
            path.addRoundedRect(r, 12, 12)
            p.fillPath(path, self._glass_tint())
            p.setPen(QColor(255, 255, 255, 60) if theme.dark else QColor(0, 0, 0, 45))
            p.drawPath(path)
        else:
            path.addRoundedRect(r, 12, 12)
            p.fillPath(path, self.tint.color("page"))
            p.setPen(theme.color("separator_hex"))
            p.drawPath(path)
        if self._len > 0:
            track = QRectF(14, self.height() - 6, self.width() - 28, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 90) if self.glass else theme.color("fill_strong"))
            p.drawRoundedRect(track, 1, 1)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * min(1, self._pos / self._len), 3), 1, 1)
        p.end()


class Tray(QSystemTrayIcon):
    def __init__(self, window, playback, icon):
        super().__init__(icon, window)
        self.w = window
        self.pb = playback
        self.setToolTip("Aria")
        menu = QMenu()
        self.now = menu.addAction("")
        self.now.setEnabled(False)
        menu.addSeparator()
        self.toggle_act = menu.addAction("Play", playback.toggle)
        menu.addAction("Previous", playback.previous)
        menu.addAction("Next", playback.next)
        menu.addSeparator()
        menu.addAction(icons.icon("mini", theme.color("label"), 16), "Mini Player", window.show_mini)
        menu.addAction("Show Aria", window.bring_back)
        menu.addSeparator()
        menu.addAction("Quit", window.quit_app)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        playback.current_changed.connect(self._on_track)
        playback.state_changed.connect(self._on_state)
        self._on_track(playback.current)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.w.isVisible() and not self.w.isMinimized():
                self.w.hide()
            else:
                self.w.bring_back()

    def _on_track(self, t) -> None:
        text = f"{t.title} — {t.artist}" if t and t.artist else (t.title if t else "Nothing playing")
        self.now.setText(text[:60])
        self.setToolTip(f"Aria\n{text}"[:120])

    def _on_state(self, s: str) -> None:
        self.toggle_act.setText("Pause" if s in (PLAYING, LOADING) else "Play")
