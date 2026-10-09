"""Track list: model + painted delegate + view with hover actions, drag reorder and empty state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QToolTip

from aria.core.models import Track, format_duration
from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod

ROW_H = 52
THUMB_W, THUMB_H = 64, 36
TrackRole = Qt.ItemDataRole.UserRole + 1


@dataclass
class RowAction:
    icon: str
    tooltip: str
    run: Callable[[int], None]
    active: Callable[[Track], bool] | None = None   # drawn in accent when true (e.g. saved)


class TrackModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks: list[Track] = []
        self.playing_key: str | None = None
        self.saved: Callable[[str], bool] = lambda _k: False

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tracks)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        if role == TrackRole:
            return t
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{t.title} · {t.artist}" if t.artist else t.title
        return None

    def refresh_key(self, key: str) -> None:
        for i, t in enumerate(self.tracks):
            if t.key == key:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)

    def refresh_all(self) -> None:
        if self.tracks:
            self.dataChanged.emit(self.index(0), self.index(len(self.tracks) - 1))


class TrackDelegate(QStyledItemDelegate):
    def __init__(self, view: TrackListView):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_H)

    def _action_rects(self, rect: QRect) -> list[QRect]:
        n = len(self.view.actions)
        right = rect.right() - 8
        return [QRect(right - (n - i) * 30, rect.top() + (ROW_H - 30) // 2, 30, 30) for i in range(n)]

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        t: Track = index.data(TrackRole)
        model: TrackModel = index.model()
        r = option.rect.adjusted(0, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.view.hover_row
        playing = t.key == model.playing_key

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if selected:
            p.setBrush(theme.color("accent_soft"))
            p.drawRoundedRect(QRectF(r), 7, 7)
        elif hovered:
            p.setBrush(theme.color("fill"))
            p.drawRoundedRect(QRectF(r), 7, 7)

        if self.view.numbered:
            # album track number instead of a thumbnail
            tr = QRectF(r.left() + 8, r.top(), 28, r.height())
            if playing:
                p.drawPixmap(QRectF(tr.center().x() - 8, tr.center().y() - 8, 16, 16).toRect(),
                             icons.pixmap("volume", theme.color("accent"), 16))
            else:
                p.setFont(font("callout", mono=True))
                p.setPen(theme.color("secondary"))
                p.drawText(tr, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))
            self._paint_right_and_text(p, r, tr, t, index, selected, hovered, playing)
            p.restore()
            return

        # thumbnail
        tr = QRectF(r.left() + 8, r.top() + (r.height() - THUMB_H) / 2, THUMB_W, THUMB_H)
        clip = QPainterPath()
        clip.addRoundedRect(tr, 4, 4)
        pm = thumbs_mod.instance().get(t.thumbnail) if t.thumbnail else None
        p.setClipPath(clip)
        if pm and not pm.isNull():
            scaled = pm.size().scaled(tr.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            src_w = pm.width() * tr.width() / scaled.width()
            src_h = pm.height() * tr.height() / scaled.height()
            src = QRectF((pm.width() - src_w) / 2, (pm.height() - src_h) / 2, src_w, src_h)
            p.drawPixmap(tr, pm, src)
        else:
            p.fillRect(tr, theme.color("fill"))
            p.drawPixmap(QRectF(tr.center().x() - 8, tr.center().y() - 8, 16, 16).toRect(),
                         icons.pixmap("music", theme.color("tertiary"), 16))
        p.setClipping(False)
        if playing:
            p.setBrush(theme.color("canvas"))
            p.setOpacity(0.55)
            p.drawRoundedRect(tr, 4, 4)
            p.setOpacity(1)
            p.drawPixmap(QRectF(tr.center().x() - 8, tr.center().y() - 8, 16, 16).toRect(),
                         icons.pixmap("volume", "#ffffff", 16))

        self._paint_right_and_text(p, r, tr, t, index, selected, hovered, playing)
        p.restore()

    def _paint_right_and_text(self, p, r, tr, t, index, selected, hovered, playing) -> None:
        # right side: hover actions, otherwise duration + badges
        show_actions = (hovered or selected) and self.view.actions
        right_edge = r.right() - 12
        if show_actions:
            for rect, act in zip(self._action_rects(r), self.view.actions):
                if self.view.hover_action == (index.row(), act):
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(theme.color("fill_strong"))
                    p.drawRoundedRect(QRectF(rect), 7, 7)
                on = act.active(t) if act.active else False
                color = theme.color("accent") if on else theme.color("label")
                p.drawPixmap(rect.adjusted(7, 7, -7, -7), icons.pixmap(act.icon, color, 16))
            right_edge = self._action_rects(r)[0].left() - 8
        else:
            p.setFont(font("caption", mono=True))
            p.setPen(theme.color("secondary"))
            dur = format_duration(t.duration) if t.duration else ""
            fm = QFontMetrics(p.font())
            dw = fm.horizontalAdvance("0:00:00") if t.duration >= 3600 else fm.horizontalAdvance("00:00")
            p.drawText(QRect(right_edge - dw, r.top(), dw, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur)
            right_edge -= dw + 10
            if t.local_path:
                p.drawPixmap(QRect(right_edge - 14, r.center().y() - 7, 14, 14),
                             icons.pixmap("check-circle", theme.color("secondary"), 14))
                right_edge -= 22

        # texts
        x = int(tr.right()) + 12
        w = max(10, right_edge - x)
        p.setFont(font("body", 600 if playing else 400))
        p.setPen(theme.color("accent") if playing else theme.color("label"))
        fm = QFontMetrics(p.font())
        p.drawText(QRect(x, r.top() + 7, w, 20), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(t.title, Qt.TextElideMode.ElideRight, w))
        p.setFont(font("caption"))
        p.setPen(theme.color("secondary"))
        sub = " · ".join(s for s in (t.artist, t.source_label) if s)
        p.drawText(QRect(x, r.top() + 27, w, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight, w))

    def helpEvent(self, event, view, option, index) -> bool:
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            for rect, act in zip(self._action_rects(option.rect.adjusted(0, 1, -2, -1)), self.view.actions):
                if rect.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), act.tooltip, view)
                    return True
            # Only when the title doesn't fit in the row.
            t: Track = index.data(TrackRole)
            room = option.rect.width() - THUMB_W - 140
            if QFontMetrics(font("body")).horizontalAdvance(t.title) > room:
                QToolTip.showText(event.globalPos(), t.title, view)
            else:
                QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)


class TrackListView(QListView):
    # Set by the app: called with the track the user is resting on, so its stream
    # is resolved before they press play.
    warm_hook: Callable[[Track], None] | None = None

    activated_row = Signal(int)             # double-click / Enter
    delete_pressed = Signal(list)           # rows
    moved = Signal(int, int)                # drag reorder: from, to
    context_requested = Signal(list, QPoint)

    def __init__(self, empty_icon: str = "music", empty_title: str = "", empty_text: str = "",
                 reorderable: bool = False, parent=None):
        super().__init__(parent)
        self.model_ = TrackModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(TrackDelegate(self))
        self.actions: list[RowAction] = []
        self.hover_row = -1
        self.hover_action: tuple[int, RowAction] | None = None
        self.empty = (empty_icon, empty_title, empty_text)
        self.reorderable = reorderable
        self.numbered = False
        self._press: QPoint | None = None
        self._drag_row = -1
        self._drop_row = -1

        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(26)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reserve_scrollbar(self)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.doubleClicked.connect(lambda ix: self.activated_row.emit(ix.row()))
        self._warm = QTimer(self)
        self._warm.setSingleShot(True)
        self._warm.setInterval(350)
        self._warm.timeout.connect(self._warm_current)
        self.selectionModel().currentChanged.connect(lambda *_: self._warm.start())
        theme.changed.connect(self.viewport().update)
        thumbs_mod.instance().ready.connect(lambda _k: self.viewport().update())

    # ---- api -----------------------------------------------------------------

    def set_tracks(self, tracks: list[Track]) -> None:
        self.hover_row = -1
        self.model_.set_tracks(tracks)

    def tracks(self) -> list[Track]:
        return self.model_.tracks

    def selected_rows(self) -> list[int]:
        return sorted(ix.row() for ix in self.selectionModel().selectedIndexes())

    def selected_tracks(self) -> list[Track]:
        return [self.model_.tracks[i] for i in self.selected_rows()]

    def _warm_current(self) -> None:
        ix = self.currentIndex()
        if ix.isValid() and TrackListView.warm_hook:
            TrackListView.warm_hook(self.model_.tracks[ix.row()])

    def set_playing(self, key: str | None) -> None:
        self.model_.playing_key = key
        self.viewport().update()

    # ---- events ------------------------------------------------------------------

    def _context(self, pos: QPoint) -> None:
        ix = self.indexAt(pos)
        if ix.isValid() and not self.selectionModel().isSelected(ix):
            self.setCurrentIndex(ix)
        rows = self.selected_rows()
        if rows:
            self.context_requested.emit(rows, self.viewport().mapToGlobal(pos))

    def _action_at(self, pos: QPoint) -> tuple[int, RowAction] | None:
        ix = self.indexAt(pos)
        if not ix.isValid() or not self.actions:
            return None
        rect = self.visualRect(ix).adjusted(0, 1, -2, -1)
        for r, act in zip(self.itemDelegate()._action_rects(rect), self.actions):
            if r.contains(pos):
                return ix.row(), act
        return None

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        row = self.indexAt(pos).row()
        hit = self._action_at(pos)
        if row != self.hover_row or hit != self.hover_action:
            self.hover_row = row
            self.hover_action = hit
            self.viewport().update()
        if self.reorderable and self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._drag_row < 0 and (pos - self._press).manhattanLength() > 6:
                self._drag_row = self.indexAt(self._press).row()
            if self._drag_row >= 0:
                self._drop_row = self._drop_index(pos)
                self.viewport().update()
                return
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row = -1
        self.hover_action = None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        hit = self._action_at(pos) if e.button() == Qt.MouseButton.LeftButton else None
        if hit:
            hit[1].run(hit[0])
            return
        self._press = pos if e.button() == Qt.MouseButton.LeftButton else None
        self._drag_row = -1
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_row >= 0:
            src, dst = self._drag_row, self._drop_row
            self._drag_row = self._drop_row = -1
            self._press = None
            self.viewport().update()
            if dst > src:
                dst -= 1
            if dst != src and dst >= 0:
                self.moved.emit(src, dst)
            return
        self._press = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._action_at(e.position().toPoint()):
            return
        super().mouseDoubleClickEvent(e)

    def _drop_index(self, pos: QPoint) -> int:
        ix = self.indexAt(pos)
        if not ix.isValid():
            return self.model_.rowCount()
        rect = self.visualRect(ix)
        return ix.row() + (1 if pos.y() > rect.center().y() else 0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.activated_row.emit(self.currentIndex().row())
            return
        if e.key() == Qt.Key.Key_Delete and self.selected_rows():
            self.delete_pressed.emit(self.selected_rows())
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.model_.rowCount() == 0 and any(self.empty[1:]):
            paint_empty(p, self.viewport().rect(), *self.empty)
        if self._drag_row >= 0 and self._drop_row >= 0:
            if self._drop_row < self.model_.rowCount():
                y = self.visualRect(self.model_.index(self._drop_row)).top()
            else:
                y = self.visualRect(self.model_.index(self.model_.rowCount() - 1)).bottom()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(6, y - 1, self.viewport().width() - 12, 2), 0.6, 0.6)
        p.end()


def paint_empty(p: QPainter, rect: QRect, icon: str, title: str, text: str) -> None:
    """§4.16: 44 px icon → 6 → title3 → one secondary sentence, centred."""
    h = 44 + 6 + 22 + (20 if text else 0)
    top = rect.center().y() - h // 2
    p.drawPixmap(QRect(rect.center().x() - 22, top, 44, 44), icons.pixmap(icon, theme.color("tertiary"), 44))
    p.setFont(font("title3"))
    p.setPen(theme.color("label"))
    p.drawText(QRect(rect.left() + 16, top + 50, rect.width() - 32, 22), Qt.AlignmentFlag.AlignCenter, title)
    if text:
        p.setFont(font("body"))
        p.setPen(theme.color("secondary"))
        p.drawText(QRect(rect.left() + 16, top + 74, rect.width() - 32, 20), Qt.AlignmentFlag.AlignCenter, text)


def reserve_scrollbar(view) -> None:
    """§4.18: always keep the scrollbar's space; hide its handle when nothing scrolls."""
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    bar = view.verticalScrollBar()

    def update(_min=0, _max=0):
        empty = bar.maximum() <= bar.minimum()
        if bar.property("empty") != empty:
            bar.setProperty("empty", empty)
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    bar.rangeChanged.connect(update)
    update()
