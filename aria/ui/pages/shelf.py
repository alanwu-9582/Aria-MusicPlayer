"""Digital Record Shelf: albums and playlists arranged by hand, opened like a record sleeve."""

from __future__ import annotations

import logging
import math
import random

from PySide6.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLineEdit, QMenu, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from aria import providers
from aria.core import tasks
from aria.core.lists import Album
from aria.core.models import SOURCE_LABELS, Track, format_duration
from aria.ui import icons
from aria.ui.pages.base import TintedPage
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets import tint as tint_mod
from aria.ui.widgets.controls import Button, hbox, label
from aria.ui.widgets.dialogs import confirm
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView, paint_empty, reserve_scrollbar

log = logging.getLogger(__name__)

COVER = 150
GAP_X = 30
TEXT_H = 44
PLANK_H = 8
ROW_H = COVER + PLANK_H + TEXT_H + 26
MARGIN = 8
COVER_PX = 360            # decoded cover height

_playlists = None          # set by ShelfPage: the user's playlists, for live shelf entries


def tracks_of(album: Album) -> list[Track]:
    """An album's tracks; for the user's own playlists, the playlist as it is right now."""
    if album.playlist_id and _playlists is not None:
        p = _playlists.get(album.playlist_id)
        return list(p.tracks) if p else []
    return album.tracks


def cover_sources(album: Album) -> list:
    """One cover, or up to four song covers for a playlist without one (mosaic)."""
    thumbs = thumbs_mod.instance()
    if album.cover and not album.playlist_id:
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
    p.fillRect(rect, theme.color("fill_strong"))
    s = rect.width() * 0.3
    icon = "list" if album.kind == "playlist" else "disc"
    p.drawPixmap(QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s).toRect(),
                 icons.pixmap(icon, theme.color("tertiary"), int(s)))
    p.restore()


def draw_vinyl(p: QPainter, center, r: float, label_color: QColor) -> None:
    """A black record: grooves, a sheen, and a label in the album's tint."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111113"))
    p.drawEllipse(center, r, r)
    pen = QPen(QColor(255, 255, 255, 14))
    pen.setWidthF(0.8)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in range(6, 18):
        rr = r * k / 18
        p.drawEllipse(center, rr, rr)
    sheen = QRadialGradient(center.x() - r * 0.3, center.y() - r * 0.4, r)
    sheen.setColorAt(0, QColor(255, 255, 255, 30))
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
    if album.playlist_id:
        n = len(tracks_of(album))
        return f"Playlist · {n} song{'s' if n != 1 else ''}"
    return album.artist or ("Playlist" if album.kind == "playlist" else "")


class ShelfView(QWidget):
    """The shelf itself, painted: sleeves standing on planks."""

    open_album = Signal(str)
    context = Signal(str, QPoint)

    def __init__(self, shelf):
        super().__init__()
        self.shelf = shelf
        self.setMouseTracking(True)
        self._hover = -1
        self._slide = 0.0                      # 0..1 vinyl slide-out of the hovered sleeve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
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

    # ---- events --------------------------------------------------------------

    def _on_slide(self, v) -> None:
        self._slide = float(v)
        if self._hover >= 0:          # only the hovered sleeve moves
            self.update(self._rect(self._hover).adjusted(-8, -8, 48, 8).toAlignedRect())

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
        if hover != self._hover:
            old = self._hover
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 else Qt.CursorShape.ArrowCursor)
            if old >= 0:
                self.update(self._rect(old).adjusted(-8, -8, 48, 8).toAlignedRect())
            self._anim.stop()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()

    def leaveEvent(self, e):
        self._hover = -1
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
            if i >= 0:
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
            paint_empty(p, self.rect(), "disc", "Your shelf is empty", "Use “Add…” to put an album or playlist here.")
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
            if not base.adjusted(-8, -8, 48, TEXT_H + PLANK_H + 12).intersects(dirty):
                continue
            r = base
            if i == self._hover and self._drag < 0:
                slide = self._slide * 30
                draw_vinyl(p, r.center() + QPoint(int(slide), 0) - QPoint(0, int(2 * self._slide)),
                           COVER * 0.47, label_color(a))
                r = r.translated(0, -2 * self._slide)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 30 if not theme.dark else 70))
            p.drawRoundedRect(r.translated(0, 2), 4, 4)
            draw_cover(p, r, a, dpr=dpr)
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
    """Liner notes: sleeve on the left; title, credits and the track list on the right."""

    back = Signal()

    def __init__(self, shelf, actions):
        super().__init__()
        self.shelf = shelf
        self.actions = actions
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
        for w in (self.kind, self.name, self.artist, self.meta):
            notes.addWidget(w)
        notes.addSpacing(10)
        self.play_btn = Button("Play", "primary", icon="play")
        self.shuffle_btn = Button("Shuffle", icon="shuffle")
        self.queue_btn = Button("Add to Queue", icon="queue-add")
        self.open_btn = Button("Open Source", "borderless", icon="external")
        notes.addLayout(hbox(self.play_btn, self.shuffle_btn, self.queue_btn, self.open_btn, None))
        notes.addStretch(1)
        top.addLayout(notes, 1)
        lay.addLayout(top)

        self.list = TrackListView("disc", "No tracks", "")
        self.list.numbered = True
        saved = lambda t: t.key in actions.library  # noqa: E731
        self.list.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.list.tracks()[r]])),
            RowAction("heart", "Save", lambda r: actions.toggle_saved(self.list.tracks()[r]), saved),
        ]
        self.list.activated_row.connect(lambda r: actions.play(self.list.tracks()[r:]))
        self.list.context_requested.connect(
            lambda rows, pos: actions.menu(self, [self.list.tracks()[i] for i in rows]).exec(pos))
        lay.addWidget(self.list, 1)

        self.play_btn.clicked.connect(lambda: self.album and actions.play(tracks_of(self.album)))
        self.shuffle_btn.clicked.connect(self._shuffle)
        self.queue_btn.clicked.connect(lambda: self.album and actions.enqueue(tracks_of(self.album)))
        self.open_btn.clicked.connect(lambda: self.album and QDesktopServices.openUrl(QUrl(self.album.url)))

    def show_album(self, a: Album) -> None:
        self.album = a
        tracks = tracks_of(a)
        self.art.set_album(a)
        self.kind.setText("PLAYLIST" if a.kind == "playlist" else "ALBUM")
        self.name.setText(a.title)
        self.artist.setText(a.artist)
        self.artist.setVisible(bool(a.artist))
        total = sum(t.duration for t in tracks)
        bits = [a.year, f"{len(tracks)} song{'s' if len(tracks) != 1 else ''}",
                format_duration(total) if total else "",
                "Aria" if a.playlist_id else SOURCE_LABELS.get(a.source, "")]
        self.meta.setText(" · ".join(b for b in bits if b))
        self.open_btn.setVisible(bool(a.url))
        self.list.set_tracks(tracks)
        self.list.scrollToTop()

    def refresh(self) -> None:
        if self.album:
            self.show_album(self.album)

    def _shuffle(self) -> None:
        if self.album:
            tracks = list(tracks_of(self.album))
            random.shuffle(tracks)
            self.actions.play(tracks)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.back.emit()
        else:
            super().keyPressEvent(e)


class AddDialog(QDialog):
    """Put an album or playlist on the shelf: search by name, paste a link, or pick one of your own playlists."""

    def __init__(self, parent, shelf, playlists, toast):
        super().__init__(parent)
        self.shelf = shelf
        self.playlists = playlists
        self.toast = toast
        self._latest = tasks.Latest()
        self.setWindowTitle(" ")
        self.resize(640, 560)
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(14)
        col.addWidget(label("Add to Shelf", "Title2"))
        self.mode = Segmented(["Find Online", "My Playlists"])
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
        self.mine = TrackListView("list", "No playlists yet", "Create one from the sidebar.")
        self.mine.actions = [RowAction("plus", "Add to Shelf", self._add_mine,
                                       lambda t: self.shelf.has_playlist(t.id))]
        self.mine.activated_row.connect(self._add_mine)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.results)
        self.stack.addWidget(self.mine)
        col.addWidget(self.stack, 1)
        done = Button("Done", "primary")
        done.clicked.connect(self.accept)
        col.addLayout(hbox(None, done))
        self.field.setFocus()

    def _switch(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.field.setVisible(i == 0)
        self.status.setText("")
        if i == 1:
            self.mine.set_tracks([Track(source="aria", id=p.id, title=p.name,
                                        artist=f"{len(p.tracks)} songs",
                                        thumbnail=next((t.thumbnail for t in p.tracks if t.thumbnail), ""))
                                  for p in self.playlists.items])

    def _add_mine(self, row: int) -> None:
        pid = self.mine.tracks()[row].id
        p = self.playlists.get(pid)
        if p and self.shelf.add_playlist(p):
            self.toast("success", f"Added “{p.name}” to the shelf")
        self.mine.viewport().update()

    def run(self) -> None:
        q = self.field.text().strip()
        if not q:
            return
        if providers.is_link(q):
            self._add_url(q)
            return
        ticket = self._latest.next()
        self.status.setText("Searching…")

        def done(found):
            if self._latest.is_current(ticket):
                self.status.setText(f"{len(found)} results" if found else "Nothing found")
                self.results.set_tracks([Track(source=a["source"], id=a["url"], title=a["title"],
                                               artist=a["artist"] or ("Album" if a.get("kind") == "album" else "Playlist"),
                                               url=a["url"], thumbnail=a["cover"]) for a in found])

        tasks.run(lambda: providers.search_albums(q, 15), done,
                  lambda e: self.status.setText("Couldn’t connect"))

    def _add_row(self, row: int) -> None:
        t = self.results.tracks()[row]
        if not self.shelf.has(t.url):
            self._add_url(t.url)

    def _add_url(self, url: str) -> None:
        self.status.setText("Loading…")

        def done(info: dict):
            album = Album(**{k: info[k] for k in ("title", "artist", "cover", "url", "source", "year")},
                          tracks=info["tracks"], kind=info.get("kind", "album"))
            added = self.shelf.add(album)
            self.status.setText("")
            self.results.viewport().update()
            self.toast("success" if added else "info", f"Added “{info['title']}”" if added else "Already on the shelf")

        def failed(exc):
            log.error("Couldn't load album: %s", exc)
            self.status.setText("Couldn’t load that album or playlist")

        tasks.run(lambda: providers.album(url), done, failed)


class ShelfPage(TintedPage):
    def __init__(self, shelf, playlists, actions, toast, settings):
        super().__init__("Shelf", settings)
        global _playlists
        _playlists = playlists
        self.shelf = shelf
        self.playlists = playlists
        self.actions = actions
        self.toast = toast

        self.add_btn = Button("Add…", "borderless", icon="plus")
        self.add_btn.clicked.connect(self.add_album)
        self.header.addWidget(self.add_btn)
        self.count = label("", "Caption")
        self.header.addWidget(self.count)

        self.stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        reserve_scrollbar(scroll)
        self.view = ShelfView(shelf)
        scroll.setWidget(self.view)
        self.detail = AlbumView(shelf, actions)
        self.stack.addWidget(scroll)
        self.stack.addWidget(self.detail)
        self.root.addWidget(self.stack, 1)

        self.view.open_album.connect(self.open_album)
        self.view.context.connect(self._menu)
        self.detail.back.connect(self.close_album)
        shelf.changed.connect(self._update_count)
        playlists.changed.connect(lambda: shelf.sync_playlists(playlists))
        playlists.playlist_changed.connect(self._playlist_edited)
        thumbs_mod.instance().ready.connect(lambda _k: self._update_tint())
        shelf.sync_playlists(playlists)
        self._update_count()

    def _playlist_edited(self, pid: str) -> None:
        self.view.update()
        if self.stack.currentIndex() == 1 and self.detail.album and self.detail.album.playlist_id == pid:
            self.detail.refresh()

    def _update_count(self) -> None:
        n = len(self.shelf.albums)
        self.count.setText(f"{n:,} item{'s' if n != 1 else ''}")

    def open_album(self, aid: str) -> None:
        a = self.shelf.get(aid)
        if not a:
            return
        self.detail.show_album(a)
        self.stack.setCurrentIndex(1)
        self.add_btn.hide()
        self.count.hide()
        self._update_tint()
        self.detail.setFocus()

    def close_album(self) -> None:
        self.stack.setCurrentIndex(0)
        self.add_btn.show()
        self.count.show()
        self.set_hue(None)

    def _update_tint(self) -> None:
        if self.stack.currentIndex() == 1 and self.detail.album:
            pm = cover_pixmap(self.detail.album)
            if pm is not None:
                self.set_hue(tint_mod.hue_of(pm) if not pm.isNull() else None)

    def _menu(self, aid: str, pos: QPoint) -> None:
        a = self.shelf.get(aid)
        if not a:
            return
        m = QMenu(self)
        c = theme.color("label")
        m.addAction(icons.icon("play", c, 16), "Play", lambda: self.actions.play(tracks_of(a)))
        m.addAction(icons.icon("queue-add", c, 16), "Add to Queue", lambda: self.actions.enqueue(tracks_of(a)))
        m.addAction(icons.icon("disc", c, 16), "Open", lambda: self.open_album(a.id))
        m.addSeparator()
        m.addAction(icons.icon("trash", c, 16), "Remove from Shelf", lambda: self._remove(a))
        m.exec(pos)

    def _remove(self, a: Album) -> None:
        if confirm(self, f"Remove “{a.title}” from the shelf?",
                   "It only comes off the shelf; your Library and playlists aren’t touched.", "Remove", danger=True):
            self.shelf.remove(a.id)

    def add_album(self) -> None:
        AddDialog(self, self.shelf, self.playlists, self.toast).exec()

    def put_playlist(self, pid: str) -> None:
        p = self.playlists.get(pid)
        if p:
            added = self.shelf.add_playlist(p)
            self.toast("success" if added else "info", f"Added “{p.name}” to the shelf" if added else "Already on the shelf")

    def on_shown(self) -> None:
        if self.stack.currentIndex() == 0:
            self.set_hue(None)
