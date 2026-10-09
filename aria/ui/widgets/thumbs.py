"""Thumbnail loading: memory LRU → disk cache → network, decoded off the GUI thread."""

from __future__ import annotations

import collections
import hashlib

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

from aria import paths
from aria.core import tasks
from aria.core.models import YOUTUBE, Track
from aria.providers import http

LIST_HEIGHT = 120      # decoded height for list rows (rendered at 36–40 px, crisp at 2–3× DPI)
ART_HEIGHT = 720


def artwork_urls(track: Track) -> list[str]:
    if track.source == YOUTUBE:
        return [f"https://i.ytimg.com/vi/{track.id}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{track.id}/hqdefault.jpg", track.thumbnail]
    return [track.thumbnail]


def _load(urls: list[str], height: int) -> QImage | None:
    for url in urls:
        if not url:
            continue
        path = paths.THUMB_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".img")
        data = None
        if path.exists():
            data = path.read_bytes()
        else:
            try:
                r = http.get(url, timeout=10)
                data = r.content
                path.write_bytes(data)
            except Exception:
                continue
        img = QImage.fromData(data)
        if img.isNull():
            continue
        if url.endswith("hqdefault.jpg") and img.height() > 0:
            # hqdefault is 4:3 with letterbox bars; crop to the 16:9 picture
            h = img.width() * 9 // 16
            img = img.copy(0, (img.height() - h) // 2, img.width(), h)
        if img.height() > height:
            img = img.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
        return img
    return None


class Thumbs(QObject):
    ready = Signal(str)

    def __init__(self, capacity: int = 400):
        super().__init__()
        self._cache: collections.OrderedDict[str, QPixmap | None] = collections.OrderedDict()
        self._pending: set[str] = set()
        self._capacity = capacity

    def get(self, urls: str | list[str], height: int = LIST_HEIGHT) -> QPixmap | None:
        """Cached pixmap, or None (then ``ready`` fires with the key once loaded)."""
        urls = [urls] if isinstance(urls, str) else [u for u in urls if u]
        if not urls:
            return None
        key = f"{height}|{urls[0]}"
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            tasks.run(lambda: _load(urls, height), lambda img: self._store(key, img),
                      lambda _e: self._store(key, None), images=True)
        return None

    def key(self, urls: str | list[str], height: int = LIST_HEIGHT) -> str:
        first = urls if isinstance(urls, str) else next((u for u in urls if u), "")
        return f"{height}|{first}"

    def _store(self, key: str, img: QImage | None) -> None:
        self._pending.discard(key)
        self._cache[key] = QPixmap.fromImage(img) if img is not None else None
        while len(self._cache) > self._capacity:
            self._cache.popitem(last=False)
        self.ready.emit(key)


thumbs: Thumbs | None = None


def instance() -> Thumbs:
    global thumbs
    if thumbs is None:
        thumbs = Thumbs()
    return thumbs
