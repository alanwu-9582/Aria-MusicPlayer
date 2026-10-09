"""Page scaffold (§6.2): large title + page actions, a 30-high toolbar, then content."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from aria.ui.widgets.controls import label


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
