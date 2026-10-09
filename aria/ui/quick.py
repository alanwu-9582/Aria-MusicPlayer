"""Quick Actions (Ctrl+K): a floating command field for songs and player commands.

Type to filter commands and find songs — first in your own music (library,
playlists, history, queue), then on YouTube. ↑↓ choose, Enter runs (plays a
song), Shift+Enter adds a song to the queue, Esc closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QFrame, QLineEdit, QListView, QStyledItemDelegate, QVBoxLayout, QWidget

from aria import providers
from aria.core import tasks, textnorm
from aria.core.models import Track
from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod

ROW, HEAD = 40, 26
MAX_ROWS = 9
LOCAL_SONGS = 6
ONLINE_SONGS = 5


@dataclass
class Command:
    title: str
    icon: str
    run: Callable[[], None]
    hint: str = ""                     # shortcut shown on the right
    keywords: str = ""
    enabled: Callable[[], bool] | None = None


@dataclass
class Item:
    kind: str                          # header | command | track
    title: str
    subtitle: str = ""
    icon: str = ""
    hint: str = ""
    run: Callable[[], None] | None = None
    alt: Callable[[], None] | None = None
    track: Track | None = None


def score(query: str, text: str) -> float:
    """How well ``query`` matches ``text`` (0 = not at all): prefix > word start > inside > in order."""
    q = query.casefold().strip()
    t = text.casefold()
    if not q:
        return 1.0
    if t.startswith(q):
        return 100 - len(t) / 100
    i = t.find(q)
    if i > 0 and not t[i - 1].isalnum():
        return 85 - i / 10
    if i >= 0:
        return 70 - i / 10
    words = q.split()
    if len(words) > 1 and all(w in t for w in words):
        return 60
    if len(q) < 3:
        return 0                        # two letters in scattered places mean nothing
    pos, gaps = -1, 0
    for ch in q.replace(" ", ""):
        j = t.find(ch, pos + 1)
        if j < 0:
            return 0
        gaps += j - pos - 1
        pos = j
    return max(1.0, 40 - gaps)


class ItemModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[Item] = []

    def set_items(self, items: list[Item]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def flags(self, index):
        if index.isValid() and self.items[index.row()].kind == "header":
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return it.title
        if role == Qt.ItemDataRole.UserRole:
            return it
        return None


class ItemDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        it: Item = index.data(Qt.ItemDataRole.UserRole)
        return QSize(option.rect.width(), HEAD if it.kind == "header" else ROW)

    def paint(self, p: QPainter, option, index) -> None:
        it: Item = index.data(Qt.ItemDataRole.UserRole)
        r = option.rect.adjusted(6, 0, -6, 0)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if it.kind == "header":
            p.setFont(font("caption", 600))
            p.setPen(theme.color("secondary"))
            p.drawText(r.adjusted(10, 6, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, it.title)
            p.restore()
            return
        selected = index.row() == self.view.currentIndex().row()
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(QRectF(r).adjusted(0, 2, 0, -2), 7, 7)
        fg = theme.color("on_accent" if selected else "label")
        sub = theme.color("on_accent" if selected else "secondary")
        if not selected:
            sub = theme.color("secondary")
        else:
            sub.setAlphaF(0.8)
        icon_r = QRect(r.left() + 10, r.center().y() - 14, 28, 28)
        if it.track is not None:
            pm = thumbs_mod.instance().get(it.track.thumbnail) if it.track.thumbnail else None
            path = QPainterPath()
            path.addRoundedRect(QRectF(icon_r), 5, 5)
            p.setClipPath(path)
            if pm and not pm.isNull():
                side = min(pm.width(), pm.height())
                p.drawPixmap(QRectF(icon_r), pm, QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side))
            else:
                p.fillRect(icon_r, theme.color("fill"))
                p.drawPixmap(icon_r.adjusted(7, 7, -7, -7), icons.pixmap("music", theme.color("tertiary"), 14))
            p.setClipping(False)
        else:
            p.drawPixmap(icon_r.adjusted(6, 6, -6, -6), icons.pixmap(it.icon or "command", fg, 16))
        hint = ("↵ Play  ⇧↵ Queue" if it.track is not None else it.hint) if selected or it.track is None else ""
        right = r.right() - 10
        if hint:
            p.setFont(font("caption"))
            p.setPen(sub)
            hw = QFontMetrics(p.font()).horizontalAdvance(hint)
            p.drawText(QRect(right - hw, r.top(), hw, r.height()), Qt.AlignmentFlag.AlignVCenter, hint)
            right -= hw + 12
        x = icon_r.right() + 12
        w = max(10, right - x)
        if it.subtitle:
            p.setFont(font("body"))
            p.setPen(fg)
            p.drawText(QRect(x, r.top() + 3, w, 19), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(it.title, Qt.TextElideMode.ElideRight, w))
            p.setFont(font("caption"))
            p.setPen(sub)
            p.drawText(QRect(x, r.top() + 20, w, 17), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(it.subtitle, Qt.TextElideMode.ElideRight, w))
        else:
            p.setFont(font("body"))
            p.setPen(fg)
            p.drawText(QRect(x, r.top(), w, r.height()), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(it.title, Qt.TextElideMode.ElideRight, w))
        p.restore()


class QuickActions(QWidget):
    """Overlay over the window: a dimmed backdrop and the command panel near the top."""

    def __init__(self, window):
        super().__init__(window.centralWidget())
        self.w = window
        self.hide()
        self._latest = tasks.Latest()
        self._online: list[Track] = []
        self._online_for = ""

        self.panel = QFrame(self)
        self.panel.setObjectName("Quick")
        col = QVBoxLayout(self.panel)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)
        self.field = QLineEdit()
        self.field.setObjectName("QuickField")
        self.field.setPlaceholderText("Search songs or type a command")
        self.field.textChanged.connect(self._on_text)
        self.field.installEventFilter(self)
        col.addWidget(self.field)
        self.list = QListView()
        self.model = ItemModel(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(ItemDelegate(self.list))
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.clicked.connect(lambda ix: self._run(ix.row()))
        self.list.entered.connect(lambda ix: self._select(ix.row()))
        col.addWidget(self.list)

        self._search = QTimer(self)
        self._search.setSingleShot(True)
        self._search.setInterval(450)
        self._search.timeout.connect(self._search_online)
        thumbs_mod.instance().ready.connect(lambda _k: self.list.viewport().update())
        theme.changed.connect(self._restyle)
        self._restyle()

    # ---- show / hide -------------------------------------------------------------

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.field.clear()
        self._online, self._online_for = [], ""
        self._rebuild()
        self.show()
        self.raise_()
        self.field.setFocus()

    def close_palette(self) -> None:
        self._latest.next()
        self._search.stop()
        self.hide()

    def _restyle(self) -> None:
        t = theme
        self.panel.setStyleSheet(
            f"#Quick {{ background: {t.hex('elevated')}; border: 1px solid {t.hex('separator_hex')};"
            f" border-radius: 12px; }}"
            f"#QuickField {{ font-size: 16px; padding: 6px 10px; min-height: 26px; max-height: 26px;"
            f" border: none; background: transparent; }}"
            f"#QuickField:focus {{ border: none; padding: 6px 10px; }}")

    # ---- events --------------------------------------------------------------

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_palette()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        w = min(620, self.width() - 80)
        rows = sum(HEAD if it.kind == "header" else ROW for it in self.model.items[:MAX_ROWS + 3])
        list_h = min(rows, MAX_ROWS * ROW + 2 * HEAD) if self.model.items else 0
        self.list.setFixedHeight(list_h + 4)
        self.list.setVisible(bool(self.model.items))
        h = 8 + 38 + (6 + list_h + 4 if self.model.items else 0) + 8
        self.panel.setGeometry((self.width() - w) // 2, 64, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 70 if theme.dark else 28))
        p.end()

    def eventFilter(self, obj, e):
        if obj is self.field and e.type() == QEvent.Type.KeyPress:
            key = e.key()
            if key == Qt.Key.Key_Escape:
                self.close_palette()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self.list.currentIndex().row(), alt=bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
        return super().eventFilter(obj, e)

    def _move(self, step: int) -> None:
        items = self.model.items
        i = self.list.currentIndex().row()
        for _ in range(len(items)):
            i = (i + step) % len(items) if items else -1
            if i >= 0 and items[i].kind != "header":
                self._select(i)
                return

    def _select(self, row: int) -> None:
        if 0 <= row < len(self.model.items) and self.model.items[row].kind != "header":
            ix = self.model.index(row)
            self.list.setCurrentIndex(ix)
            self.list.scrollTo(ix)
            self.list.viewport().update()

    def _run(self, row: int, alt: bool = False) -> None:
        if not (0 <= row < len(self.model.items)):
            return
        it = self.model.items[row]
        fn = it.alt if alt and it.alt else it.run
        if fn is None:
            return
        self.close_palette()
        fn()

    # ---- results -------------------------------------------------------------

    def _on_text(self, _t: str) -> None:
        self._rebuild()
        q = self.field.text().strip()
        if len(q) >= 2 and not providers.is_link(q):
            self._search.start()
        else:
            self._search.stop()

    def _search_online(self) -> None:
        q = self.field.text().strip()
        if q == self._online_for:
            return
        ticket = self._latest.next()

        def done(found: list[Track]):
            if self._latest.is_current(ticket) and self.isVisible():
                self._online, self._online_for = found[:ONLINE_SONGS], q
                self._rebuild(keep_selection=True)

        tasks.run(lambda: providers.search("youtube", q, ONLINE_SONGS), done, lambda _e: None)

    def _song_item(self, t: Track) -> Item:
        a = self.w.actions
        return Item("track", t.title, " · ".join(x for x in (t.artist, t.source_label) if x), track=t,
                    run=lambda: a.play([t]), alt=lambda: a.enqueue([t]))

    def _rebuild(self, keep_selection: bool = False) -> None:
        q = self.field.text().strip()
        keep = self.model.items[self.list.currentIndex().row()].title \
            if keep_selection and self.list.currentIndex().isValid() else None
        items: list[Item] = []

        if q and providers.is_link(q):
            a = self.w.actions
            link = q

            def resolve(then):
                tasks.run(lambda: providers.parse(link), lambda ts: ts and then(ts),
                          lambda e: a.toast("danger", "Couldn’t open that link"))
            items += [Item("header", "Link"),
                      Item("command", "Play Link", link, "play", run=lambda: resolve(a.play)),
                      Item("command", "Add Link to Queue", link, "queue-add", run=lambda: resolve(a.enqueue))]

        cmds = []
        for c in self.w.quick_commands():
            if c.enabled is not None and not c.enabled():
                continue
            s = max(score(q, c.title), score(q, c.keywords) * 0.8) if q else 1.0
            if s > 0:
                cmds.append((s, c))
        cmds.sort(key=lambda sc: -sc[0])
        limit = 8 if not q else 5
        if cmds:
            items.append(Item("header", "Commands"))
            items += [Item("command", c.title, icon=c.icon, hint=c.hint, run=c.run) for _s, c in cmds[:limit]]

        if q and not providers.is_link(q):
            mine = self._local_songs(q)
            if mine:
                items.append(Item("header", "Your Music"))
                items += [self._song_item(t) for t in mine]
            mine_keys = {t.key for t in mine}
            # Only results for what's typed now (not the previous query's).
            online = [t for t in self._online if t.key not in mine_keys] if self._online_for == q else []
            if online:
                items.append(Item("header", "YouTube"))
                items += [self._song_item(t) for t in online]
            items.append(Item("command", f"Search for “{q}”", icon="search", hint="",
                              run=lambda: (self.w.go(1), self.w.search_page.run(q))))

        self.model.set_items(items)
        self._place()
        target = next((i for i, it in enumerate(items) if keep and it.title == keep), None)
        if target is None:
            target = next((i for i, it in enumerate(items) if it.kind != "header"), -1)
        self._select(target)

    def _local_songs(self, q: str) -> list[Track]:
        words = [textnorm.fold(w) for w in q.split()]
        seen: set[str] = set()
        out: list[Track] = []
        w = self.w
        pools = [w.library.tracks, *(p.tracks for p in w.playlists.items), w.playback.history[::-1], w.playback.queue]
        for pool in pools:
            for t in pool:
                if t.key in seen:
                    continue
                seen.add(t.key)
                hay = textnorm.fold(f"{t.title} {t.artist}")
                if all(word in hay for word in words):
                    out.append(t)
                    if len(out) >= LOCAL_SONGS:
                        return out
        return out
