"""Music Constellation: explore artists and songs as a map instead of a list.

Smoothness comes from three things:
- every node is a sprite driven by a critically-damped spring each frame, so moves,
  hovers and transitions blend into each other instead of jumping between tweens;
- the starfield and node bitmaps are rendered once and blitted, so a frame is cheap;
- neighbours are prefetched while the pointer rests on them, so a click usually lands instantly.
"""

from __future__ import annotations

import collections
import logging
import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit, QMenu, QSizePolicy, QToolTip, QWidget

from aria import providers
from aria.core import constellation as cst
from aria.core import tasks, textnorm
from aria.ui import icons
from aria.ui.pages.base import Page
from aria.ui.theme import OVERLAY, font, parse_color, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import Button

log = logging.getLogger(__name__)

WHITE = QColor(255, 255, 255)
STIFFNESS = 9.0          # spring rate (1/s): higher = snappier
DRIFT = 2.2              # px of idle floating
CACHE_SIZE = 40


def _white(a: float) -> QColor:
    c = QColor(WHITE)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class _Sprite:
    __slots__ = ("node", "x", "y", "tx", "ty", "scale", "tscale", "alpha", "talpha", "phase", "dying")

    def __init__(self, node: cst.Node, x: float, y: float):
        self.node = node
        self.x = self.tx = x
        self.y = self.ty = y
        self.scale, self.tscale = 0.2, 1.0
        self.alpha, self.talpha = 0.0, 1.0
        self.phase = random.random() * math.tau
        self.dying = False


class Sky(QWidget):
    """The map. Centre node in the middle, songs on an inner orbit, artists further out."""

    explore = Signal(object)            # Node
    play = Signal(object)               # Node
    menu = Signal(object, object)       # Node, global pos
    back = Signal()
    resting = Signal(object)            # Node the pointer has rested on (prefetch)

    def __init__(self):
        super().__init__()
        self.galaxy: cst.Galaxy | None = None
        self.loading = False
        self.can_back = False
        self.empty_text = ("Pick an artist to start", "Type a name, or start from what’s playing.")
        self._sprites: dict[str, _Sprite] = {}
        self._hover: _Sprite | None = None
        self._pending: _Sprite | None = None          # node being explored (pulses while loading)
        self._origin = QPointF()
        self._bg: QPixmap | None = None
        self._sprite_cache: dict[tuple, QPixmap] = {}
        self._clock = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._frame)
        self._rest = QTimer(self)
        self._rest.setSingleShot(True)
        self._rest.setInterval(300)
        self._rest.timeout.connect(lambda: self._hover and self.resting.emit(self._hover.node))
        self.setMouseTracking(True)
        self.setMinimumSize(420, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        thumbs_mod.instance().ready.connect(self._thumb_ready)
        theme.changed.connect(self._invalidate_bg)

    # ---- data ----------------------------------------------------------------

    def set_galaxy(self, g: cst.Galaxy, origin: QPointF | None = None) -> None:
        old = self._sprites
        self.galaxy = g
        start = origin if origin is not None else self._center()
        self._origin = start
        fresh: dict[str, _Sprite] = {}
        for node in [g.center] + g.nodes:
            key = node.key
            if key in old and not old[key].dying:
                s = old.pop(key)
                s.node = node                      # survivors glide to their new place
            else:
                s = _Sprite(node, start.x(), start.y())
            s.dying = False
            s.talpha = 1.0
            s.tscale = 1.0
            fresh[key] = s
        for s in old.values():                     # leavers fade out where they are
            s.dying = True
            s.talpha = 0.0
            s.tscale = 0.6
            fresh[f"ghost:{id(s)}"] = s
        self._sprites = fresh
        self._pending = None
        self._layout()
        self._wake()

    def set_loading(self, on: bool, node: cst.Node | None = None) -> None:
        self.loading = on
        self._pending = self._sprites.get(node.key) if (on and node) else None
        self._wake()

    # ---- layout --------------------------------------------------------------

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2 + 6)

    def _layout(self) -> None:
        g = self.galaxy
        if not g:
            return
        c = self._center()
        span = min(self.width(), self.height())
        songs = [n for n in g.nodes if n.kind == cst.SONG]
        artists = [n for n in g.nodes if n.kind == cst.ARTIST]
        for ring, nodes, radius, offset in ((0, songs, span * 0.25, -math.pi / 2),
                                            (1, artists, span * 0.41, -math.pi / 2 + 0.35)):
            n = max(1, len(nodes))
            for i, node in enumerate(nodes):
                a = offset + math.tau * i / n
                r = radius * (1.08 - 0.16 * node.weight) + (8 if i % 2 else -8) * ring
                s = self._sprites.get(node.key)
                if s:
                    s.tx, s.ty = c.x() + r * math.cos(a), c.y() + r * math.sin(a) * 0.86
        s = self._sprites.get(g.center.key)
        if s:
            s.tx, s.ty = c.x(), c.y()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._invalidate_bg()
        self._layout()
        for s in self._sprites.values():          # resizing shouldn't animate
            s.x, s.y = s.tx, s.ty
        self.update()

    def _invalidate_bg(self) -> None:
        self._bg = None
        self.update()

    def _thumb_ready(self, _key: str) -> None:
        self._sprite_cache.clear()
        self.update()

    # ---- animation -----------------------------------------------------------

    def _wake(self) -> None:
        self._clock = time.monotonic()
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._wake()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _frame(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._clock)
        self._clock = now
        k = 1 - math.exp(-STIFFNESS * dt)
        for s in self._sprites.values():
            s.x += (s.tx - s.x) * k
            s.y += (s.ty - s.y) * k
            hover = 1.12 if s is self._hover else 1.0
            pulse = 1.0 + 0.06 * math.sin(now * 6) if s is self._pending else 1.0
            s.scale += (s.tscale * hover * pulse - s.scale) * k
            dim = 0.45 if (self.loading and s is not self._pending and not s.dying) else 1.0
            s.alpha += (s.talpha * dim - s.alpha) * k
        self._sprites = {key: s for key, s in self._sprites.items() if not (s.dying and s.alpha < 0.02)}
        self.update()
        if not self.isActiveWindow() and not self.loading and self._settled():
            self._timer.stop()                    # idle in the background: no frames

    def _settled(self) -> bool:
        return all(abs(s.tx - s.x) < 0.3 and abs(s.ty - s.y) < 0.3 and abs(s.alpha - s.talpha) < 0.01
                   for s in self._sprites.values())

    # ---- hit testing -----------------------------------------------------------

    def _base(self, s: _Sprite) -> float:
        node = s.node
        if self.galaxy and node is self.galaxy.center:
            return 84
        return 40 + 18 * node.weight if node.kind == cst.ARTIST else 34 + 10 * node.weight

    def _pos(self, s: _Sprite) -> QPointF:
        t = time.monotonic()
        return QPointF(s.x + DRIFT * math.sin(t * 0.7 + s.phase), s.y + DRIFT * math.cos(t * 0.6 + s.phase))

    def _at(self, pos: QPointF) -> _Sprite | None:
        best = None
        for s in self._sprites.values():
            if s.dying:
                continue
            d = (self._pos(s) - pos).manhattanLength()
            if d < self._base(s) * s.scale / 2 + 6 and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None

    # ---- events --------------------------------------------------------------

    def mouseMoveEvent(self, e):
        s = self._at(e.position())
        if s is not self._hover:
            self._hover = s
            self.setCursor(Qt.CursorShape.PointingHandCursor if s else Qt.CursorShape.ArrowCursor)
            self._wake()
            if s:
                self._rest.start()
                node = s.node
                tip = node.name if node.kind == cst.ARTIST else f"{node.name}\n{node.subtitle}"
                extra = [x for x, on in (("Often played together", node.together), ("Saved", node.saved)) if on]
                QToolTip.showText(e.globalPosition().toPoint(), tip + ("\n" + " · ".join(extra) if extra else ""), self)
            else:
                self._rest.stop()
                QToolTip.hideText()

    def leaveEvent(self, e):
        self._hover = None
        self._rest.stop()
        self._wake()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.can_back and self._back_rect().contains(e.position()):
            self.back.emit()
            return
        s = self._at(e.position())
        if not s:
            return
        if e.button() == Qt.MouseButton.RightButton:
            self.menu.emit(s.node, e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.LeftButton and not (self.galaxy and s.node is self.galaxy.center):
            self._origin = QPointF(s.x, s.y)
            self.explore.emit(s.node)

    def mouseDoubleClickEvent(self, e):
        s = self._at(e.position())
        if s and s.node.track:
            self.play.emit(s.node)

    def _back_rect(self) -> QRectF:
        return QRectF(12, 12, 30, 30)

    # ---- rendering -----------------------------------------------------------

    def _background(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        if self._bg is None or self._bg.size() != self.size() * dpr:
            pm = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(0, 0, self.width(), self.height())
            clip = QPainterPath()
            clip.addRoundedRect(r, 12, 12)
            p.setClipPath(clip)
            p.fillRect(r, theme.color("canvas"))
            rnd = random.Random(7)
            p.setPen(Qt.PenStyle.NoPen)
            for _ in range(160):
                b = rnd.random()
                p.setBrush(_white(0.05 + 0.18 * b))
                size = 0.6 + 1.1 * b
                p.drawEllipse(QPointF(rnd.random() * r.width(), rnd.random() * r.height()), size, size)
            p.end()
            self._bg = pm
        return self._bg

    def _sprite_pixmap(self, node: cst.Node, size: int, center: bool) -> QPixmap:
        """Node bitmap: clipped thumbnail + ring + saved badge, rendered once per size."""
        dpr = self.devicePixelRatioF()
        pm = thumbs_mod.instance().get(node.thumb) if node.thumb else None
        key = (node.key, size, center, dpr, pm.cacheKey() if pm else 0)
        cached = self._sprite_cache.get(key)
        if cached is not None:
            return cached
        pad = 6
        full = size + 2 * pad
        out = QPixmap(round(full * dpr), round(full * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(pad, pad, size, size)
        path = QPainterPath()
        if node.kind == cst.ARTIST:
            path.addEllipse(rect)
        else:
            path.addRoundedRect(rect, size * 0.22, size * 0.22)
        p.save()
        p.setClipPath(path)
        if pm and not pm.isNull():
            side = min(pm.width(), pm.height())
            p.drawPixmap(rect, pm, QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side))
        else:
            p.fillRect(rect, QColor("#2c2c2e"))
            s = size * 0.42
            p.drawPixmap(QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s).toRect(),
                         icons.pixmap("user" if node.kind == cst.ARTIST else "music", _white(0.5), int(s)))
        p.restore()
        ring = QPen(theme.color("accent") if center else _white(0.55 if node.kind == cst.ARTIST else 0.25))
        ring.setWidthF(2.0 if center else 1.2)
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if node.saved:
            b = QRectF(rect.right() - 13, rect.top() - 1, 16, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawEllipse(b)
            p.drawPixmap(b.adjusted(3, 3, -3, -3).toRect(), icons.pixmap("heart", theme.color("on_accent"), 10))
        p.end()
        if len(self._sprite_cache) > 200:
            self._sprite_cache.clear()
        self._sprite_cache[key] = out
        return out

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self._background())
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)

        g = self.galaxy
        if not g:
            p.setFont(font("title3", 600))
            p.setPen(_white(0.6))
            p.drawText(QRectF(0, r.center().y() - 26, r.width(), 24), Qt.AlignmentFlag.AlignCenter, self.empty_text[0])
            p.setFont(font("callout"))
            p.setPen(_white(0.4))
            p.drawText(QRectF(0, r.center().y() + 2, r.width(), 20), Qt.AlignmentFlag.AlignCenter, self.empty_text[1])
        else:
            center = self._sprites.get(g.center.key)
            cpos = self._pos(center) if center else self._center()
            for s in self._sprites.values():                       # edges first
                if center is None or s is center or s.alpha < 0.02:
                    continue
                node = s.node
                hot = s is self._hover
                col = theme.color("accent") if node.together else _white(0.10 + 0.16 * node.weight + (0.25 if hot else 0))
                col.setAlphaF(col.alphaF() * s.alpha)
                pen = QPen(col)
                pen.setWidthF(1.6 if hot else 1.0)
                if node.together:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(cpos, self._pos(s))
            order = sorted(self._sprites.values(), key=lambda s: (s is center, s is self._hover))
            for s in order:
                self._paint_sprite(p, s, s is center)

        if self.can_back:
            br = self._back_rect()
            p.setPen(QPen(_white(0.12), 1))
            p.setBrush(parse_color(OVERLAY))
            p.drawRoundedRect(br, 9, 9)
            p.drawPixmap(br.adjusted(7, 7, -7, -7).toRect(), icons.pixmap("arrow-left", "#ffffff", 16))
        if self.loading:
            self._badge(p, "Loading…", top_right=True)
        if g:
            songs = sum(1 for n in g.nodes if n.kind == cst.SONG)
            artists = sum(1 for n in g.nodes if n.kind == cst.ARTIST)
            saved = sum(1 for n in g.nodes if n.saved)
            bits = [g.center.name if g.center.kind == cst.ARTIST else g.center.subtitle,
                    f"{artists} related artist{'s' if artists != 1 else ''}" if artists else "",
                    f"{songs} song{'s' if songs != 1 else ''}" if songs else "",
                    f"{saved} saved" if saved else ""]
            self._badge(p, " · ".join(b for b in bits if b))
        p.end()

    def _paint_sprite(self, p: QPainter, s: _Sprite, is_center: bool) -> None:
        if s.alpha < 0.02:
            return
        node = s.node
        base = self._base(s)
        drawn = int(round(base * 1.12))                     # rendered a bit large, scaled down: crisp on hover
        sprite = self._sprite_pixmap(node, drawn, is_center)
        size = base * s.scale
        pos = self._pos(s)
        p.setOpacity(s.alpha)
        if is_center:
            glow = QColor(theme.color("accent"))
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(3):
                glow.setAlphaF((0.10 - 0.03 * k) * s.alpha)
                p.setBrush(glow)
                d = size / 2 + 8 + 7 * k
                p.drawEllipse(pos, d, d)
        full = size * (drawn + 12) / drawn
        p.drawPixmap(QRectF(pos.x() - full / 2, pos.y() - full / 2, full, full), sprite, QRectF(sprite.rect()))

        hot = s is self._hover
        name = node.name if node.kind == cst.ARTIST else textnorm.display_title(node.name, node.subtitle)
        f = font("callout" if is_center else "caption", 600 if (is_center or node.kind == cst.ARTIST) else 400)
        p.setFont(f)
        width = 180 if is_center or hot else 112
        text = QFontMetrics(f).elidedText(name, Qt.TextElideMode.ElideRight, width)
        p.setPen(_white(0.92 if is_center or hot else 0.72))
        p.drawText(QRectF(pos.x() - width / 2, pos.y() + size / 2 + 4, width, 18), Qt.AlignmentFlag.AlignCenter, text)
        p.setOpacity(1.0)

    def _badge(self, p: QPainter, text: str, top_right: bool = False) -> None:
        f = font("caption", 600)
        w = min(QFontMetrics(f).horizontalAdvance(text) + 24, self.width() - 24)
        rect = QRectF(self.width() - 12 - w, 12, w, 26) if top_right else QRectF(12, self.height() - 38, w, 26)
        p.setPen(QPen(_white(0.12), 1))
        p.setBrush(parse_color(OVERLAY))
        p.drawRoundedRect(rect, 8.6, 8.6)
        p.setFont(f)
        p.setPen(WHITE)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, int(w - 16)))


class ConstellationPage(Page):
    def __init__(self, playback, library, actions):
        super().__init__("Constellation")
        self.pb = playback
        self.library = library
        self.actions = actions
        self._trail: list[tuple[str, object]] = []     # (kind, artist name | track)
        self._latest = tasks.Latest()
        self._started = False
        self._cache: collections.OrderedDict[str, cst.Galaxy] = collections.OrderedDict()
        self._inflight: dict[str, list] = {}           # key → callbacks waiting for that galaxy

        bar = self.toolbar()
        self.field = QLineEdit()
        self.field.setPlaceholderText("Artist")
        self.field.setFixedWidth(260)
        self._icon = QAction(self.field)
        self.field.addAction(self._icon, QLineEdit.ActionPosition.LeadingPosition)
        self.field.returnPressed.connect(lambda: self.explore_artist(self.field.text().strip(), reset=True))
        self.from_now = Button("Start from Now Playing", "borderless", icon="orbit")
        self.from_now.clicked.connect(self.start_from_current)
        bar.addWidget(self.field)
        bar.addWidget(self.from_now)
        bar.addStretch(1)

        self.sky = Sky()
        self.root.addWidget(self.sky, 1)
        self.sky.explore.connect(self._on_node)
        self.sky.play.connect(lambda n: actions.play([n.track]))
        self.sky.menu.connect(self._menu)
        self.sky.back.connect(self.go_back)
        self.sky.resting.connect(self._prefetch)
        theme.changed.connect(self._retint)
        self._retint()

    def _retint(self) -> None:
        self._icon.setIcon(icons.icon("search", theme.color("tertiary"), 15))

    def on_shown(self) -> None:
        if not self._started and self.pb.current:
            self.start_from_current()

    def start_from_current(self) -> None:
        t = self.pb.current or (self.pb.history[-1] if self.pb.history else None)
        if t:
            self.explore_artist(textnorm.artist_of(t.title, t.artist) or t.artist, reset=True)

    # ---- data ----------------------------------------------------------------

    @staticmethod
    def _key(kind: str, value) -> str:
        return f"{kind}:{textnorm.artist_key(value) if kind == cst.ARTIST else value.key}"

    def _work(self, kind: str, value):
        lib, hist = list(self.library.tracks), list(self.pb.history)
        if kind == cst.ARTIST:
            return lambda: cst.explore_artist(providers.youtube, value, lib, hist)
        return lambda: cst.explore_song(providers.youtube, self.pb.recommender, value, lib)

    def _fetch(self, kind: str, value, done, failed=None) -> None:
        key = self._key(kind, value)
        if key in self._cache:
            self._cache.move_to_end(key)
            done(self._cache[key])
            return
        waiting = self._inflight.get(key)
        if waiting is not None:
            waiting.append((done, failed))
            return
        self._inflight[key] = [(done, failed)]

        def ok(g):
            self._cache[key] = g
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            for d, _f in self._inflight.pop(key, []):
                d(g)

        def err(e):
            for _d, f in self._inflight.pop(key, []):
                if f:
                    f(e)

        tasks.run(self._work(kind, value), ok, err)

    def _prefetch(self, node: cst.Node) -> None:
        """The pointer rested on a node: get its neighbourhood ready before the click."""
        if node.kind == cst.ARTIST:
            self._fetch(cst.ARTIST, node.name, lambda _g: None)
        elif node.track:
            self._fetch(cst.SONG, node.track, lambda _g: None)

    # ---- navigation ----------------------------------------------------------

    def _on_node(self, node: cst.Node) -> None:
        if node.kind == cst.ARTIST:
            self.explore_artist(node.name, from_node=node)
        elif node.track:
            self.explore_song(node.track, from_node=node)

    def go_back(self) -> None:
        if len(self._trail) < 2:
            return
        self._trail.pop()
        kind, value = self._trail.pop()
        if kind == cst.ARTIST:
            self.explore_artist(value)
        else:
            self.explore_song(value)

    def explore_artist(self, name: str, reset: bool = False, from_node=None) -> None:
        if not name:
            return
        self._started = True
        if reset:
            self._trail.clear()
        self._trail.append((cst.ARTIST, name))
        self.field.setText(name)
        self._show(cst.ARTIST, name, from_node)

    def explore_song(self, track, from_node=None) -> None:
        self._started = True
        self._trail.append((cst.SONG, track))
        self._show(cst.SONG, track, from_node)

    def _show(self, kind: str, value, from_node) -> None:
        ticket = self._latest.next()
        self.sky.can_back = len(self._trail) > 1
        self.sky.set_loading(True, from_node)

        def done(g):
            if self._latest.is_current(ticket):
                self.sky.set_loading(False)
                self.sky.set_galaxy(g, self.sky._origin if self.sky.galaxy else None)

        def failed(exc):
            if self._latest.is_current(ticket):
                self.sky.set_loading(False)
                log.error("Constellation failed: %s", exc)
                self.actions.toast("danger", "Couldn’t load the map")

        self._fetch(kind, value, done, failed)

    def _menu(self, node: cst.Node, pos) -> None:
        m = QMenu(self)
        c = theme.color("label")
        if node.track:
            m.addAction(icons.icon("play", c, 16), "Play", lambda: self.actions.play([node.track]))
            m.addAction(icons.icon("queue-add", c, 16), "Add to Queue", lambda: self.actions.enqueue([node.track]))
            self.actions.fill_playlist_menu(m.addMenu(icons.icon("list", c, 16), "Add to Playlist"), [node.track], self)
            m.addAction(icons.icon("heart", c, 16), "Save to Library", lambda: self.actions.save([node.track]))
            m.addSeparator()
        if node.kind == cst.ARTIST:
            m.addAction(icons.icon("orbit", c, 16), f"Explore {node.name}", lambda: self.explore_artist(node.name))
        elif node.track:
            m.addAction(icons.icon("orbit", c, 16), "Explore This Song", lambda: self.explore_song(node.track))
            artist = node.subtitle
            if artist:
                m.addAction(icons.icon("user", c, 16), f"Explore {artist}", lambda: self.explore_artist(artist))
        m.exec(pos)
