"""Run blocking work off the GUI thread and get the result back on it.

Work goes into one of a few lanes, each with its own bounded pool, so nothing optional can
crowd out what the listener is waiting for:

* ``play``     — resolving the song asked for now (and the one right after it): a reserved lane
* ``io``       — searches, recommendations, lyrics, prefetch and other network work (the default)
* ``download`` — downloads, one at a time, so a batch never takes the network from playback
* ``bulk``     — long jobs (batch source switching, link checks, backups, waiting on
                 the analysis worker), so they never hold the lanes above
* ``images``   — thumbnails, so a page of images never delays anything else
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)

LANES = {"play": 2, "io": 5, "download": 1, "bulk": 2, "images": 4}
_pools: dict[str, QThreadPool] = {}
for _name, _n in LANES.items():
    _pools[_name] = QThreadPool()
    _pools[_name].setMaxThreadCount(_n)
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
            log.warning("Background task failed: %s", exc)

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
        on_error: Callable[[Exception], None] | None = None, images: bool = False, lane: str = "io") -> None:
    """Run ``fn`` in ``lane``'s pool; callbacks fire on the GUI thread."""
    relay = _Relay(on_done, on_error)
    _alive.add(relay)
    _pools["images" if images else lane].start(_Job(fn, relay))


class Progress(QObject):
    """Report progress from a worker: ``emit(done, total)`` is safe from any thread;
    the callback runs on the GUI thread."""

    _tick = Signal(int, int)

    def __init__(self, callback: Callable[[int, int], None]):
        super().__init__()
        self.callback = callback
        self._tick.connect(self._deliver)      # a slot of this (GUI-thread) object: queued across threads

    @Slot(int, int)
    def _deliver(self, done: int, total: int) -> None:
        self.callback(done, total)

    def emit(self, done: int, total: int) -> None:
        self._tick.emit(done, total)


class Latest:
    """Ticket counter so only the newest request's result is applied."""

    def __init__(self):
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n

    def is_current(self, ticket: int) -> bool:
        return ticket == self._n

    def current(self) -> int:
        return self._n


def shutdown(timeout_ms: int = 1500) -> None:
    for pool in _pools.values():
        pool.clear()
    for name in ("play", "io"):
        _pools[name].waitForDone(timeout_ms // 2)
