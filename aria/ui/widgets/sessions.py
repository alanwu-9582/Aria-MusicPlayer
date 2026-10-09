"""Listening sessions as quiet cards: "October 9 · Evening Session — 18 tracks"."""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from aria.core.listening import Session
from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.tracklist import paint_empty, reserve_scrollbar

ROW_H = 66
SessionRole = Qt.ItemDataRole.UserRole + 2
PART_ICON = {"Morning": "sun", "Afternoon": "sun", "Evening": "moon", "Night": "moon", "Late Night": "moon"}


def span(s: Session, live: bool) -> str:
    start = time.strftime("%H:%M", time.localtime(s.started))
    if live:
        return f"Since {start}"
    end = time.strftime("%H:%M", time.localtime(s.ended or s.started))
    return f"{start} – {end}"


class SessionModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions: list[Session] = []
        self.live_id: str | None = None

    def set_sessions(self, sessions: list[Session], live_id: str | None) -> None:
        self.beginResetModel()
        self.sessions = list(sessions)
        self.live_id = live_id
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.sessions)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        s = self.sessions[index.row()]
        if role == SessionRole:
            return s
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return s.card
        return None


class SessionDelegate(QStyledItemDelegate):
    def __init__(self, view: SessionListView):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(self.view.viewport().width(), ROW_H)

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        s: Session = index.data(SessionRole)
        live = s.id == index.model().live_id
        r = option.rect.adjusted(0, 2, -2, -2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.view.hover_row
        p.setBrush(theme.color("accent_soft" if selected else ("fill" if hovered else "content")))
        p.drawRoundedRect(QRectF(r), 9, 9)

        # part-of-day mark
        badge = QRectF(r.left() + 12, r.center().y() - 16, 32, 32)
        p.setBrush(theme.color("fill"))
        p.drawRoundedRect(badge, 8, 8)
        part = s.title.split("· ", 1)[-1].replace(" Session", "")
        p.drawPixmap(badge.adjusted(8, 8, -8, -8).toRect(),
                     icons.pixmap(PART_ICON.get(part, "clock"), theme.color("accent" if live else "secondary"), 16))

        # covers of the first songs, overlapping, on the right
        covers = [t.thumbnail for t in s.tracks if t.thumbnail][:3]
        cx = r.right() - 12
        thumbs = thumbs_mod.instance()
        for url in reversed(covers):
            cr = QRectF(cx - 34, r.center().y() - 17, 34, 34)
            pm = thumbs.get(url)
            path = QPainterPath()
            path.addRoundedRect(cr, 6, 6)
            p.save()
            p.setClipPath(path)
            if pm and not pm.isNull():
                side = min(pm.width(), pm.height())
                p.drawPixmap(cr, pm, QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side))
            else:
                p.fillRect(cr, theme.color("fill_strong"))
            p.restore()
            p.setPen(theme.color("content"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cr, 6, 6)
            p.setPen(Qt.PenStyle.NoPen)
            cx -= 22
        text_right = (cx - 22 - 12) if covers else r.right() - 12

        x = int(badge.right()) + 12
        w = max(10, int(text_right - x))
        p.setFont(font("headline"))
        p.setPen(theme.color("label"))
        p.drawText(QRect(x, r.top() + 12, w, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(s.card, Qt.TextElideMode.ElideRight, w))
        p.setFont(font("caption"))
        p.setPen(theme.color("accent" if live else "secondary"))
        sub = " · ".join(b for b in (("Now" if live else ""), span(s, live), s.details) if b)
        p.drawText(QRect(x, r.top() + 33, w, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight, w))
        p.restore()


class SessionListView(QListView):
    opened = Signal(str)                    # session id
    context_requested = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = SessionModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(SessionDelegate(self))
        self.hover_row = -1
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(26)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reserve_scrollbar(self)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.clicked.connect(lambda ix: self.opened.emit(self.model_.sessions[ix.row()].id))
        theme.changed.connect(self.viewport().update)
        thumbs_mod.instance().ready.connect(lambda _k: self.viewport().update())

    def set_sessions(self, sessions: list[Session], live_id: str | None) -> None:
        self.hover_row = -1
        self.model_.set_sessions(sessions, live_id)

    def _context(self, pos: QPoint) -> None:
        ix = self.indexAt(pos)
        if ix.isValid():
            self.setCurrentIndex(ix)
            self.context_requested.emit(self.model_.sessions[ix.row()].id, self.viewport().mapToGlobal(pos))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.scheduleDelayedItemsLayout()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.opened.emit(self.model_.sessions[self.currentIndex().row()].id)
            return
        super().keyPressEvent(e)

    def mouseMoveEvent(self, e):
        row = self.indexAt(e.position().toPoint()).row()
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.model_.rowCount() == 0:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_empty(p, self.viewport().rect(), "clock", "No sessions yet",
                        "Each stretch of listening becomes a card here.")
            p.end()
