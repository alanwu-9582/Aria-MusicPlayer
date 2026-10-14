"""Track list: model + painted delegate + view with hover actions, drag reorder and empty state.

A row's states are told apart by different means, never by one colour for all:
hover — a faint background; selected — a clear but quiet neutral background; playing — a fixed
speaker mark on the cover (and the title in the accent); keyboard focus — an accent outline;
unavailable (a local file that's gone) — dimmed, still readable.

Titles are the cleaned song name; a version (Live, Cover, Remix…) shows as a small tag of its
own. A list can fold uploads of one song under the first (``set_groups``): “2 versions” on that
row opens them. New contents keep the list's place, selection and current row whenever the
songs are still there, so updates never jump to the top or flash.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListView, QScrollArea, QStyle, QStyledItemDelegate, QToolTip

from aria.core import identity, textnorm
from aria.core.models import LOCAL, Track, format_duration
from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import SELF_EXPLANATORY

ROW_H = 52
THUMB_W, THUMB_H = 64, 36
INDENT = 26                    # a folded version, under its song
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
        self.tracks: list[Track] = []           # the rows as shown
        self.depth: list[int] = []              # per row: 0, or 1 for a version folded under its song
        self.more: list[int] = []               # per row: versions folded under it
        self.groups: list[list[Track]] = []
        self.expanded: set[str] = set()         # songs whose versions show
        self.playing_key: str | None = None
        self._gone: dict[str, bool] = {}

    def set_groups(self, groups: list[list[Track]]) -> None:
        self.beginResetModel()
        self.groups = [list(g) for g in groups if g]
        heads = {g[0].key for g in self.groups}
        self.expanded &= heads
        self._flatten()
        self.endResetModel()

    def set_tracks(self, tracks: list[Track]) -> None:
        self.set_groups([[t] for t in tracks])

    def _flatten(self) -> None:
        self.tracks, self.depth, self.more = [], [], []
        self._gone.clear()
        for g in self.groups:
            self.tracks.append(g[0])
            self.depth.append(0)
            self.more.append(len(g) - 1)
            if g[0].key in self.expanded:
                for t in g[1:]:
                    self.tracks.append(t)
                    self.depth.append(1)
                    self.more.append(0)

    def toggle(self, row: int) -> None:
        """Show or fold the versions under the song on ``row``."""
        if not (0 <= row < len(self.tracks)) or not self.more[row]:
            return
        key, n = self.tracks[row].key, self.more[row]
        if key in self.expanded:
            self.beginRemoveRows(QModelIndex(), row + 1, row + n)
            self.expanded.discard(key)
            self._flatten()
            self.endRemoveRows()
        else:
            self.beginInsertRows(QModelIndex(), row + 1, row + n)
            self.expanded.add(key)
            self._flatten()
            self.endInsertRows()
        ix = self.index(row)
        self.dataChanged.emit(ix, ix)

    def unavailable(self, t: Track) -> bool:
        """A local song whose file isn't there any more (asked once per song while it's listed)."""
        gone = self._gone.get(t.key)
        if gone is None:
            gone = self._gone[t.key] = t.source == LOCAL and not (t.local_path and os.path.exists(t.local_path))
        return gone

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.tracks)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        if role == TrackRole:
            return t
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            name, who = identity.display(t)
            return f"{name} · {who}" if who else name
        return None

    def refresh_key(self, key: str) -> None:
        self._gone.pop(key, None)
        for i, t in enumerate(self.tracks):
            if t.key == key:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)

    def refresh_all(self) -> None:
        self._gone.clear()
        if self.tracks:
            self.dataChanged.emit(self.index(0), self.index(len(self.tracks) - 1))


def _pill(p: QPainter, x: float, y: float, text: str, strong: bool = False, chevron: str = "") -> QRectF:
    """A small tag (a version, “2 versions”) at (x, middle y); returns where it went."""
    f = font("caption", 600)
    fm = QFontMetrics(f)
    w = fm.horizontalAdvance(text) + 14 + (14 if chevron else 0)
    r = QRectF(x, y - 9, w, 18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.color("fill_strong" if strong else "fill"))
    p.drawRoundedRect(r, 5, 5)
    p.setFont(f)
    p.setPen(theme.color("secondary"))
    p.drawText(r.adjusted(7, 0, -(19 if chevron else 7), 0), Qt.AlignmentFlag.AlignVCenter, text)
    if chevron:
        p.drawPixmap(QRectF(r.right() - 18, r.center().y() - 6, 12, 12).toRect(),
                     icons.pixmap(chevron, theme.color("secondary"), 12))
    return r


def _pill_width(text: str, chevron: bool = False) -> int:
    return QFontMetrics(font("caption", 600)).horizontalAdvance(text) + 14 + (14 if chevron else 0)


class TrackDelegate(QStyledItemDelegate):
    def __init__(self, view: TrackListView):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        # Rows always span the visible width (a list first laid out while hidden would
        # otherwise keep a stale width and draw its row buttons off the edge).
        return QSize(self.view.viewport().width(), ROW_H)

    def _action_rects(self, rect: QRect) -> list[QRect]:
        n = len(self.view.actions)
        right = rect.right() - 8
        return [QRect(right - (n - i) * 30, rect.top() + (ROW_H - 30) // 2, 30, 30) for i in range(n)]

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        t: Track = index.data(TrackRole)
        model: TrackModel = index.model()
        row = index.row()
        r = option.rect.adjusted(0, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = row == self.view.hover_row
        playing = t.key == model.playing_key
        gone = model.unavailable(t)
        depth = model.depth[row] if row < len(model.depth) else 0

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if selected:
            p.setBrush(theme.color("selection"))
            p.drawRoundedRect(QRectF(r), 7, 7)
        elif hovered:
            p.setBrush(theme.color("hover"))
            p.drawRoundedRect(QRectF(r), 7, 7)
        if self.view.keyboard_focus() and self.view.currentIndex().row() == row:
            pen = QPen(theme.color("accent"))
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.75, 0.75, -0.75, -0.75), 7, 7)
            p.setPen(Qt.PenStyle.NoPen)
        if depth:
            # a folded version: indented under its song, with a quiet line down the side
            p.setBrush(theme.color("separator_hex"))
            p.drawRoundedRect(QRectF(r.left() + 14, r.top() + 6, 2, r.height() - 12), 1, 1)
            r = r.adjusted(INDENT, 0, 0, 0)

        if self.view.numbered:
            # album track number instead of a thumbnail
            tr = QRectF(r.left() + 8, r.top(), 28, r.height())
            if playing:
                p.drawPixmap(QRectF(tr.center().x() - 8, tr.center().y() - 8, 16, 16).toRect(),
                             icons.pixmap("volume", theme.color("accent"), 16))
            else:
                p.setFont(font("callout", mono=True))
                p.setPen(theme.color("secondary"))
                p.drawText(tr, Qt.AlignmentFlag.AlignCenter, str(row + 1))
            self._paint_right_and_text(p, r, tr, t, index, selected, hovered, playing, gone)
            p.restore()
            return

        # thumbnail
        tr = QRectF(r.left() + 8, r.top() + (r.height() - THUMB_H) / 2, THUMB_W, THUMB_H)
        clip = QPainterPath()
        clip.addRoundedRect(tr, 4, 4)
        pm = thumbs_mod.instance().get(t.thumbnail) if t.thumbnail else None
        p.setClipPath(clip)
        if gone:
            p.setOpacity(0.45)
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
        p.setOpacity(1)
        p.setClipping(False)
        if playing:
            p.setBrush(theme.color("canvas"))
            p.setOpacity(0.55)
            p.drawRoundedRect(tr, 4, 4)
            p.setOpacity(1)
            p.drawPixmap(QRectF(tr.center().x() - 8, tr.center().y() - 8, 16, 16).toRect(),
                         icons.pixmap("volume", "#ffffff", 16))

        self._paint_right_and_text(p, r, tr, t, index, selected, hovered, playing, gone)
        p.restore()

    def _paint_right_and_text(self, p, r, tr, t, index, selected, hovered, playing, gone=False) -> None:
        model: TrackModel = index.model()
        row = index.row()
        # right side: hover actions, otherwise duration + badges
        show_actions = (hovered or selected) and self.view.actions
        right_edge = r.right() - 12
        if show_actions:
            for rect, act in zip(self._action_rects(r), self.view.actions):
                if self.view.hover_action == (row, act):
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

        # texts: the song's name, its version as a tag, the folded versions; the artist below
        x = int(tr.right()) + 12
        w = max(10, right_edge - x)
        name, who, tag = identity.display_parts(t)
        more = model.more[row] if row < len(model.more) else 0
        more_text = f"{more} version{'s' if more != 1 else ''}" if more else ""
        extra = (_pill_width(tag) + 6 if tag else 0) + (_pill_width(more_text, True) + 6 if more else 0)
        p.setFont(font("body", 600 if playing else 400))
        p.setPen(theme.color("accent") if playing else theme.color("secondary" if gone else "label"))
        fm = QFontMetrics(p.font())
        shown = fm.elidedText(name, Qt.TextElideMode.ElideRight, max(10, w - extra))
        p.drawText(QRect(x, r.top() + 7, w, 20), Qt.AlignmentFlag.AlignVCenter, shown)
        px = x + fm.horizontalAdvance(shown) + 6
        mid = r.top() + 17
        if tag:
            px = _pill(p, px, mid, tag).right() + 6
        if more:
            open_ = t.key in model.expanded
            hit = _pill(p, px, mid, more_text, strong=open_, chevron="chevron-down" if open_ else "chevron-right")
            self.view.more_hits[row] = hit.toAlignedRect()
        p.setFont(font("caption"))
        sub = " · ".join(s for s in (who, t.source_label) if s)
        fs = QFontMetrics(p.font())
        p.setPen(theme.color("tertiary" if gone else "secondary"))
        sub_text = fs.elidedText(sub, Qt.TextElideMode.ElideRight, w)
        p.drawText(QRect(x, r.top() + 27, w, 18), Qt.AlignmentFlag.AlignVCenter, sub_text)
        if self.view.raw_titles and textnorm.fold(t.title) != textnorm.fold(name) and sub_text == sub:
            # what the upload itself is called: quieter, after the artist (Search)
            used = fs.horizontalAdvance(sub + "   ")
            p.setPen(theme.color("tertiary"))
            p.drawText(QRect(x + used, r.top() + 27, max(0, w - used), 18), Qt.AlignmentFlag.AlignVCenter,
                       fs.elidedText(t.title, Qt.TextElideMode.ElideRight, max(0, w - used)))

    def helpEvent(self, event, view, option, index) -> bool:
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            for rect, act in zip(self._action_rects(option.rect.adjusted(0, 1, -2, -1)), self.view.actions):
                if rect.contains(event.pos()):
                    if act.icon in SELF_EXPLANATORY:
                        QToolTip.hideText()     # a heart or an × says it already
                    else:
                        QToolTip.showText(event.globalPos(), act.tooltip, view)
                    return True
            # Only when the title doesn't fit in the row.
            t: Track = index.data(TrackRole)
            room = option.rect.width() - THUMB_W - 140
            name = identity.display(t)[0]
            if QFontMetrics(font("body")).horizontalAdvance(name) > room:
                QToolTip.showText(event.globalPos(), name, view)
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
        self.more_hits: dict[int, QRect] = {}  # row → its “N versions” tag (where it was painted)
        self.empty = (empty_icon, empty_title, empty_text)
        self.reorderable = reorderable
        self.drag_out = False                 # songs can be dragged out onto drop targets
        self.numbered = False
        self.raw_titles = False               # also show what each upload is called (Search)
        self._kbd = False                     # the focus came from the keyboard: outline the current row
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
        self.set_groups([[t] for t in tracks])

    def set_groups(self, groups: list[list[Track]]) -> None:
        """New contents. Songs still there keep their selection, the current row stays, and the
        list stays where it was (the first song in sight stays first); nothing changes at all
        when the songs are the same."""
        m = self.model_
        if [[t.key for t in g] for g in groups] == [[t.key for t in g] for g in m.groups]:
            m.groups = [list(g) for g in groups]
            m._flatten()
            m.refresh_all()
            return
        chosen = {t.key for t in self.selected_tracks()}
        cur = self.currentIndex().row()
        cur_key = m.tracks[cur].key if 0 <= cur < len(m.tracks) else None
        bar = self.verticalScrollBar()
        value = bar.value()
        top = self.indexAt(QPoint(8, 1)).row()
        # songs came or went above: follow the first one in sight; the same songs in a new order
        # (or a list at its top): stay put
        new = {t.key for g in groups for t in (g if g and g[0].key in m.expanded else g[:1])}
        old = {t.key for t in m.tracks}
        same_songs = new == old
        if not new & old:
            value = 0                                  # other songs altogether: from the top
        top_key = m.tracks[top].key if 0 <= top < len(m.tracks) and value > 0 and not same_songs else None
        into = value - top * ROW_H if top >= 0 else 0
        self.hover_row = -1
        self.more_hits.clear()
        m.set_groups(groups)
        keys = [t.key for t in m.tracks]
        sel = self.selectionModel()
        self.doItemsLayout()
        auto = self.hasAutoScroll()
        self.setAutoScroll(False)                     # putting the current row back mustn't scroll to it
        if cur_key in keys:
            sel.setCurrentIndex(m.index(keys.index(cur_key)), sel.SelectionFlag.NoUpdate)
        for i, k in enumerate(keys):
            if k in chosen:
                sel.select(m.index(i), sel.SelectionFlag.Select)
        self.setAutoScroll(auto)
        bar.setValue(keys.index(top_key) * ROW_H + into if top_key in keys else value)

    def tracks(self) -> list[Track]:
        return self.model_.tracks

    def selected_rows(self) -> list[int]:
        return sorted(ix.row() for ix in self.selectionModel().selectedIndexes())

    def selected_tracks(self) -> list[Track]:
        return [self.model_.tracks[i] for i in self.selected_rows() if i < len(self.model_.tracks)]

    def _warm_current(self) -> None:
        ix = self.currentIndex()
        if ix.isValid() and TrackListView.warm_hook:
            TrackListView.warm_hook(self.model_.tracks[ix.row()])

    def set_playing(self, key: str | None) -> None:
        self.model_.playing_key = key
        self.viewport().update()

    def keyboard_focus(self) -> bool:
        return self._kbd and self.hasFocus()

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

    def _more_at(self, pos: QPoint) -> int:
        row = self.indexAt(pos).row()
        hit = self.more_hits.get(row)
        return row if hit is not None and hit.adjusted(-3, -4, 3, 4).contains(pos) else -1

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        row = self.indexAt(pos).row()
        hit = self._action_at(pos)
        if row != self.hover_row or hit != self.hover_action:
            self.hover_row = row
            self.hover_action = hit
            self.viewport().update()
        if self.drag_out and not self.reorderable and self._press is not None \
                and e.buttons() & Qt.MouseButton.LeftButton and (pos - self._press).manhattanLength() > 8:
            row = self.indexAt(self._press).row()
            if row >= 0:
                rows = self.selected_rows() if row in self.selected_rows() else [row]
                self._press = None
                from aria.ui.widgets import dragdrop
                dragdrop.start(self, [self.model_.tracks[r] for r in rows])
                return
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
        self._kbd = False
        hit = self._action_at(pos) if e.button() == Qt.MouseButton.LeftButton else None
        if hit:
            hit[1].run(hit[0])
            self.viewport().update()            # the icon shows the new state (queued, saved…)
            return
        more = self._more_at(pos) if e.button() == Qt.MouseButton.LeftButton else -1
        if more >= 0:
            self.more_hits.clear()
            self.model_.toggle(more)
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
        pos = e.position().toPoint()
        if self._action_at(pos) or self._more_at(pos) >= 0:
            return
        super().mouseDoubleClickEvent(e)

    def focusInEvent(self, e):
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def _drop_index(self, pos: QPoint) -> int:
        ix = self.indexAt(pos)
        if not ix.isValid():
            return self.model_.rowCount()
        rect = self.visualRect(ix)
        return ix.row() + (1 if pos.y() > rect.center().y() else 0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                       Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._kbd = True
            self.viewport().update()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.activated_row.emit(self.currentIndex().row())
            return
        if e.key() == Qt.Key.Key_Delete and self.selected_rows():
            self.delete_pressed.emit(self.selected_rows())
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.scheduleDelayedItemsLayout()       # re-measure rows for the new width

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


def plain_scroll(reserve: bool = True) -> QScrollArea:
    """The app's scroll area: frameless, see-through, vertical only (scrollbar space kept unless ``reserve`` is off)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
    if reserve:
        reserve_scrollbar(area)
    return area


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
