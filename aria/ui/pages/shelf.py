"""Digital Record Shelf: everything the listener collects, arranged by hand, opened like a record sleeve.

Albums and playlists found online stand next to the listener's own playlists and Smart
Collections (those stand there by themselves: new ones go in front). Any of them opens into the
same liner-notes page — the sleeve, the credits, the track list — with what that kind allows:
an album plays as an album, a playlist can be reordered and edited, a Smart Collection fills
itself from its rules. Covers are picked automatically (the album's, or a mosaic of the songs'),
or the listener chooses a picture.

Two displays: Cover (sleeves face the room) and Spine (cases packed side by side; hovering
pulls one half out to show its cover). A click opens; the small play button on a hovered sleeve
and the right-click menu do the rest.
"""

from __future__ import annotations

import logging
import math
import os
import random
import shutil
import time

from PySide6.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLineEdit, QMenu, QStackedWidget, QVBoxLayout,
                               QWidget)

from aria import paths, providers
from aria.core import smart, tasks
from aria.core.lists import Album
from aria.core.models import SOURCE_LABELS, Track, format_duration
from aria.ui import icons
from aria.ui.pages.base import TintedPage
from aria.ui.theme import MICRO_MS, font, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.controls import Button, IconButton, add_item, hbox, label, menu_below
from aria.ui.widgets.dialogs import confirm, prompt
from aria.ui.widgets.rack import RackItem, RackPanel, RackView
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView, paint_empty, plain_scroll

log = logging.getLogger(__name__)

COVER = 150
GAP_X = 30
TEXT_H = 44
PLANK_H = 8
ROW_H = COVER + PLANK_H + TEXT_H + 26
MARGIN = 8
COVER_PX = 360            # decoded cover height
PLAY = 30                 # the play button on a hovered sleeve
KIND_LABEL = {"album": "Album", "playlist": "Playlist", "smart": "Smart Collection"}
KIND_ICON = {"album": "disc", "playlist": "list", "smart": "smart"}
IMAGES = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"

_playlists = None          # set by ShelfPage: the listener's playlists, for their live entries
_smart_tracks = None       # set by ShelfPage: Smart Collection id → its songs right now


def kind_of(album: Album) -> str:
    return "smart" if album.smart_id else ("playlist" if album.kind == "playlist" else "album")


def tracks_of(album: Album) -> list[Track]:
    """An entry's songs; for the listener's own lists, the list as it is right now."""
    if album.playlist_id and _playlists is not None:
        p = _playlists.get(album.playlist_id)
        return list(p.tracks) if p else []
    if album.smart_id:
        return list(_smart_tracks(album.smart_id)) if _smart_tracks is not None else []
    return album.tracks


def cover_sources(album: Album) -> list:
    """The chosen picture; else the album's cover; else up to four song covers (a mosaic)."""
    thumbs = thumbs_mod.instance()
    if album.custom_cover:
        return [thumbs.get(str(paths.COVERS_DIR / album.custom_cover), COVER_PX)]
    if album.cover and not album.own:
        return [thumbs.get(album.cover, COVER_PX)]
    urls: list[str] = []
    for t in tracks_of(album):
        if t.thumbnail and t.thumbnail not in urls:
            urls.append(t.thumbnail)
        if len(urls) == 4:
            break
    if len(urls) in (2, 3):
        urls = urls[:1]
    return [thumbs.get(u) for u in urls]


def cover_pixmap(album: Album):
    pms = cover_sources(album)
    return pms[0] if pms else None


_tiles: dict[tuple, QPixmap] = {}


def _tile(album: Album, side: int, radius: float, dpr: float) -> QPixmap | None:
    """Rounded, cropped cover rendered once per size: painting the shelf is just blits."""
    pms = cover_sources(album)
    if not pms or any(pm is None or pm.isNull() for pm in pms):
        return None
    key = (album.id, side, radius, dpr, tuple(pm.cacheKey() for pm in pms))
    tile = _tiles.get(key)
    if tile is None:
        tile = QPixmap(round(side * dpr), round(side * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, side, side), radius, radius)
        p.setClipPath(clip)
        n = 2 if len(pms) == 4 else 1
        cell = side / n
        for i, pm in enumerate(pms[: n * n]):
            r = QRectF((i % n) * cell, (i // n) * cell, cell, cell)
            s = min(pm.width(), pm.height())
            p.drawPixmap(r, pm, QRectF((pm.width() - s) / 2, (pm.height() - s) / 2, s, s))
        p.end()
        if len(_tiles) > 300:
            _tiles.clear()
        _tiles[key] = tile
    return tile


def draw_cover(p: QPainter, rect: QRectF, album: Album, radius: float = 4, dpr: float = 2.0) -> None:
    tile = _tile(album, int(rect.width()), radius, dpr)
    if tile is not None:
        p.drawPixmap(rect.topLeft(), tile)
        return
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    p.fillRect(rect, theme.color("fill_strong"))                 # the same size, while the cover loads
    s = rect.width() * 0.3
    p.drawPixmap(QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s).toRect(),
                 icons.pixmap(KIND_ICON[kind_of(album)], theme.color("tertiary"), int(s)))
    p.restore()


def draw_vinyl(p: QPainter, center, r: float, label_color: QColor) -> None:
    """A black record: grooves, a sheen, and a label in the album's tint.
    On a dark window the record is lifted a little and gets a lit rim so it stays readable."""
    dark = theme.dark
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2a2a2e") if dark else QColor("#111113"))
    p.drawEllipse(center, r, r)
    pen = QPen(QColor(255, 255, 255, 26 if dark else 14))
    pen.setWidthF(0.8)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in range(6, 18):
        rr = r * k / 18
        p.drawEllipse(center, rr, rr)
    rim = QPen(QColor(255, 255, 255, 70 if dark else 30))
    rim.setWidthF(1.2)
    p.setPen(rim)
    p.drawEllipse(center, r - 0.6, r - 0.6)
    sheen = QRadialGradient(center.x() - r * 0.3, center.y() - r * 0.4, r)
    sheen.setColorAt(0, QColor(255, 255, 255, 52 if dark else 30))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(sheen)
    p.drawEllipse(center, r, r)
    p.setBrush(label_color)
    p.drawEllipse(center, r * 0.32, r * 0.32)
    p.setBrush(QColor("#111113"))
    p.drawEllipse(center, r * 0.04, r * 0.04)
    p.restore()


def label_color(album: Album) -> QColor:
    pm = cover_pixmap(album)
    hs = tint_mod.hue_of(pm) if pm and not pm.isNull() else None
    return QColor.fromHslF(hs[0], min(0.45, hs[1]), 0.55) if hs else theme.color("accent")


def subtitle(album: Album) -> str:
    if album.own:
        n = len(tracks_of(album))
        return f"{KIND_LABEL[kind_of(album)]} · {n} song{'s' if n != 1 else ''}"
    return album.artist or ("Playlist" if album.kind == "playlist" else "")


class ShelfView(QWidget):
    """The shelf itself, painted: sleeves standing on planks."""

    open_album = Signal(str)
    play_album = Signal(str)
    context = Signal(str, QPoint)

    def __init__(self, shelf):
        super().__init__()
        self.shelf = shelf
        self.setMouseTracking(True)
        self._hover = -1
        self._on_play = False                  # the pointer is on the hovered sleeve's play button
        self._slide = 0.0                      # 0..1 vinyl slide-out of the hovered sleeve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(MICRO_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_slide)
        self._press: QPoint | None = None
        self._drag = -1
        self._drop = -1
        self._drag_pos = QPoint()
        shelf.changed.connect(self._relayout)
        thumbs_mod.instance().ready.connect(lambda _k: self.update())
        theme.changed.connect(self.update)

    # ---- geometry ------------------------------------------------------------

    def _cols(self) -> int:
        return max(1, (self.width() - 2 * MARGIN + GAP_X) // (COVER + GAP_X))

    def _rect(self, i: int) -> QRectF:
        cols = self._cols()
        row, col = divmod(i, cols)
        used = cols * COVER + (cols - 1) * GAP_X
        left = MARGIN + max(0, (self.width() - 2 * MARGIN - used) // 2)
        return QRectF(left + col * (COVER + GAP_X), 16 + row * ROW_H, COVER, COVER)

    def _play_rect(self, i: int) -> QRectF:
        """The hovered sleeve's play button, in its lower right corner (it lifts with the sleeve)."""
        r = self._rect(i).translated(0, -3 * self._slide)
        return QRectF(r.right() - PLAY - 8, r.bottom() - PLAY - 8, PLAY, PLAY)

    def _index_at(self, pos) -> int:
        for i in range(len(self.shelf.albums)):
            if self._rect(i).adjusted(-6, -6, 6, TEXT_H).contains(pos):
                return i
        return -1

    def _drop_index(self, pos) -> int:
        best, dist = len(self.shelf.albums), 1e9
        for i in range(len(self.shelf.albums) + 1):
            r = self._rect(i) if i < len(self.shelf.albums) else self._rect(i - 1).translated(COVER + GAP_X, 0)
            d = math.hypot(pos.x() - (r.left() - GAP_X / 2), pos.y() - r.center().y())
            if d < dist:
                best, dist = i, d
        return best

    def _relayout(self) -> None:
        rows = max(1, math.ceil(len(self.shelf.albums) / self._cols()))
        self.setMinimumHeight(rows * ROW_H + 24)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(600, 400)

    def top_of(self, i: int) -> int:
        return int(self._rect(i).top()) - 16

    # ---- events --------------------------------------------------------------

    def _on_slide(self, v) -> None:
        self._slide = float(v)
        if self._hover >= 0:          # only the hovered sleeve moves
            self.update(self._dirty(self._hover))

    def _dirty(self, i: int):
        return self._rect(i).adjusted(-10, -10, 48, PLANK_H + TEXT_H + 12).toAlignedRect()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._drag < 0 and (pos - self._press).manhattanLength() > 8:
                self._drag = self._index_at(self._press)
            if self._drag >= 0:
                self._drag_pos = pos
                self._drop = self._drop_index(pos)
                self.update()
                return
        hover = self._index_at(pos)
        on_play = hover >= 0 and self._play_rect(hover).contains(pos)
        if on_play != self._on_play and hover >= 0:
            self._on_play = on_play
            self.update(self._dirty(hover))
        if hover != self._hover:
            old = self._hover
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 else Qt.CursorShape.ArrowCursor)
            if old >= 0:
                self.update(self._dirty(old))
            self._anim.stop()
            if theme.reduce_motion:
                self._on_slide(1.0)
            else:
                self._anim.setStartValue(0.0)
                self._anim.setEndValue(1.0)
                self._anim.start()

    def leaveEvent(self, e):
        self._hover = -1
        self._on_play = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self._drag = -1

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint()
        if self._drag >= 0:
            src, dst = self._drag, self._drop
            self._drag = self._drop = -1
            if dst > src:
                dst -= 1
            self.shelf.move(src, dst)
        elif self._press is not None and e.button() == Qt.MouseButton.LeftButton:
            i = self._index_at(pos)
            if i >= 0 and self._play_rect(i).contains(pos):
                self.play_album.emit(self.shelf.albums[i].id)          # the sleeve's own play button
            elif i >= 0:
                self.open_album.emit(self.shelf.albums[i].id)
        self._press = None
        self.update()

    def contextMenuEvent(self, e):
        i = self._index_at(e.pos())
        if i >= 0:
            self.context.emit(self.shelf.albums[i].id, e.globalPos())

    # ---- painting ------------------------------------------------------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        albums = self.shelf.albums
        if not albums:
            paint_empty(p, self.rect(), "disc", "Your shelf is empty", "Use “Add” to start a playlist or find an album.")
            return
        dpr = self.devicePixelRatioF()
        dirty = QRectF(e.rect())
        cols = self._cols()
        rows = math.ceil(len(albums) / cols)
        for row in range(rows):
            first = self._rect(row * cols)
            last = self._rect(min(len(albums), (row + 1) * cols) - 1)
            plank = QRectF(first.left() - 18, first.bottom(),
                           max(last.right(), first.left() + cols * (COVER + GAP_X) - GAP_X) - first.left() + 36, PLANK_H)
            if not plank.adjusted(0, 0, 0, 6).intersects(dirty):
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 40 if not theme.dark else 90))
            p.drawRoundedRect(plank.translated(0, 3), 2.6, 2.6)
            p.setBrush(theme.color("fill_strong"))
            p.drawRoundedRect(plank, 2.6, 2.6)

        for i, a in enumerate(albums):
            if i == self._drag:
                continue
            base = self._rect(i)
            if not base.adjusted(-10, -10, 48, TEXT_H + PLANK_H + 12).intersects(dirty):
                continue
            r = base
            hovered = i == self._hover and self._drag < 0
            if hovered:
                # The sleeve lifts a little and the record peeks out — all inside the gap, nothing overlaps.
                slide = self._slide * 30
                draw_vinyl(p, r.center() + QPoint(int(slide), 0) - QPoint(0, int(3 * self._slide)),
                           COVER * 0.47, label_color(a))
                r = r.translated(0, -3 * self._slide)
            p.setPen(Qt.PenStyle.NoPen)
            lift = self._slide if hovered else 0.0
            p.setBrush(QColor(0, 0, 0, int((70 if theme.dark else 30) + 50 * lift)))
            p.drawRoundedRect(r.translated(0, 2 + 3 * lift), 4, 4)
            draw_cover(p, r, a, dpr=dpr)
            if hovered:
                ring = QColor(255, 255, 255, int(90 * lift)) if theme.dark else QColor(0, 0, 0, int(40 * lift))
                p.setPen(ring)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                self._paint_play(p, self._play_rect(i), lift)
            text = QRectF(r.left(), base.bottom() + PLANK_H + 8, COVER, 20)
            p.setFont(font("callout", 600))
            p.setPen(theme.color("label"))
            p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(a.title, Qt.TextElideMode.ElideRight, COVER))
            p.setFont(font("caption"))
            p.setPen(theme.color("secondary"))
            p.drawText(text.translated(0, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(subtitle(a), Qt.TextElideMode.ElideRight, COVER))

        if self._drag >= 0:
            if self._drop < len(albums):
                mr = self._rect(self._drop)
                x = mr.left() - GAP_X / 2
            else:
                mr = self._rect(len(albums) - 1)
                x = mr.right() + GAP_X / 2
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(x - 1, mr.top(), 2, COVER), 0.6, 0.6)
            p.setOpacity(0.85)
            dr = QRectF(self._drag_pos.x() - COVER / 2, self._drag_pos.y() - COVER / 2, COVER, COVER)
            draw_cover(p, dr, albums[self._drag], dpr=dpr)
            p.setOpacity(1)
        p.end()

    def _paint_play(self, p: QPainter, r: QRectF, lift: float) -> None:
        """A small play button on the hovered sleeve: playing it is one click, opening it another."""
        p.setOpacity(lift)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 22, 235 if self._on_play else 190))
        p.drawRoundedRect(r, 9, 9)
        s = 14
        p.drawPixmap(QRectF(r.center().x() - s / 2 + 1, r.center().y() - s / 2, s, s).toRect(),
                     icons.pixmap("play", QColor("#ffffff"), s))
        p.setOpacity(1.0)


class SleeveArt(QWidget):
    """Detail artwork: the cover with its record half out of the sleeve."""

    def __init__(self):
        super().__init__()
        self.album: Album | None = None
        self.setFixedSize(380, 260)
        thumbs_mod.instance().ready.connect(lambda _k: self.update())

    def set_album(self, a: Album) -> None:
        self.album = a
        self.update()

    def paintEvent(self, _e):
        if not self.album:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cover = QRectF(0, 0, 260, 260)
        draw_vinyl(p, cover.center() + QPoint(105, 0), 124, label_color(self.album))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRoundedRect(cover.translated(0, 3), 6, 6)
        draw_cover(p, cover, self.album, 6, self.devicePixelRatioF())
        p.end()


class AlbumView(QWidget):
    """Liner notes: sleeve on the left; title, credits and the track list on the right. The same
    page for an album, a playlist or a Smart Collection — each with what it allows."""

    back = Signal()

    def __init__(self, shelf, actions, playlists, menu_for):
        super().__init__()
        self.shelf = shelf
        self.actions = actions
        self.playlists = playlists
        self.album: Album | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.back_btn = Button("Shelf", "borderless", icon="arrow-left")
        self.back_btn.clicked.connect(self.back)
        lay.addLayout(hbox(self.back_btn, None))

        top = QHBoxLayout()
        top.setSpacing(28)
        self.art = SleeveArt()
        top.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)
        notes = QVBoxLayout()
        notes.setSpacing(4)
        self.kind = label("ALBUM", "SectionTitle")
        self.name = label("", "Large")
        self.name.setWordWrap(True)
        self.artist = label("", "Title3")
        self.meta = label("", "Secondary")
        self.meta.setWordWrap(True)
        for w in (self.kind, self.name, self.artist, self.meta):
            notes.addWidget(w)
        notes.addSpacing(10)
        self.play_btn = Button("Play", "primary", icon="play")
        self.shuffle_btn = Button("Shuffle", icon="shuffle")
        self.download_btn = Button("Download", icon="download")
        self.open_btn = Button("Open Source", "borderless", icon="external")
        self.more_btn = IconButton("more", "More", tone="secondary")
        self.more_btn.clicked.connect(lambda: self.album and menu_for(self.album, self.more_btn))
        notes.addLayout(hbox(self.play_btn, self.shuffle_btn, self.download_btn, self.open_btn, self.more_btn, None))
        notes.addStretch(1)
        top.addLayout(notes, 1)
        lay.addLayout(top)

        self.list = TrackListView("disc", "No tracks", "")
        saved = lambda t: t.key in actions.library  # noqa: E731
        self._common = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.list.tracks()[r]]), actions.queued),
            RowAction("heart", "Save", lambda r: actions.toggle_saved(self.list.tracks()[r]), saved),
        ]
        self._own = self._common + [RowAction("close", "Remove from Playlist (Delete)", lambda r: self._remove([r]))]
        self.list.activated_row.connect(self._play_from)
        self.list.context_requested.connect(self._menu)
        self.list.moved.connect(lambda s, d: self.album and self.album.playlist_id
                                and playlists.move(self.album.playlist_id, s, d))
        self.list.delete_pressed.connect(self._remove)
        lay.addWidget(self.list, 1)

        self.play_btn.clicked.connect(self._play_all)
        self.shuffle_btn.clicked.connect(self._shuffle)
        self.download_btn.clicked.connect(self._download_all)
        actions.library.track_updated.connect(lambda _k: self.isVisible() and self._update_download())
        self.open_btn.clicked.connect(lambda: self.album and QDesktopServices.openUrl(QUrl(self.album.url)))

    def show_album(self, a: Album, top: bool = True) -> None:
        same = self.album is not None and self.album.id == a.id
        self.album = a
        tracks = tracks_of(a)
        kind = kind_of(a)
        self.art.set_album(a)
        self.kind.setText(KIND_LABEL[kind].upper())
        self.name.setText(a.title)
        self.artist.setText(a.artist)
        self.artist.setVisible(bool(a.artist) and not a.own)
        total = sum(t.duration for t in tracks)
        count = f"{len(tracks)} song{'s' if len(tracks) != 1 else ''}"
        if a.smart_id:
            sc = self.playlists.get_smart(a.smart_id)
            rules = (" · " if sc.match == "all" else " or ").join(smart.describe(r) for r in sc.rules) if sc else ""
            bits = [rules, count]
        else:
            bits = [a.year, count, format_duration(total) if total else "",
                    "" if a.own else SOURCE_LABELS.get(a.source, "")]
        self.meta.setText(" · ".join(b for b in bits if b))
        self.open_btn.setVisible(bool(a.url) and not a.own)
        own_list = bool(a.playlist_id)
        self.list.numbered = kind == "album"
        self.list.reorderable = own_list
        self.list.actions = self._own if own_list else self._common
        self.list.empty = {"playlist": ("list", "This playlist is empty", "Right-click any song › Add to Playlist."),
                           "smart": ("smart", "Nothing matches yet", "Songs appear here as they fit the rules."),
                           }.get(kind, ("disc", "No tracks", "")) if a.own else ("disc", "No tracks", "")
        self.list.set_tracks(tracks)
        for b in (self.play_btn, self.shuffle_btn):
            b.setEnabled(bool(tracks))
        self._update_download()
        if top and not same:
            self.list.scrollToTop()

    def refresh(self) -> None:
        if self.album:
            self.show_album(self.album, top=False)

    def _missing(self) -> list[Track]:
        return [t for t in tracks_of(self.album) if t.source != "local" and not self.actions.local_path_of(t)] \
            if self.album else []

    def _update_download(self) -> None:
        """“Download” while something is left to download; “Downloaded” (inactive) once it's all here."""
        missing = self._missing()
        self.download_btn.setEnabled(bool(missing))
        self.download_btn.setText("Download" if missing else "Downloaded")

    def _download_all(self) -> None:
        missing = self._missing()
        if missing:
            self.actions.download(missing)
            self.list.viewport().update()

    def _play_all(self) -> None:
        a = self.album
        if a is None:
            return
        tracks = self.list.selected_tracks() if a.own and len(self.list.selected_tracks()) > 1 else tracks_of(a)
        if kind_of(a) == "album":
            self.actions.play_album(tracks, a.title, 0)          # Album Integrity: always the album's order
        else:
            self.actions.play_playlist(tracks)

    def _play_from(self, row: int) -> None:
        """Albums play as albums (in order, gapless, from that song); in a playlist a chosen song
        plays on its own (the Play button plays the whole list)."""
        a = self.album
        if not a:
            return
        tracks = self.list.tracks()
        if kind_of(a) == "album":
            self.actions.play_album(tracks, a.title, row)
        else:
            self.actions.play(tracks[row:row + 1])

    def _shuffle(self) -> None:
        if self.album:
            tracks = list(tracks_of(self.album))
            random.shuffle(tracks)
            self.actions.play(tracks)

    def _menu(self, rows, pos) -> None:
        tracks = [self.list.tracks()[r] for r in rows]
        extra = [("close", "Remove from Playlist", lambda: self._remove(rows))] if self.album and self.album.playlist_id \
            else None
        self.actions.menu(self, tracks, extra).exec(pos)

    def _remove(self, rows) -> None:
        a = self.album
        if not a or not a.playlist_id or not rows:
            return
        tracks = [self.list.tracks()[r] for r in rows]
        name = f"“{tracks[0].title}”" if len(tracks) == 1 else f"{len(tracks)} songs"
        pid = a.playlist_id
        # Plain removals are instant; the dialog only appears to offer deleting downloads.
        self.actions.remove_with_files(self, tracks, f"Remove {name} from this playlist?",
                                       "They stay in your Library and queue.",
                                       lambda: self.playlists.remove(pid, rows), confirm_always=False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.back.emit()
        else:
            super().keyPressEvent(e)


class AddDialog(QDialog):
    """Put an album or playlist found online on the shelf: search by name or paste a link."""

    def __init__(self, parent, shelf, toast):
        super().__init__(parent)
        self.shelf = shelf
        self.toast = toast
        self._latest = tasks.Latest()
        self.setWindowTitle(" ")
        self.resize(640, 560)
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(14)
        col.addWidget(label("Find an Album or Playlist", "Title2"))
        self.mode = Segmented(["Find Online", "Spotify Artist"])
        self._infos: dict[str, dict] = {}            # url → what was already loaded for it
        self.mode.changed.connect(self._switch)
        col.addWidget(self.mode)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Album or playlist name, or a link")
        self.field.returnPressed.connect(self.run)
        col.addWidget(self.field)
        self.status = label("", "Caption")
        col.addWidget(self.status)

        self.results = TrackListView("disc", "Find an album", "Type a name and press Enter.")
        self.results.actions = [RowAction("plus", "Add to Shelf", self._add_row, lambda t: self.shelf.has(t.url))]
        self.results.activated_row.connect(self._add_row)
        col.addWidget(self.results, 1)
        done = Button("Done", "primary")
        done.clicked.connect(self.accept)
        col.addLayout(hbox(None, done))
        self.field.setFocus()

    def _switch(self, i: int) -> None:
        self.field.setPlaceholderText("Artist name, or a Spotify artist link" if i == 1
                                      else "Album or playlist name, or a link")
        self.status.setText("")
        if self.field.text().strip():
            self.results.set_tracks([])
            self.run()

    def run(self) -> None:
        q = self.field.text().strip()
        if not q:
            return
        if providers.is_link(q):
            self._add_url(q)
            return
        ticket = self._latest.next()
        self.status.setText("Searching…")
        if self.mode.index() == 1:
            self._find_artist(q, ticket)
            return

        def done(found):
            if self._latest.is_current(ticket):
                self.status.setText(f"{len(found)} results" if found else "Nothing found")
                self.results.set_tracks([Track(source=a["source"], id=a["url"], title=a["title"],
                                               artist=a["artist"] or ("Album" if a.get("kind") == "album" else "Playlist"),
                                               url=a["url"], thumbnail=a["cover"]) for a in found])

        tasks.run(lambda: providers.search_albums(q, 15), done,
                  lambda e: self.status.setText("Couldn’t connect"))

    def _find_artist(self, q: str, ticket) -> None:
        """Spotify: the artist's top tracks and public playlists of their songs."""
        def done(found: list[dict]):
            if not self._latest.is_current(ticket):
                return
            self.status.setText(f"{len(found)} found" if found else "Nothing found on Spotify")
            self._infos.update({a["url"]: a for a in found})
            self.results.set_tracks([Track(source=a["source"], id=a["url"], title=a["title"],
                                           artist=f"{'Artist' if '/artist/' in a['url'] else 'Playlist'} · "
                                                  f"{len(a['tracks'])} songs",
                                           url=a["url"], thumbnail=a["cover"]) for a in found])

        tasks.run(lambda: providers.spotify.search_artist(q), done,
                  lambda e: self.status.setText("Couldn’t connect"))

    def _add_row(self, row: int) -> None:
        t = self.results.tracks()[row]
        if not self.shelf.has(t.url):
            info = self._infos.get(t.url)
            if info:
                self._put(info)
            else:
                self._add_url(t.url)

    def _put(self, info: dict) -> None:
        album = Album(**{k: info[k] for k in ("title", "artist", "cover", "url", "source", "year")},
                      tracks=info["tracks"], kind=info.get("kind", "album"))
        added = self.shelf.add(album)
        self.status.setText("")
        self.results.viewport().update()
        self.toast("success" if added else "info", f"Added “{info['title']}”" if added else "Already on the shelf")

    def _add_url(self, url: str) -> None:
        self.status.setText("Loading…")

        def done(info: dict):
            self._put(info)

        def failed(exc):
            log.error("Couldn't load album: %s", exc)
            self.status.setText("Couldn’t load that album or playlist")

        tasks.run(lambda: providers.album(url), done, failed)


class ShelfPage(TintedPage):
    def __init__(self, shelf, playlists, actions, toast, settings, smart_tracks, listening=None):
        super().__init__("Shelf", settings)
        global _playlists, _smart_tracks
        _playlists = playlists
        _smart_tracks = self._smart
        self.shelf = shelf
        self.playlists = playlists
        self.actions = actions
        self.toast = toast
        self.evaluate = smart_tracks                    # SmartCollection → its songs right now
        self._smart_cache: dict[str, list[Track]] = {}
        self._return: tuple | None = None               # where the shelf was when an entry opened

        self.add_btn = Button("Add", "borderless", icon="plus")
        self.add_btn.clicked.connect(self._add_menu)
        self.header.addWidget(self.add_btn)
        self.count = label("", "Caption")
        self.header.addWidget(self.count)
        self.display = Segmented(["Cover", "Spine"], compact=True)
        self.header.addWidget(self.display)

        self.stack = QStackedWidget()
        self.scroll = plain_scroll()
        self.view = ShelfView(shelf)
        self.scroll.setWidget(self.view)
        self.detail = AlbumView(shelf, actions, playlists, self._entry_menu)
        self.spines = RackView(spine_w=40, case_h=190, reveal=0.55)
        self.spine_panel = RackPanel(self.spines, ("disc", "Your shelf is empty",
                                                   "Use “Add” to start a playlist or find an album."), detail=False)
        self.stack.addWidget(self.scroll)
        self.stack.addWidget(self.detail)
        self.stack.addWidget(self.spine_panel)
        self.root.addWidget(self.stack, 1)
        self.spines.clicked_item.connect(lambda i: self.open_album(self.shelf.albums[i].id))
        self.spines.activated.connect(lambda i: self.open_album(self.shelf.albums[i].id))
        self.spines.context.connect(lambda i, pos: self._menu(self.shelf.albums[i].id, pos))
        self.spines.reorderable = True
        self.spines.moved.connect(self.shelf.move)
        thumbs_mod.instance().ready.connect(lambda _k: self.spines.isVisible() and self.spines.refresh_covers())
        self._view = 2 if settings["shelf_view"] == "spine" else 0
        self.display.set_index(1 if self._view == 2 else 0)
        self.display.changed.connect(self._set_display)
        self.stack.setCurrentIndex(self._view)

        self.view.open_album.connect(self.open_album)
        self.view.play_album.connect(lambda aid: self.shelf.get(aid) and self.play_entry(self.shelf.get(aid)))
        self.view.context.connect(self._menu)
        self.detail.back.connect(self.close_album)
        shelf.changed.connect(self._shelf_changed)
        playlists.playlist_changed.connect(self._playlist_edited)
        # Smart Collections follow the library and play stats (coalesced)
        self._smart_timer = QTimer(self, singleShot=True, interval=400)
        self._smart_timer.timeout.connect(self._smart_changed)
        actions.library.changed.connect(self._smart_timer.start)
        actions.library.track_updated.connect(lambda _k: self._smart_timer.start())
        actions.library.track_updated.connect(lambda _k: self._view == 2 and self._sync_spines())
        if listening is not None:
            listening.stats_changed.connect(self._smart_timer.start)
        thumbs_mod.instance().ready.connect(lambda _k: self._update_tint())
        self._update_count()
        if self._view == 2:
            self._sync_spines()

    # ---- Smart Collections -------------------------------------------------------

    def _smart(self, sid: str) -> list[Track]:
        """A Smart Collection's songs (worked out once until the library or the stats change)."""
        hit = self._smart_cache.get(sid)
        if hit is None:
            sc = self.playlists.get_smart(sid)
            hit = self._smart_cache[sid] = self.evaluate(sc) if sc else []
        return hit

    def _smart_changed(self) -> None:
        self._smart_cache.clear()
        self.view.update()
        if self._view == 2:
            self._sync_spines()
        if self.stack.currentIndex() == 1 and self.detail.album and self.detail.album.smart_id:
            self.detail.refresh()

    # ---- displays ------------------------------------------------------------

    def _set_display(self, i: int) -> None:
        self._view = 2 if i == 1 else 0
        self.settings["shelf_view"] = "spine" if i == 1 else "cover"
        if self._view == 2:
            self._sync_spines()
        if self.stack.currentIndex() != 1:          # leave an open album open
            self.stack.setCurrentIndex(self._view)

    def _sync_spines(self) -> None:
        dpr = self.devicePixelRatioF()
        items = []
        for a in self.shelf.albums:
            kind = kind_of(a)
            tracks = tracks_of(a)
            have = sum(1 for t in tracks if self.actions.local_path_of(t))
            offline = "full" if tracks and have == len(tracks) else "partial" if have else "none"
            items.append(RackItem(key=a.id, title=a.title, subtitle=a.artist or subtitle(a), kind=kind, offline=offline,
                                  tooltip=" — ".join(x for x in (a.title, subtitle(a)) if x),
                                  cover=(lambda a=a: _tile(a, 190, 0.0, dpr))))
        keep = self.spines.items[self.spines.selected].key if 0 <= self.spines.selected < len(self.spines.items) else None
        self.spines.set_items(items, keep)

    def _shelf_changed(self) -> None:
        self._update_count()
        if self._view == 2:
            self._sync_spines()
        a = self.detail.album
        if self.stack.currentIndex() == 1 and a is not None:
            if self.shelf.get(a.id) is None:
                self.close_album()                    # it came off the shelf (a playlist was deleted)
            else:
                self.detail.show_album(self.shelf.get(a.id), top=False)    # renamed, or a new cover
                self._update_tint()

    def _playlist_edited(self, list_id: str) -> None:
        if self.playlists.get_smart(list_id):
            self._smart_cache.pop(list_id, None)      # its rules changed
        self.view.update()
        if self._view == 2:
            self._sync_spines()
        a = self.detail.album
        if self.stack.currentIndex() == 1 and a and list_id in (a.playlist_id, a.smart_id):
            self.detail.refresh()

    def _update_count(self) -> None:
        n = len(self.shelf.albums)
        self.count.setText(f"{n:,} item{'s' if n != 1 else ''}")

    # ---- opening -------------------------------------------------------------

    def open_album(self, aid: str) -> None:
        a = self.shelf.get(aid)
        if not a:
            return
        if self.stack.currentIndex() != 1:
            # remember the place on the shelf: back goes there, not to the top
            bar = (self.scroll if self._view == 0 else self.spine_panel.scroll).verticalScrollBar()
            self._return = (self._view, bar.value(), aid)
        self.detail.show_album(a)
        self.stack.setCurrentIndex(1)
        for w in (self.add_btn, self.count, self.display):
            w.hide()
        self._update_tint()
        self.detail.setFocus()

    def open_list(self, list_id: str) -> None:
        """Open one of the listener's playlists or Smart Collections."""
        self.shelf.sync_playlists(self.playlists)
        a = self.shelf.entry_for(list_id)
        if a is not None:
            self.open_album(a.id)

    def close_album(self) -> None:
        self.stack.setCurrentIndex(self._view)
        for w in (self.add_btn, self.count, self.display):
            w.show()
        self.set_hue(None)
        back, self._return = self._return, None
        if back is not None and back[0] == self._view:
            view, value, aid = back
            bar = (self.scroll if view == 0 else self.spine_panel.scroll).verticalScrollBar()
            QTimer.singleShot(0, lambda: bar.setValue(value))
            if view == 2:
                keys = [it.key for it in self.spines.items]
                if aid in keys:
                    self.spines.select(keys.index(aid), emit=False)
        (self.view if self._view == 0 else self.spines).setFocus()

    def _update_tint(self) -> None:
        if self.stack.currentIndex() == 1 and self.detail.album:
            pm = cover_pixmap(self.detail.album)
            if pm is not None:
                self.set_hue(tint_mod.hue_of(pm) if not pm.isNull() else None)

    # ---- actions -----------------------------------------------------------------

    def play_entry(self, a: Album) -> None:
        tracks = tracks_of(a)
        if not tracks:
            self.toast("info", "Nothing to play yet")
        elif kind_of(a) == "album":
            self.actions.play_album(tracks, a.title)
        else:
            self.actions.play_playlist(tracks)

    def _add_menu(self) -> None:
        m = QMenu(self)
        add_item(m, "list", "New Playlist…", self.new_playlist)
        sub = m.addMenu(icons.icon("smart", theme.color("label"), 16), "New Smart Collection")
        for i, (name, _rules) in enumerate(smart.PRESETS):
            sub.addAction(name, lambda i=i: self.new_smart(i))
        sub.addSeparator()
        sub.addAction("Custom Rules…", lambda: self.new_smart(None))
        m.addSeparator()
        add_item(m, "search", "Find an Album or Playlist…", self.add_album)
        menu_below(self.add_btn, m)

    def new_playlist(self) -> None:
        name = prompt(self, "New Playlist", "Playlist name", "Create")
        if name:
            p = self.playlists.create(name)
            self.open_list(p.id)

    def new_smart(self, preset: int | None = None) -> None:
        """A Smart Collection from a preset, or (None) from the rule editor."""
        if preset is not None:
            name, rules = smart.PRESETS[preset]
            sc = smart.SmartCollection(name=name, rules=[dict(r) for r in rules])
        else:
            from aria.ui.widgets.smart_dialog import edit_smart
            sc = edit_smart(self, None, lambda c: len(self.evaluate(c)))
            if sc is None:
                return
        self.playlists.add_smart(sc)
        self.open_list(sc.id)

    def add_album(self) -> None:
        AddDialog(self, self.shelf, self.toast).exec()

    def _menu(self, aid: str, pos: QPoint) -> None:
        a = self.shelf.get(aid)
        if a is not None:
            self._entry_menu(a, pos, opened=False)

    def _entry_menu(self, a: Album, where, opened: bool = True) -> None:
        """Right-click on a sleeve, or ⋯ on its open page: play first, then what this kind allows."""
        m = QMenu(self)
        if not opened:
            add_item(m, "play", "Play", lambda: self.play_entry(a))
            add_item(m, "queue-add", "Add to Queue", lambda: self.actions.enqueue(tracks_of(a)), bool(tracks_of(a)))
            add_item(m, "disc", "Open", lambda: self.open_album(a.id))
            m.addSeparator()
        if a.playlist_id:
            add_item(m, "pencil", "Rename…", lambda: self.rename(a))
        elif a.smart_id:
            add_item(m, "smart", "Edit Rules…", lambda: self.edit_rules(a))
        add_item(m, "image", "Choose Cover…", lambda: self.choose_cover(a))
        if a.custom_cover:
            add_item(m, "undo", "Use Automatic Cover", lambda: self.shelf.set_cover(a.id, ""))
        m.addSeparator()
        if a.playlist_id:
            add_item(m, "trash", "Delete Playlist…", lambda: self.delete_playlist(a.playlist_id))
        elif a.smart_id:
            add_item(m, "trash", "Delete Collection…", lambda: self.delete_smart(a.smart_id))
        else:
            add_item(m, "trash", "Remove from Shelf", lambda: self._remove(a))
        if isinstance(where, QWidget):
            menu_below(where, m)
        else:
            m.exec(where)

    def rename(self, a: Album) -> None:
        p = self.playlists.get(a.playlist_id)
        if p:
            name = prompt(self, "Rename Playlist", "Playlist name", "Save", p.name)
            if name:
                self.playlists.rename(p.id, name)

    def edit_rules(self, a: Album) -> None:
        from aria.ui.widgets.smart_dialog import edit_smart
        sc = self.playlists.get_smart(a.smart_id)
        if not sc:
            return
        edited = edit_smart(self, sc, lambda c: len(self.evaluate(c)))
        if edited:
            sc.name, sc.rules, sc.match, sc.scope = edited.name, edited.rules, edited.match, edited.scope
            self.playlists.update_smart(sc)

    def delete_playlist(self, pid: str) -> None:
        p = self.playlists.get(pid)
        if p:
            self.actions.remove_with_files(self, list(p.tracks), f"Delete “{p.name}”?",
                                           "Songs stay in your Library and queue.",
                                           lambda: self.playlists.delete(pid))

    def delete_smart(self, sid: str) -> None:
        sc = self.playlists.get_smart(sid)
        if sc and confirm(self, f"Delete “{sc.name}”?", "Only the collection goes; its songs stay where they are.",
                          "Delete", danger=True):
            self.playlists.delete_smart(sid)

    def choose_cover(self, a: Album) -> None:
        """A picture of the listener's own for this entry (copied into Aria's data, so it stays)."""
        src, _ = QFileDialog.getOpenFileName(self, "Choose Cover", "", IMAGES)
        if not src:
            return
        if QImage(src).isNull():
            self.toast("warning", "That picture couldn’t be read")
            return
        paths.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{a.id}-{int(time.time())}{os.path.splitext(src)[1].lower() or '.png'}"
        try:
            shutil.copyfile(src, paths.COVERS_DIR / name)
        except OSError as exc:
            log.error("Couldn’t copy the cover: %s", exc)
            self.toast("danger", "Couldn’t save that picture")
            return
        self.shelf.set_cover(a.id, name)

    def _remove(self, a: Album) -> None:
        if confirm(self, f"Remove “{a.title}” from the shelf?",
                   "It only comes off the shelf; your Library and playlists aren’t touched.", "Remove", danger=True):
            self.shelf.remove(a.id)

    def on_shown(self) -> None:
        if self.stack.currentIndex() != 1:
            self.set_hue(None)
