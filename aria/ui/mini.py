"""Mini player (always on top) and the system tray icon."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from aria.core.player import LOADING, PLAYING
from aria.ui import icons
from aria.ui.player_bar import Cover, ElidedLabel
from aria.ui.theme import theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.controls import IconButton
from aria.ui.widgets.slider import Slider


class MiniPlayer(QWidget):
    expand = Signal()

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
        self._on_track(playback.current)
        self._on_state(playback.state)

    def _on_track(self, t) -> None:
        self.cover.set_track(t)
        self.title.set_full_text(t.title if t else "Aria")
        self.artist.set_full_text(t.artist if t else "")
        self._update_hue()

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
        self.mute.setToolTip("Unmute" if v == 0 else "Mute")

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

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, self.tint.color("page"))
        p.setPen(theme.color("separator_hex"))
        p.drawPath(path)
        if self._len > 0:
            track = QRectF(14, self.height() - 6, self.width() - 28, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("fill_strong"))
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
