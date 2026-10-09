"""CD rack: items stand on shelves as jewel-case spines (§ Shelf & Disc Rack View).

Hovering — or moving the keyboard focus to — a case pulls it partly out of
the rack: it lifts a little and its front cover slides out beside the spine
while the cases after it ease aside. Every row keeps that much room free at
its end, so nothing is covered, clipped or re-wrapped. Only the row in motion
is repainted, and nothing redraws while the rack is idle.

Shared by the Library and playlist pages (Disc Rack) and the Shelf (Spine).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.tracklist import paint_empty

PLANK_H = 7
TOP_ROOM = 12                 # space above the cases for the lift
BOTTOM_ROOM = 20
MARGIN = 16
GAP = 3
LIFT = 7
ANIM_MS = 190
KIND_ICON = {"single": "disc", "album": "disc", "playlist": "list"}

reduce_motion = False         # set from the settings: hover state changes instantly


@dataclass
class RackItem:
    key: str
    title: str
    subtitle: str = ""
    kind: str = "single"                                    # single | album | playlist
    cover: Callable[[], QPixmap | None] = field(default=lambda: None)
    tooltip: str = ""


def _square(pm: QPixmap, side: int, dpr: float) -> QPixmap:
    """Centre-cropped square, rendered once at the size it's drawn."""
    out = QPixmap(round(side * dpr), round(side * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    s = min(pm.width(), pm.height())
    p.drawPixmap(QRectF(0, 0, side, side), pm, QRectF((pm.width() - s) / 2, (pm.height() - s) / 2, s, s))
    p.end()
    return out


class RackView(QWidget):
    """The painted rack. Indices refer to the item list given to ``set_items``."""

    activated = Signal(int)                 # double-click / Enter
    clicked_item = Signal(int)              # single click (after selection)
    selection_changed = Signal(int)         # -1 = none
    context = Signal(int, QPoint)

    def __init__(self, spine_w: int = 34, case_h: int = 168, reveal: float = 0.32, parent=None):
        super().__init__(parent)
        self.spine_w = spine_w
        self.case_h = case_h
        self.reveal = reveal                  # share of the cover that slides out
        self.items: list[RackItem] = []
        self.selected = -1
        self.playing_key: str | None = None
        self._hover = -1
        self._pull: dict[int, float] = {}     # index → 0..1 pulled out
        self._anims: dict[int, QVariantAnimation] = {}
        self._spines: dict[tuple, QPixmap] = {}
        self._covers: dict[tuple, QPixmap] = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        theme.changed.connect(self._restyle)

    # ---- api -----------------------------------------------------------------

    def set_items(self, items: list[RackItem], keep_key: str | None = None) -> None:
        for a in self._anims.values():
            a.stop()
        self._anims.clear()
        self._pull.clear()
        self._hover = -1
        self.items = items
        keys = [it.key for it in items]
        self.selected = keys.index(keep_key) if keep_key in keys else -1
        if len(self._spines) > 600:
            self._spines.clear()
            self._covers.clear()
        self._relayout()

    def select(self, i: int, emit: bool = True) -> None:
        i = i if 0 <= i < len(self.items) else -1
        if i == self.selected:
            return
        old = self.selected
        self.selected = i
        if self.hasFocus() and self._hover < 0:
            self._animate(old, 0.0)
            self._animate(i, 1.0)
        self._update_item(old)
        self._update_item(i)
        if i >= 0:
            self._ensure_visible(i)
        if emit:
            self.selection_changed.emit(i)

    def set_playing(self, key: str | None) -> None:
        self.playing_key = key
        self.update()

    def refresh_covers(self) -> None:
        """A cover finished loading somewhere: repaint (cache keys follow the pixmaps)."""
        self.update()

    # ---- geometry ------------------------------------------------------------

    def _step(self) -> int:
        return self.spine_w + GAP

    def _cols(self) -> int:
        room = self.width() - 2 * MARGIN + GAP - int(self._reveal_w())   # room for one pulled-out cover
        return max(1, room // self._step())

    def _row_h(self) -> int:
        return TOP_ROOM + self.case_h + PLANK_H + BOTTOM_ROOM

    def _rect(self, i: int) -> QRectF:
        """Where case ``i`` stands, after any pulled-out case before it in its row has made room."""
        cols = self._cols()
        row, col = divmod(i, cols)
        shift = sum(t for j, t in self._pull.items() if j < i and j // cols == row) * self._reveal_w()
        return QRectF(MARGIN + col * self._step() + shift, row * self._row_h() + TOP_ROOM,
                      self.spine_w, self.case_h)

    def _reveal_w(self) -> float:
        return self.case_h * self.reveal

    def _index_at(self, pos) -> int:
        row, ry = divmod(int(pos.y()), self._row_h())
        if not (TOP_ROOM - LIFT <= ry <= TOP_ROOM + self.case_h) or pos.x() < MARGIN:
            return -1
        cols = self._cols()
        for i in range(row * cols, min(len(self.items), (row + 1) * cols)):
            t = self._pull.get(i, 0.0)
            r = self._rect(i)
            if r.left() <= pos.x() <= r.right() + self._reveal_w() * t + GAP:
                return i
        return -1

    def _dirty(self, i: int) -> QRect:
        """The whole row of case ``i``: the cases after it move too."""
        r = self._rect(i)
        return QRect(0, int(r.top()) - LIFT - 8, self.width(), self.case_h + LIFT + 20)

    def _update_item(self, i: int) -> None:
        if 0 <= i < len(self.items):
            self.update(self._dirty(i))

    def _relayout(self) -> None:
        rows = max(1, math.ceil(len(self.items) / self._cols()))
        self.setMinimumHeight(rows * self._row_h() + 8)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(600, self._row_h() * 2)

    def _ensure_visible(self, i: int) -> None:
        area = self.parent().parent() if self.parent() else None
        if area is not None and hasattr(area, "ensureVisible"):
            r = self._rect(i).toAlignedRect()
            area.ensureVisible(r.center().x(), r.center().y(), 40, self.case_h // 2 + 30)

    # ---- motion --------------------------------------------------------------

    def _animate(self, i: int, target: float) -> None:
        if i < 0:
            return
        anim = self._anims.pop(i, None)
        if anim:
            anim.stop()
        start = self._pull.get(i, 0.0)
        if reduce_motion or abs(start - target) < 0.01:
            self._set_pull(i, target)
            return
        # Only the newest pulls keep animating: sweeping across the rack never piles work up.
        while len(self._anims) >= 2:
            j, old = next(iter(self._anims.items()))
            old.stop()
            del self._anims[j]
            self._set_pull(j, old.endValue())
        anim = QVariantAnimation(self)
        anim.setDuration(int(ANIM_MS * abs(target - start)) + 40)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.valueChanged.connect(lambda v, i=i: self._set_pull(i, float(v)))
        anim.finished.connect(lambda i=i, a=anim: self._anims.get(i) is a and self._anims.pop(i))
        self._anims[i] = anim
        anim.start()

    def _set_pull(self, i: int, t: float) -> None:
        if t <= 0.001:
            self._pull.pop(i, None)
        else:
            self._pull[i] = t
        self._update_item(i)

    def _pulled_target(self) -> int:
        """Hover wins; otherwise the keyboard-selected case while the rack has focus."""
        if self._hover >= 0:
            return self._hover
        return self.selected if self.hasFocus() else -1

    def _retarget(self, old: int) -> None:
        new = self._pulled_target()
        # Everything but the new target goes back in (covers any case left half-way out).
        for j in {old, *self._pull, *(k for k, a in self._anims.items() if a.endValue() > 0)}:
            if j != new and j >= 0:
                self._animate(j, 0.0)
        if new >= 0 and (self._pull.get(new, 0.0) < 1.0 or new in self._anims):
            self._animate(new, 1.0)

    # ---- events --------------------------------------------------------------

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i != self._hover:
            old = self._pulled_target()
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self._retarget(old)

    def leaveEvent(self, e):
        old = self._pulled_target()
        self._hover = -1
        self._retarget(old)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if self.selected < 0 and self.items and e.reason() in (Qt.FocusReason.TabFocusReason,
                                                                Qt.FocusReason.BacktabFocusReason):
            self.select(0)
        self._retarget(-1)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self._hover < 0:
            self._animate(self.selected, 0.0)

    def mousePressEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if i >= 0:
                self.select(i)
                self.clicked_item.emit(i)
            else:
                old = self._pulled_target()
                self.select(-1)                  # a click on empty space clears the selection
                self._retarget(old)
        elif e.button() == Qt.MouseButton.RightButton and i >= 0:
            self.select(i)

    def mouseDoubleClickEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i >= 0:
            self.activated.emit(i)

    def contextMenuEvent(self, e):
        i = self._index_at(e.pos()) if e.reason() == e.Reason.Mouse else self.selected
        if i >= 0:
            pos = e.globalPos() if e.reason() == e.Reason.Mouse else \
                self.mapToGlobal(self._rect(i).center().toPoint())
            self.context.emit(i, pos)

    def keyPressEvent(self, e):
        k = e.key()
        cols = self._cols()
        cur = self.selected
        moves = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -cols, Qt.Key.Key_Down: cols,
                 Qt.Key.Key_Home: -len(self.items), Qt.Key.Key_End: len(self.items)}
        if k in moves and self.items:
            old = self._pulled_target()
            nxt = 0 if cur < 0 else max(0, min(len(self.items) - 1, cur + moves[k]))
            self.selected = cur        # select() animates via _retarget below
            self.select(nxt)
            self._retarget(old)
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and cur >= 0:
            self.activated.emit(cur)
            return
        if k == Qt.Key.Key_Escape and cur >= 0:
            old = self._pulled_target()
            self.select(-1)
            self._retarget(old)
            return
        super().keyPressEvent(e)

    def event(self, e):
        if e.type() == e.Type.ToolTip:
            i = self._index_at(e.pos())
            if i >= 0:
                it = self.items[i]
                QToolTip.showText(e.globalPos(), it.tooltip or " — ".join(x for x in (it.title, it.subtitle) if x), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    # ---- painting ------------------------------------------------------------

    def _restyle(self) -> None:
        self._spines.clear()
        self.update()

    def _cover(self, it: RackItem, dpr: float) -> QPixmap | None:
        pm = it.cover()
        if pm is None or pm.isNull():
            return None
        key = (it.key, pm.cacheKey(), self.case_h, dpr)
        sq = self._covers.get(key)
        if sq is None:
            sq = self._covers[key] = _square(pm, self.case_h, dpr)
        return sq

    def _spine(self, it: RackItem, dpr: float) -> QPixmap:
        pm = it.cover()
        ck = pm.cacheKey() if pm is not None and not pm.isNull() else 0
        key = (it.key, ck, self.spine_w, self.case_h, theme.dark, dpr)
        sp = self._spines.get(key)
        if sp is None:
            sp = self._spines[key] = self._render_spine(it, pm if ck else None, dpr)
        return sp

    def _render_spine(self, it: RackItem, pm: QPixmap | None, dpr: float) -> QPixmap:
        w, h = self.spine_w, self.case_h
        out = QPixmap(round(w * dpr), round(h * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        hs = tint_mod.hue_of(pm) if pm is not None else None
        dark = theme.dark
        if hs:
            body = QColor.fromHslF(hs[0], min(hs[1], 0.30), 0.30 if dark else 0.74)
        else:
            body = QColor("#3a3a3e") if dark else QColor("#d9d9de")
        r = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(r, 2.5, 2.5)
        p.fillPath(path, body)
        # thickness: a lit hinge on the left, a shaded edge on the right
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, QColor(255, 255, 255, 60 if dark else 90))
        g.setColorAt(0.12, QColor(255, 255, 255, 0))
        g.setColorAt(0.85, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 70 if dark else 45))
        p.fillPath(path, g)
        p.setPen(QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 28))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 2.5, 2.5)

        light_text = body.lightnessF() < 0.55
        fg = QColor("#f5f5f7") if light_text else QColor("#1d1d1f")
        sub = QColor(fg)
        sub.setAlphaF(0.72)
        # kind mark at the top
        icon = KIND_ICON.get(it.kind, "disc")
        s = min(12, w - 12)
        p.drawPixmap(QRectF((w - s) / 2, 8, s, s).toRect(), icons.pixmap(icon, sub, s))
        # rotated title (top → bottom) and artist at the foot
        p.save()
        p.translate(w / 2, 8 + s + 6)
        p.rotate(90)
        length = h - (8 + s + 6) - 8
        tf = font("caption", 600) if w < 40 else font("callout", 600)
        sf = font("caption")
        fm_t, fm_s = QFontMetrics(tf), QFontMetrics(sf)
        sub_text = it.subtitle
        sub_w = min(fm_s.horizontalAdvance(sub_text), int(length * 0.38)) if sub_text else 0
        title_w = length - (sub_w + 10 if sub_w else 0)
        two_lines = w >= 40 and sub_text
        if two_lines:
            # wide spines: title above artist, both along the full length
            p.setFont(tf)
            p.setPen(fg)
            p.drawText(QRectF(0, -fm_t.height() + 1, length, fm_t.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm_t.elidedText(it.title, Qt.TextElideMode.ElideRight, int(length)))
            p.setFont(sf)
            p.setPen(sub)
            p.drawText(QRectF(0, 1, length, fm_s.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm_s.elidedText(sub_text, Qt.TextElideMode.ElideRight, int(length)))
        else:
            p.setFont(tf)
            p.setPen(fg)
            p.drawText(QRectF(0, -fm_t.height() / 2, title_w, fm_t.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm_t.elidedText(it.title, Qt.TextElideMode.ElideRight, int(title_w)))
            if sub_w:
                p.setFont(sf)
                p.setPen(sub)
                p.drawText(QRectF(length - sub_w, -fm_s.height() / 2, sub_w, fm_s.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           fm_s.elidedText(sub_text, Qt.TextElideMode.ElideRight, sub_w))
        p.restore()
        p.end()
        return out

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.items:
            p.end()
            return
        dpr = self.devicePixelRatioF()
        dirty = QRectF(e.rect())
        cols = self._cols()
        rows = math.ceil(len(self.items) / cols)
        first_row = max(0, int(dirty.top() // self._row_h()) - 1)
        last_row = min(rows - 1, int(dirty.bottom() // self._row_h()) + 1)

        for row in range(first_row, last_row + 1):
            a = self._rect(row * cols)
            b = self._rect(min(len(self.items), (row + 1) * cols) - 1)
            plank = QRectF(a.left() - 10, a.bottom(), max(b.right(), a.left() + cols * self._step() - GAP)
                           - a.left() + 20, PLANK_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 90 if theme.dark else 40))
            p.drawRoundedRect(plank.translated(0, 3), 2.3, 2.3)
            p.setBrush(theme.color("fill_strong"))
            p.drawRoundedRect(plank, 2.3, 2.3)
            for i in range(row * cols, min(len(self.items), (row + 1) * cols)):
                t = self._pull.get(i, 0.0)
                r = self._rect(i)
                if r.adjusted(-2, -LIFT - 4, 2 + self._reveal_w() * t, 4).intersects(dirty):
                    self._paint_case(p, i, r, t, dpr)
        p.end()

    def _paint_case(self, p: QPainter, i: int, r: QRectF, t: float, dpr: float) -> None:
        it = self.items[i]
        selected = i == self.selected
        # The selected case stands a little proud of the others (lifted and outlined).
        r = r.translated(0, -max(LIFT * t, 3.0 if selected else 0.0))
        if t > 0:
            rw = self._reveal_w() * t
            cover_r = QRectF(r.right(), r.top(), rw, r.height())
            whole = cover_r.united(r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int((110 if theme.dark else 55) * t)))
            p.drawRoundedRect(whole.translated(0, 3 + 2 * t).adjusted(1, 1, 1, 1), 3, 3)
            sq = self._cover(it, dpr)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(cover_r, 2.5, 2.5)
            p.setClipPath(clip)
            if sq is not None:
                # the front slides out from behind the spine: the edge nearest the spine shows first
                src_w = sq.width() * rw / self.case_h
                p.drawPixmap(cover_r, sq, QRectF(0, 0, src_w, sq.height()))
            else:
                p.fillRect(cover_r, theme.color("fill_strong"))
                s = min(28, cover_r.width() * 0.5)
                if s > 6:
                    p.drawPixmap(QRectF(cover_r.center().x() - s / 2, cover_r.center().y() - s / 2, s, s).toRect(),
                                 icons.pixmap(KIND_ICON.get(it.kind, "disc"), theme.color("tertiary"), int(s)))
            # jewel-case sheen along the front
            g = QLinearGradient(cover_r.topLeft(), cover_r.bottomRight())
            g.setColorAt(0, QColor(255, 255, 255, 34))
            g.setColorAt(0.5, QColor(255, 255, 255, 0))
            p.fillRect(cover_r, g)
            p.restore()
            p.setPen(QColor(255, 255, 255, 40) if theme.dark else QColor(0, 0, 0, 30))   # pale covers keep an edge
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cover_r.adjusted(0.5, 0.5, -0.5, -0.5), 2.5, 2.5)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 50 if theme.dark else 22))
            p.drawRoundedRect(r.translated(0, 1.5), 2.5, 2.5)
        p.drawPixmap(r.topLeft(), self._spine(it, dpr))
        if selected:
            ring = theme.color("label")
            ring.setAlphaF(0.55)
            pen = QPen(ring)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 2.5, 2.5)
        if it.key == self.playing_key:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawEllipse(QRectF(r.center().x() - 3, r.bottom() - 10, 6, 6))


class DetailStrip(QWidget):
    """Cover, name and details of the selected case, with a few actions on the right."""

    def __init__(self):
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

        from aria.ui.player_bar import ElidedLabel
        super().__init__()
        self.setFixedHeight(60)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(MARGIN, 0, 4, 0)
        lay.setSpacing(12)
        self.art = QLabel()
        self.art.setFixedSize(48, 48)
        lay.addWidget(self.art)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addStretch(1)
        self.title = ElidedLabel("Headline")
        self.sub = ElidedLabel("Caption")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(4)
        lay.addLayout(self.buttons)

    def show_item(self, cover: QPixmap | None, title: str, sub: str) -> None:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(48 * dpr), round(48 * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 48, 48), 6, 6)
        p.setClipPath(path)
        if cover is not None and not cover.isNull():
            s = min(cover.width(), cover.height())
            p.drawPixmap(QRectF(0, 0, 48, 48), cover, QRectF((cover.width() - s) / 2, (cover.height() - s) / 2, s, s))
        else:
            p.fillRect(QRectF(0, 0, 48, 48), theme.color("fill"))
            p.drawPixmap(QRect(14, 14, 20, 20), icons.pixmap("music", theme.color("tertiary"), 20))
        p.end()
        self.art.setPixmap(pm)
        self.title.set_full_text(title)
        self.sub.set_full_text(sub)


class RackPanel(QWidget):
    """A scrolling rack plus a slim detail strip for the selected case."""

    def __init__(self, rack: RackView, empty: tuple[str, str, str] = ("disc", "", "")):
        from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout

        from aria.ui.widgets.tracklist import reserve_scrollbar
        super().__init__()
        self.rack = rack
        self.empty = empty
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        reserve_scrollbar(self.scroll)
        self.scroll.setWidget(rack)
        self.scroll.verticalScrollBar().setSingleStep(30)
        lay.addWidget(self.scroll, 1)
        self.detail = DetailStrip()
        self.detail.hide()
        lay.addWidget(self.detail)

    def paintEvent(self, _e):
        if not self.rack.items and any(self.empty[1:]):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_empty(p, self.rect(), *self.empty)
            p.end()


def track_items(tracks) -> list[RackItem]:
    """Every song as a single CD: the song name on the spine, the performer at its foot."""
    from aria.core import textnorm
    from aria.ui.widgets import thumbs as thumbs_mod

    thumbs = thumbs_mod.instance()
    out = []
    for t in tracks:
        artist = textnorm.artist_of(t.title, t.artist) or textnorm.clean_channel(t.artist)
        out.append(RackItem(key=t.key, title=textnorm.display_title(t.title, t.artist), subtitle=artist,
                            kind="single", tooltip=f"{t.title}\n{t.artist}" if t.artist else t.title,
                            cover=(lambda u=t.thumbnail: thumbs.get(u, 180) if u else None)))
    return out
