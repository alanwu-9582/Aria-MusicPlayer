"""Buttons, icon buttons and small layout helpers (§4.1)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
                               QToolButton, QWidget)

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


class Toggle(QAbstractButton):
    """Text toggle (§4.5): outlined when off; an inset accent block with on_accent text when on."""

    def __init__(self, text: str, tooltip: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(ROW)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        if tooltip:
            self.setToolTip(tooltip)
        self._hover = False
        theme.changed.connect(self.update)

    def sizeHint(self):
        return QSize(QFontMetrics(font("callout", 600)).horizontalAdvance(self.text()) + 32, ROW)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = theme.color("accent") if self.hasFocus() else theme.color("tertiary" if self._hover else "separator_hex")
        p.setPen(border)
        p.setBrush(theme.color("content"))
        p.drawRoundedRect(r, 6, 6)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(r.adjusted(2.5, 2.5, -2.5, -2.5), 4, 4)
        on = self.isChecked()
        p.setFont(font("callout", 600 if on else 400))
        p.setPen(theme.color("on_accent" if on else ("label" if self._hover else "secondary")))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class CheckBox(QAbstractButton):
    """16 px box (radius 4) + callout text; checked = accent fill with a check mark."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(ROW)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(font("callout")).horizontalAdvance(self.text()) + 26, ROW)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, (self.height() - 16) / 2 + 0.5, 15, 15)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(box, 4, 4)
            p.drawPixmap(box.adjusted(2, 2, -2, -2).toRect(), icons.pixmap("check", theme.color("on_accent"), 12))
        else:
            p.setPen(theme.color("accent") if self.hasFocus() else theme.color("tertiary"))
            p.setBrush(theme.color("control"))
            p.drawRoundedRect(box, 4, 4)
        p.setFont(font("callout"))
        p.setPen(theme.color("label"))
        p.drawText(QRectF(24, 0, self.width() - 24, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


class MenuButton(QAbstractButton):
    """A pop-up choice (§4.3): the current option + chevron; clicking lists the options."""

    changed = Signal(int)

    def __init__(self, items: list[str], index: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.items = items
        self._index = max(0, min(len(items) - 1, index))
        self._hover = False
        self.setFixedHeight(ROW)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._pop)
        theme.changed.connect(self.update)

    def index(self) -> int:
        return self._index

    def set_index(self, i: int, emit: bool = False) -> None:
        i = max(0, min(len(self.items) - 1, i))
        if i != self._index:
            self._index = i
            self.update()
            if emit:
                self.changed.emit(i)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(font("callout"))
        return QSize(max(fm.horizontalAdvance(t) for t in self.items) + 40, ROW)

    def _pop(self) -> None:
        m = QMenu(self)
        for i, text in enumerate(self.items):
            act = m.addAction(text, lambda i=i: self.set_index(i, emit=True))
            if i == self._index:
                act.setIcon(icons.icon("check", theme.color("accent"), 16))
        m.setMinimumWidth(self.width())
        m.exec(self.mapToGlobal(QPoint(0, self.height() + 2)))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(theme.color("accent") if self.hasFocus() else theme.color("separator_hex"))
        p.setBrush(theme.color("control_hover" if self._hover else "control"))
        p.drawRoundedRect(r, 7, 7)
        p.setFont(font("callout"))
        p.setPen(theme.color("label" if self.isEnabled() else "tertiary"))
        text = QFontMetrics(p.font()).elidedText(self.items[self._index], Qt.TextElideMode.ElideRight,
                                                 int(r.width() - 34))
        p.drawText(r.adjusted(10, 0, -26, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.drawPixmap(QRectF(r.right() - 22, r.center().y() - 7, 14, 14).toRect(),
                     icons.pixmap("chevron-down", theme.color("secondary"), 14))
        p.end()
