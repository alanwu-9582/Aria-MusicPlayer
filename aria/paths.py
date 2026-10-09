"""Filesystem locations. Everything lives next to the app so it stays portable."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
VLC_DIR = ROOT / "vlc-3.0.18"

DATA_DIR = ROOT / "data"
AUDIO_DIR = DATA_DIR / "audio"
CACHE_DIR = DATA_DIR / "cache"
THUMB_DIR = CACHE_DIR / "thumbs"

LIBRARY_FILE = DATA_DIR / "library.json"
SESSION_FILE = DATA_DIR / "session.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "aria.log"

# v1 files, migrated on first launch
LEGACY_SAVED_FILE = ASSETS_DIR / "saved.json"
LEGACY_QUEUE_FILE = ASSETS_DIR / "queue.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, THUMB_DIR):
        d.mkdir(parents=True, exist_ok=True)
