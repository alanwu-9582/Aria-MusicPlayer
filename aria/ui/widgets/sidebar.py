"""Sidebar (§6.1): 212 expanded / 60 collapsed, grouped items 32 high, playlists,
collapse + appearance at the bottom. Only the item list scrolls (§6.5)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import IconButton, label

EXPANDED, COLLAPSED = 212, 60
APPEARANCE_ICON = {"system": "monitor", "light": "sun", "dark": "moon"}
APPEARANCE_TIP = {"system": "Appearance: System", "light": "Appearance: Light", "dark": "Appearance: Dark"}


class SidebarItem(QAbstractButton):
    def __init__(self, icon: str, text: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tip = tooltip or text
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
        return QSize(32, 32)          # width comes from the sidebar, never from the item

    def minimumSizeHint(self) -> QSize:
        return QSize(32, 32)

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
    playlist_selected = Signal(str)
    playlist_context = Signal(str, object)      # id, global pos
    new_playlist = Signal()
    collapse_toggled = Signal(bool)
    appearance_clicked = Signal()

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
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
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
            for icon, text in pages:
                n += 1
                item = SidebarItem(icon, text, f"{text}（Ctrl+{n}）")
                item.clicked.connect(lambda _c=False, k=len(self.items): self.select(k, emit=True))
                self.items.append(item)
                self.body.addWidget(item)

        # playlists
        self.body.addSpacing(12)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.pl_title = self._section("Playlists", add_to_body=False)
        self.add_btn = IconButton("plus", "New Playlist (Ctrl+N)", size=22, icon_size=14, tone="secondary")
        self.add_btn.clicked.connect(self.new_playlist)
        head.addWidget(self.pl_title, 1)
        head.addWidget(self.add_btn)
        head.addSpacing(6)
        self.body.addLayout(head)
        self.pl_box = QVBoxLayout()
        self.pl_box.setSpacing(2)
        self.body.addLayout(self.pl_box)
        self.pl_items: dict[str, SidebarItem] = {}
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
        bottom.addWidget(self.collapse_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.appearance_btn)
        self._bottom = bottom
        outer.addLayout(bottom)
        self.setFixedWidth(EXPANDED)

    def _section(self, title: str, add_to_body: bool = True) -> QLabel:
        lbl = label(title, "SectionTitle")
        lbl.setContentsMargins(10, 4, 0, 4)
        self.titles.append(lbl)
        if add_to_body:
            self.body.addWidget(lbl)
        return lbl

    def set_logo(self, pixmap) -> None:
        self.logo.setPixmap(pixmap)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

    # ---- playlists -------------------------------------------------------------

    def set_playlists(self, playlists: list[tuple[str, str]]) -> None:
        current = next((pid for pid, it in self.pl_items.items() if it.isChecked()), None)
        for it in self.pl_items.values():
            it.deleteLater()
        self.pl_items.clear()
        for pid, name in playlists:
            it = SidebarItem("list", name)
            it.collapsed = self.collapsed
            it.setToolTip(name if self.collapsed else "")
            it.setChecked(pid == current)
            it.clicked.connect(lambda _c=False, p=pid: self.select_playlist(p, emit=True))
            it.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            it.customContextMenuRequested.connect(
                lambda pos, p=pid, w=it: self.playlist_context.emit(p, w.mapToGlobal(pos)))
            self.pl_items[pid] = it
            self.pl_box.addWidget(it)

    # ---- selection -------------------------------------------------------------

    def select(self, index: int, emit: bool = False) -> None:
        for i, item in enumerate(self.items):
            item.setChecked(i == index)
        for it in self.pl_items.values():
            it.setChecked(False)
        if emit:
            self.page_selected.emit(index)

    def select_playlist(self, pid: str, emit: bool = False) -> None:
        for item in self.items:
            item.setChecked(False)
        for p, it in self.pl_items.items():
            it.setChecked(p == pid)
        if emit:
            self.playlist_selected.emit(pid)

    # ---- appearance ------------------------------------------------------------

    def set_collapsed(self, collapsed: bool, emit: bool = False) -> None:
        self.collapsed = collapsed
        self.setFixedWidth(COLLAPSED if collapsed else EXPANDED)
        self.brand.setVisible(not collapsed)
        for lbl in self.titles:
            lbl.setVisible(not collapsed)
        self.add_btn.setVisible(not collapsed)
        for item in self.items + list(self.pl_items.values()):
            item.collapsed = collapsed
            item.setToolTip(item._tip if collapsed else "")
            item.update()
        # Logo: left-aligned with the brand when expanded, centred in the rail when collapsed.
        self._brand_row.setContentsMargins(0 if collapsed else 18, 0, 0 if collapsed else 8, 0)
        self._brand_row.setAlignment(self.logo, Qt.AlignmentFlag.AlignHCenter if collapsed else Qt.AlignmentFlag.AlignLeft)
        self.collapse_btn.setToolTip("Expand Sidebar" if collapsed else "Collapse Sidebar")
        # Collapsed: the appearance button stacks *above*; the collapse button stays put.
        self._bottom.setDirection(QHBoxLayout.Direction.BottomToTop if collapsed else QHBoxLayout.Direction.LeftToRight)
        self._bottom.setContentsMargins(15, 8, 15 if collapsed else 10, 0)
        if emit:
            self.collapse_toggled.emit(collapsed)

    def set_appearance(self, appearance: str) -> None:
        self.appearance_btn.set_icon(APPEARANCE_ICON[appearance])
        self.appearance_btn.setToolTip(APPEARANCE_TIP[appearance])
        self.appearance_btn.setAccessibleName(APPEARANCE_TIP[appearance])
