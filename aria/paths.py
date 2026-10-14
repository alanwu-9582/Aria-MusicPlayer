"""Filesystem locations.

From source everything lives next to the app so it stays portable. The packaged app (Aria.exe)
keeps its data in %LOCALAPPDATA%/Aria — the install folder may not be writable — unless a
``data`` folder sits next to Aria.exe (a portable copy).
"""

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))          # running as the packaged Aria.exe
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
ROOT = Path(getattr(sys, "_MEIPASS", APP_DIR))        # the app's own (read-only) files
ASSETS_DIR = ROOT / "assets"
VLC_DIR = ROOT / "vlc-3.0.18"


def _data_dir() -> Path:
    if os.environ.get("ARIA_DATA_DIR"):               # a second copy (or a test run) with its own library
        return Path(os.environ["ARIA_DATA_DIR"])
    if not FROZEN or (APP_DIR / "data").is_dir():
        return APP_DIR / "data"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Aria"


DATA_DIR = _data_dir()
AUDIO_DIR = DATA_DIR / "audio"
CACHE_DIR = DATA_DIR / "cache"
THUMB_DIR = CACHE_DIR / "thumbs"
COVERS_DIR = DATA_DIR / "covers"           # pictures chosen for shelf items

LIBRARY_FILE = DATA_DIR / "library.json"
SESSION_FILE = DATA_DIR / "session.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PLAYLISTS_FILE = DATA_DIR / "playlists.json"
SHELF_FILE = DATA_DIR / "shelf.json"
DOWNLOADS_FILE = DATA_DIR / "downloads.json"     # downloads not finished yet (continued at the next start)
LISTENING_FILE = DATA_DIR / "listening.json"
BALANCE_FILE = DATA_DIR / "balance.json"
LOG_FILE = DATA_DIR / "aria.log"
# Packaged app: newer copies of pure-Python packages (yt-dlp), used in place of the bundled ones
PACKAGES_DIR = DATA_DIR / "packages"

# v1 files, migrated on first launch
LEGACY_SAVED_FILE = ASSETS_DIR / "saved.json"
LEGACY_QUEUE_FILE = ASSETS_DIR / "queue.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, THUMB_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _ytdlp_version(folder: Path) -> tuple[int, ...]:
    found = sorted(folder.glob("yt_dlp-*.dist-info"))
    return tuple(int(x) for x in found[-1].name[7:-10].split(".") if x.isdigit()) if found else ()


# Only while they're newer than the bundled copies (a newer Aria.exe may bundle a newer yt-dlp).
if FROZEN and PACKAGES_DIR.is_dir() and _ytdlp_version(PACKAGES_DIR) > _ytdlp_version(ROOT):
    sys.path.insert(0, str(PACKAGES_DIR))
