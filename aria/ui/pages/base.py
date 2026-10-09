"""Page scaffold (§6.2): large title + page actions, a 30-high toolbar, then content."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QLinearGradient, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from aria.ui.theme import theme
from aria.ui.widgets.controls import label
from aria.ui.widgets.tint import TintAnimator


class Page(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 20, 24, 16)
        self.root.setSpacing(12)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title = label(title, "Large")
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self.root.addLayout(self.header)

    def toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.root.addLayout(bar)
        return bar

    def on_shown(self) -> None:
        """Called when the page becomes visible."""


class TintedPage(Page):
    """A page whose background takes a quiet tint from the current cover (Smart Artwork)."""

    def __init__(self, title: str, settings, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.settings = settings
        self.tint = TintAnimator(self)
        self.tint.changed.connect(self.update)
        theme.changed.connect(self.update)

    def set_hue(self, hs) -> None:
        self.tint.set(hs if self.settings["smart_artwork"] else None)

    def paintEvent(self, _e):
        p = QPainter(self)
        window = theme.color("window")
        p.fillRect(self.rect(), window)
        top = self.tint.color("page")
        if top != window:
            g = QLinearGradient(QPointF(0, 0), QPointF(0, self.height()))
            g.setColorAt(0.0, top)
            g.setColorAt(0.75, window)
            p.fillRect(self.rect(), g)
        p.end()
