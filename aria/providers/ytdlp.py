"""Thin, quiet wrappers around yt-dlp."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import yt_dlp

from aria import paths
from aria.core.models import Stream

log = logging.getLogger("yt-dlp")


class _Logger:
    def debug(self, msg):  # yt-dlp routes info to debug too
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        if "JavaScript runtime" not in msg:
            log.debug(msg)

    def error(self, msg):
        log.debug(msg)


BASE = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "logger": _Logger(),
    "socket_timeout": 15,
    "noplaylist": True,
    # Caches YouTube player JS / solved challenges between runs (faster resolves).
    "cachedir": str(paths.CACHE_DIR / "yt-dlp"),
    # YouTube needs a JS runtime to unlock full-speed formats; use whichever is installed.
    "js_runtimes": {"deno": {}, "node": {}},
}

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"


def _ydl(**opts) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL({**BASE, **opts})


def flat(target: str, limit: int | None = None, playlist: bool = True) -> list[dict]:
    """List entries of a search / playlist without resolving each one."""
    opts: dict[str, Any] = {"extract_flat": "in_playlist", "noplaylist": not playlist}
    if limit:
        opts["playlistend"] = limit
    with _ydl(**opts) as y:
        info = y.extract_info(target, download=False) or {}
    entries = info.get("entries")
    if entries is None:
        return [info]
    return [e for e in entries if e]


def extract(url: str, fmt: str = AUDIO_FORMAT) -> dict:
    with _ydl(format=fmt) as y:
        return y.extract_info(url, download=False) or {}


def stream_of(info: dict) -> Stream:
    url = info.get("url")
    if not url:
        fmts = [f for f in info.get("requested_formats") or [] if f.get("acodec") != "none"]
        url = fmts[0]["url"] if fmts else ""
    if not url:
        raise RuntimeError("找不到可播放的音訊")
    return Stream(url=url, headers=dict(info.get("http_headers") or {}), expires_at=expiry_of(url))


def expiry_of(url: str, default_ttl: float = 3600) -> float:
    m = re.search(r"[?&/](?:expire|deadline|e)[=/](\d{10})", url)
    return float(m.group(1)) if m else time.time() + default_ttl


def download(url: str, outtmpl: str, progress: Callable[[float], None] | None = None,
             fmt: str = AUDIO_FORMAT) -> str:
    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(d.get("downloaded_bytes", 0) / total)

    with _ydl(format=fmt, outtmpl=outtmpl, progress_hooks=[hook], http_chunk_size=10_485_760) as y:
        info = y.extract_info(url, download=True)
        return y.prepare_filename(info)


def best_thumbnail(info: dict) -> str:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        return thumbs[-1].get("url", "")
    return info.get("thumbnail") or ""
