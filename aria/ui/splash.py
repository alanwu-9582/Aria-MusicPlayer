"""Launch card shown while the main window is being built, so half-laid-out widgets never flash."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from aria.ui import icons
from aria.ui.theme import font, theme


class Splash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(320, 200)
        self._text = "Loading…"
        self._phase = 0.0
        self._logo = icons.app_logo(72, self.devicePixelRatioF())
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)

    def start(self) -> None:
        self.show()
        self._timer.start()
        QApplication.processEvents()

    def step(self, text: str) -> None:
        """Update the status line; keeps the card painted while the app builds."""
        self._text = text
        self.repaint()
        QApplication.processEvents()

    def finish(self, window: QWidget) -> None:
        # Let the window lay itself out and paint once before revealing it.
        def reveal():
            window.setWindowOpacity(1.0)
            window.raise_()
            window.activateWindow()
            self._timer.stop()
            self.close()

        window.setWindowOpacity(0.0)
        window.show()
        QTimer.singleShot(180, reveal)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.025) % 1.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, theme.color("content"))
        p.setPen(theme.color("separator_hex"))
        p.drawPath(path)
        p.drawPixmap(int((self.width() - 72) / 2), 30, self._logo)
        p.setFont(font("title2"))
        p.setPen(theme.color("label"))
        p.drawText(QRectF(0, 110, self.width(), 26), Qt.AlignmentFlag.AlignCenter, "Aria")
        p.setFont(font("caption"))
        p.setPen(theme.color("secondary"))
        p.drawText(QRectF(0, 138, self.width(), 18), Qt.AlignmentFlag.AlignCenter, self._text)
        # thin indeterminate bar (§4.17)
        bar = QRectF((self.width() - 160) / 2, 168, 160, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("fill_strong"))
        p.drawRoundedRect(bar, 1, 1)
        seg = 48
        x = bar.left() + (bar.width() + seg) * self._phase - seg
        p.setClipRect(bar)
        p.setBrush(theme.color("accent"))
        p.drawRoundedRect(QRectF(x, bar.top(), seg, bar.height()), 1, 1)
        p.end()
