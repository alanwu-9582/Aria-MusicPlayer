"""Segmented control (§4.2): equal-width segments on a fill track, the selected one raised."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.ui.theme import ROW, font, radius, theme


class Segmented(QWidget):
    changed = Signal(int)

    def __init__(self, items: list[str], compact: bool = False, tooltips: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.items = items
        self.tooltips = tooltips or []
        self.compact = compact
        self._index = 0
        self._hover = -1
        self._kbd_focus = False
        self.setFixedHeight(ROW)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed if compact else QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setAccessibleName(" / ".join(items))
        theme.changed.connect(self.update)

    # ---- api -----------------------------------------------------------------

    def index(self) -> int:
        return self._index

    def set_index(self, i: int, emit: bool = False) -> None:
        i = max(0, min(len(self.items) - 1, i))
        if i != self._index:
            self._index = i
            self.setAccessibleDescription(f"{self.items[i]}, {i + 1} of {len(self.items)}")
            self.update()
            if emit:
                self.changed.emit(i)

    # ---- geometry --------------------------------------------------------------

    def _segment_width(self) -> int:
        fm = QFontMetrics(font("callout", 600))
        pad = 20 if self.compact else 26
        return max(fm.horizontalAdvance(t) for t in self.items) + pad

    def sizeHint(self) -> QSize:
        return QSize(self._segment_width() * len(self.items) + 4, ROW)

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(font("callout", 600))
        return QSize((max(fm.horizontalAdvance(t) for t in self.items) + 12) * len(self.items) + 4, ROW)

    def _rects(self) -> list[QRectF]:
        inner = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        w = inner.width() / len(self.items)
        return [QRectF(inner.left() + i * w, inner.top(), w, inner.height()) for i in range(len(self.items))]

    def _at(self, x: float) -> int:
        for i, r in enumerate(self._rects()):
            if r.left() <= x < r.right():
                return i
        return -1

    # ---- events ------------------------------------------------------------------

    def mouseMoveEvent(self, e):
        hover = self._at(e.position().x())
        if hover != self._hover:
            self._hover = hover
            if 0 <= hover < len(self.tooltips):
                self.setToolTip(self.tooltips[hover])
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position().x())
        if i >= 0:
            self.set_index(i, emit=True)

    def focusInEvent(self, e):
        self._kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kbd_focus = False
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.set_index(self._index - 1, emit=True)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.set_index(self._index + 1, emit=True)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("fill"))
        p.drawRoundedRect(track, radius(7, track.width(), track.height()), radius(7, track.width(), track.height()))
        if self._kbd_focus and self.hasFocus():
            p.setPen(theme.color("accent"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)

        rects = self._rects()
        for i, r in enumerate(rects):
            if i == self._index:
                # 0.6 px soft shadow under the raised segment
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 26 if not theme.dark else 60))
                p.drawRoundedRect(r.translated(0, 0.6), 5.5, 5.5)
                p.setBrush(theme.color("segment_on"))
                p.drawRoundedRect(r, radius(5.5, r.width(), r.height()), radius(5.5, r.width(), r.height()))
            elif i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color("fill"))
                p.drawRoundedRect(r, 5.5, 5.5)
            # thin divider between two unselected segments
            if i > 0 and self._index not in (i, i - 1):
                p.setPen(theme.color("separator_hex"))
                p.drawLine(int(r.left()), int(r.top() + 5), int(r.left()), int(r.bottom() - 5))

            selected = i == self._index
            p.setFont(font("callout", 600 if selected else 400))
            p.setPen(theme.color("label" if self.isEnabled() else "tertiary"))
            text = QFontMetrics(p.font()).elidedText(self.items[i], Qt.TextElideMode.ElideRight, int(r.width() - 8))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
