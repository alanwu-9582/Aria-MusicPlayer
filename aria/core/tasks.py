"""Run blocking work off the GUI thread and get the result back on it."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)

_pool = QThreadPool()            # searches, stream resolution, downloads, recommendations
_pool.setMaxThreadCount(6)
_image_pool = QThreadPool()      # thumbnails, so a page of images never delays playback
_image_pool.setMaxThreadCount(4)
_alive: set["_Relay"] = set()


class _Relay(QObject):
    """Lives on the GUI thread, so its slots run there (queued from the worker)."""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, on_done, on_error):
        super().__init__()
        self.on_done = on_done
        self.on_error = on_error
        self.done.connect(self._finish_ok)
        self.failed.connect(self._finish_err)

    @Slot(object)
    def _finish_ok(self, result):
        self._release()
        if self.on_done:
            self.on_done(result)

    @Slot(object)
    def _finish_err(self, exc):
        self._release()
        if self.on_error:
            self.on_error(exc)
        else:
            log.warning("背景工作失敗: %s", exc)

    def _release(self):
        _alive.discard(self)
        self.deleteLater()


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], relay: _Relay):
        super().__init__()
        self.fn = fn
        self.relay = relay

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # handed to the caller instead of dying in the pool
            self.relay.failed.emit(exc)
        else:
            self.relay.done.emit(result)


def run(fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None, images: bool = False) -> None:
    """Run ``fn`` in a pool; callbacks fire on the GUI thread."""
    relay = _Relay(on_done, on_error)
    _alive.add(relay)
    (_image_pool if images else _pool).start(_Job(fn, relay))


class Latest:
    """Ticket counter so only the newest request's result is applied."""

    def __init__(self):
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n

    def is_current(self, ticket: int) -> bool:
        return ticket == self._n


def shutdown(timeout_ms: int = 1500) -> None:
    for pool in (_pool, _image_pool):
        pool.clear()
    _pool.waitForDone(timeout_ms)
