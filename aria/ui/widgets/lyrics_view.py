"""Lyrics panel: synced lines follow the song (click a line to jump there); plain lyrics just scroll."""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.core.lyrics import Lyrics
from aria.ui.theme import font, theme
from aria.ui.widgets.tracklist import paint_empty

GAP = 12
STANZA = 14
FOLLOW_PAUSE = 4.0           # seconds the view stays where the user scrolled it


class LyricsView(QWidget):
    seek = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lyrics: Lyrics | None = None
        self.state = "empty"                     # empty | loading | none | ready
        self._current = -1
        self._offset = 0.0
        self._user_scrolled = 0.0
        self._layout: list[QRect] = []
        self._hover = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_offset)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.changed.connect(self.update)

    # ---- api -----------------------------------------------------------------

    def set_state(self, state: str, lyrics: Lyrics | None = None) -> None:
        self.state = state
        self.lyrics = lyrics if state == "ready" else None
        self._current = -1
        self._offset = 0.0
        self._relayout()
        self.update()

    def set_position(self, seconds: float) -> None:
        if not self.lyrics or not self.lyrics.synced:
            return
        i = self.lyrics.index_at(seconds)
        if i != self._current:
            self._current = i
            if time.monotonic() - self._user_scrolled > FOLLOW_PAUSE:
                self._follow()
            self.update()

    # ---- layout --------------------------------------------------------------

    def _fonts(self, i: int):
        synced = self.lyrics and self.lyrics.synced
        return font("title3", 600) if synced and i == self._current else font("body", 500 if synced else 400)

    def _relayout(self) -> None:
        self._layout = []
        if not self.lyrics:
            return
        y = 0
        w = max(80, self.width() - 32)
        for i, (_t, text) in enumerate(self.lyrics.lines):
            if not text:
                y += STANZA
                self._layout.append(QRect(16, y, w, 0))
                continue
            fm = QFontMetrics(font("title3", 600))      # size for the largest style so lines never jump
            r = fm.boundingRect(QRect(0, 0, w, 10_000), Qt.TextFlag.TextWordWrap, text)
            self._layout.append(QRect(16, y, w, r.height()))
            y += r.height() + GAP

    def _content_height(self) -> int:
        return self._layout[-1].bottom() + 40 if self._layout else 0

    def _clamp(self, v: float) -> float:
        lo = -self.height() * 0.35
        hi = max(lo, self._content_height() - self.height() * 0.5)
        return max(lo, min(hi, v))

    def _follow(self) -> None:
        if 0 <= self._current < len(self._layout):
            r = self._layout[self._current]
            target = self._clamp(r.center().y() - self.height() * 0.38)
            self._anim.stop()
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()

    def _set_offset(self, v) -> None:
        self._offset = float(v)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()
        # Line heights changed: keep the sung line in view (or stay inside the text).
        self._anim.stop()
        if self._current >= 0:
            r = self._layout[self._current] if self._current < len(self._layout) else None
            self._offset = self._clamp(r.center().y() - self.height() * 0.38) if r else 0.0
        else:
            self._offset = max(0.0, self._clamp(self._offset))

    # ---- events --------------------------------------------------------------

    def wheelEvent(self, e):
        self._anim.stop()
        self._user_scrolled = time.monotonic()
        self._offset = self._clamp(self._offset - e.angleDelta().y() / 2)
        self.update()

    def _line_at(self, pos) -> int:
        for i, r in enumerate(self._layout):
            if r.height() and r.translated(0, -int(self._offset)).adjusted(0, -GAP // 2, 0, GAP // 2).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._line_at(e.position().toPoint()) if self.lyrics and self.lyrics.synced else -1
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._line_at(e.position().toPoint())
        if i >= 0 and self.lyrics and self.lyrics.synced and self.lyrics.lines[i][0] is not None:
            self._user_scrolled = 0
            self.seek.emit(self.lyrics.lines[i][0])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state != "ready" or not self.lyrics:
            title, text = {"loading": ("Looking for lyrics…", ""), "none": ("No lyrics found", "Try Deep Lyrics Search in Settings."),
                           "empty": ("No lyrics yet", "Lyrics show here while a song plays.")}.get(self.state, ("", ""))
            paint_empty(p, self.rect(), "list", title, text)
            return
        synced = self.lyrics.synced
        for i, (r, (_t, text)) in enumerate(zip(self._layout, self.lyrics.lines)):
            if not text:
                continue
            rr = r.translated(0, -int(self._offset))
            if rr.bottom() < -40 or rr.top() > self.height() + 40:
                continue
            if synced:
                current = i == self._current
                past = i < self._current
                color = theme.color("label" if current or i == self._hover else ("tertiary" if past else "secondary"))
            else:
                color = theme.color("label")
            p.setFont(self._fonts(i))
            p.setPen(color)
            p.drawText(QRectF(rr), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft, text)
        p.end()
