"""Track actions shared by every list: play, queue, save, download, open, copy."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMenu, QWidget

from aria.core.models import LOCAL, Track
from aria.ui import icons
from aria.ui.theme import theme


class TrackActions:
    def __init__(self, playback, library, downloader, playlists, toast):
        self.pb = playback
        self.library = library
        self.downloader = downloader
        self.playlists = playlists
        self.toast = toast

    def add_to_playlist(self, pid: str | None, tracks: list[Track], parent: QWidget) -> None:
        """Add to an existing playlist, or (pid None) ask for a name and create one."""
        if pid is None:
            from aria.ui.widgets.dialogs import prompt
            name = prompt(parent, "New Playlist", "Playlist name", "Create")
            if not name:
                return
            p = self.playlists.create(name, tracks)
            self.toast("success", f"Created “{p.name}”")
            return
        n = self.playlists.add(pid, tracks)
        name = self.playlists.get(pid).name
        self.toast("success", f"Added to “{name}”" if n else f"Already in “{name}”")

    def fill_playlist_menu(self, menu: QMenu, tracks: list[Track], parent: QWidget) -> QMenu:
        """Existing playlists (✓ when the song is already in it) + “New Playlist…”."""
        c = theme.color("label")
        keys = {t.key for t in tracks}
        for pl in self.playlists.items:
            act = menu.addAction(pl.name, lambda pid=pl.id: self.add_to_playlist(pid, tracks, parent))
            if keys and keys <= {t.key for t in pl.tracks}:
                act.setIcon(icons.icon("check", theme.color("accent"), 16))
        if self.playlists.items:
            menu.addSeparator()
        menu.addAction(icons.icon("plus", c, 16), "New Playlist…", lambda: self.add_to_playlist(None, tracks, parent))
        return menu

    def playlist_menu(self, parent: QWidget, tracks: list[Track]) -> QMenu:
        return self.fill_playlist_menu(QMenu(parent), tracks, parent)

    # ---- removing ------------------------------------------------------------

    def local_path_of(self, track: Track) -> str:
        """The downloaded file for ``track`` (copies in playlists may not carry it)."""
        saved = self.library.get(track.key)
        path = track.local_path or (saved.local_path if saved else "")
        return path if path and track.source != LOCAL and os.path.exists(path) else ""

    def delete_downloads(self, tracks: list[Track]) -> tuple[int, int, int]:
        """Delete downloaded files; returns (deleted, kept, later). The file playing right now is
        deleted once the song is over."""
        deleted = kept = later = 0
        for t in tracks:
            path = self.local_path_of(t)
            if not path:
                continue
            if self.pb.current and self.pb.current.key == t.key:
                self.pb.delete_after_playback(t.key, path)
                later += 1
                continue
            try:
                os.remove(path)
            except OSError:
                kept += 1
                continue
            deleted += 1
            self.library.set_local(t.key, "")
            self.playlists.set_local(t.key, "")
            self.pb.forget_local(t.key)
        return deleted, kept, later

    def remove_with_files(self, parent: QWidget, tracks: list[Track], title: str, detail: str,
                          remove, confirm_always: bool = True) -> None:
        """Confirm a removal; when some of the songs were downloaded, offer to delete those files too."""
        from aria.ui.widgets.dialogs import confirm
        files = [t for t in tracks if self.local_path_of(t)]
        also = False
        if files:
            n = len(files)
            ok, also = confirm(parent, title, detail, "Remove", danger=True,
                               option="Also delete the downloaded file" if n == 1 else f"Also delete {n} downloaded files")
            if not ok:
                return
        elif confirm_always and not confirm(parent, title, detail, "Remove", danger=True):
            return
        remove()
        if also:
            deleted, kept, later = self.delete_downloads(files)
            parts = ["Removed"]
            if deleted:
                parts.append(f"{deleted} file{'s' if deleted != 1 else ''} deleted")
            if later:
                parts.append("the playing file goes when the song ends")
            if kept:
                parts.append(f"{kept} couldn’t be deleted")
            self.toast("warning" if kept else "success", " · ".join(parts))
        else:
            self.toast("success", "Removed")

    def reveal(self, track: Track) -> None:
        """Show the downloaded file in Explorer."""
        path = self.local_path_of(track)
        if path:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def play(self, tracks: list[Track]) -> None:
        if not tracks:
            return
        self.pb.play_now(tracks[0])
        if len(tracks) > 1:
            self.pb.enqueue(tracks[1:], play_next=True)

    def play_album(self, tracks: list[Track], title: str, start: int = 0) -> None:
        """Album mode: in order, gapless, no recommendations until it's over."""
        if tracks:
            self.pb.play_album(tracks, title, start)
            self.toast("info", f"Playing “{title}” as an album")

    def play_next(self, tracks: list[Track]) -> None:
        if tracks:
            self.pb.enqueue(tracks, play_next=True)
            self.toast("success", "Playing next" if len(tracks) == 1 else f"{len(tracks)} songs playing next")

    def enqueue(self, tracks: list[Track]) -> None:
        if tracks:
            self.pb.enqueue(tracks)
            self.toast("success", "Added to queue" if len(tracks) == 1 else f"Added {len(tracks)} songs to queue")

    def save(self, tracks: list[Track]) -> None:
        added = self.library.add(tracks)
        self.toast("success", f"Saved {added} song{'s' if added != 1 else ''}" if added else "Already in Library")

    def toggle_saved(self, track: Track) -> None:
        saved = self.library.toggle(track)
        self.toast("success", "Saved to Library" if saved else "Removed from Library")

    def download(self, tracks: list[Track]) -> None:
        self.library.add(tracks)   # downloads live in the library
        n = self.downloader.add([self.library.get(t.key) or t for t in tracks])
        self.toast("info", f"Downloading {n} song{'s' if n != 1 else ''}" if n else "Already downloaded")

    def open_source(self, track: Track) -> None:
        if track.source == LOCAL:
            QDesktopServices.openUrl(QUrl.fromLocalFile(track.local_path))
        elif track.url:
            QDesktopServices.openUrl(QUrl(track.url))

    def copy_link(self, tracks: list[Track]) -> None:
        QGuiApplication.clipboard().setText("\n".join(t.url for t in tracks if t.url))
        self.toast("success", "Link copied")

    def menu(self, parent: QWidget, tracks: list[Track], extra: list[tuple[str, str, object]] | None = None) -> QMenu:
        """Context menu; ``extra`` = [(icon, text, callback)] appended after a separator."""
        m = QMenu(parent)
        c = theme.color("label")

        def add(icon, text, fn):
            m.addAction(icons.icon(icon, c, 16), text, fn)

        add("play", "Play Now", lambda: self.play(tracks))
        add("play-next", "Play Next", lambda: self.play_next(tracks))
        add("queue-add", "Add to Queue", lambda: self.enqueue(tracks))
        m.addSeparator()
        self.fill_playlist_menu(m.addMenu(icons.icon("list", c, 16), "Add to Playlist"), tracks, parent)
        if not all(t.key in self.library for t in tracks):
            add("heart", "Save to Library", lambda: self.save(tracks))
        if any(t.source != LOCAL and not self.local_path_of(t) for t in tracks):
            add("download", "Download", lambda: self.download(tracks))
        if len(tracks) == 1:
            add("external", "Open Source", lambda: self.open_source(tracks[0]))
            if self.local_path_of(tracks[0]):
                add("folder", "Show in Folder", lambda: self.reveal(tracks[0]))
        if any(t.url and t.source != LOCAL for t in tracks):
            add("link", "Copy Link", lambda: self.copy_link(tracks))
        if extra:
            m.addSeparator()
            for icon, text, fn in extra:
                add(icon, text, fn)
        return m
