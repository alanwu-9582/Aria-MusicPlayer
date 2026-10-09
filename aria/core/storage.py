"""JSON persistence: atomic writes, debounced saves, v1 migration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QTimer

from aria import paths
from aria.core.models import WEB, Track

log = logging.getLogger(__name__)


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        log.error("Couldn’t read %s: %s", path.name, exc)
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class DebouncedSaver:
    """Coalesces bursts of changes into one write."""

    def __init__(self, path: Path, snapshot: Callable[[], Any], delay_ms: int = 400):
        self.path = path
        self.snapshot = snapshot
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self.flush)

    def schedule(self) -> None:
        self.timer.start()

    def flush(self) -> None:
        self.timer.stop()
        try:
            write_json(self.path, self.snapshot())
        except OSError as exc:
            log.error("Couldn’t save %s: %s", self.path.name, exc)


class Settings:
    DEFAULTS = {
        "volume": 70,
        "autoplay": True,           # auto-recommend when the queue runs out
        "repeat": "off",            # off | all | one
        "appearance": "system",     # system | light | dark
        "sidebar_collapsed": False,
        "page": 0,
        "search_source": "youtube",
        "panel_tab": 0,
        "geometry": "",
        "crossfade": True,          # blend into the next song
        "crossfade_secs": 6,
        "soft_resume": True,        # long pause → fade back in
        "auto_local": True,         # switch to the file when a download of the playing song finishes
        "smart_artwork": True,      # tint the now-playing page from the cover
        "close_to_tray": True,
        "lyrics_enhanced": False,   # also look for lyrics in descriptions / comments
        "mini_pos": "",
    }

    def __init__(self):
        self.values = {**self.DEFAULTS, **read_json(paths.SETTINGS_FILE, {})}
        self._saver = DebouncedSaver(paths.SETTINGS_FILE, lambda: self.values)

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key, self.DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        if self.values.get(key) != value:
            self.values[key] = value
            self._saver.schedule()

    def flush(self) -> None:
        self._saver.flush()


# ---- v1 migration ------------------------------------------------------------

def legacy_track(d: dict) -> Track | None:
    url = d.get("watch_url") or ""
    if not url:
        return None
    from aria.providers import detect  # local import: providers import models only
    found = detect(url)
    source, ident = found if found else (WEB, url)
    local = d.get("saved_dist") or ""
    if local and not os.path.isabs(local):
        local = str(paths.ROOT / local)
    return Track(source=source, id=ident, title=d.get("title", url), artist=d.get("author", ""),
                 url=url, thumbnail=d.get("thumbnail_url", ""),
                 local_path=local if local and os.path.exists(local) else "")


def migrate_legacy() -> tuple[list[Track], list[Track]] | None:
    """Read v1 assets/saved.json & queue.json once; returns (library, queue) or None."""
    if paths.LIBRARY_FILE.exists():
        return None
    saved = read_json(paths.LEGACY_SAVED_FILE, {}).get("saved", [])
    queue = read_json(paths.LEGACY_QUEUE_FILE, {}).get("queue", [])
    if not saved and not queue:
        return None
    lib = [t for t in map(legacy_track, saved) if t]
    q = [t for t in map(legacy_track, queue) if t]
    log.info("Imported from v1: %d library songs, %d queued", len(lib), len(q))
    return lib, q
