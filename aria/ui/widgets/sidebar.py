"""Sidebar (§6.1): 212 expanded / 60 collapsed, items 32 high, collapse + appearance at the bottom."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import IconButton, label

EXPANDED, COLLAPSED = 212, 60
APPEARANCE_ICON = {"system": "monitor", "light": "sun", "dark": "moon"}
APPEARANCE_TIP = {"system": "外觀：跟隨系統", "light": "外觀：淺色", "dark": "外觀：深色"}


class SidebarItem(QAbstractButton):
    def __init__(self, icon: str, text: str, shortcut: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{text}（{shortcut}）")
        self.setAccessibleName(text)
        self.collapsed = False
        self._hover = False
        self._kbd_focus = False
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.changed.connect(self.update)

    def focusInEvent(self, e):
        self._kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kbd_focus = False
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
        else:
            super().keyPressEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(EXPANDED - 16, 32)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        if self.isChecked():
            p.setBrush(theme.color("accent_soft"))
            p.drawRoundedRect(r, 7, 7)
        elif self._hover:
            p.setBrush(theme.color("fill"))
            p.drawRoundedRect(r, 7, 7)
        if self._kbd_focus and self.hasFocus():
            p.setPen(theme.color("accent"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        color = theme.color("accent" if self.isChecked() else "secondary")
        ix = (r.width() - 17) / 2 if self.collapsed else 10
        p.drawPixmap(QRectF(ix, (r.height() - 17) / 2, 17, 17).toRect(), icons.pixmap(self.icon_name, color, 17))
        if not self.collapsed:
            p.setFont(font("body", 600 if self.isChecked() else 500))
            p.setPen(theme.color("label"))
            text = QFontMetrics(p.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, int(r.width() - 46))
            p.drawText(QRectF(38, 0, r.width() - 42, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()


class Sidebar(QFrame):
    page_selected = Signal(int)
    collapse_toggled = Signal(bool)
    appearance_clicked = Signal()

    def __init__(self, pages: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.collapsed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 18, 8, 12)
        lay.setSpacing(2)

        self.brand = label("Aria", "Title2")
        self.brand.setContentsMargins(10, 0, 0, 0)
        lay.addWidget(self.brand)
        lay.addSpacing(18)

        self.items: list[SidebarItem] = []
        for i, (icon, text) in enumerate(pages):
            item = SidebarItem(icon, text, f"Ctrl+{i + 1}")
            item.clicked.connect(lambda _c=False, n=i: self.select(n, emit=True))
            self.items.append(item)
            lay.addWidget(item)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(2, 0, 2, 0)
        bottom.setSpacing(4)
        self.collapse_btn = IconButton("sidebar", "收合側邊欄", tone="secondary")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(not self.collapsed, emit=True))
        self.appearance_btn = IconButton("monitor", APPEARANCE_TIP["system"], tone="secondary")
        self.appearance_btn.clicked.connect(self.appearance_clicked)
        bottom.addWidget(self.collapse_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.appearance_btn)
        self._bottom = bottom
        lay.addLayout(bottom)
        self.setFixedWidth(EXPANDED)

    def select(self, index: int, emit: bool = False) -> None:
        for i, item in enumerate(self.items):
            item.setChecked(i == index)
        if emit:
            self.page_selected.emit(index)

    def set_collapsed(self, collapsed: bool, emit: bool = False) -> None:
        self.collapsed = collapsed
        self.setFixedWidth(COLLAPSED if collapsed else EXPANDED)
        self.brand.setText("A" if collapsed else "Aria")
        self.brand.setAlignment(Qt.AlignmentFlag.AlignHCenter if collapsed else Qt.AlignmentFlag.AlignLeft)
        self.brand.setContentsMargins(0 if collapsed else 10, 0, 0, 0)
        for item in self.items:
            item.collapsed = collapsed
            item.update()
        self.collapse_btn.setToolTip("展開側邊欄" if collapsed else "收合側邊欄")
        # Collapsed: stack the two buttons vertically so they fit in 60 px.
        self._bottom.setDirection(QHBoxLayout.Direction.TopToBottom if collapsed else QHBoxLayout.Direction.LeftToRight)
        if emit:
            self.collapse_toggled.emit(collapsed)

    def set_appearance(self, appearance: str) -> None:
        self.appearance_btn.set_icon(APPEARANCE_ICON[appearance])
        self.appearance_btn.setToolTip(APPEARANCE_TIP[appearance])
        self.appearance_btn.setAccessibleName(APPEARANCE_TIP[appearance])
