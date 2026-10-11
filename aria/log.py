"""Logging that feeds both a rotating file and the in-app console."""

from __future__ import annotations

import collections
import logging
import logging.handlers
import time

from PySide6.QtCore import QObject, Signal

from aria import paths


class LogRecord:
    __slots__ = ("time", "level", "name", "message")

    def __init__(self, time_: float, level: int, name: str, message: str):
        self.time = time_
        self.level = level
        self.name = name
        self.message = message


class LogBus(QObject):
    """Thread-safe bridge: records emitted from any thread arrive on the GUI thread."""

    record = Signal(object)
    cleared = Signal(int)                # cleared automatically after this many messages

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.history: collections.deque[LogRecord] = collections.deque(maxlen=capacity)
        self.limit = 0                   # clear after this many messages (0 = keep the latest ``capacity``)
        self.record.connect(self._keep)

    def _keep(self, r: LogRecord) -> None:
        if self.limit and len(self.history) >= self.limit:
            n = len(self.history)
            self.history.clear()
            self.cleared.emit(n)
        self.history.append(r)


class _BusHandler(logging.Handler):
    def __init__(self, bus: LogBus):
        super().__init__(logging.INFO)
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg = f"{msg}: {record.exc_info[1]}"
            self.bus.record.emit(LogRecord(record.created, record.levelno, record.name, msg))
        except Exception:  # never let logging crash the app
            pass


bus: LogBus | None = None


def setup() -> LogBus:
    global bus
    paths.ensure_dirs()
    bus = LogBus()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(paths.LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(_BusHandler(bus))
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return bus


def stamp(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))
