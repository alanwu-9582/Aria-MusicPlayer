"""Cover wall: the Library as a mosaic of covers (the Library's Collage view).

The same idea as Immersive Mode's Collage, made for browsing: every song is a tile — a square or
a simple rectangle of whole cells (1 × 1 mostly, now and then 2 × 2, 2 × 1 or 1 × 2) — with its
name on its cover, in the Library's order, row after row with no gaps. A song keeps its shape
(it comes from the song, not from chance), so the wall looks the same each time it's opened.

It takes the same items as the Disc Rack and answers the same way: a click selects (the detail
strip below shows it), a double-click or Enter plays, right-click / Menu opens the song's actions,
the arrow keys walk the wall, and songs can be dragged out onto the drop targets. Tiles never
move when covers arrive: a cover still on its way is a quiet tile of the same size.
"""

from __future__ import annotations

import math
import random
import zlib

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.ui import icons
from aria.ui.theme import font, radius, theme
from aria.ui.widgets import thumbs as thumbs_mod

CELL = 132                    # about this wide; the cells share the width exactly
GAP = 6
MARGIN = 12
POP = 2.0                     # the hovered tile lifts by this much on every side
SHAPES = ((1, 1, 9), (2, 2, 3), (2, 1, 2), (1, 2, 2))      # cols, rows, weight


def wall_layout(keys: list[str], cols: int) -> tuple[list[tuple[int, int, int, int]], int]:
    """Cells (x, y, w, h) for each key, in order, filling the first free cell each time (so no
    gaps but at the very end); each song's shape comes from its key. Returns them and the rows."""
    taken: set[tuple[int, int]] = set()
    out = []
    cx = cy = 0
    for key in keys:
        while (cx, cy) in taken:
            cx += 1
            if cx >= cols:
                cx, cy = 0, cy + 1
        options = [(a, b, w) for a, b, w in SHAPES
                   if cx + a <= cols and not any((cx + i, cy + j) in taken for i in range(a) for j in range(b))]
        a, b, _w = random.Random(zlib.crc32(key.encode("utf-8"))).choices(options, [o[2] for o in options])[0]
        for i in range(a):
            for j in range(b):
                taken.add((cx + i, cy + j))
        out.append((cx, cy, a, b))
    rows = max((y + h for _x, y, _w, h in out), default=0)
    return out, rows


def _blit(p: QPainter, r: QRectF, pm: QPixmap) -> None:
    """A cover into any rectangle: its middle, never stretched."""
    w, h = pm.width(), pm.height()
    if w / max(1, h) > r.width() / r.height():
        sw = h * r.width() / r.height()
        src = QRectF((w - sw) / 2, 0, sw, h)
    else:
        sh = w * r.height() / r.width()
        src = QRectF(0, (h - sh) / 2, w, sh)
    p.drawPixmap(r, pm, src)


class CoverWall(QWidget):
    """The painted wall. Indices refer to the item list given to ``set_items`` (RackItems)."""

    activated = Signal(int)
    clicked_item = Signal(int)
    selection_changed = Signal(int)
    context = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.selected = -1
        self.playing_key: str | None = None
        self.reorderable = False              # (the wall keeps the Library's order)
        self.drag_tracks = None               # i → [Track]: lets a tile be carried out
        self._cells: list[tuple[int, int, int, int]] = []
        self._rects: list[QRectF] = []
        self._cell = float(CELL)
        self._laid = (-1, ())
        self._hover = -1
        self._kbd = False                     # keyboard focus shows (not after a click)
        self._press: QPoint | None = None
        self._pix: dict[tuple, QPixmap] = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        theme.changed.connect(self.update)

    # ---- api -----------------------------------------------------------------

    def set_items(self, items, keep_key: str | None = None) -> None:
        self.items = items
        keys = [it.key for it in items]
        self.selected = keys.index(keep_key) if keep_key in keys else -1
        self._hover = -1
        if len(self._pix) > 400:
            self._pix.clear()
        self._relayout()

    def select(self, i: int, emit: bool = True) -> None:
        i = i if 0 <= i < len(self.items) else -1
        if i == self.selected:
            return
        old, self.selected = self.selected, i
        for j in (old, i):
            self._update_tile(j)
        if i >= 0:
            self.ensure_visible(i)
        if emit:
            self.selection_changed.emit(i)

    def set_playing(self, key: str | None) -> None:
        self.playing_key = key
        self.update()

    def refresh_covers(self) -> None:
        self.update()

    def index_at_y(self, y: float) -> int:
        """The first tile showing at height ``y`` (the place to keep when the view changes)."""
        best = -1
        for i, r in enumerate(self._rects):
            if r.bottom() > y and (best < 0 or (r.top(), r.left()) < (self._rects[best].top(), self._rects[best].left())):
                best = i
        return best

    def top_of(self, i: int) -> int:
        return int(self._rects[i].top() - MARGIN) if 0 <= i < len(self._rects) else 0

    def ensure_visible(self, i: int) -> None:
        area = self.parent().parent() if self.parent() else None
        if area is not None and hasattr(area, "ensureVisible") and 0 <= i < len(self._rects):
            r = self._rects[i]
            area.ensureVisible(int(r.center().x()), int(r.center().y()), 20, int(r.height() / 2) + 16)

    # ---- geometry ------------------------------------------------------------

    def _relayout(self) -> None:
        w = max(1, self.width() - 2 * MARGIN)
        cols = max(2, int((w + GAP) // (CELL + GAP)))
        self._cell = w / cols
        keys = tuple(it.key for it in self.items)
        if self._laid != (cols, keys):
            self._cells, rows = wall_layout(list(keys), cols)
            self._laid = (cols, keys)
        else:
            rows = max((y + h for _x, y, _w, h in self._cells), default=0)
        c = self._cell
        self._rects = [QRectF(MARGIN + x * c + GAP / 2, MARGIN + y * c + GAP / 2, a * c - GAP, b * c - GAP)
                       for x, y, a, b in self._cells]
        self.setMinimumHeight(int(rows * c + 2 * MARGIN))
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(600, 400)

    def _index_at(self, pos) -> int:
        p = QPointF(pos)
        return next((i for i, r in enumerate(self._rects) if r.contains(p)), -1)

    def _shown(self, i: int) -> QRectF:
        r = self._rects[i]
        return r.adjusted(-POP, -POP, POP, POP) if i == self._hover else r

    def _update_tile(self, i: int) -> None:
        if 0 <= i < len(self._rects):
            self.update(self._rects[i].adjusted(-POP - 3, -POP - 3, POP + 3, POP + 3).toAlignedRect())

    # ---- events --------------------------------------------------------------

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.drag_tracks is not None and self._press is not None and e.buttons() & Qt.MouseButton.LeftButton \
                and (pos - self._press).manhattanLength() > 8:
            i = self._index_at(self._press)
            self._press = None
            if i >= 0:
                from aria.ui.widgets import dragdrop
                dragdrop.start(self, self.drag_tracks(i))
            return
        i = self._index_at(pos)
        if i != self._hover:
            old, self._hover = self._hover, i
            self._update_tile(old)
            self._update_tile(i)
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e):
        old, self._hover = self._hover, -1
        self._update_tile(old)

    def mousePressEvent(self, e):
        i = self._index_at(e.position().toPoint())
        self._kbd = False
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self.select(i)                    # a click on empty space clears the selection
        elif e.button() == Qt.MouseButton.RightButton and i >= 0:
            self.select(i)

    def mouseReleaseEvent(self, e):
        if self._press is not None and e.button() == Qt.MouseButton.LeftButton:
            i = self._index_at(e.position().toPoint())
            if i >= 0 and i == self._index_at(self._press):
                self.clicked_item.emit(i)
        self._press = None

    def mouseDoubleClickEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i >= 0:
            self.activated.emit(i)

    def contextMenuEvent(self, e):
        mouse = e.reason() == e.Reason.Mouse
        i = self._index_at(e.pos()) if mouse else self.selected
        if i >= 0:
            self.context.emit(i, e.globalPos() if mouse else self.mapToGlobal(self._rects[i].center().toPoint()))

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self._kbd = True
            if self.selected < 0 and self.items:
                self.select(0)
        self._update_tile(self.selected)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._update_tile(self.selected)

    def keyPressEvent(self, e):
        k = e.key()
        dirs = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if k in dirs and self.items:
            self._kbd = True
            self.select(self._step(*dirs[k]))
            return
        if k in (Qt.Key.Key_Home, Qt.Key.Key_End) and self.items:
            self._kbd = True
            self.select(0 if k == Qt.Key.Key_Home else len(self.items) - 1)
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.selected >= 0:
            self.activated.emit(self.selected)
            return
        if k == Qt.Key.Key_Escape and self.selected >= 0:
            self.select(-1)
            return
        super().keyPressEvent(e)

    def _step(self, dx: int, dy: int) -> int:
        """The nearest tile that way from the selected one (the first one when nothing is selected)."""
        cur = self.selected
        if cur < 0:
            return 0
        here = self._rects[cur].center()
        best, score = cur, math.inf
        for i, r in enumerate(self._rects):
            d = r.center() - here
            along = d.x() * dx + d.y() * dy
            if i == cur or along <= 1:
                continue
            across = abs(d.y()) if dx else abs(d.x())
            s = along + 2 * across
            if s < score:
                best, score = i, s
        return best

    # ---- painting ------------------------------------------------------------

    def _cover(self, i: int, r: QRectF, dpr: float) -> QPixmap | None:
        it = self.items[i]
        big = max(r.width(), r.height()) * dpr > 200
        th = thumbs_mod.instance()
        src = th.get(it.thumb, 360) if (big and it.thumb) else None
        if src is None or src.isNull():
            src = it.cover()
        if src is None or src.isNull():
            return None
        key = (it.key, src.cacheKey(), int(r.width()), int(r.height()), dpr)
        pm = self._pix.get(key)
        if pm is None:
            pm = QPixmap(round(r.width() * dpr), round(r.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            _blit(q, QRectF(0, 0, r.width(), r.height()), src)
            q.end()
            self._pix[key] = pm
        return pm

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dirty = QRectF(e.rect()).adjusted(-POP - 3, -POP - 3, POP + 3, POP + 3)
        dpr = self.devicePixelRatioF()
        for i, r in enumerate(self._rects):
            if i != self._hover and r.intersects(dirty):
                self._paint_tile(p, i, r, dpr)
        if 0 <= self._hover < len(self._rects):
            self._paint_tile(p, self._hover, self._shown(self._hover), dpr)
        p.end()

    def _paint_tile(self, p: QPainter, i: int, r: QRectF, dpr: float) -> None:
        it = self.items[i]
        rad = radius(6, r.width(), r.height())
        clip = QPainterPath()
        clip.addRoundedRect(r, rad, rad)
        p.save()
        p.setClipPath(clip)
        pm = self._cover(i, self._rects[i], dpr)
        if pm is not None:
            p.drawPixmap(r, pm, QRectF(pm.rect()))
        else:
            p.fillRect(r, theme.color("fill_strong"))              # the same size while the cover loads
            s = min(32.0, r.width() * 0.25)
            p.drawPixmap(QRectF(r.center().x() - s / 2, r.center().y() - s / 2 - 8, s, s).toRect(),
                         icons.pixmap("music", theme.color("tertiary"), int(s)))
        hovered = i == self._hover
        # the name on the cover, bottom left, over a soft shade
        side = min(r.width(), r.height())
        band_h = min(r.height(), 64.0)
        band = QRectF(r.left(), r.bottom() - band_h, r.width(), band_h)
        g = QLinearGradient(band.topLeft(), band.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 175 if hovered else 150))
        p.fillRect(band, g)
        pad = 9.0 if side < 200 else 12.0
        w = int(r.width() - 2 * pad)
        tf = font("callout", 600) if side < 200 else font("headline")
        sf = font("caption")
        fm_t, fm_s = QFontMetrics(tf), QFontMetrics(sf)
        sub_y = r.bottom() - pad - fm_s.height()
        p.setFont(tf)
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QRectF(r.left() + pad, sub_y - fm_t.height(), w, fm_t.height()), Qt.AlignmentFlag.AlignLeft,
                   fm_t.elidedText(it.title, Qt.TextElideMode.ElideRight, w))
        p.setFont(sf)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(r.left() + pad, sub_y, w, fm_s.height()), Qt.AlignmentFlag.AlignLeft,
                   fm_s.elidedText(it.subtitle, Qt.TextElideMode.ElideRight, w))
        if it.key == self.playing_key:
            # playing: a small fixed mark in the corner (never a coloured wash over the cover)
            mark = QRectF(r.left() + 8, r.top() + 8, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(mark, 7, 7)
            p.drawPixmap(mark.adjusted(4, 4, -4, -4).toRect(), icons.pixmap("volume", theme.color("on_accent"), 14))
        if it.offline == "full":
            p.drawPixmap(QRectF(r.right() - 22, r.top() + 8, 14, 14).toRect(),
                         icons.pixmap("check-circle", QColor(255, 255, 255, 200), 14))
        p.restore()
        selected = i == self.selected
        focused = selected and self._kbd and self.hasFocus()
        if selected or focused:
            ring = theme.color("accent") if focused else theme.color("label")
            if not focused:
                ring.setAlphaF(0.55)                                  # chosen: clear, but quiet
            pen = QPen(ring)
            pen.setWidthF(2.0)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            o = r.adjusted(-3, -3, 3, 3)
            ro = radius(rad + 3, o.width(), o.height())
            p.drawRoundedRect(o, ro, ro)
