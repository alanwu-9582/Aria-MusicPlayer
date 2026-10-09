"""Console commands."""

from __future__ import annotations

import logging
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from aria import __version__, paths, providers
from aria.core import tasks
from aria.core.models import format_duration

log = logging.getLogger("console")

HELP = """\
play [query|link]    play / pause; with an argument, play it now
add <query|link>     add to queue (links may be playlists)
search <query>       search on the Search page
next · prev · stop   skip / stop
vol [0-100]          show or set the volume
seek <sec|min:sec>   jump to a time
queue · history      list the queue / history
shuffle · clear      shuffle / clear the queue
auto [on|off]        autoplay
repeat [off|all|one] repeat mode
rec                  refresh and list recommendations
theme [system|light|dark]
data                 open the data folder
update               update yt-dlp (when YouTube stops playing)
version · cls        version / clear the console"""


class Commands:
    def __init__(self, window):
        self.w = window
        self.pb = window.playback

    def __call__(self, line: str) -> str | None:
        name, _, arg = line.strip().partition(" ")
        fn = getattr(self, f"cmd_{name.lower()}", None)
        if fn is None:
            return f"Unknown command “{name}” — type help"
        return fn(arg.strip())

    # ---- playback ------------------------------------------------------------

    def _resolve(self, arg: str, then) -> str:
        def work():
            if providers.is_link(arg):
                return providers.parse(arg)
            return providers.search("youtube", arg, 1)

        def done(tracks):
            if not tracks:
                log.warning("Nothing found: %s", arg)
                return
            then(tracks)

        tasks.run(work, done, lambda e: log.error("Nothing found for %s: %s", arg, e))
        return "Working…"

    def cmd_play(self, arg):
        if not arg:
            self.pb.toggle()
            return None
        return self._resolve(arg, lambda ts: self.w.actions.play(ts))

    def cmd_add(self, arg):
        if not arg:
            return "Usage: add <query|link>"
        return self._resolve(arg, lambda ts: self.w.actions.enqueue(ts))

    def cmd_search(self, arg):
        self.w.go(1)
        self.w.search_page.run(arg)

    def cmd_pause(self, _):
        self.pb.toggle()

    def cmd_next(self, _):
        self.pb.next()

    def cmd_prev(self, _):
        self.pb.previous()

    def cmd_stop(self, _):
        self.pb.stop()

    def cmd_vol(self, arg):
        if arg:
            v = max(0, min(100, int(arg)))
            self.pb.set_volume(v)
        return f"Volume {self.pb.player.volume}"

    def cmd_seek(self, arg):
        parts = [float(x) for x in arg.split(":")]
        secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0]
        self.pb.seek(secs)
        return f"Jumped to {format_duration(secs)}"

    def cmd_queue(self, _):
        lines = [f"{i + 1:>3}. {t.title}" for i, t in enumerate(self.pb.queue[:50])]
        cur = f"▶ {self.pb.current.title}" if self.pb.current else "(nothing playing)"
        return "\n".join([cur] + lines) if lines else cur + "\nThe queue is empty"

    def cmd_history(self, _):
        return "\n".join(f"{t.title}" for t in self.pb.history[::-1][:30]) or "No history"

    def cmd_shuffle(self, _):
        self.pb.shuffle()

    def cmd_clear(self, _):
        self.pb.clear()

    def cmd_auto(self, arg):
        on = {"on": True, "off": False}.get(arg.lower(), not self.pb.autoplay) if arg else not self.pb.autoplay
        self.w.player_bar.auto_btn.setChecked(on)
        return f"Autoplay {'on' if on else 'off'}"

    def cmd_repeat(self, arg):
        if arg in ("off", "all", "one"):
            while self.pb.repeat != arg:
                self.pb.cycle_repeat()
        else:
            self.pb.cycle_repeat()
        self.w.player_bar._show_repeat(self.pb.repeat)
        return f"Repeat: {self.pb.repeat}"

    def cmd_rec(self, _):
        self.pb.refresh_recommendations(force=True)
        if self.pb.recommendations:
            return "\n".join(t.title for t in self.pb.recommendations)
        return "Finding recommendations…"

    # ---- app -----------------------------------------------------------------

    def cmd_theme(self, arg):
        if arg in ("system", "light", "dark"):
            self.w.set_appearance(arg)
        return f"Appearance: {self.w.settings['appearance']}"

    def cmd_data(self, _):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.DATA_DIR)))

    def cmd_update(self, _):
        def work():
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "yt-dlp-ejs"]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=300)
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])
            import importlib.metadata as md
            return md.version("yt-dlp")

        tasks.run(work, lambda v: log.info("yt-dlp updated to %s — restart Aria to use it", v),
                  lambda e: log.error("Update failed: %s", e))
        return "Updating…"

    def cmd_version(self, _):
        return f"Aria {__version__}"

    def cmd_help(self, _):
        return HELP

    def cmd_cls(self, _):
        return "\f"
