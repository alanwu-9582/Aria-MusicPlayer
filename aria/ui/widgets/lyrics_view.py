"""Lyrics panel: synced lines follow the song (click a line to jump there); plain lyrics just scroll.

Two looks share the code: the side panel (theme colours) and Immersive Mode (light text on
the dark backdrop, larger type, lines fading with distance from the one being sung). When the
sung line changes, emphasis cross-fades briefly and the text scrolls smoothly — nothing
repaints while the line stays the same.

A line's translation, when the lyrics have one, sits under it in smaller, quieter type
(Settings › Lyrics Translation). Lyrics timed word by word fill the sung line as it is sung
(Settings › Word-by-Word Lyrics): only that line repaints, at the player's position ticks — no
animation loop. Romanization rows are kept but not shown: the original script is the lyric.
"""

from __future__ import annotations

import time
import weakref

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QSizePolicy, QWidget

from aria.core.lyrics import Lyrics
from aria.ui.theme import UI_FAMILIES, font, theme
from aria.ui.widgets.tint import mix as _mix
from aria.ui.widgets.tracklist import paint_empty

GAP = 12
STANZA = 14
FOLLOW_PAUSE = 4.0           # seconds the view stays where the user scrolled it
EMPHASIS_MS = 240


class LyricsView(QWidget):
    seek = Signal(float)
    changed = Signal()                           # lyrics or their state changed (Immersive Mode follows)
    show_trans = True                            # Settings › Lyrics Translation
    show_words = True                            # Settings › Word-by-Word Lyrics
    _views: weakref.WeakSet = weakref.WeakSet()

    @classmethod
    def set_extras(cls, trans: bool, words: bool) -> None:
        """The two lyric settings changed: every lyrics view re-lays out once."""
        cls.show_trans, cls.show_words = bool(trans), bool(words)
        for v in list(cls._views):
            v._relayout()
            v._snap()
            v.update()

    def __init__(self, parent=None, immersive: bool = False, centered: bool = False):
        super().__init__(parent)
        self.immersive = immersive
        self.centered = centered
        self.scale = 36                          # immersive type: window height / scale
        self.lyrics: Lyrics | None = None
        self.state = "empty"                     # empty | loading | none | ready
        self._current = -1
        self._previous = -1
        self._emph = 1.0                         # 0 → 1 while the sung line changes
        self._offset = 0.0
        self._user_scrolled = 0.0
        self._layout: list[QRect] = []
        self._main_h: list[int] = []             # per line: the original's height (its translation sits below)
        self._pos = 0.0
        self.clickable = True                    # a click on a synced line jumps there
        self._hover = -1
        LyricsView._views.add(self)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(420 if immersive else 380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_offset)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(EMPHASIS_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.valueChanged.connect(self._set_emph)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if immersive:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        theme.changed.connect(self.update)

    # ---- api -----------------------------------------------------------------

    def set_state(self, state: str, lyrics: Lyrics | None = None) -> None:
        self.state = state
        self.lyrics = lyrics if state == "ready" else None
        self._current = self._previous = -1
        self._offset = 0.0
        self._relayout()
        self.update()
        self.changed.emit()

    def set_centered(self, centered: bool) -> None:
        if centered != self.centered:
            self.centered = centered
            self._relayout()
            self._snap()
            self.update()

    def set_position(self, seconds: float) -> None:
        if not self.lyrics or not self.lyrics.synced:
            return
        self._pos = seconds - self.lyrics.offset
        i = self.lyrics.index_at(self._pos)                          # + offset = the words come later
        if i == self._current and self._words_on(i) and 0 <= i < len(self._layout):
            # word by word: only the sung line repaints
            self.update(self._layout[i].translated(0, -int(self._offset)).adjusted(-4, -6, 4, 6))
        if i != self._current:
            self._previous, self._current = self._current, i
            self._fade.stop()
            self._fade.start()
            if time.monotonic() - self._user_scrolled > FOLLOW_PAUSE:
                self._follow()
            self.update()

    # ---- type ------------------------------------------------------------------

    def _size(self) -> int:
        """Immersive type scales with the screen it's on."""
        return max(18, min(44, int(self.window().height() / self.scale))) if self.immersive else 0

    def _big(self) -> QFont:
        if not self.immersive:
            return font("title3", 600)
        f = QFont()
        f.setFamilies(UI_FAMILIES)
        f.setPixelSize(self._size())
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def _small(self) -> QFont:
        if not self.immersive:
            synced = self.lyrics and self.lyrics.synced
            return font("body", 500 if synced else 400)
        f = QFont(self._big())
        f.setPixelSize(int(self._size() * 0.8))
        f.setWeight(QFont.Weight.Medium)
        return f

    def _font(self, i: int) -> QFont:
        synced = self.lyrics and self.lyrics.synced
        return self._big() if synced and i == self._current else self._small()

    def _align(self):
        return Qt.AlignmentFlag.AlignHCenter if self.centered else Qt.AlignmentFlag.AlignLeft

    def _small_extra(self) -> QFont:
        """A translation's type: smaller and lighter than its line."""
        f = QFont(self._small())
        f.setPixelSize(max(11, int((self._size() or 15) * 0.62)))
        f.setWeight(QFont.Weight.Normal)
        return f

    def _trans(self, i: int) -> str:
        if not (self.show_trans and self.lyrics and i < len(self.lyrics.trans)):
            return ""
        return self.lyrics.trans[i] or ""

    def _words_on(self, i: int) -> bool:
        return bool(self.show_words and self.lyrics and self.lyrics.word_timed and 0 <= i < len(self.lyrics.words)
                    and self.lyrics.words[i])

    def _anchor(self) -> float:
        return 0.5 if self.immersive else 0.38

    # ---- layout --------------------------------------------------------------

    def _relayout(self) -> None:
        self._layout = []
        if not self.lyrics:
            return
        gap = int(self._size() * 0.7) if self.immersive else GAP
        stanza = int(self._size() * 0.8) if self.immersive else STANZA
        y = 0
        pad = 8 if self.immersive else 16
        w = max(80, self.width() - 2 * pad)
        fm = QFontMetrics(self._big())                  # size for the largest style so lines never jump
        fs = QFontMetrics(self._small_extra())
        self._main_h = []
        for i, (_t, text) in enumerate(self.lyrics.lines):
            if not text:
                y += stanza
                self._layout.append(QRect(pad, y, w, 0))
                self._main_h.append(0)
                continue
            r = fm.boundingRect(QRect(0, 0, w, 10_000), Qt.TextFlag.TextWordWrap, text)
            h = r.height()
            self._main_h.append(h)
            tr = self._trans(i)
            if tr:
                h += 2 + fs.boundingRect(QRect(0, 0, w, 10_000), Qt.TextFlag.TextWordWrap, tr).height()
            self._layout.append(QRect(pad, y, w, h))
            y += h + gap

    def _content_height(self) -> int:
        return self._layout[-1].bottom() + 40 if self._layout else 0

    def _clamp(self, v: float) -> float:
        lo = -self.height() * (0.5 if self.immersive else 0.35)
        hi = max(lo, self._content_height() - self.height() * 0.5)
        return max(lo, min(hi, v))

    def _target(self) -> float | None:
        line = self._current
        if line < 0 and self.immersive and self.lyrics and self.lyrics.synced:
            line = 0                                     # before the singing starts, the first line waits in the middle
        if 0 <= line < len(self._layout):
            r = self._layout[line]
            return self._clamp(r.center().y() - self.height() * self._anchor())
        return None

    def _follow(self) -> None:
        target = self._target()
        if target is not None:
            self._anim.stop()
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()

    def _snap(self) -> None:
        self._anim.stop()
        target = self._target()
        if target is not None:
            self._offset = target
        elif self.immersive and self.lyrics and not self.lyrics.synced:
            self._offset = self._clamp(-self.height() * 0.1)
        else:
            self._offset = max(0.0, self._clamp(self._offset))

    def _set_offset(self, v) -> None:
        self._offset = float(v)
        self.update()

    def _set_emph(self, v) -> None:
        self._emph = float(v)
        # only the two lines trading places change
        for i in (self._current, self._previous):
            if 0 <= i < len(self._layout):
                self.update(self._layout[i].translated(0, -int(self._offset)).adjusted(-4, -6, 4, 6))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()
        self._snap()                 # line heights changed: keep the sung line in view

    # ---- events --------------------------------------------------------------

    def wheelEvent(self, e):
        self._anim.stop()
        self._user_scrolled = time.monotonic()
        self._offset = self._clamp(self._offset - e.angleDelta().y() / 2)
        self.update()

    def _line_at(self, pos) -> int:
        gap = int(self._size() * 0.7) if self.immersive else GAP
        for i, r in enumerate(self._layout):
            if r.height() and r.translated(0, -int(self._offset)).adjusted(0, -gap // 2, 0, gap // 2).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._line_at(e.position().toPoint()) if self.clickable and self.lyrics and self.lyrics.synced else -1
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()
        e.ignore()                   # Immersive Mode also wants to know the mouse moved

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.clickable:
            return
        i = self._line_at(e.position().toPoint())
        if i >= 0 and self.lyrics and self.lyrics.synced and self.lyrics.lines[i][0] is not None:
            self._user_scrolled = 0
            self.seek.emit(max(0.0, self.lyrics.lines[i][0] + self.lyrics.offset))

    # ---- painting --------------------------------------------------------------

    def _colors(self):
        """(sung, upcoming, past, hover) colours for the current look."""
        if self.immersive:
            bright = QColor(255, 255, 255, 240)
            return bright, QColor(255, 255, 255, 105), QColor(255, 255, 255, 75), QColor(255, 255, 255, 190)
        return theme.color("label"), theme.color("secondary"), theme.color("tertiary"), theme.color("label")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.state != "ready" or not self.lyrics:
            if not self.immersive:
                title, text = {"loading": ("Looking for lyrics…", ""),
                               "none": ("No lyrics found", "Try Deep Lyrics Search in Settings."),
                               "empty": ("No lyrics yet", "Lyrics show here while a song plays.")}.get(self.state, ("", ""))
                paint_empty(p, self.rect(), "list", title, text)
            p.end()
            return
        synced = self.lyrics.synced
        sung, upcoming, past, hover = self._colors()
        dirty = e.rect()
        for i, (r, (_t, text)) in enumerate(zip(self._layout, self.lyrics.lines)):
            if not text:
                continue
            rr = r.translated(0, -int(self._offset))
            if rr.bottom() < -40 or rr.top() > self.height() + 40 or not rr.adjusted(-4, -6, 4, 6).intersects(dirty):
                continue
            if not synced:
                color = QColor(sung) if not self.immersive else QColor(255, 255, 255, 200)
            else:
                rest = past if i < self._current else upcoming
                if i == self._current:
                    color = _mix(rest, sung, self._emph)
                elif i == self._previous:
                    color = _mix(sung, rest, self._emph)
                else:
                    color = QColor(hover if i == self._hover else rest)
                if self.immersive and i != self._current:
                    # the further from the sung line (or the first, before singing starts), the quieter
                    far = abs(i - max(self._current, 0))
                    color.setAlphaF(color.alphaF() * max(0.3, 1 - 0.12 * max(0, far - 1)))
            if self.immersive:
                # lines melt away at the top and bottom edges instead of being cut off
                edge = min(rr.center().y(), self.height() - rr.center().y())
                if edge < 70:
                    color.setAlphaF(color.alphaF() * max(0.0, edge / 70))
            main = QRectF(rr.left(), rr.top(), rr.width(), self._main_h[i] if i < len(self._main_h) else rr.height())
            if synced and i == self._current and self._words_on(i):
                self._paint_words(p, main, i, text, color, rest if i < len(self._layout) else color)
            else:
                p.setFont(self._font(i))
                p.setPen(color)
                p.drawText(main, Qt.TextFlag.TextWordWrap | self._align(), text)
            tr = self._trans(i)
            if tr:
                quiet = QColor(color)
                quiet.setAlphaF(quiet.alphaF() * 0.72)
                p.setFont(self._small_extra())
                p.setPen(quiet)
                p.drawText(QRectF(rr.left(), main.bottom() + 2, rr.width(), rr.bottom() - main.bottom()),
                           Qt.TextFlag.TextWordWrap | self._align(), tr)
        p.end()

    def _sung_chars(self, i: int, text: str) -> int:
        """How much of line ``i`` has been sung: whole words, and part of the one being sung."""
        words = self.lyrics.words[i]
        done = 0
        for k, (start, chunk) in enumerate(words):
            if self._pos < start:
                break
            end = words[k + 1][0] if k + 1 < len(words) else start + max(0.4, 0.09 * len(chunk))
            frac = min(1.0, (self._pos - start) / max(0.05, end - start))
            done += round(len(chunk) * frac) if frac < 1.0 else len(chunk)
            if frac < 1.0:
                break
        return min(len(text), done)

    def _paint_words(self, p: QPainter, rect: QRectF, i: int, text: str, sung: QColor, rest: QColor) -> None:
        """The sung line, filled word by word: the part sung so far bright, the rest quiet."""
        layout = QTextLayout(text, self._font(i))
        opt = QTextOption(self._align())
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(opt)
        layout.beginLayout()
        y = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            line.setPosition(QPointF(0, y))
            y += line.height()
        layout.endLayout()
        quiet = QColor(rest)
        quiet.setAlphaF(max(quiet.alphaF(), sung.alphaF() * 0.45))
        p.setPen(quiet)
        layout.draw(p, rect.topLeft())
        done = self._sung_chars(i, text)
        if done <= 0:
            return
        p.save()
        p.setPen(sung)
        for k in range(layout.lineCount()):
            ln = layout.lineAt(k)
            start = ln.textStart()
            if done <= start:
                break
            x = ln.cursorToX(min(done, start + ln.textLength()))
            x = x[0] if isinstance(x, tuple) else x
            nat = ln.naturalTextRect()
            p.setClipRect(QRectF(rect.left() + nat.left() - 2, rect.top() + ln.y(), x - nat.left() + 2, ln.height()))
            layout.draw(p, rect.topLeft())
        p.restore()
