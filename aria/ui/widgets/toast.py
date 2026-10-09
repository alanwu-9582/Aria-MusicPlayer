"""Toast (§4.14): bottom-centre, 40 high, dark translucent, status dot, 180 ms fade, 2.6 s stay."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from aria.ui.theme import TOAST, font, parse_color, theme

DOT = {"info": "accent", "success": "green", "warning": "orange", "danger": "red"}


class Toast(QWidget):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text = ""
        self._kind = "info"
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._done)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._hide)
        self.hide()

    def show_message(self, text: str, kind: str = "info") -> None:
        self._text = text
        self._kind = kind
        w = QFontMetrics(font("body", 500)).horizontalAdvance(text) + 58
        self.resize(w, 40)
        self.reposition()
        start = self._effect.opacity() if self.isVisible() else 0.0
        self._fade.stop()
        self._effect.setOpacity(start)
        self.raise_()
        self.show()
        self._fade.setStartValue(start)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(2600)
        self.setAccessibleName(text)
        self.update()

    def reposition(self) -> None:
        host = self.parentWidget()
        self.move((host.width() - self.width()) // 2, host.height() - 96 - self.height() // 2)

    def _hide(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _done(self) -> None:
        if self._fade.endValue() == 0.0:
            self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(parse_color(TOAST))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.setBrush(theme.color(DOT.get(self._kind, "accent")))
        p.drawEllipse(QRectF(18, self.height() / 2 - 4, 8, 8))
        p.setPen(QColor("#ffffff"))
        p.setFont(font("body", 500))
        p.drawText(QRectF(36, 0, self.width() - 52, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()
