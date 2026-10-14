"""Library Snapshot: complete backups of the user's Aria data.

A snapshot is a zip of every personal data file — library, playlists and
Smart Collections, shelf (with the covers chosen for it), queue and history,
listening sessions and stats, settings — plus a small manifest. Snapshots are taken automatically (daily or
weekly) and on demand, kept in ``data/snapshots``, and can be restored to go
back to that point in time. An exported backup can also carry the downloaded
songs, which moves a whole Aria to another computer.

Restoring writes the files and needs a restart: the running app holds the
current data in memory and would otherwise write it back.
"""

from __future__ import annotations

import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from aria import paths

log = logging.getLogger(__name__)

VERSION = 1
KEEP_AUTO = 10
EVERY = {"daily": 86400, "weekly": 7 * 86400}


def data_files() -> list[Path]:
    return [paths.LIBRARY_FILE, paths.PLAYLISTS_FILE, paths.SHELF_FILE, paths.SESSION_FILE,
            paths.LISTENING_FILE, paths.SETTINGS_FILE]


def folder() -> Path:
    return paths.DATA_DIR / "snapshots"


@dataclass
class Snapshot:
    path: Path
    created: float
    reason: str                 # auto | manual | before-restore | import
    songs: int
    playlists: int
    size: int

    @property
    def label(self) -> str:
        lt = time.localtime(self.created)
        when = f"{time.strftime('%b', lt)} {lt.tm_mday}, {time.strftime('%H:%M', lt)}"
        what = {"auto": "", "manual": "Manual", "before-restore": "Before restore", "import": "Before import"}
        bits = [when, what.get(self.reason, ""), f"{self.songs} songs", f"{self.playlists} playlists"]
        return " · ".join(b for b in bits if b)


def _counts() -> tuple[int, int]:
    def load(p):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    lib = load(paths.LIBRARY_FILE).get("tracks", [])
    pl = load(paths.PLAYLISTS_FILE)
    return len(lib), len(pl.get("playlists", [])) + len(pl.get("smart", []))


def create(reason: str = "manual", dest: Path | None = None, include_audio: bool = False) -> Path:
    """Write a snapshot zip (the caller has flushed pending saves). ``dest`` defaults to the snapshot folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if dest is None:
        folder().mkdir(parents=True, exist_ok=True)
        dest = folder() / f"aria-{stamp}-{reason}.zip"
    songs, playlists = _counts()
    audio = [p for p in paths.AUDIO_DIR.iterdir() if p.is_file() and not p.name.endswith(".part")] \
        if include_audio and paths.AUDIO_DIR.exists() else []
    tmp = dest.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"version": VERSION, "created": time.time(), "reason": reason,
                                                "songs": songs, "playlists": playlists,
                                                "audio": len(audio)}, ensure_ascii=False))
        for f in data_files():
            if f.exists():
                z.write(f, f.name)
        if paths.COVERS_DIR.exists():
            for c in paths.COVERS_DIR.iterdir():
                if c.is_file():
                    z.write(c, f"covers/{c.name}")
        for a in audio:
            z.write(a, f"audio/{a.name}", compress_type=zipfile.ZIP_STORED)   # already compressed
    os.replace(tmp, dest)
    log.info("Snapshot saved: %s", dest.name)
    if dest.parent == folder():
        prune()
    return dest


def read(path: Path) -> Snapshot | None:
    try:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return Snapshot(path, m.get("created", path.stat().st_mtime), m.get("reason", "manual"),
                    m.get("songs", 0), m.get("playlists", 0), path.stat().st_size)


def snapshots() -> list[Snapshot]:
    """Newest first."""
    if not folder().exists():
        return []
    found = [s for s in map(read, folder().glob("aria-*.zip")) if s]
    return sorted(found, key=lambda s: -s.created)


def prune(keep: int = KEEP_AUTO) -> None:
    """Keep the newest automatic snapshots; the ones made by hand stay until deleted."""
    autos = [s for s in snapshots() if s.reason == "auto"]
    for s in autos[keep:]:
        try:
            s.path.unlink()
        except OSError:
            pass


def due(every: str) -> bool:
    """Is an automatic snapshot due ("daily" / "weekly"; anything else = off)?"""
    if every not in EVERY:
        return False
    last = next((s.created for s in snapshots() if s.reason == "auto"), 0)
    return time.time() - last >= EVERY[every]


def _relink(obj, audio_dir: Path):
    """Point "local_path" fields at this computer's audio folder (drop the ones that don't exist)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "local_path" and isinstance(v, str) and v:
                if not os.path.exists(v):
                    here = audio_dir / Path(v.replace("\\", "/")).name
                    obj[k] = str(here) if here.exists() else ""
            else:
                _relink(v, audio_dir)
    elif isinstance(obj, list):
        for v in obj:
            _relink(v, audio_dir)
    return obj


def restore(path: Path) -> None:
    """Replace the data files with the snapshot's (and unpack any songs it carries)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("Not an Aria backup")
        paths.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        for n in names:
            if n.startswith("covers/") and not n.endswith("/"):
                paths.COVERS_DIR.mkdir(parents=True, exist_ok=True)
                (paths.COVERS_DIR / Path(n).name).write_bytes(z.read(n))
            if n.startswith("audio/") and not n.endswith("/"):
                target = paths.AUDIO_DIR / Path(n).name
                if not target.exists():
                    with z.open(n) as src, open(target, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
        for f in data_files():
            if f.name in names:
                data = json.loads(z.read(f.name))
                if f != paths.SETTINGS_FILE:
                    data = _relink(data, paths.AUDIO_DIR)
                tmp = f.with_suffix(".restore")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, f)
            elif f.exists() and f != paths.SETTINGS_FILE:
                f.unlink()                     # the snapshot had none: start that part empty
    log.info("Restored snapshot %s", path.name)
