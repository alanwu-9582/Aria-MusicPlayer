"""Buttons, icon buttons and small layout helpers (§4.1)."""

from __future__ import annotations

import re

from PySide6.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
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


# Icons whose meaning is plain on sight: they keep an accessible name but show no tooltip
# (tooltips are for what the interface doesn't already say).
SELF_EXPLANATORY = {"play", "pause", "next", "previous", "heart", "volume", "volume-low", "mute",
                    "shuffle", "close", "search"}


def accessible(text: str) -> str:
    """Accessible name from a tooltip text: without the shortcut in brackets."""
    return re.sub(r"\s*[（(][^)）]*[)）]\s*$", "", text).strip()


class IconButton(QToolButton):
    """30×30 icon-only button; checkable ones turn accent_soft + accent when on (§4.1, §4.5).
    ``tooltip`` always becomes the accessible name; it's shown as a tooltip only when the icon
    alone doesn't say it (``tip`` forces it on or off)."""

    def __init__(self, icon: str, tooltip: str, checkable: bool = False, size: int = ROW,
                 icon_size: int = 16, tone: str = "label", parent: QWidget | None = None,
                 tip: bool | None = None):
        super().__init__(parent)
        self._icon = icon
        self._icon_px = icon_size
        self._tone = tone
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setIconSize(icons.size(icon_size))
        self.show_tip = (icon not in SELF_EXPLANATORY) if tip is None else tip
        self.set_tip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.changed.connect(self._retint)
        self._retint()

    def set_icon(self, name: str) -> None:
        self._icon = name
        self._retint()

    def set_tip(self, text: str) -> None:
        """Update what the button says: the accessible name always, the tooltip only when useful."""
        self.setAccessibleName(accessible(text))
        self.setToolTip(text if self.show_tip else "")

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


def menu_below(anchor: QWidget, menu: QMenu) -> None:
    """Open ``menu`` under ``anchor`` (the ⋯ buttons that hold the less-used actions)."""
    menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))


def add_item(menu: QMenu, icon: str, text: str, fn, enabled: bool = True):
    act = menu.addAction(icons.icon(icon, theme.color("label"), 16), text, fn)
    act.setEnabled(enabled)
    return act


class Toggle(QAbstractButton):
    """Setting switch (§4.5): its name on the left, a switch on the right; the whole row flips it.

    Every corner stays under a third of its shape's shortest side — the track is 34 × 20 with
    radius 6, the knob 14 × 14 with radius 4, the hover row 30 high with radius 6.
    """

    TRACK_W, TRACK_H, KNOB = 34, 20, 14
    TRACK_R, KNOB_R, ROW_R = 6.0, 4.0, 6.0

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
        self._pos = 0.0                                  # knob: 0 off … 1 on
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._move)
        self.toggled.connect(self._slide)
        theme.changed.connect(self.update)

    def sizeHint(self):
        return QSize(QFontMetrics(font("callout")).horizontalAdvance(self.text()) + self.TRACK_W + 28, ROW)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def _slide(self, on: bool) -> None:
        from aria.ui.widgets import rack as rack_mod
        self._anim.stop()
        if not self.isVisible() or rack_mod.reduce_motion:
            self._move(1.0 if on else 0.0)               # nothing to watch: just be there
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _move(self, v) -> None:
        self._pos = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect())
        if self._hover and self.isEnabled():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("fill"))
            p.drawRoundedRect(r, self.ROW_R, self.ROW_R)
        track = QRectF(r.right() - 8 - self.TRACK_W, (r.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        # the name, cut short rather than running under the switch
        p.setFont(font("callout"))
        p.setPen(theme.color("label"))
        text_r = QRectF(8, 0, track.left() - 16, r.height())
        p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(p.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_r.width())))
        # track: grey when off, accent when on (blended while the knob travels)
        off, on = theme.color("fill_strong"), theme.color("accent")
        t = self._pos
        fill = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * t, off.greenF() + (on.greenF() - off.greenF()) * t,
                               off.blueF() + (on.blueF() - off.blueF()) * t, off.alphaF() + (on.alphaF() - off.alphaF()) * t)
        p.setPen(theme.color("accent") if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(track, self.TRACK_R, self.TRACK_R)
        pad = (self.TRACK_H - self.KNOB) / 2
        knob = QRectF(track.left() + pad + (self.TRACK_W - self.KNOB - 2 * pad) * t, track.top() + pad,
                      self.KNOB, self.KNOB)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(knob.translated(0, 0.8), self.KNOB_R, self.KNOB_R)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(knob, self.KNOB_R, self.KNOB_R)
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
