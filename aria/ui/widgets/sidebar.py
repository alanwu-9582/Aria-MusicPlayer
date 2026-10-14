"""Sidebar (§6.1): 212 expanded / 60 collapsed, grouped items 32 high, collapse + appearance
at the bottom. Only the item list scrolls (§6.5). Playlists and Smart Collections live on the
Shelf, with the albums."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import IconButton, label
from aria.ui.widgets.tracklist import plain_scroll

EXPANDED, COLLAPSED = 212, 60
APPEARANCE_ICON = {"system": "monitor", "light": "sun", "dark": "moon"}
APPEARANCE_TIP = {"system": "Appearance: System", "light": "Appearance: Light", "dark": "Appearance: Dark"}


class SidebarItem(QAbstractButton):
    tracks_dropped = Signal(list)
    """A navigation entry. ``primary`` (Now Playing) stands a little taller and keeps a faint
    accent background even when not selected — a quiet main entrance, not a call to action."""

    def __init__(self, icon: str, text: str, tooltip: str = "", primary: bool = False, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.primary = primary
        self.height_px = 44 if primary else 32
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(self.height_px)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tip = tooltip or text
        self.setAccessibleName(text)
        self.collapsed = False
        self._hover = False
        self._kbd_focus = False
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        theme.changed.connect(self.update)

    def dragEnterEvent(self, e):
        from aria.ui.widgets import dragdrop
        if dragdrop.carried(e.mimeData()):
            self._hover = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hover = False
        self.update()

    def dropEvent(self, e):
        from aria.ui.widgets import dragdrop
        tracks = dragdrop.carried(e.mimeData())
        self._hover = False
        self.update()
        if tracks:
            e.acceptProposedAction()
            self.tracks_dropped.emit(tracks)

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
        return QSize(32, self.height_px)      # width comes from the sidebar, never from the item

    def minimumSizeHint(self) -> QSize:
        return QSize(32, self.height_px)

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
        rad = 9 if self.primary else 7
        if self.isChecked():
            p.setBrush(theme.color("accent_soft"))
            p.drawRoundedRect(r, rad, rad)
        elif self.primary:
            # stands out a little even unselected, in a neutral tone (the accent means "selected")
            fill = theme.color("fill")
            fill.setAlphaF(fill.alphaF() * (1.0 if self._hover else 0.55))
            p.setBrush(fill)
            p.drawRoundedRect(r, rad, rad)
        elif self._hover:
            p.setBrush(theme.color("fill"))
            p.drawRoundedRect(r, rad, rad)
        if self._kbd_focus and self.hasFocus():
            p.setPen(theme.color("accent"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        color = theme.color("accent" if self.isChecked() else ("label" if self.primary else "secondary"))
        size = 19 if self.primary else 17
        ix = (r.width() - size) / 2 if self.collapsed else 10 - (size - 17) / 2
        p.drawPixmap(QRectF(ix, (r.height() - size) / 2, size, size).toRect(), icons.pixmap(self.icon_name, color, size))
        if not self.collapsed:
            p.setFont(font("body", 600 if (self.isChecked() or self.primary) else 500))
            p.setPen(theme.color("label"))
            text = QFontMetrics(p.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, int(r.width() - 46))
            p.drawText(QRectF(38, 0, r.width() - 42, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()


class Sidebar(QFrame):
    page_selected = Signal(int)
    collapse_toggled = Signal(bool)
    appearance_clicked = Signal()
    quit_clicked = Signal()
    tracks_to_queue = Signal(list)              # songs dropped on Now Playing

    def __init__(self, groups: list[tuple[str, list[tuple[str, str]]]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.collapsed = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 18, 0, 12)
        outer.setSpacing(0)

        # brand
        brand_row = self._brand_row = QHBoxLayout()
        brand_row.setContentsMargins(18, 0, 8, 0)
        brand_row.setSpacing(8)
        self.logo = QLabel()
        self.logo.setFixedSize(24, 24)
        self.brand = label("Aria", "Title2")
        brand_row.addWidget(self.logo)
        brand_row.addWidget(self.brand, 1)
        outer.addLayout(brand_row)
        outer.addSpacing(14)

        # scrolling item list
        self.scroll = plain_scroll(reserve=False)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(8, 0, 8, 0)
        self.body.setSpacing(2)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)

        self.items: list[SidebarItem] = []
        self.titles: list[QLabel] = []
        n = 0
        for gi, (title, pages) in enumerate(groups):
            if gi:
                self.body.addSpacing(12)
            self._section(title)
            for icon, text, *flags in pages:
                n += 1
                item = SidebarItem(icon, text, f"{text} (Ctrl+{n})", primary="primary" in flags)
                item.clicked.connect(lambda _c=False, k=len(self.items): self.select(k, emit=True))
                self.items.append(item)
                self.body.addWidget(item)

        self.body.addStretch(1)

        bottom = QHBoxLayout()
        # Left margin 15 centres the 30 px button in the 60 px rail, so the collapse
        # button sits at the same spot whether the sidebar is open or not.
        bottom.setContentsMargins(15, 8, 10, 0)
        bottom.setSpacing(4)
        self.collapse_btn = IconButton("sidebar", "Collapse Sidebar", tone="secondary")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(not self.collapsed, emit=True))
        self.appearance_btn = IconButton("monitor", APPEARANCE_TIP["system"], tone="secondary")
        self.appearance_btn.clicked.connect(self.appearance_clicked)
        # quits for real, even when closing the window would keep the music going in the tray
        self.quit_btn = IconButton("power", "Quit Aria (Ctrl+Q)", tone="secondary")
        self.quit_btn.clicked.connect(self.quit_clicked)
        now = self.items[0]                       # Now Playing: drop songs here to queue them
        now.setAcceptDrops(True)
        now.tracks_dropped.connect(self.tracks_to_queue)
        bottom.addWidget(self.collapse_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.appearance_btn)
        bottom.addWidget(self.quit_btn)
        self._bottom = bottom
        outer.addLayout(bottom)
        self.setFixedWidth(EXPANDED)

    def _section(self, title: str) -> QLabel:
        lbl = label(title, "SectionTitle")
        lbl.setContentsMargins(10, 4, 0, 4)
        self.titles.append(lbl)
        self.body.addWidget(lbl)
        return lbl

    def set_logo(self, pixmap) -> None:
        self.logo.setPixmap(pixmap)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

    # ---- selection -------------------------------------------------------------

    def select(self, index: int, emit: bool = False) -> None:
        for i, item in enumerate(self.items):
            item.setChecked(i == index)
        if emit:
            self.page_selected.emit(index)

    # ---- appearance ------------------------------------------------------------

    def set_collapsed(self, collapsed: bool, emit: bool = False) -> None:
        self.collapsed = collapsed
        self.setFixedWidth(COLLAPSED if collapsed else EXPANDED)
        self.brand.setVisible(not collapsed)
        for lbl in self.titles:
            lbl.setVisible(not collapsed)
        for item in self.items:
            item.collapsed = collapsed
            item.setToolTip(item._tip if collapsed else "")
            item.update()
        # Logo: left-aligned with the brand when expanded, centred in the rail when collapsed.
        self._brand_row.setContentsMargins(0 if collapsed else 18, 0, 0 if collapsed else 8, 0)
        self._brand_row.setAlignment(self.logo, Qt.AlignmentFlag.AlignHCenter if collapsed else Qt.AlignmentFlag.AlignLeft)
        self.collapse_btn.setToolTip("Expand Sidebar" if collapsed else "Collapse Sidebar")
        # Collapsed: the appearance and quit buttons stack *above*; the collapse button stays put.
        self._bottom.setDirection(QHBoxLayout.Direction.BottomToTop if collapsed else QHBoxLayout.Direction.LeftToRight)
        self._bottom.setContentsMargins(15, 8, 15 if collapsed else 10, 0)
        self._relayout()
        QTimer.singleShot(0, self._relayout)      # again once the window is up (at start-up it isn't yet)
        if emit:
            self.collapse_toggled.emit(collapsed)

    def _relayout(self) -> None:
        """Make the item list take the rail's new width now. Laid out while the window was still
        hidden (starting collapsed), it would otherwise keep the old width and the icons sit off-centre."""
        body = self.scroll.widget()
        self.body.invalidate()
        self.body.activate()
        body.resize(self.scroll.viewport().width(), body.sizeHint().height()
                    if body.height() < body.sizeHint().height() else body.height())
        self.body.setGeometry(body.rect())
        for item in self.items:
            item.update()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._relayout)

    def set_appearance(self, appearance: str) -> None:
        self.appearance_btn.set_icon(APPEARANCE_ICON[appearance])
        self.appearance_btn.setToolTip(APPEARANCE_TIP[appearance])
        self.appearance_btn.setAccessibleName(APPEARANCE_TIP[appearance])
