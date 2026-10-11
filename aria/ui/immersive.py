"""Immersive Mode: full-screen listening with the cover and the lyrics at its heart.

It's its own window over the main one, driven by the same Playback, so going in or
out never interrupts the music (queue, volume, Tempo Flow and cross-fades carry on).

Two layouts: Centered Artwork (cover in the middle, title beneath, a few lines of
lyrics under it) and Editorial (cover on the left, title and the full lyrics on
the right). Without lyrics it stays a quiet cover-only page.

Kept light on resources: the backdrop (a blurred, muted cover), the cover with its
shadow and the title are painted once per song or window size into one cached
image. After that only the lyrics, the thin progress line and the controls ever
repaint. The controls appear when the mouse moves and fade after 3 s.
Volume: the slider in the controls, ↑ / ↓, or the mouse wheel away from the lyrics.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QWidget

from aria.core.models import format_duration
from aria.core.player import LOADING, PLAYING
from aria.ui import icons
from aria.ui.theme import UI_FAMILIES
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.thumbs import ART_HEIGHT, artwork_urls
from aria.ui.widgets.lyrics_view import LyricsView

IDLE_MS = 3000
CENTERED, EDITORIAL = "centered", "editorial"


def _font(px: int, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(UI_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


class GlassButton(QAbstractButton):
    """A light icon on the dark backdrop; a faint disc on hover; checked = brighter."""

    def __init__(self, icon: str, name: str, size: int = 40, icon_px: int = 18, solid: bool = False):
        super().__init__()
        self.icon_name = icon
        self.icon_px = icon_px
        self.solid = solid
        self.setFixedSize(size, size)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False

    def set_icon(self, name: str) -> None:
        self.icon_name = name
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        if self.solid:
            p.setBrush(QColor(255, 255, 255, 235 if self._hover else 215))
            p.drawEllipse(r)
        elif self._hover or self.isChecked():
            p.setBrush(QColor(255, 255, 255, 34 if self._hover else 22))
            p.drawEllipse(r)
        color = QColor(20, 20, 22) if self.solid else QColor(255, 255, 255, 235 if (self._hover or self.isChecked()) else 180)
        s = self.icon_px
        p.drawPixmap(QRect((self.width() - s) // 2, (self.height() - s) // 2, s, s), icons.pixmap(self.icon_name, color, s))
        p.end()


class GlassSlider(QWidget):
    """A slim light volume slider for the dark backdrop: click or drag; the wheel steps by 5."""

    moved = Signal(int)

    def __init__(self, width: int = 120):
        super().__init__()
        self.setFixedSize(width, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Volume")
        self.value = 70
        self._drag = False
        self._hover = False

    def set_value(self, v: int) -> None:
        v = max(0, min(100, int(v)))
        if v != self.value:
            self.value = v
            self.update()

    def _track(self) -> QRectF:
        return QRectF(8, self.height() / 2 - 1.5, self.width() - 16, 3)

    def _from_x(self, x: float) -> int:
        t = self._track()
        return round(max(0.0, min(1.0, (x - t.left()) / t.width())) * 100)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._emit(self._from_x(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag:
            self._emit(self._from_x(e.position().x()))
        e.ignore()                         # keeps the controls awake

    def mouseReleaseEvent(self, e):
        self._drag = False

    def wheelEvent(self, e):
        step = 5 if e.angleDelta().y() > 0 else -5
        self._emit(self.value + step)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def _emit(self, v: int) -> None:
        self.set_value(v)
        self.moved.emit(self.value)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._track()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 50))
        p.drawRoundedRect(t, 1.5, 1.5)
        x = t.left() + t.width() * self.value / 100
        p.setBrush(QColor(255, 255, 255, 210))
        p.drawRoundedRect(QRectF(t.left(), t.top(), x - t.left(), t.height()), 1.5, 1.5)
        r = 7 if (self._hover or self._drag) else 5
        p.drawEllipse(QRectF(x - r, t.center().y() - r, 2 * r, 2 * r))
        p.end()


class Controls(QWidget):
    """Transport and mode buttons along the bottom; fades in and out as a whole."""

    def __init__(self, parent):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.layout_btn = GlassButton("sidebar", "Switch Layout (V)", 36, 17)
        self.lyrics_btn = GlassButton("list", "Show Lyrics (L)", 36, 17)
        self.lyrics_btn.setCheckable(True)
        self.prev = GlassButton("previous", "Previous", 44, 18)
        self.play = GlassButton("play", "Play / Pause", 56, 22, solid=True)
        self.next = GlassButton("next", "Next", 44, 18)
        self.exit_btn = GlassButton("close", "Exit Immersive Mode (Esc)", 36, 17)
        self.mute = GlassButton("volume", "Mute", 36, 17)
        self.volume = GlassSlider(110)
        # left and right groups have the same width, so the transport sits in the middle
        for b in (self.layout_btn, self.lyrics_btn):
            lay.addWidget(b)
        lay.addSpacing(18 + 110 - 36)
        for b in (self.prev, self.play, self.next):
            lay.addWidget(b)
        lay.addSpacing(18)
        lay.addWidget(self.mute)
        lay.addWidget(self.volume)
        lay.addWidget(self.exit_btn)
        self.setFixedHeight(60)
        self.adjustSize()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(260)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._faded)

    def _faded(self) -> None:
        if self.fade.endValue() == 0.0:
            self.setVisible(False)

    def sizeHint(self) -> QSize:
        return QSize(36 * 2 + (18 + 110 - 36) + 44 * 2 + 56 + 18 + 36 + 110 + 36 + 10 * 8, 60)

    def show_animated(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if abs(self.effect.opacity() - target) < 0.01 and self.isVisible() == on:
            return
        self.fade.stop()
        self.setVisible(True)
        self.fade.setStartValue(self.effect.opacity())
        self.fade.setEndValue(target)
        self.fade.start()


class ImmersiveView(QWidget):
    closed = Signal()

    def __init__(self, playback, settings, source_lyrics: LyricsView):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Aria — Immersive")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pb = playback
        self.settings = settings
        self.source = source_lyrics                    # Now Playing's lyrics: fetched once, shown twice
        self._static: QPixmap | None = None
        self._static_key = None
        self._pos = 0.0
        self._len = 0.0

        self.lyrics = LyricsView(self, immersive=True)
        self.lyrics.seek.connect(playback.seek)
        self.controls = Controls(self)
        c = self.controls
        c.prev.clicked.connect(playback.previous)
        c.play.clicked.connect(playback.toggle)
        c.next.clicked.connect(playback.next)
        c.layout_btn.clicked.connect(self.toggle_layout)
        c.lyrics_btn.clicked.connect(self.toggle_lyrics)
        c.exit_btn.clicked.connect(self.close)
        c.lyrics_btn.setChecked(bool(settings["immersive_lyrics"]))
        # volume: the same as everywhere else (player bar, mini player stay in step)
        self._unmuted = playback.player.volume or 70
        c.volume.moved.connect(playback.set_volume)
        c.mute.clicked.connect(self._toggle_mute)
        playback.volume_changed.connect(self._on_volume)
        self._on_volume(playback.player.volume)

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_MS)
        self._idle.timeout.connect(self._go_idle)

        playback.current_changed.connect(self._on_track)
        playback.position_changed.connect(self._on_position)
        playback.state_changed.connect(self._on_state)
        source_lyrics.changed.connect(self._copy_lyrics)
        thumbs_mod.instance().ready.connect(self._on_thumb)
        self._copy_lyrics()
        self._on_state(playback.state)

    # ---- entering / leaving --------------------------------------------------

    def enter(self, screen=None) -> None:
        if screen is not None:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._wake()

    def closeEvent(self, e):
        self._idle.stop()
        self.unsetCursor()
        self.closed.emit()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.close()
        elif k == Qt.Key.Key_Space:
            self.pb.toggle()
        elif k == Qt.Key.Key_L:
            self.toggle_lyrics()
        elif k == Qt.Key.Key_V:
            self.toggle_layout()
        elif k == Qt.Key.Key_Up:
            self.nudge_volume(5)
        elif k == Qt.Key.Key_Down:
            self.nudge_volume(-5)
        elif k == Qt.Key.Key_M:
            self._toggle_mute()
        elif k == Qt.Key.Key_Right and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.pb.next()
        elif k == Qt.Key.Key_Left and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.pb.previous()
        else:
            super().keyPressEvent(e)
            return
        self._wake()

    # ---- volume ----------------------------------------------------------------

    def _on_volume(self, v: int) -> None:
        if v:
            self._unmuted = v
        self.controls.volume.set_value(v)
        self.controls.mute.set_icon("mute" if v == 0 else "volume-low" if v < 50 else "volume")

    def _toggle_mute(self) -> None:
        self.pb.set_volume(0 if self.pb.player.volume else (self._unmuted or 70))

    def nudge_volume(self, step: int) -> None:
        self.pb.set_volume(max(0, min(100, self.pb.player.volume + step)))
        self._wake()                       # show the slider while it changes

    def wheelEvent(self, e):
        # over the lyrics the wheel scrolls them; anywhere else it's the volume
        self.nudge_volume(5 if e.angleDelta().y() > 0 else -5)

    # ---- modes ---------------------------------------------------------------

    @property
    def layout_mode(self) -> str:
        return EDITORIAL if self.settings["immersive_layout"] == EDITORIAL else CENTERED

    def toggle_layout(self) -> None:
        self.settings["immersive_layout"] = CENTERED if self.layout_mode == EDITORIAL else EDITORIAL
        self._arrange()

    def toggle_lyrics(self) -> None:
        self.settings["immersive_lyrics"] = not self.settings["immersive_lyrics"]
        self.controls.lyrics_btn.setChecked(bool(self.settings["immersive_lyrics"]))
        self._arrange()

    def _has_lyrics(self) -> bool:
        return bool(self.settings["immersive_lyrics"]) and self.lyrics.state == "ready" and self.lyrics.lyrics is not None

    # ---- idle / mouse ----------------------------------------------------------

    def _wake(self) -> None:
        self.unsetCursor()
        self.controls.show_animated(True)
        self._idle.start()

    def _go_idle(self) -> None:
        if self.controls.underMouse():
            self._idle.start()
            return
        self.controls.show_animated(False)
        self.setCursor(Qt.CursorShape.BlankCursor)          # the lyrics stay; the pointer and buttons go

    def mouseMoveEvent(self, e):
        self._wake()
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.close()

    def event(self, e):
        if e.type() == e.Type.HoverMove:
            self._wake()
        return super().event(e)

    # ---- data ------------------------------------------------------------------

    def _copy_lyrics(self) -> None:
        src = self.source
        self.lyrics.set_state(src.state, src.lyrics)
        self.lyrics.set_position(self.pb.player.position())
        self._arrange()

    def _on_track(self, _t) -> None:
        self._arrange()

    def _on_thumb(self, key: str) -> None:
        """Only the playing song's artwork arriving matters here (it may change the cover's shape)."""
        t = self.pb.current
        if t and self.isVisible() and key == thumbs_mod.instance().key(artwork_urls(t), ART_HEIGHT):
            self._arrange()

    def _on_state(self, s: str) -> None:
        self.controls.play.set_icon("pause" if s in (PLAYING, LOADING) else "play")

    def _on_position(self, pos: float, length: float) -> None:
        self._pos, self._len = pos, length
        if self.isVisible():
            self.lyrics.set_position(pos)
            self.update(self._progress_rect().adjusted(-4, -24, 4, 4))   # just the progress line

    # ---- geometry --------------------------------------------------------------

    def _boxes(self) -> dict:
        """Where everything goes for the current layout and size."""
        W, H = self.width(), self.height()
        lyr = self._has_lyrics()
        m = max(40, int(min(W, H) * 0.07))
        out = {}
        if self.layout_mode == EDITORIAL:
            side = int(min(H - 2 * m - 40, W * (0.42 if lyr else 0.46)))
            sz = self._fit(side, W * 0.5)
            cover = QRect(m + int(W * 0.03), (H - sz.height()) // 2 - 20, sz.width(), sz.height())
            text_x = cover.right() + int(W * 0.06)
            text_w = W - text_x - m
            out["cover"] = cover
            if lyr:
                top = min(cover.top(), int(H * 0.12))
                out["title"] = QRect(text_x, top, text_w, int(H * 0.17))
                lyr_top = out["title"].bottom() + 16
                out["lyrics"] = QRect(text_x, lyr_top, text_w, H - 130 - lyr_top)   # down to the controls
            else:
                out["title"] = QRect(text_x, cover.center().y() - int(H * 0.09), text_w, int(H * 0.18))
            out["align"] = Qt.AlignmentFlag.AlignLeft
        else:
            if lyr:
                side = int(min(H * 0.42, W * 0.36))
                sz = self._fit(side, W * 0.5)
                cover = QRect((W - sz.width()) // 2, int(H * 0.08), sz.width(), sz.height())
                out["title"] = QRect(m, cover.bottom() + 22, W - 2 * m, 74)
                top = out["title"].bottom() + 6
                out["lyrics"] = QRect(int(W * 0.18), top, int(W * 0.64), H - top - 120)
            else:
                side = int(min(H * 0.6, W * 0.5))
                sz = self._fit(side, W * 0.62)
                cover = QRect((W - sz.width()) // 2, int((H - sz.height()) / 2) - 50, sz.width(), sz.height())
                out["title"] = QRect(m, cover.bottom() + 28, W - 2 * m, 80)
            out["cover"] = cover
            out["align"] = Qt.AlignmentFlag.AlignHCenter
        return out

    def _progress_rect(self) -> QRect:
        W, H = self.width(), self.height()
        w = min(int(W * 0.5), 720)
        return QRect((W - w) // 2, H - 34, w, 3)

    def _arrange(self) -> None:
        boxes = self._boxes()
        lyr = "lyrics" in boxes
        self.lyrics.setVisible(lyr)
        if lyr:
            self.lyrics.set_centered(self.layout_mode == CENTERED)
            self.lyrics.setGeometry(boxes["lyrics"])
        hint = self.controls.sizeHint()
        self.controls.setGeometry((self.width() - hint.width()) // 2, self.height() - 108, hint.width(), hint.height())
        self.controls.raise_()
        self._invalidate()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrange()

    # ---- the cached backdrop ---------------------------------------------------

    def _invalidate(self) -> None:
        self._static_key = None
        self.update()

    def _cover(self):
        """The same large artwork Now Playing shows (already cached)."""
        t = self.pb.current
        pm = thumbs_mod.instance().get(artwork_urls(t), ART_HEIGHT) if t else None
        return pm if pm is not None and not pm.isNull() else None

    def _aspect(self) -> float:
        pm = self._cover()
        return pm.width() / pm.height() if pm is not None and pm.height() else 1.0

    def _fit(self, side: int, cap_w: float) -> QSize:
        """Square covers stay square; a wide video frame keeps its shape."""
        ar = self._aspect()
        if ar < 1.15:
            return QSize(side, side)
        w = int(min(cap_w, side * 1.35))
        return QSize(w, int(w / ar))

    def _render_static(self) -> QPixmap:
        """Backdrop + cover + shadow + title, painted once per song / size / layout."""
        dpr = self.devicePixelRatioF()
        W, H = self.width(), self.height()
        out = QPixmap(int(W * dpr), int(H * dpr))
        out.setDevicePixelRatio(dpr)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cover = self._cover()
        hs = tint_mod.hue_of(cover) if cover is not None else None
        base = tint_mod.surface(hs, "canvas")
        p.fillRect(QRect(0, 0, W, H), base)
        if cover is not None:
            # a heavy blur for free: shrink to a few pixels, then let smooth scaling spread it out
            tiny = cover.scaled(14, 14, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            mid = tiny.scaled(140, 140, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            p.setOpacity(0.30)
            p.drawPixmap(QRect(-W // 10, -H // 10, int(W * 1.2), int(H * 1.2)), mid)
            p.setOpacity(1.0)
        # muted and quiet: a veil in the base colour, darker towards the edges
        veil = QColor(base)
        veil.setAlphaF(0.55)
        p.fillRect(QRect(0, 0, W, H), veil)
        vg = QRadialGradient(W / 2, H / 2, max(W, H) * 0.75)
        vg.setColorAt(0.0, QColor(0, 0, 0, 0))
        vg.setColorAt(1.0, QColor(0, 0, 0, 120))
        p.fillRect(QRect(0, 0, W, H), vg)

        boxes = self._boxes()
        cr = QRectF(boxes["cover"])
        radius = min(cr.width(), cr.height()) * 0.025
        for k in range(14, 0, -2):                     # soft shadow under the cover
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(cr.adjusted(-k, -k + 10, k, k + 14), radius + k, radius + k)
        clip = QPainterPath()
        clip.addRoundedRect(cr, radius, radius)
        p.save()
        p.setClipPath(clip)
        if cover is not None:
            # fill the box, cropping only what doesn't fit its shape
            ar_box = cr.width() / cr.height()
            sw, sh = cover.width(), cover.height()
            if sw / sh > ar_box:
                src = QRectF((sw - sh * ar_box) / 2, 0, sh * ar_box, sh)
            else:
                src = QRectF(0, (sh - sw / ar_box) / 2, sw, sw / ar_box)
            p.drawPixmap(cr, cover, src)
        else:
            p.fillRect(cr, QColor(255, 255, 255, 18))
            ic = int(cr.width() * 0.22)
            p.drawPixmap(QRect(int(cr.center().x() - ic / 2), int(cr.center().y() - ic / 2), ic, ic),
                         icons.pixmap("music", QColor(255, 255, 255, 90), ic))
        p.restore()

        t = self.pb.current
        tr = boxes["title"]
        align = boxes["align"]
        editorial = self.layout_mode == EDITORIAL
        title_px = max(22, min(46 if editorial else 30, int(H / (18 if editorial else 28))))
        tf, af = _font(title_px, QFont.Weight.DemiBold), _font(int(title_px * 0.55))
        title = t.title if t else "Nothing playing"
        artist = " · ".join(x for x in ((t.artist if t else ""), (t.source_label if t else "")) if x)
        fm = QFontMetrics(tf)
        flags = align | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        lines = fm.boundingRect(QRect(0, 0, tr.width(), 10_000), flags, title).height()
        max_h = fm.lineSpacing() * (2 if editorial else 1)
        if lines > max_h or not editorial:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, tr.width() * (2 if editorial else 1) - 10)
        p.setFont(tf)
        p.setPen(QColor(255, 255, 255, 240))
        title_rect = QRect(tr.left(), tr.top(), tr.width(), min(max_h, lines) + 4)
        p.drawText(title_rect, flags, title)
        p.setFont(af)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRect(tr.left(), title_rect.bottom() + 6, tr.width(), int(title_px * 0.9)),
                   align | Qt.AlignmentFlag.AlignTop,
                   QFontMetrics(af).elidedText(artist, Qt.TextElideMode.ElideRight, tr.width()))
        p.end()
        return out

    def paintEvent(self, e):
        key = (self.pb.current.key if self.pb.current else None, self.width(), self.height(), self.layout_mode,
               self._has_lyrics(), self._cover() is not None)
        if self._static is None or key != self._static_key:
            self._static = self._render_static()
            self._static_key = key
        p = QPainter(self)
        r = e.rect()
        p.drawPixmap(r, self._static, QRectF(QRectF(r).topLeft() * self._static.devicePixelRatio(),
                                              QRectF(r).size() * self._static.devicePixelRatio()).toRect())
        # thin progress line with the times at its ends
        pr = self._progress_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(pr), 1.5, 1.5)
        if self._len > 0:
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(QRectF(pr.left(), pr.top(), pr.width() * min(1.0, self._pos / self._len), pr.height()),
                              1.5, 1.5)
            p.setFont(_font(12))
            p.setPen(QColor(255, 255, 255, 130))
            p.drawText(QRect(pr.left() - 70, pr.top() - 8, 60, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       format_duration(self._pos))
            p.drawText(QRect(pr.right() + 10, pr.top() - 8, 60, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       format_duration(self._len))
        p.end()

    def mousePressEvent(self, e):
        pr = self._progress_rect().adjusted(-4, -10, 4, 10)
        if e.button() == Qt.MouseButton.LeftButton and pr.contains(e.position().toPoint()) and self._len > 0:
            frac = (e.position().x() - pr.left() - 4) / max(1, pr.width() - 8)
            self.pb.seek(max(0.0, min(1.0, frac)) * self._len)
        self._wake()

