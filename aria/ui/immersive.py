"""Immersive Mode: full-screen listening with the cover and the lyrics at its heart.

It's its own window over the main one, driven by the same Playback, so going in or
out never interrupts the music (queue, volume, Tempo Flow and cross-fades carry on).

Two layouts (V switches, the choice is remembered):

* Editorial Lyrics — the title and large synced lyrics on the left, a big square cover on
  the right, over a blurred, muted backdrop of the cover. No lyrics: the title alone.
* Collage — the whole screen a mosaic of square covers of the songs coming up (queue first,
  For You after), each with its name on it; the playing song is the big square in the middle
  with its lyrics, play / pause and progress right on the cover. The covers can be clicked,
  right-clicked and reached from the keyboard; they change only when Collage opens or the
  song changes (see :mod:`aria.ui.collage`).

Kept light on resources: the background and the front (cover, shadow, title, card) are each
painted once per song or window size into a cached image. After that only the lyrics, the thin
progress line and the controls ever repaint. The controls appear when the mouse moves and fade
after 3 s.
Volume: the slider in the controls, ↑ / ↓, or the mouse wheel away from the lyrics.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QWidget

from aria.core.models import format_duration
from aria.core.player import LOADING, PLAYING
from aria.ui import icons
from aria.ui.collage import CollageView
from aria.ui.theme import pixel_font as _font, radius, thin_bar as bar
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.thumbs import ART_HEIGHT, artwork_urls
from aria.ui.widgets.lyrics_view import LyricsView

IDLE_MS = 3000
EDITORIAL, GALLERY = "editorial", "collage"       # GALLERY: the Collage layout ("gallery" in older settings)


SEEK_STEP = 5                  # seconds ←/→ jump


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
        rr = radius(r.width(), r.width(), r.height())          # §0.2: rounded squares, not discs
        if self.solid:
            p.setBrush(QColor(255, 255, 255, 235 if self._hover else 215))
            p.drawRoundedRect(r, rr, rr)
        elif self._hover or self.isChecked():
            p.setBrush(QColor(255, 255, 255, 34 if self._hover else 22))
            p.drawRoundedRect(r, rr, rr)
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
        bar(p, t)
        x = t.left() + t.width() * self.value / 100
        p.setBrush(QColor(255, 255, 255, 210))
        bar(p, QRectF(t.left(), t.top(), x - t.left(), t.height()))
        r = 7 if (self._hover or self._drag) else 5
        p.drawEllipse(QRectF(x - r, t.center().y() - r, 2 * r, 2 * r))     # knobs stay round (§0.2 exempts them)
        p.end()


class Controls(QWidget):
    """Transport and mode buttons along the bottom; fades in and out as a whole."""

    def __init__(self, parent):
        super().__init__(parent)
        self.pill = False                      # Collage: a dark rounded back, so the buttons read over the covers
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.layout_btn = GlassButton("sidebar", "Switch Layout (V)", 36, 17)
        self.lyrics_btn = GlassButton("list", "Show Lyrics (L)", 36, 17)
        self.lyrics_btn.setCheckable(True)
        self.save_btn = GlassButton("heart", "Save to Library", 36, 17)
        self.save_btn.setCheckable(True)
        self.playlist_btn = GlassButton("queue-add", "Add to Playlist", 36, 17)
        self.prev = GlassButton("previous", "Previous", 44, 18)
        self.play = GlassButton("play", "Play / Pause", 56, 22, solid=True)
        self.next = GlassButton("next", "Next", 44, 18)
        self.exit_btn = GlassButton("close", "Exit Immersive Mode (Esc)", 36, 17)
        self.mute = GlassButton("volume", "Mute", 36, 17)
        self.volume = GlassSlider(110)
        # left and right groups have the same width, so the transport sits in the middle
        for b in (self.layout_btn, self.lyrics_btn, self.save_btn, self.playlist_btn):
            lay.addWidget(b)
        # the transport sits in the middle: the left group is padded to the right group's width
        lay.addSpacing(18 + (36 + 110 + 36 + 10 * 2) - (36 * 4 + 10 * 3))
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

    def set_pill(self, on: bool) -> None:
        if on != self.pill:
            self.pill = on
            self.play.solid = not on               # Collage: plain like the buttons beside it
            self.play.update()
            self.layout().setContentsMargins(14 if on else 0, 0, 14 if on else 0, 0)
            self.update()

    def paintEvent(self, _e):
        if self.pill:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QColor(255, 255, 255, 22))
            p.setBrush(QColor(14, 14, 18, 215))
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(r, radius(18, r.width(), r.height()), radius(18, r.width(), r.height()))
            p.end()

    def _faded(self) -> None:
        if self.fade.endValue() == 0.0:
            self.setVisible(False)

    def sizeHint(self) -> QSize:
        return QSize(self.layout().sizeHint().width(), 60)

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

    def __init__(self, playback, settings, source_lyrics: LyricsView, actions=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Aria — Immersive")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pb = playback
        self.settings = settings
        self.source = source_lyrics                    # Now Playing's lyrics: fetched once, shown twice
        self._static: QPixmap | None = None           # the front: cover, title (and Collage's card)
        self._static_key = None
        self._back: QPixmap | None = None             # Editorial's blurred backdrop
        self._back_key = None
        self._pos = 0.0
        self._len = 0.0

        self.actions = actions
        self.collage = CollageView(self, playback, actions)
        self.collage.hide()
        self.collage_play = GlassButton("pause", "Play / Pause", 32, 15)     # on the middle square's foot
        self.collage_play.setParent(self.collage)
        self.collage_play.clicked.connect(playback.toggle)
        self.collage.transition.connect(self._on_transition)
        self.collage.relaid.connect(self._arrange)      # the middle square moved: lyrics and play button follow
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
        c.save_btn.clicked.connect(self._toggle_saved)
        c.playlist_btn.clicked.connect(self._playlist_menu)
        if actions is not None:
            actions.library.changed.connect(self._sync_saved)
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
        self._sync_saved()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._wake()
        QTimer.singleShot(0, self._refill)

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
        elif k == Qt.Key.Key_Right:
            self.pb.seek_by(SEEK_STEP)
        elif k == Qt.Key.Key_Left:
            self.pb.seek_by(-SEEK_STEP)
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
        return GALLERY if self.settings["immersive_layout"] in (GALLERY, "gallery") else EDITORIAL

    def toggle_layout(self) -> None:
        """Editorial Lyrics ⇄ Collage; the music, queue, lyrics and progress carry on untouched."""
        self.settings["immersive_layout"] = EDITORIAL if self.layout_mode == GALLERY else GALLERY
        self._arrange()
        self._refill()

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
        self._sync_saved()
        self._arrange()
        self._refill(crossfade=True)

    def _refill(self, crossfade: bool = False) -> None:
        """Who's on the Collage: only on opening it and when the song changes."""
        if self.layout_mode == GALLERY and self.isVisible():
            self.collage.refill(crossfade)

    def _on_thumb(self, key: str) -> None:
        """Only the playing song's artwork arriving matters here."""
        t = self.pb.current
        if t and self.isVisible() and key == thumbs_mod.instance().key(artwork_urls(t), ART_HEIGHT):
            self._arrange()

    def _toggle_saved(self) -> None:
        if self.actions is not None and self.pb.current is not None:
            self.actions.toggle_saved(self.pb.current)
        self._sync_saved()
        self._wake()

    def _sync_saved(self) -> None:
        t = self.pb.current
        saved = bool(t and self.actions is not None and t.key in self.actions.library)
        b = self.controls.save_btn
        b.setChecked(saved)
        b.setAccessibleName("Remove from Library" if saved else "Save to Library")

    def _playlist_menu(self) -> None:
        if self.actions is None or self.pb.current is None:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        self.actions.fill_playlist_menu(m, [self.pb.current], self)
        b = self.controls.playlist_btn
        m.exec(b.mapToGlobal(b.rect().topLeft()) - QPoint(0, m.sizeHint().height() + 6))   # above the bar
        self._wake()

    def _on_state(self, s: str) -> None:
        icon = "pause" if s in (PLAYING, LOADING) else "play"
        self.controls.play.set_icon(icon)
        self.collage_play.set_icon(icon)
        self.collage.on_state(s)

    def _on_transition(self, moving: bool) -> None:
        """Collage's move to a clicked square: the middle square's lyrics and button sit it out."""
        if self.layout_mode == GALLERY:
            self.collage_play.setVisible(not moving)
            self.lyrics.setVisible(not moving and "lyrics" in self._boxes())

    def _on_position(self, pos: float, length: float) -> None:
        self._pos, self._len = pos, length
        if self.isVisible():
            self.lyrics.set_position(pos)
            if self.layout_mode == GALLERY:
                self.collage.set_progress(pos, length)            # on the middle square's foot
            else:
                self.update(self._progress_rect().adjusted(-74, -24, 74, 4))  # just the progress line and its times

    # ---- geometry --------------------------------------------------------------

    def _boxes(self) -> dict:
        """Where everything goes for the current layout and size (controls keep the bottom ~130 px)."""
        W, H = self.width(), self.height()
        lyr = self._has_lyrics()
        m = max(40, int(min(W, H) * 0.07))
        bottom = H - 130
        out = {}
        if self.layout_mode == GALLERY:
            # the middle square of the mosaic: the lyrics go on its cover
            out["card"] = out["cover"] = self.collage.centre.toRect()
            if lyr:
                out["lyrics"] = self.collage.lyrics_rect().toRect()
            out["title"] = QRect()
            out["align"] = Qt.AlignmentFlag.AlignLeft
            return out
        # Editorial Lyrics: words on the left, a large square cover on the right
        side = int(min(H * 0.62, W * 0.40, bottom - m))         # always square (a video frame is cropped)
        cover = QRect(W - m - int(W * 0.03) - side, (bottom - side) // 2 + 20, side, side)
        text_x = m + int(W * 0.03)
        text_w = max(200, cover.left() - int(W * 0.06) - text_x)
        out["cover"] = cover
        if lyr:
            top = max(m // 2, int(H * 0.09))
            out["title"] = QRect(text_x, top, text_w, int(H * 0.16))
            lyr_top = out["title"].bottom() + 12
            out["lyrics"] = QRect(text_x, lyr_top, text_w, bottom - lyr_top)
        else:
            out["title"] = QRect(text_x, cover.center().y() - int(H * 0.09), text_w, int(H * 0.2))
        out["align"] = Qt.AlignmentFlag.AlignLeft
        return out

    def _progress_rect(self) -> QRect:
        W, H = self.width(), self.height()
        w = min(int(W * 0.5), 720)
        return QRect((W - w) // 2, H - 34, w, 3)

    def _arrange(self) -> None:
        collage = self.layout_mode == GALLERY
        self.collage.setVisible(collage)
        if collage:
            self.collage.setGeometry(self.rect())
            self.collage.place()
            self.collage.lower()
            self.collage_play.setGeometry(self.collage.play_rect().toRect())
        boxes = self._boxes()
        lyr = "lyrics" in boxes
        self.lyrics.setVisible(lyr and not (collage and self.collage.busy))
        self.collage_play.setVisible(collage and not self.collage.busy)
        if lyr:
            # Collage: sized once to the middle square (never to a line), so lines fit and don't jump
            self.lyrics.scale = max(18, min(34, round(self.collage.centre.height() / 15))) if collage else 27
            self.lyrics.set_centered(False)
            self.lyrics.setGeometry(boxes["lyrics"])
        self.controls.set_pill(collage)
        hint = self.controls.sizeHint()
        self.controls.setGeometry((self.width() - hint.width()) // 2, self.height() - 108, hint.width(), hint.height())
        self.controls.raise_()
        self._invalidate()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrange()

    # ---- the cached backdrop ---------------------------------------------------

    def _invalidate(self) -> None:
        self._static_key = self._back_key = None
        self.update()

    def _cover(self):
        """The same large artwork Now Playing shows (already cached)."""
        t = self.pb.current
        pm = thumbs_mod.instance().get(artwork_urls(t), ART_HEIGHT) if t else None
        return pm if pm is not None and not pm.isNull() else None

    def _backdrop(self) -> QPixmap:
        """Editorial's background: the cover, blurred and muted (once per song and size)."""
        dpr = self.devicePixelRatioF()
        W, H = self.width(), self.height()
        out = QPixmap(int(W * dpr), int(H * dpr))
        out.setDevicePixelRatio(dpr)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cover = self._cover()
        base = self._base()
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
        veil = QColor(base)
        veil.setAlphaF(0.55)
        p.fillRect(QRect(0, 0, W, H), veil)
        vg = QRadialGradient(W / 2, H / 2, max(W, H) * 0.75)
        vg.setColorAt(0.0, QColor(0, 0, 0, 0))
        vg.setColorAt(1.0, QColor(0, 0, 0, 120))
        p.fillRect(QRect(0, 0, W, H), vg)
        p.end()
        return out

    def _base(self) -> QColor:
        cover = self._cover()
        return tint_mod.surface(tint_mod.hue_of(cover) if cover is not None else None, "canvas")

    def _front(self) -> QPixmap:
        """Editorial's cover, its shadow and the title: transparent, once per song / size."""
        dpr = self.devicePixelRatioF()
        W, H = self.width(), self.height()
        out = QPixmap(int(W * dpr), int(H * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        boxes = self._boxes()
        cover = self._cover()
        cr = QRectF(boxes["cover"])
        radius = min(cr.width(), cr.height()) * 0.03
        for k in range(10, 0, -2):                     # a very light shadow under the cover
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 7))
            p.drawRoundedRect(cr.adjusted(-k, -k + 8, k, k + 10), radius + k, radius + k)
        clip = QPainterPath()
        clip.addRoundedRect(cr, radius, radius)
        p.save()
        p.setClipPath(clip)
        if cover is not None:
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
        title_px = max(22, min(44, int(H / 20)))
        if "lyrics" not in boxes:
            title_px = max(26, min(54, int(H / 16)))     # no lyrics: the title carries the page
        tf, af = _font(title_px, QFont.Weight.DemiBold), _font(int(title_px * 0.55))
        from aria.core import identity
        title, who = identity.display(t) if t else ("Nothing playing", "")
        artist = " · ".join(x for x in (who, (t.source_label if t else "")) if x)
        fm = QFontMetrics(tf)
        flags = align | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        lines = fm.boundingRect(QRect(0, 0, tr.width(), 10_000), flags, title).height()
        max_h = fm.lineSpacing() * 2
        if lines > max_h:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, tr.width() * 2 - 10)
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
        gallery_mode = self.layout_mode == GALLERY
        p = QPainter(self)
        r = e.rect()

        def blit(pm, opacity=1.0):
            if pm is None:
                return
            p.setOpacity(opacity)
            d = pm.devicePixelRatio()
            if round(pm.width() / d) == self.width() and round(pm.height() / d) == self.height():
                p.drawPixmap(r, pm, QRectF(QRectF(r).topLeft() * d, QRectF(r).size() * d).toRect())
            else:
                p.drawPixmap(self.rect(), pm)            # made for another size: stretch until redone
            p.setOpacity(1.0)

        if gallery_mode:
            p.fillRect(r, QColor(8, 8, 10))                 # between the squares; they paint the rest
            p.end()
            return
        else:
            bkey = (self.pb.current.key if self.pb.current else None, self.width(), self.height(),
                    self._cover() is not None)
            if self._back is None or bkey != self._back_key:
                self._back, self._back_key = self._backdrop(), bkey
            blit(self._back)
        fkey = (self.pb.current.key if self.pb.current else None, self.width(), self.height(), self.layout_mode,
                self._has_lyrics(), self._cover() is not None)
        if self._static is None or fkey != self._static_key:
            self._static, self._static_key = self._front(), fkey
        blit(self._static)
        # thin progress line with the times at its ends
        pr = self._progress_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        bar(p, QRectF(pr))
        if self._len > 0:
            p.setBrush(QColor(255, 255, 255, 200))
            bar(p, QRectF(pr.left(), pr.top(), pr.width() * min(1.0, self._pos / self._len), pr.height()))
            p.setFont(_font(12))
            p.setPen(QColor(255, 255, 255, 130))
            p.drawText(QRect(pr.left() - 70, pr.top() - 8, 60, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       format_duration(self._pos))
            p.drawText(QRect(pr.right() + 10, pr.top() - 8, 60, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       format_duration(self._len))
        p.end()

    def mousePressEvent(self, e):
        pr = self._progress_rect().adjusted(-4, -10, 4, 10)
        if e.button() == Qt.MouseButton.LeftButton and pr.contains(e.position().toPoint()) and self._len > 0 \
                and self.layout_mode != GALLERY:
            frac = (e.position().x() - pr.left() - 4) / max(1, pr.width() - 8)
            self.pb.seek(max(0.0, min(1.0, frac)) * self._len)
        self._wake()

