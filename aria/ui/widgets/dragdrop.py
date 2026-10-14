"""Dragging songs between parts of the window: out of a list or the rack, onto a drop target
(Add to Queue, Remove from Library, the sidebar's Now Playing).

The songs travel inside the app, so the drag carries just a marker; the tracks themselves are
held here for its duration. ``bus`` says when a drag starts and ends, so targets can appear
only while there is something to drop.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QObject, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QWidget

from aria.core.models import Track
from aria.ui import icons
from aria.ui.theme import font, theme

MIME = "application/x-aria-tracks"


class _Bus(QObject):
    started = Signal(object)          # the widget the songs come from
    finished = Signal()


bus = _Bus()
_carried: list[Track] = []


def carried(mime: QMimeData | None) -> list[Track]:
    """The songs a drop brings (empty for anything that isn't ours)."""
    return list(_carried) if mime is not None and mime.hasFormat(MIME) else []


def _badge(tracks: list[Track], dpr: float) -> QPixmap:
    text = tracks[0].title if len(tracks) == 1 else f"{len(tracks)} songs"
    f = font("callout", 600)
    fm = QFontMetrics(f)
    text = fm.elidedText(text, Qt.TextElideMode.ElideRight, 260)
    w, h = fm.horizontalAdvance(text) + 44, 30
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.color("accent"))
    p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.drawPixmap(10, 7, icons.pixmap("music", theme.color("on_accent"), 16))
    p.setFont(f)
    p.setPen(theme.color("on_accent"))
    p.drawText(QRectF(32, 0, w - 38, h), Qt.AlignmentFlag.AlignVCenter, text)
    p.end()
    return pm


def start(source: QWidget, tracks: list[Track]) -> None:
    """Carry ``tracks`` from ``source`` until they are dropped somewhere (blocks until then)."""
    if not tracks:
        return
    _carried[:] = tracks
    mime = QMimeData()
    mime.setData(MIME, b"1")
    drag = QDrag(source)
    drag.setMimeData(mime)
    pm = _badge(tracks, source.devicePixelRatioF())
    drag.setPixmap(pm)
    drag.setHotSpot(QPoint(18, 15))                 # the badge hangs just right of the pointer
    bus.started.emit(source)
    try:
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
    finally:
        _carried.clear()
        bus.finished.emit()


class DropZone(QFrame):
    """A labelled target that lights up while songs hover over it."""

    dropped = Signal(list)

    def __init__(self, icon: str, text: str, danger: bool = False):
        super().__init__()
        self.icon, self.text, self.danger = icon, text, danger
        self._over = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(52)
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(220, 52)

    def dragEnterEvent(self, e):
        if carried(e.mimeData()):
            self._over = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._over = False
        self.update()

    def dropEvent(self, e):
        tracks = carried(e.mimeData())
        self._over = False
        self.update()
        if tracks:
            e.acceptProposedAction()
            self.dropped.emit(tracks)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tone = theme.color("red" if self.danger else "accent")
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = QColor(tone)
        fill.setAlphaF(0.16 if self._over else 0.06)
        border = QColor(tone)
        border.setAlphaF(0.9 if self._over else 0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("window"))            # solid: it may lie over other content
        p.drawRoundedRect(r, 10, 10)
        p.setPen(border)
        p.setBrush(fill)
        p.drawRoundedRect(r, 10, 10)
        f = font("callout", 600)
        fm = QFontMetrics(f)
        w = 22 + fm.horizontalAdvance(self.text)
        x = (self.width() - w) / 2
        p.drawPixmap(int(x), int((self.height() - 16) / 2), icons.pixmap(self.icon, tone, 16))
        p.setFont(f)
        p.setPen(tone)
        p.drawText(QRectF(x + 22, 0, w, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)
        p.end()
