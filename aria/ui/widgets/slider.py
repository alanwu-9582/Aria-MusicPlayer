"""Slider (§4.4 B): 4 px track, round thumb 12 → 14 on hover/drag.

Click anywhere on the track jumps there and keeps dragging; pressing the thumb
doesn't jump; double-click restores the default; arrows step, Shift ×10.
``moved`` fires while dragging, ``committed`` once on release.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.ui.theme import ROW, theme


class Slider(QWidget):
    moved = Signal(float)
    committed = Signal(float)

    def __init__(self, maximum: float = 100.0, default: float = 0.0, step: float = 1.0,
                 name: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._max = maximum
        self._value = default
        self.default = default
        self.step = step
        self._hover = False
        self._drag = False
        self._grab = 0.0
        self.setFixedHeight(ROW)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        theme.changed.connect(self.update)

    # ---- api -----------------------------------------------------------------

    def value(self) -> float:
        return self._value

    def maximum(self) -> float:
        return self._max

    def dragging(self) -> bool:
        return self._drag

    def set_maximum(self, m: float) -> None:
        self._max = max(0.0, m)
        self._value = min(self._value, self._max)
        self.update()

    def set_value(self, v: float) -> None:
        """Programmatic update; ignored while the user is dragging."""
        if self._drag:
            return
        v = max(0.0, min(self._max, v))
        if v != self._value:
            self._value = v
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(160, ROW)

    # ---- geometry --------------------------------------------------------------

    def _track(self) -> QRectF:
        return QRectF(8, self.height() / 2 - 2, max(1, self.width() - 16), 4)

    def _x_of(self, v: float) -> float:
        t = self._track()
        return t.left() + (v / self._max if self._max else 0) * t.width()

    def _v_of(self, x: float) -> float:
        t = self._track()
        frac = (x - t.left()) / t.width()
        return max(0.0, min(self._max, frac * self._max))

    def _thumb_hit(self, pos: QPointF) -> bool:
        return abs(pos.x() - self._x_of(self._value)) <= 8

    def _set(self, v: float, commit: bool) -> None:
        v = max(0.0, min(self._max, v))
        self._value = v
        self.update()
        self.moved.emit(v)
        if commit:
            self.committed.emit(v)

    # ---- events ------------------------------------------------------------------

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._max:
            return
        self._drag = True
        if self._thumb_hit(e.position()):
            self._grab = e.position().x() - self._x_of(self._value)
        else:
            self._grab = 0.0
            self._set(self._v_of(e.position().x()), commit=False)
        self.update()

    def mouseMoveEvent(self, e):
        hover = self._thumb_hit(e.position()) or self._track().adjusted(-4, -8, 4, 8).contains(e.position())
        if hover != self._hover:
            self._hover = hover
            self.update()
        if self._drag:
            self._set(self._v_of(e.position().x() - self._grab), commit=False)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.committed.emit(self._value)
            self.update()

    def mouseDoubleClickEvent(self, e):
        self._drag = False
        self._set(self.default, commit=True)

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def keyPressEvent(self, e):
        step = self.step * (10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._set(self._value + step, commit=True)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._set(self._value - step, commit=True)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.angleDelta().x()
        if delta:
            self._set(self._value + self.step * (1 if delta > 0 else -1), commit=True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        t = self._track()
        p.setBrush(theme.color("fill_strong"))
        p.drawRoundedRect(t, 1.33, 1.33)
        x = self._x_of(self._value)
        if self._max:
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(t.left(), t.top(), x - t.left(), t.height()), 1.33, 1.33)
        big = self._hover or self._drag or self.hasFocus()
        d = 14 if big else 12
        p.setBrush(theme.color("slider_thumb_hover" if self._drag else "slider_thumb"))
        if self.hasFocus():
            p.setPen(theme.color("accent"))
        p.drawEllipse(QPointF(x, t.center().y()), d / 2, d / 2)
        p.end()
