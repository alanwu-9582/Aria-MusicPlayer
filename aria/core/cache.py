"""The disposable cache: cover images, lyrics and yt-dlp's data, all under ``data/cache``.

Clearing it never touches the library, playlists, settings or downloaded songs;
everything in it is fetched again when needed.
"""

from __future__ import annotations

import logging
import os
import shutil

from aria import paths

log = logging.getLogger(__name__)


def size() -> int:
    """Bytes used by the cache (blocking: walks the folder)."""
    total = 0
    for root, _dirs, files in os.walk(paths.CACHE_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clear() -> int:
    """Delete everything in the cache; returns the bytes freed. Files in use are skipped."""
    before = size()
    for entry in list(paths.CACHE_DIR.iterdir()) if paths.CACHE_DIR.exists() else []:
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            pass
    paths.ensure_dirs()                       # covers keep caching straight away
    # Remembered stream links go too (useful when a stream stopped working).
    from aria import providers
    with providers._streams_lock:
        providers._streams.clear()
    freed = max(0, before - size())
    log.info("Cleared the cache (%s)", human(freed))
    return freed


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
