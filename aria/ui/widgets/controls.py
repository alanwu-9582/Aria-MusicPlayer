"""Buttons, icon buttons and small layout helpers (§4.1)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QWidget

from aria.ui import icons
from aria.ui.theme import ROW, font, theme


class Button(QPushButton):
    """Text button. kind: secondary (default) | primary | borderless | danger | destructive."""

    def __init__(self, text: str, kind: str = "secondary", icon: str | None = None,
                 tooltip: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setProperty("kind", kind)
        self.setFixedHeight(ROW)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon = icon
        self._kind = kind
        if tooltip:
            self.setToolTip(tooltip)
        if icon:
            self.setIconSize(icons.size(15))
            theme.changed.connect(self._retint)
            self._retint()

    def _retint(self) -> None:
        tone = {"primary": "on_accent", "borderless": "accent", "danger": "red",
                "destructive": "on_accent"}.get(self._kind, "label")
        self.setIcon(icons.icon(self._icon, theme.color(tone), 15, disabled=theme.color("tertiary")))


class IconButton(QToolButton):
    """30×30 icon-only button; checkable ones turn accent_soft + accent when on (§4.1, §4.5)."""

    def __init__(self, icon: str, tooltip: str, checkable: bool = False, size: int = ROW,
                 icon_size: int = 16, tone: str = "label", parent: QWidget | None = None):
        super().__init__(parent)
        self._icon = icon
        self._icon_px = icon_size
        self._tone = tone
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setIconSize(icons.size(icon_size))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip.split("（")[0])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.changed.connect(self._retint)
        self._retint()

    def set_icon(self, name: str) -> None:
        self._icon = name
        self._retint()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self._retint()

    def _retint(self) -> None:
        self.setIcon(icons.icon(self._icon, theme.color(self._tone), self._icon_px,
                                on=theme.color("accent"), disabled=theme.color("tertiary")))


def label(text: str = "", role: str = "", parent: QWidget | None = None, mono: bool = False) -> QLabel:
    """QLabel with a type-scale role (Large, Title2, Title3, Headline, Secondary, Caption, SectionTitle)."""
    lbl = QLabel(text, parent)
    if role:
        lbl.setObjectName(role)
    if mono:
        lbl.setFont(font("caption", mono=True))
    return lbl


def vline() -> QFrame:
    """1 px toolbar group divider with 6 px each side (§6.2)."""
    wrap = QFrame()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(6, 6, 6, 6)
    line = QFrame()
    line.setObjectName("VLine")
    line.setFixedWidth(1)
    lay.addWidget(line)
    wrap.setFixedHeight(ROW)
    return wrap


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Expanding, w.sizePolicy().verticalPolicy())
    return w
