"""Artwork canvas (§7.1): always dark, radius 12, content inset 18 and fitted."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.core.models import Track
from aria.ui import icons
from aria.ui.theme import OVERLAY, font, parse_color, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.thumbs import ART_HEIGHT, artwork_urls


class Artwork(QWidget):
    hue_changed = Signal(object)           # (hue, sat) of the cover, or None

    def __init__(self, parent=None, tint: tint_mod.TintAnimator | None = None):
        super().__init__(parent)
        self.track: Track | None = None
        self.loading = False
        self.tint = tint
        if tint is not None:
            tint.changed.connect(self.update)
        self._hue_for: str | None = None
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Artwork")
        thumbs_mod.instance().ready.connect(self._on_ready)
        theme.changed.connect(self.update)

    def set_track(self, track: Track | None) -> None:
        self.track = track
        self._hue_for = None
        self.update()
        QTimer.singleShot(0, self._emit_hue)

    def _emit_hue(self) -> None:
        """Report the cover's colour once per track (after it has loaded)."""
        if not self.track:
            self.hue_changed.emit(None)
            return
        if self._hue_for == self.track.key:
            return
        pm = thumbs_mod.instance().get(self._urls(), ART_HEIGHT)
        if pm is not None:
            self._hue_for = self.track.key
            self.hue_changed.emit(tint_mod.hue_of(pm) if not pm.isNull() else None)

    def set_loading(self, loading: bool) -> None:
        if loading != self.loading:
            self.loading = loading
            self.update()

    def _urls(self) -> list[str]:
        return artwork_urls(self.track) if self.track else []

    def _on_ready(self, key: str) -> None:
        if self.track and key == thumbs_mod.instance().key(self._urls(), ART_HEIGHT):
            self.update()
            self._emit_hue()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, self.tint.color("canvas") if self.tint else theme.color("canvas"))

        inner = r.adjusted(18, 18, -18, -18)
        pm = thumbs_mod.instance().get(self._urls(), ART_HEIGHT) if self.track else None
        if pm and not pm.isNull():
            size = pm.size().scaled(inner.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(inner.center().x() - size.width() / 2, inner.center().y() - size.height() / 2,
                            size.width(), size.height())
            art = QPainterPath()
            art.addRoundedRect(target, 10, 10)
            p.setClipPath(art)
            p.drawPixmap(target, pm, QRectF(pm.rect()))
            p.setClipPath(clip)
        else:
            c = r.center()
            if self.track:
                p.drawPixmap(QRectF(c.x() - 22, c.y() - 22, 44, 44).toRect(),
                             icons.pixmap("music", QColor(255, 255, 255, 90), 44))
            else:
                p.setFont(font("title3", 600))
                p.setPen(QColor(255, 255, 255, 153))
                p.drawText(QRectF(r.left(), c.y() - 26, r.width(), 24), Qt.AlignmentFlag.AlignCenter, "Nothing playing")
                p.setFont(font("callout"))
                p.setPen(QColor(255, 255, 255, 102))
                p.drawText(QRectF(r.left(), c.y() + 2, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                           "Pick a song from Search or your Library")

        if self.loading:
            text = "Loading…"
            f = font("caption", 600)
            w = QFontMetrics(f).horizontalAdvance(text) + 20
            badge = QRectF(r.right() - 12 - w, r.top() + 12, w, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(parse_color(OVERLAY))
            p.drawRoundedRect(badge, 6, 6)
            p.setPen(QColor("#ffffff"))
            p.setFont(f)
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
