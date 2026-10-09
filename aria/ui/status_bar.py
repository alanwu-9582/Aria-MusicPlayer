"""Status bar (§6.1): 30 high, caption text joined by " · ", 160×5 progress on the right."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar

from aria.ui.theme import ROW
from aria.ui.widgets.controls import label


class StatusBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(ROW + 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(8)
        self.text = label("", "Caption")
        self.job = label("", "Caption")
        self.bar = QProgressBar()
        self.bar.setFixedWidth(160)
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.job)
        lay.addWidget(self.bar)
        self.set_job(None)

    def set_parts(self, *parts: str) -> None:
        self.text.setText(" · ".join(p for p in parts if p))

    def set_job(self, text: str | None, fraction: float | None = None) -> None:
        """Background work: "Downloading 2/5" + progress; None hides it."""
        visible = text is not None
        self.job.setVisible(visible)
        self.bar.setVisible(visible)
        if visible:
            self.job.setText(text)
            if fraction is None:
                self.bar.setRange(0, 0)          # indeterminate
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(fraction * 1000))
