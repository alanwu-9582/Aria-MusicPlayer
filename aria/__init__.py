"""Aria MusicPlayer."""

__version__ = "2.35.1"

import sys as _sys

if getattr(_sys, "frozen", False):      # packaged: put updated packages (yt-dlp) first, in every process
    from aria import paths as _paths  # noqa: E402,F401
