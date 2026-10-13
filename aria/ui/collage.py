"""Immersive Collage: the whole screen a mosaic of covers, the song playing at its heart.

The playing song is the big square in the middle: its cover, dimmed, with the title and
artist in the corner, the current lyrics over it, and play / pause and the progress line
along its foot. Around it every tile — a rectangle of whole cells of one grid (1 × 1, 2 × 1,
1 × 2, 2 × 2, 3 × 2, 2 × 3, 3 × 3: simple ratios, one unit) running off the edges of the
screen — is a song coming up, its name written on it: from the queue first (about two thirds),
For You after, the library when those run short; never the song playing.

The tiles are live: a click selects one (its name and where it comes from show), a double-click
plays it (from the queue as the queue would, from For You as a recommendation is taken),
right-click offers the usual song actions; from the keyboard Tab / Shift+Tab select, Enter
plays, Menu opens the actions, ←/→ still seek. The hovered tile lifts a little. The middle
square isn't a song to pick: only its play button and progress line respond there.

When the next song is on the wall — double-clicked, or simply next (the song ended, Next) — the
wall slides to it: every tile glides, whole cells at a time, on one grid; only that tile grows,
into the middle square's place (the tiles it grows over go). The wall stays that way — the
tiles still on screen keep their songs and places; only the old middle square's place (once the
new song is actually playing, each new tile fading in on its own) and whatever slides in from
beyond the edges (already there as it comes) get new songs. Nothing is scaled but the tile that
grows, so the grid never gaps. Each tile is painted on its own from its cached cover; nothing
repaints the whole wall at once. Reduce Motion skips the move.

Otherwise what's on the wall changes only when Collage opens or the song changes — never for
queue edits, new recommendations, progress or lyrics. Then songs still on it keep their tiles
and only the tiles that change fade in, once; the middle square cross-fades to the new cover.
A new window size lays the wall out afresh (same songs).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from aria.core import identity, textnorm
from aria.ui.theme import pixel_font as _font, thin_bar as _bar
from aria.ui.widgets import rack as rack_mod
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.thumbs import ART_HEIGHT, artwork_urls

GAP = 4
MAX_TILES = 40
QUEUE_SHARE = 0.65
FADE_MS = 300
CENTRE_SHARE = 0.46              # the middle square: about this much of the screen's height
ZOOM_MS = 420                    # the camera's move to the next song's tile (ease-out, no overshoot)
POP = 3.0                        # the hovered tile lifts by this much on every side
SHAPES = ((1, 1, 5), (2, 1, 5), (1, 2, 5), (2, 2, 6), (3, 2, 3), (2, 3, 3), (3, 1, 2), (1, 3, 2),
          (3, 3, 2), (4, 2, 1), (2, 4, 1))                                   # cols, rows, weight
BIG_PX = 560                     # squares drawn larger than this use the full artwork
HOLD_MS = 6000                   # longest wait for the new song to start before the wall changes anyway
BACK = QColor(8, 8, 10)


@dataclass
class Tile:
    rect: QRectF
    cols: int = 1
    rows: int = 0

    def __post_init__(self):
        self.rows = self.rows or self.cols

    @property
    def span(self) -> int:
        return max(self.cols, self.rows)


def visible(r: QRectF, w: int, h: int) -> float:
    """Share of ``r`` on a w × h screen."""
    inter = r.intersected(QRectF(0, 0, w, h))
    return (inter.width() * inter.height()) / (r.width() * r.height()) if r.width() > 0 else 0.0


def layout(w: int, h: int, want: int = MAX_TILES) -> tuple[list[Tile], QRectF]:
    """The squares around a centred middle square, on one grid that runs off the screen's edges,
    with about ``want`` showing; and the middle square. Slots come back visible, biggest and
    nearest the middle first, so the next songs get the best places."""
    best = None
    for cols in range(6, 23):
        c = w / cols
        k = max(2, round(h * CENTRE_SHARE / c))
        side = k * c
        cx0, cy0 = (w - side) / 2, (h - side) / 2
        x0 = cx0 - math.ceil(cx0 / c) * c
        y0 = cy0 - math.ceil(cy0 / c) * c
        ncols, nrows = math.ceil((w - x0) / c), math.ceil((h - y0) / c)
        ccol, crow = round((cx0 - x0) / c), round((cy0 - y0) / c)
        free = [[not (ccol <= x < ccol + k and crow <= y < crow + k) for x in range(ncols)] for y in range(nrows)]
        for big in (2.5, 1.0, 0.35):                    # few … many tiles
            tiles = _pack(ncols, nrows, c, x0, y0, [row[:] for row in free], random.Random(), big)   # a new pattern each time
            # every tile that shows at all needs a song, or the edges go dark
            shown = sum(1 for t in tiles if visible(t.rect, w, h) > 0.04)
            score = abs(shown - want) + (2 * (shown - want) if shown > want else 0)
            if best is None or score < best[0]:
                centre = QRectF(x0 + ccol * c + GAP / 2, y0 + crow * c + GAP / 2, side - GAP, side - GAP)
                best = (score, tiles, centre)
    if best is None:
        return [], QRectF()
    _s, tiles, centre = best
    mid = centre.center()
    tiles = [t for t in tiles if visible(t.rect, w, h) > 0.04]          # nothing wasted off screen
    tiles.sort(key=lambda t: (visible(t.rect, w, h) < 0.5, -t.cols * t.rows, (t.rect.center() - mid).manhattanLength()))
    return tiles, centre


def _pack(cols: int, rows: int, c: float, x0: float, y0: float, free: list[list[bool]], rng: random.Random,
          big: float = 1.0, group: list[list] | None = None) -> list[Tile]:
    """Fill the free cells, in a random order, with rectangles of whole cells (``SHAPES``: simple
    ratios), the larger ones weighted by ``big``. Cells of different ``group``s never share a
    rectangle. A 1 × 1 always fits, so nothing is left over."""
    tiles: list[Tile] = []
    order = [(y, x) for y in range(rows) for x in range(cols)]
    rng.shuffle(order)
    for y, x in order:
        if not free[y][x]:
            continue
        g = group[y][x] if group is not None else None

        def fits(a: int, b: int) -> bool:
            return x + a <= cols and y + b <= rows and all(
                free[y + j][x + i] and (group is None or group[y + j][x + i] == g) for j in range(b) for i in range(a))
        options = [(a, b, wt * (big if a * b > 1 else 1.0)) for a, b, wt in SHAPES if fits(a, b)]
        a, b, _w = rng.choices(options, weights=[o[2] for o in options])[0]
        for j in range(b):
            for i in range(a):
                free[y + j][x + i] = False
        tiles.append(Tile(QRectF(x0 + x * c + GAP / 2, y0 + y * c + GAP / 2, a * c - GAP, b * c - GAP), a, b))
    return tiles


def pick(queue, recs, current, n: int) -> list[tuple[object, str]]:
    """Up to ``n`` (song, "Queue" | "For You"): the queue first (≈ 65 %), For You after; the song
    playing and repeats of one song (other uploads of it too) are left out."""
    seen_keys, seen_songs = set(), set()
    if current is not None:
        seen_keys.add(current.key)
        seen_songs.add(textnorm.fold(textnorm.core_title(current.title, current.artist)))

    def fresh(t) -> bool:
        song = textnorm.fold(textnorm.core_title(t.title, t.artist))
        if t.key in seen_keys or (song and song in seen_songs):
            return False
        seen_keys.add(t.key)
        if song:
            seen_songs.add(song)
        return True
    q = [(t, "Queue") for t in queue if fresh(t)]
    r = [(t, "For You") for t in recs if fresh(t)]
    want_q = max(n - len(r), round(n * QUEUE_SHARE))
    out = q[:want_q]
    out += r[: n - len(out)]
    out += q[want_q: want_q + n - len(out)]
    return out


def _cell(r: QRectF) -> QRectF:
    """A square's place on the grid (its painted rect plus half the gap all round)."""
    return r.adjusted(-GAP / 2, -GAP / 2, GAP / 2, GAP / 2)


def _shrink(r: QRectF) -> QRectF:
    return r.adjusted(GAP / 2, GAP / 2, -GAP / 2, -GAP / 2)


def _side(r: QRectF) -> float:
    return max(r.width(), r.height())


def _blit(p: QPainter, r: QRectF, pm: QPixmap) -> None:
    """A square cover into any rectangle: the middle of it, never stretched."""
    w, h = pm.width(), pm.height()
    if r.width() >= r.height():
        sh = h * r.height() / r.width()
        src = QRectF(0, (h - sh) / 2, w, sh)
    else:
        sw = w * r.width() / r.height()
        src = QRectF((w - sw) / 2, 0, sw, h)
    p.drawPixmap(r, pm, src)


class CollageView(QWidget):
    transition = Signal(bool)            # the move to a clicked square started / is over (Immersive hides its overlays)
    relaid = Signal()                    # a fresh layout: the middle square may have moved

    def __init__(self, parent, playback, actions=None):
        super().__init__(parent)
        self.pb, self.actions = playback, actions
        self.tiles: list[Tile] = []
        self.items: list[tuple[object, str] | None] = []     # per slot: (song, source) or None (a quiet blank)
        self.centre = QRectF()
        self._size = (0, 0)
        self._want = MAX_TILES
        self._pix: dict[tuple, QPixmap] = {}
        self._hover = -1
        self._focus = -1
        self._fresh: set[int] = set()                         # slots fading in
        self._t = 1.0
        self._shown = None                                    # the song the middle square shows (it changes with the wall)
        self._centre_now: QPixmap | None = None               # the middle square's cover, and the one fading out
        self._centre_prev: QPixmap | None = None
        self._pos, self._len = 0.0, 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(FADE_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade)
        # the move to a clicked square
        self._zoom = QVariantAnimation(self)
        self._zoom.setDuration(ZOOM_MS)
        self._zoom.setStartValue(0.0)
        self._zoom.setEndValue(1.0)
        self._zoom.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._zoom.valueChanged.connect(self._on_zoom)
        self._zoom.finished.connect(self._zoomed)
        self._u = 0.0
        self._z: dict | None = None          # wall / old / cover snapshots, the square's rect, the song awaited
        self._holding = False                # moved; waiting for the song to start
        self._pending = False                # the song changed meanwhile: refill once it's over
        self._ghost: tuple[QPixmap, QRectF] | None = None    # the old middle, under its new squares fading in
        self._grid = (0.0, 0.0, 1.0)         # origin and cell of the grid the squares sit on
        self._free = False                   # the wall has been moved: not a fresh layout any more
        self._hold_timer = QTimer(self, singleShot=True, interval=HOLD_MS)
        self._hold_timer.timeout.connect(self._settle)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Up next")
        thumbs_mod.instance().ready.connect(self._on_thumb)

    # ---- the middle square's parts (Immersive Mode puts the lyrics and play button there) ----------

    def pad(self) -> float:
        return max(16.0, self.centre.width() * 0.05)

    def lyrics_rect(self) -> QRectF:
        c, p = self.centre, self.pad()
        return QRectF(c.left() + p, c.top() + p + 52, c.width() - 2 * p, c.height() - 2 * p - 52 - 44)

    def play_rect(self) -> QRectF:
        c, p = self.centre, self.pad()
        return QRectF(c.left() + p - 6, c.bottom() - p - 30, 32, 32)

    def progress_rect(self) -> QRectF:
        c, p = self.centre, self.pad()
        return QRectF(c.left() + p + 36, c.bottom() - p - 15.5, c.width() - 2 * p - 36, 3)

    def set_progress(self, pos: float, length: float) -> None:
        self._pos, self._len = pos, length
        self.update(self.progress_rect().adjusted(-2, -4, 2, 4).toAlignedRect())

    # ---- geometry and contents ----------------------------------------------------

    def place(self) -> None:
        """Lay the squares out for this size; the songs on them stay (a new size isn't a new wall)."""
        size = (self.width(), self.height())
        if size == self._size and self.tiles:
            return
        self._size = size
        self._relayout()

    def _relayout(self) -> None:
        old = [it for it in self.items if it is not None]
        self.tiles, self.centre = layout(self.width(), self.height(), self._want)
        self.items = (old + [None] * len(self.tiles))[: len(self.tiles)]
        self._hover = self._focus = -1
        self._free = False
        self.relaid.emit()
        t0 = next(iter(self.tiles), None)
        if t0 is not None:
            c = (t0.rect.width() + GAP) / t0.cols
            self._grid = ((t0.rect.left() - GAP / 2) % c, (t0.rect.top() - GAP / 2) % c, c)
        self.update()

    def refill(self, crossfade: bool = False, fade: bool = True) -> None:
        """The song changed (or Collage just opened): who's on the wall now. Songs still on it keep
        their squares; only the squares that change fade in (and the middle square cross-fades)."""
        if self.busy:
            self._pending = True                             # after the move
            return
        if not self.tiles:
            return
        cur = self.pb.current
        if crossfade and cur is not None and self._shown is not None and cur.key != self._shown.key:
            W, H = self.width(), self.height()
            here = next((k for k, it in enumerate(self.items)
                         if it is not None and it[0].key == cur.key and visible(self.tiles[k].rect, W, H) > 0.3), -1)
            if here >= 0 and self._begin(here):
                return                                       # the next song was on the wall: slide to it
        self._shown = cur
        everyone = pick(self.pb.queue, self.pb.recommendations, self.pb.current, MAX_TILES + 12)
        want = min(MAX_TILES, max(6, len(everyone)))
        if want != self._want and not self._free:            # few songs: fewer, larger squares, not holes
            self._want = want
            self._relayout()
        n = len(self.tiles)
        chosen = everyone[:n]
        if len(chosen) < n:                                  # fewer coming up than places: the library next
            have = {t.key for t, _s in chosen} | ({cur.key} if cur else set())
            chosen += [(t, "Library") for t in self._library_order() if t.key not in have][: n - len(chosen)]
        wanted = {t.key: (t, src) for t, src in chosen}
        old = (self.items + [None] * n)[:n]
        new: list = [None] * n
        for i, it in enumerate(old):
            if it is not None and it[0].key in wanted:
                new[i] = wanted.pop(it[0].key)                 # still coming up: stays where it is
        rest = [(t, src) for t, src in chosen if t.key in wanted]
        free = [i for i in range(n) if new[i] is None]         # best squares first (slots are ordered so)
        for item, i in zip(rest, free):
            new[i] = item

        def key(it):
            return it[0].key if it is not None else None
        changed = {i for i in range(n) if key(new[i]) != key(old[i])}
        self.items = new
        self._hover = -1
        animate = fade and not rack_mod.reduce_motion and self.isVisible()
        self._centre_prev = self._centre_now if (crossfade and animate) else None
        if animate and (changed or self._centre_prev is not None):
            self._fresh, self._t = changed, 0.0
            self._fade.stop()
            self._fade.start()
        else:
            self._fresh, self._t = set(), 1.0
        self.update()
        self._prefetch()

    def _library_order(self) -> list:
        """Library songs in a steady shuffled order: the ones new squares take when fewer songs
        are coming up than places (their covers are fetched ahead, in this order)."""
        tracks = list(getattr(getattr(self.pb, "library", None), "tracks", []) or [])
        if getattr(self, "_lib_n", -1) != len(tracks):
            random.Random(len(tracks)).shuffle(tracks)
            self._lib, self._lib_n = tracks, len(tracks)
        return self._lib

    def _prefetch(self) -> None:
        """Ask for the covers of the songs that may come onto the wall next, so new squares
        arrive with their covers rather than as grey placeholders."""
        th = thumbs_mod.instance()
        on_wall = {it[0].key for it in self.items if it is not None}
        soon = [t for t, _src in pick(self.pb.queue, self.pb.recommendations, self.pb.current, MAX_TILES + 24)]
        soon += [t for t in self._library_order() if t.key not in on_wall][:40]
        for t in soon:
            if t.thumbnail:
                th.get(t.thumbnail, 360)

    def _on_fade(self, v) -> None:
        self._t = float(v)
        for i in self._fresh:
            if i < len(self.tiles):
                self.update(self.tiles[i].rect.toAlignedRect())
        if self._centre_prev is not None:
            self.update(self.centre.toAlignedRect())
        if self._ghost is not None:
            self.update(self._ghost[1].toAlignedRect())
        if self._t >= 1.0:
            self._fresh, self._centre_prev, self._ghost = set(), None, None

    # ---- the move to a clicked square --------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._z is not None

    def _big_cover(self, it) -> QPixmap | None:
        """The clicked song's cover, sharp enough for the middle square."""
        t = it[0]
        th = thumbs_mod.instance()
        for src in (th.get(artwork_urls(t), ART_HEIGHT), th.get(t.thumbnail, 360) if t.thumbnail else None):
            if src is not None and not src.isNull():
                return self._square(src, int(self.centre.width() * self.devicePixelRatioF()))
        return self._cover_for(it, 360)                       # the square's own, until a sharper one is in

    def _plan(self, i: int) -> dict:
        """Where everything ends up once square ``i`` is the middle one. The wall slides whole
        cells, so the square lands inside the middle square's place; the squares that slide onto
        that place go, the rest still on screen stay (songs kept), and the free cells — the old
        middle's place, and what slides in at the edges — get new 1 × 1 / 2 × 2 squares."""
        W, H = self.width(), self.height()
        ox, oy, c = self._grid
        tc, cc = _cell(self.tiles[i].rect), _cell(self.centre)
        k = round(cc.width() / c)
        na, nb = max(1, round(tc.width() / c)), max(1, round(tc.height() / c))
        dx = round((cc.left() - ox) / c) + (k - na) // 2 - round((tc.left() - ox) / c)
        dy = round((cc.top() - oy) / c) + (k - nb) // 2 - round((tc.top() - oy) / c)
        pan = QPointF(dx * c, dy * c)

        def covers(a: QRectF, b: QRectF) -> bool:
            r = a.intersected(b)
            return r.width() > 1 and r.height() > 1
        kept = []
        for j, tile in enumerate(self.tiles):
            if j != i:
                f = _cell(tile.rect).translated(pan)
                if not covers(f, cc) and visible(_shrink(f), W, H) > 0.04:
                    kept.append((j, f))
        taken = [f for _j, f in kept] + [cc]
        free: dict[tuple[int, int], bool] = {}               # cell → it was on screen before the slide
        gx0, gy0 = math.floor(-ox / c), math.floor(-oy / c)
        gx1, gy1 = math.ceil((W - ox) / c), math.ceil((H - oy) / c)
        for gy in range(gy0, gy1):
            for gx in range(gx0, gx1):
                cell = QRectF(ox + gx * c, oy + gy * c, c, c)
                if not any(covers(cell, r) for r in taken):
                    free[(gx, gy)] = visible(_shrink(cell.translated(-pan)), W, H) > 0.04
        grid = [[(gx, gy) in free for gx in range(gx0, gx1)] for gy in range(gy0, gy1)]
        group = [[free.get((gx, gy)) for gx in range(gx0, gx1)] for gy in range(gy0, gy1)]
        packed = _pack(gx1 - gx0, gy1 - gy0, c, ox + gx0 * c, oy + gy0 * c, grid,
                       random.Random(), 1.0, group)
        new: list[tuple[QRectF, bool]] = []
        for t in packed:
            f = _cell(t.rect)
            if visible(t.rect, W, H) > 0.04:
                new.append((f, free[(round((f.left() - ox) / c), round((f.top() - oy) / c))]))
        new.sort(key=lambda x: (x[0].center() - cc.center()).manhattanLength())   # nearest the middle: next up
        target = self.items[i][0]
        before = self._shown.key if self._shown is not None else None
        on_wall = {self.items[j][0].key for j, _f in kept if self.items[j] is not None}
        pool = [x for x in pick(self.pb.queue, self.pb.recommendations, target, MAX_TILES + 24) if x[0].key != before]
        songs = [x for x in pool if x[0].key not in on_wall]
        if len(songs) < len(new):                            # few coming up: songs from the library next
            used = on_wall | {x[0].key for x in songs} | {target.key, before}
            lib = [t for t in self._library_order() if t.key not in used]
            songs += [(t, "Library") for t in lib[: len(new) - len(songs)]]
            pool = pool + songs
        while pool and len(songs) < len(new):                # still short: repeat rather than leave a blank
            songs += pool[: len(new) - len(songs)]
        songs = songs[: len(new)] + [None] * max(0, len(new) - len(songs))
        # the middle square's place may slide in from beyond the edge: squares there only for the
        # slide (the growing square covers them by its end), so no gap shows on the way
        screen = QRectF(0, 0, W, H)
        transit = []
        for gy in range(round((cc.top() - oy) / c), round((cc.bottom() - oy) / c)):
            for gx in range(round((cc.left() - ox) / c), round((cc.right() - ox) / c)):
                cell = QRectF(ox + gx * c, oy + gy * c, c, c)
                if not covers(cell.translated(-pan), screen) and pool:
                    transit.append((cell.translated(-pan), pool[(gx * 7 + gy * 3) % len(pool)]))
        return {"pan": pan, "tile": self.tiles[i].rect, "kept": kept, "gone": cc.translated(pan),
                "new": new, "songs": songs, "transit": transit}

    def _begin(self, i: int) -> bool:
        """Start the slide to square ``i``; False when it isn't the time for one."""
        if rack_mod.reduce_motion or not self.isVisible() or self.busy or not self.tiles or self.items[i] is None:
            return False
        self._fade.stop()
        self._fresh, self._t, self._centre_prev, self._ghost = set(), 1.0, None, None
        self._hover = -1
        z = self._plan(i)
        pan = z["pan"]
        # every square on the slide, each with the cover it has: the ones on screen, and the new
        # ones coming in from beyond the edges (the ones for places on screen wait for the song)
        z["sprites"] = [(_cell(t.rect), self.items[j], _side(t.rect)) for j, t in enumerate(self.tiles) if j != i]
        z["sprites"] += [(r.translated(-pan), it, _side(_shrink(r))) for (r, was), it in zip(z["new"], z["songs"]) if not was]
        z["sprites"] += [(cell, it, _side(_shrink(cell))) for cell, it in z["transit"]]
        z["old"] = self.parent().grab(self.centre.toAlignedRect()) if self.parent() else self.grab(self.centre.toAlignedRect())
        z["cover"], z["item"], z["key"] = self._big_cover(self.items[i]), self.items[i], self.items[i][0].key
        self._z = z
        self._u = 0.0
        self._holding = False
        self.transition.emit(True)
        self._zoom.start()
        return True

    def _on_zoom(self, v) -> None:
        self._u = float(v)
        self.update()

    def _zoomed(self) -> None:
        if self._z is None:
            return
        self._holding = True
        self._hold_timer.start()
        self.on_state()

    def on_state(self, _s=None) -> None:
        """Playback's state changed: once the awaited song plays, the wall settles."""
        from aria.core.player import PLAYING
        t = self.pb.current
        if self._holding and t is not None and t.key == self._z["key"] and self.pb.state == PLAYING:
            self._settle()

    def _settle(self) -> None:
        """The new song plays: the slid wall is the wall; the free places on screen fill with new
        squares, each fading in on its own (over the old middle square, fading out)."""
        z = self._z
        if z is None:
            return
        self._zoom.stop()
        self._hold_timer.stop()
        W, H = self.width(), self.height()
        c = self._grid[2]

        def tile(f: QRectF) -> Tile:
            return Tile(_shrink(f), max(1, round(f.width() / c)), max(1, round(f.height() / c)))
        placed = [(tile(f), self.items[j], False) for j, f in z["kept"]]
        placed += [(tile(r), it, was) for (r, was), it in zip(z["new"], z["songs"])]
        mid = self.centre.center()
        placed.sort(key=lambda x: (visible(x[0].rect, W, H) < 0.5, -x[0].rect.width() * x[0].rect.height(),
                                   (x[0].rect.center() - mid).manhattanLength()))
        self.tiles = [x[0] for x in placed]
        self.items = [x[1] for x in placed]
        fresh = {k for k, x in enumerate(placed) if x[2]}
        self._free = True
        self._shown = self.pb.current
        self._z, self._holding, self._pending = None, False, False
        self._hover = self._focus = -1
        if fresh and self.isVisible() and not rack_mod.reduce_motion:
            self._ghost = (z["old"], _shrink(z["gone"]))
            self._fresh, self._t = fresh, 0.0
            self._fade.start()
        self.update()
        self.transition.emit(False)
        self._prefetch()

    def _cancel_move(self) -> None:
        self._zoom.stop()
        self._hold_timer.stop()
        self._z, self._holding, self._pending = None, False, False
        self.update()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self.busy:
            self._cancel_move()
            self.transition.emit(False)

    def _paint_move(self, p: QPainter, u: float) -> None:
        """One frame of the slide: every square glides by the same amount, each drawn on its own;
        only the target square grows, into the middle square's place."""
        z = self._z
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), BACK)
        off = z["pan"] * u
        screen = QRectF(self.rect())
        for cell, it, size in z["sprites"]:
            r = _shrink(cell.translated(off))
            if r.intersects(screen):
                self._paint_square(p, r, it, self._cover_for(it, size))
        r = self.centre.translated(off)                      # the old middle, carried along
        if r.intersects(screen):
            p.drawPixmap(r, z["old"], QRectF(z["old"].rect()))
        a, b = z["tile"].translated(off), self.centre        # the target: with the wall, growing into place
        r = QRectF(a.left() + (b.left() - a.left()) * u, a.top() + (b.top() - a.top()) * u,
                   a.width() + (b.width() - a.width()) * u, a.height() + (b.height() - a.height()) * u)
        clip = QPainterPath()
        clip.addRoundedRect(r, 3, 3)
        p.save()
        p.setClipPath(clip)
        if z["cover"] is None:
            z["cover"] = self._big_cover(z["item"])         # asked again until it's in
        pm = z["cover"]
        if pm is not None:
            _blit(p, r, pm)
        else:
            p.fillRect(r, QColor(40, 40, 46))
        p.fillRect(r, QColor(12, 12, 16, int(125 + (150 - 125) * u)))      # from a square's dim to the middle's
        if u < 1.0:
            p.setOpacity(1.0 - u)                            # its name gives way to the middle's
            self._paint_name(p, r.intersected(screen), z["item"], False, min(r.width(), r.height()))
        p.restore()

    # ---- covers ------------------------------------------------------------------------

    def _square(self, src: QPixmap, size: int) -> QPixmap:
        s = min(src.width(), src.height())
        sq = src.copy((src.width() - s) // 2, (src.height() - s) // 2, s, s)
        pm = sq.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        return pm

    def _cover(self, i: int) -> QPixmap | None:
        it = self.items[i] if i < len(self.items) else None
        return self._cover_for(it, _side(self.tiles[i].rect)) if i < len(self.tiles) else None

    def _cover_for(self, it, width: float) -> QPixmap | None:
        if it is None or not it[0].thumbnail:
            return None
        size = min(1200, int(width * self.devicePixelRatioF()))
        big = size > BIG_PX                                  # a square the camera has brought near: the full artwork
        key = (it[0].thumbnail, size, big)
        pm = self._pix.get(key)
        if pm is None:
            th = thumbs_mod.instance()
            src = th.get(artwork_urls(it[0]), ART_HEIGHT) if big else None
            if src is None or src.isNull():
                src = th.get(it[0].thumbnail, 180 if size <= 200 else 360)   # meanwhile (or for good)
                if src is None or src.isNull():
                    return None                              # on its way: that square repaints when it lands
                # big: kept aside, and the artwork asked for again until it's in
                return self._keep((key, "meanwhile") if big else key, self._pix.get((key, "meanwhile")) or self._square(src, size))
            pm = self._square(src, size)
        return self._keep(key, pm)

    def _keep(self, key, pm: QPixmap) -> QPixmap:
        if len(self._pix) > 240:
            self._pix.clear()
        self._pix[key] = pm
        return pm

    def _centre_cover(self) -> QPixmap | None:
        t = self._shown
        if t is None:
            return None
        size = int(self.centre.width() * self.devicePixelRatioF())
        key = ("centre", t.key, size)
        pm = self._pix.get(key)
        if pm is None:
            src = thumbs_mod.instance().get(artwork_urls(t), ART_HEIGHT)
            if src is None or src.isNull():
                return None
            pm = self._pix[key] = self._square(src, size)
        self._centre_now = pm
        return pm

    def _on_thumb(self, key: str) -> None:
        if not self.isVisible():
            return
        if self.busy:
            self.update()                                    # mid-move every frame paints everything anyway
            return
        th = thumbs_mod.instance()
        t = self._shown
        if t is not None and key == th.key(artwork_urls(t), ART_HEIGHT):
            self.update(self.centre.toAlignedRect())
        for i, it in enumerate(self.items):
            if it is not None and it[0].thumbnail and key in (th.key(it[0].thumbnail, 180), th.key(it[0].thumbnail, 360),
                                                              th.key(artwork_urls(it[0]), ART_HEIGHT)):
                self.update(self.tiles[i].rect.toAlignedRect())   # only that square

    # ---- painting ----------------------------------------------------------------------

    def paintEvent(self, e):
        p = QPainter(self)
        if self._z is not None:
            self._paint_move(p, self._u)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dirty = QRectF(e.rect())
        if self._ghost is not None and self._ghost[1].intersects(dirty):
            p.setOpacity(1.0 - self._t)
            p.drawPixmap(self._ghost[1], self._ghost[0], QRectF(self._ghost[0].rect()))
            p.setOpacity(1.0)
        for i, tile in enumerate(self.tiles):
            if i != self._hover and tile.rect.intersects(dirty):
                self._paint_tile(p, i, tile)
        if self.centre.intersects(dirty):
            self._paint_centre(p)
        if 0 <= self._hover < len(self.tiles) and self._lifted(self._hover).intersects(dirty):
            self._paint_tile(p, self._hover, self.tiles[self._hover])
        p.end()

    def _lifted(self, i: int) -> QRectF:
        r = self.tiles[i].rect
        return r.adjusted(-POP, -POP, POP, POP) if self._live(i) else r

    def _paint_tile(self, p: QPainter, i: int, tile: Tile) -> None:
        it = self.items[i] if i < len(self.items) else None
        r = self._lifted(i) if i == self._hover else tile.rect
        lit = it is not None and i in (self._hover, self._focus)
        self._paint_square(p, r, it, self._cover(i), lit, self._t if i in self._fresh else 1.0)
        if i == self._focus:
            p.setPen(QColor(255, 255, 255, 210))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)

    def _paint_square(self, p: QPainter, r: QRectF, it, pm: QPixmap | None, lit: bool = False,
                      opacity: float = 1.0, rounded: bool = True) -> None:
        """One square: its cover, quieted so the middle square leads, and its name."""
        p.save()
        if rounded:
            clip = QPainterPath()
            clip.addRoundedRect(r, 3, 3)
            p.setClipPath(clip)
        else:
            p.setClipRect(r)
        p.setOpacity(opacity)
        if pm is not None:
            _blit(p, r, pm)
        else:
            p.fillRect(r, QColor(255, 255, 255, 8 if it is None else 16))
        p.fillRect(r, QColor(10, 10, 14, 60 if lit else 125))
        if it is not None:
            # a square running off the screen keeps its name in the part that shows
            self._paint_name(p, r.intersected(QRectF(self.rect())), it, lit, min(r.width(), r.height()))
        p.restore()

    def _paint_name(self, p: QPainter, r: QRectF, it, lit: bool, side: float) -> None:
        """The song's name on its cover (bottom left), the artist small beneath; sized by the square."""
        t, src = it
        px = max(12.0, min(26.0, side * 0.085))
        h = px * 3.4
        band = QRectF(r.left(), r.bottom() - h, r.width(), h)
        g = QLinearGradient(band.topLeft(), band.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 170))
        p.fillRect(band, g)
        pad = max(7.0, side * 0.05)
        w = r.width() - 2 * pad
        tf, sf = _font(px, QFont.Weight.DemiBold), _font(px * 0.55)
        name = identity.display(t)[0]
        fm = QFontMetrics(tf)
        lines = fm.boundingRect(0, 0, int(w), 10_000, Qt.TextFlag.TextWordWrap, name).height()
        two = min(lines, fm.lineSpacing() * 2)
        if lines > two:
            name = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(w * 2 - px))
        sub_h = QFontMetrics(sf).height()
        top = r.bottom() - pad - sub_h - two
        p.setFont(tf)
        p.setPen(QColor(255, 255, 255, 240 if lit else 215))
        p.drawText(QRectF(r.left() + pad, top, w, two + 2), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft, name)
        p.setFont(sf)
        p.setPen(QColor(255, 255, 255, 150))
        sub = " · ".join(x for x in (t.artist, src if lit else "") if x)     # where it comes from, on hover
        p.drawText(QRectF(r.left() + pad, r.bottom() - pad - sub_h, w, sub_h), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(sf).elidedText(sub, Qt.TextElideMode.ElideRight, int(w)))

    def _paint_centre(self, p: QPainter) -> None:
        c = self.centre
        clip = QPainterPath()
        clip.addRoundedRect(c, 4, 4)
        p.save()
        p.setClipPath(clip)
        pm = self._centre_cover()
        if self._centre_prev is not None and self._t < 1.0:
            p.drawPixmap(c, self._centre_prev, QRectF(self._centre_prev.rect()))
            p.setOpacity(self._t)
        if pm is not None:
            p.drawPixmap(c, pm, QRectF(pm.rect()))
        else:
            p.fillRect(c, QColor(40, 40, 46))
        p.setOpacity(1.0)
        # dimmed enough for the words on it to read, darker at the top and foot
        p.fillRect(c, QColor(12, 12, 16, 150))
        g = QLinearGradient(c.topLeft(), c.bottomLeft())
        g.setColorAt(0.0, QColor(0, 0, 0, 90))
        g.setColorAt(0.25, QColor(0, 0, 0, 0))
        g.setColorAt(0.8, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 110))
        p.fillRect(c, g)
        t = self._shown
        pad = self.pad()
        w = c.width() - 2 * pad
        tf, af = _font(max(14, c.width() * 0.034), QFont.Weight.DemiBold), _font(max(11, c.width() * 0.024))
        p.setFont(tf)
        p.setPen(QColor(255, 255, 255, 240))
        name = identity.display(t)[0] if t else "Nothing playing"
        p.drawText(QRectF(c.left() + pad, c.top() + pad, w, QFontMetrics(tf).height() + 2), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(tf).elidedText(name, Qt.TextElideMode.ElideRight, int(w)))
        if t:
            p.setFont(af)
            p.setPen(QColor(255, 255, 255, 160))
            p.drawText(QRectF(c.left() + pad, c.top() + pad + QFontMetrics(tf).height() + 2, w, QFontMetrics(af).height() + 2),
                       Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(af).elidedText(" · ".join(x for x in (t.artist, t.source_label) if x),
                                                   Qt.TextElideMode.ElideRight, int(w)))
        # progress along the foot (the play button beside it is a child widget)
        pr = self.progress_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 50))
        _bar(p, pr)
        if self._len > 0:
            p.setBrush(QColor(255, 255, 255, 215))
            _bar(p, QRectF(pr.left(), pr.top(), pr.width() * min(1.0, self._pos / self._len), pr.height()))
        p.restore()

    # ---- mouse and keyboard --------------------------------------------------------------

    def _at(self, pos) -> int:
        return next((i for i, t in enumerate(self.tiles) if t.rect.contains(QPointF(pos))), -1)

    def _set_hover(self, i: int, on_bar: bool = False) -> None:
        if i != self._hover:
            old, self._hover = self._hover, i
            for j in (old, i):
                if 0 <= j < len(self.tiles):
                    self.update(self.tiles[j].rect.adjusted(-POP - 1, -POP - 1, POP + 1, POP + 1).toAlignedRect())
        live = 0 <= i < len(self.items) and self.items[i] is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if live or on_bar else Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, e):
        if self.busy:
            e.ignore()
            return
        pos = e.position()
        self._set_hover(self._at(pos), self.progress_rect().adjusted(-2, -8, 2, 8).contains(pos))
        e.ignore()                                           # Immersive Mode still wakes its controls

    def leaveEvent(self, e):
        self._set_hover(-1)

    def mousePressEvent(self, e):
        if self.busy:
            return                                           # mid-move: one song at a time
        pos = e.position()
        if e.button() == Qt.MouseButton.LeftButton and self.progress_rect().adjusted(-2, -8, 2, 8).contains(pos) \
                and self._len > 0:
            pr = self.progress_rect()
            self.pb.seek(max(0.0, min(1.0, (pos.x() - pr.left()) / pr.width())) * self._len)
            return
        i = self._at(pos)
        if not (0 <= i < len(self.items)) or self.items[i] is None:
            e.ignore()                                       # the middle square isn't a song to pick
            return
        self._select(i)                                      # one click picks it; a double-click plays it
        if e.button() == Qt.MouseButton.RightButton:
            self._menu(i, e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        i = self._at(e.position())
        if e.button() == Qt.MouseButton.LeftButton and self._live(i) and not self.busy:
            self.play(i)
        elif i < 0 and not self.centre.contains(e.position()):
            e.ignore()                                       # empty space: Immersive Mode's double-click (leave)

    def _select(self, i: int) -> None:
        old, self._focus = self._focus, i
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        for j in (old, i):
            if 0 <= j < len(self.tiles):
                self.update(self.tiles[j].rect.adjusted(-POP - 1, -POP - 1, POP + 1, POP + 1).toAlignedRect())

    def play(self, i: int) -> None:
        t, _src = self.items[i]
        pb = self.pb
        self._begin(i)                                       # the camera's move (not under Reduce Motion)
        q = next((k for k, x in enumerate(pb.queue) if x.key == t.key), None)
        if q is not None:
            pb.play_queue_item(q)                            # as the queue would
            return
        r = next((k for k, x in enumerate(pb.recommendations) if x.key == t.key), None)
        if r is not None:
            pb.accept_recommendation(r, play=True)           # as taking a recommendation does
        else:
            pb.play_now(t.copy())

    def _menu(self, i: int, pos) -> None:
        if self.actions is not None:
            self.actions.menu(self, [self.items[i][0]]).exec(pos)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if not self._live(self._focus):
            self._focus = self._order()[0] if self._order() else -1
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() != Qt.FocusReason.PopupFocusReason:   # (the right-click menu keeps the pick)
            old, self._focus = self._focus, -1
            if 0 <= old < len(self.tiles):
                self.update(self.tiles[old].rect.adjusted(-POP - 1, -POP - 1, POP + 1, POP + 1).toAlignedRect())

    def _order(self) -> list[int]:
        """Live tiles that show, in reading order (Tab / Shift+Tab go through them)."""
        W, H = self.width(), self.height()
        live = [k for k in range(len(self.tiles)) if self._live(k) and visible(self.tiles[k].rect, W, H) > 0.3]
        return sorted(live, key=lambda k: (round(self.tiles[k].rect.top()), self.tiles[k].rect.left()))

    def focusNextPrevChild(self, forward: bool) -> bool:
        order = self._order()
        if not self.hasFocus() or not order:
            return super().focusNextPrevChild(forward)
        k = order.index(self._focus) if self._focus in order else -1
        self._select(order[(k + (1 if forward else -1)) % len(order)])
        return True

    def keyPressEvent(self, e):
        if self.busy:
            e.ignore()
            return
        k = e.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._live(self._focus):
            self.play(self._focus)
        elif self._live(self._focus) and (k == Qt.Key.Key_Menu or (
                k == Qt.Key.Key_F10 and e.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            self._menu(self._focus, self.mapToGlobal(self.tiles[self._focus].rect.center().toPoint()))
        elif k == Qt.Key.Key_Escape:
            self.clearFocus()
            self.parent().setFocus()                         # back to the page: its keys work again
        else:
            e.ignore()                                       # ←/→ seek, Space, L, V… are Immersive Mode's

    def _live(self, i: int) -> bool:
        return 0 <= i < len(self.items) and self.items[i] is not None
