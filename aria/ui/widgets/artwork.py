"""Artwork canvas (§7.1): always dark, radius 12, content inset 18 and fitted."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.core.models import Track
from aria.ui import icons
from aria.ui.theme import OVERLAY, font, parse_color, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.thumbs import ART_HEIGHT, artwork_urls


class Artwork(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.track: Track | None = None
        self.loading = False
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("封面")
        thumbs_mod.instance().ready.connect(self._on_ready)
        theme.changed.connect(self.update)

    def set_track(self, track: Track | None) -> None:
        self.track = track
        self.update()

    def set_loading(self, loading: bool) -> None:
        if loading != self.loading:
            self.loading = loading
            self.update()

    def _urls(self) -> list[str]:
        return artwork_urls(self.track) if self.track else []

    def _on_ready(self, key: str) -> None:
        if self.track and key == thumbs_mod.instance().key(self._urls(), ART_HEIGHT):
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, theme.color("canvas"))

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
                p.drawText(QRectF(r.left(), c.y() - 26, r.width(), 24), Qt.AlignmentFlag.AlignCenter, "尚未播放")
                p.setFont(font("callout"))
                p.setPen(QColor(255, 255, 255, 102))
                p.drawText(QRectF(r.left(), c.y() + 2, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                           "從搜尋或收藏挑一首歌")

        if self.loading:
            text = "載入中…"
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
