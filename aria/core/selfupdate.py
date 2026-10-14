"""Keeping yt-dlp current — YouTube changes often and an old yt-dlp stops playing it.

From source this is pip. The packaged app (Aria.exe) has no pip: it fetches the newest wheels
from PyPI (both packages are pure Python), checks them and unpacks them into data/packages,
which is put ahead of the bundled copies at the next start (see ``aria.paths``).
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import io
import shutil
import subprocess
import sys
import zipfile

from aria import paths

PACKAGES = ("yt-dlp", "yt-dlp-ejs")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def _wheel(name: str) -> tuple[str, str, str]:
    """(version, url, sha256) of the newest universal wheel of ``name`` on PyPI."""
    from aria.providers import http
    info = http.get(f"https://pypi.org/pypi/{name}/json", timeout=20).json()
    for f in info["urls"]:
        if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl"):
            return info["info"]["version"], f["url"], f["digests"]["sha256"]
    raise RuntimeError(f"No wheel for {name} on PyPI")


def _unpack_latest() -> str:
    from aria.providers import http
    current = md.version("yt-dlp")
    latest = {name: _wheel(name) for name in PACKAGES}
    if _key(latest["yt-dlp"][0]) <= _key(current):
        return current
    new = paths.PACKAGES_DIR.with_name("packages.new")
    old = paths.PACKAGES_DIR.with_name("packages.old")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    for _version, url, sha in latest.values():
        data = http.get(url, timeout=120).content
        if hashlib.sha256(data).hexdigest() != sha:
            shutil.rmtree(new, ignore_errors=True)
            raise RuntimeError("the download didn’t match PyPI’s checksum")
        zipfile.ZipFile(io.BytesIO(data)).extractall(new)
    shutil.rmtree(old, ignore_errors=True)
    if paths.PACKAGES_DIR.exists():
        paths.PACKAGES_DIR.rename(old)
    new.rename(paths.PACKAGES_DIR)
    shutil.rmtree(old, ignore_errors=True)
    return latest["yt-dlp"][0]


def update_ytdlp() -> str:
    """Bring yt-dlp up to date (blocking); returns the version Aria uses after a restart."""
    if paths.FROZEN:
        return _unpack_latest()
    cmd = [sys.executable, "-m", "pip", "install", "-U", *PACKAGES]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=300)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])
    return md.version("yt-dlp")
