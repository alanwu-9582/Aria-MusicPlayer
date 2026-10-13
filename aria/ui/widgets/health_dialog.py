"""Collection Health window: what's out of order in the library, each with its suggested fix."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aria import providers
from aria.core import health, tasks
from aria.core.models import LOCAL
from aria.ui import icons
from aria.ui.player_bar import ElidedLabel
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, hbox, label
from aria.ui.widgets.tracklist import plain_scroll

log = logging.getLogger(__name__)
ORDER = [health.MISSING, health.BROKEN, health.DUPLICATE, health.EXTRA_COPY, health.ORPHAN]


class IssueRow(QFrame):
    fix = Signal(object)

    def __init__(self, issue: health.Issue):
        super().__init__()
        self.issue = issue
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = ElidedLabel()
        title.set_full_text(issue.title)
        detail = ElidedLabel("Caption")
        detail.set_full_text(issue.detail)
        col.addWidget(title)
        col.addWidget(detail)
        lay.addLayout(col, 1)
        btn = Button(issue.fix_label, "borderless")
        btn.clicked.connect(lambda: self.fix.emit(self.issue))
        lay.addWidget(btn)


class HealthDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.setWindowTitle(" ")
        self.resize(660, 580)
        self.issues: list[health.Issue] = []
        self.broken: list[health.Issue] = []
        self._checking = False

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(10)
        col.addWidget(label("Collection Health", "Title2"))
        self.summary = label("", "Secondary")
        self.links_btn = Button("Check Links", icon="link", tooltip="Make sure every saved link still plays (uses the network)")
        self.links_btn.clicked.connect(self.check_links)
        self.progress = label("", "Caption")
        col.addLayout(hbox(self.summary, None, self.progress, self.links_btn))

        self.scroll = plain_scroll()
        col.addWidget(self.scroll, 1)
        done = Button("Done", "primary")
        done.clicked.connect(self.accept)
        col.addLayout(hbox(None, done))
        self.rescan()

    # ---- scanning --------------------------------------------------------------

    def _others(self):
        w = self.w
        held = [t for p in w.playlists.items for t in p.tracks] + list(w.playback.queue) + list(w.playback.history)
        held += [t for a in w.shelf.albums for t in a.tracks]
        if w.playback.current:
            held.append(w.playback.current)
        return held

    def rescan(self) -> None:
        self.issues = health.scan(list(self.w.library.tracks), self._others())
        lib = self.w.library
        self.broken = [i for i in self.broken if all(k in lib for k in i.keys)]
        self._rebuild()

    def check_links(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.links_btn.setEnabled(False)
        tracks = [t for t in self.w.library.tracks if t.source != LOCAL]
        self.progress.setText(f"Checking 0 of {len(tracks)}…")
        report = tasks.Progress(lambda done, total: self.progress.setText(f"Checking {done} of {total}…"))

        def done(found):
            self._checking = False
            self.links_btn.setEnabled(True)
            self.broken = found
            self.progress.setText("All links play" if not found else "")
            self._rebuild()

        def failed(exc):
            self._checking = False
            self.links_btn.setEnabled(True)
            self.progress.setText("Couldn’t check links")
            log.error("Link check failed: %s", exc)

        tasks.run(lambda: health.check_links(tracks, providers.remote_stream, report.emit), done, failed)

    # ---- display ---------------------------------------------------------------

    def _rebuild(self) -> None:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 4, 12, 4)
        lay.setSpacing(4)
        allissues = self.issues + self.broken
        n = len(allissues)
        self.summary.setText("Everything looks tidy" if not n else f"{n} thing{'s' if n != 1 else ''} to look at")
        if not n:
            empty = QLabel()
            empty.setPixmap(icons.pixmap("check-circle", theme.color("tertiary"), 44))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addStretch(1)
            lay.addWidget(empty)
            msg = label("No problems found in your library.", "Secondary")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(msg)
            lay.addStretch(1)
        for kind in ORDER:
            group = [i for i in allissues if i.kind == kind]
            if not group:
                continue
            head = label(f"{health.GROUP_TITLES[kind].upper()} · {len(group)}", "SectionTitle")
            fix_all = Button("Fix All" if len(group) > 1 else "", "borderless")
            fix_all.setVisible(len(group) > 1)
            fix_all.clicked.connect(lambda _c=False, g=group: self._fix_many(g))
            lay.addSpacing(8)
            lay.addLayout(hbox(head, None, fix_all))
            for issue in group:
                row = IssueRow(issue)
                row.fix.connect(lambda i: self._fix_many([i]))
                lay.addWidget(row)
        lay.addStretch(1)
        self.scroll.setWidget(body)

    # ---- fixes -----------------------------------------------------------------

    def _fix_many(self, issues) -> None:
        fixed = 0
        for i in issues:
            try:
                self._fix(i)
                fixed += 1
            except Exception as exc:
                log.error("Couldn’t fix “%s”: %s", i.title, exc)
        if fixed:
            self.w.toast("success", f"Fixed {fixed} item{'s' if fixed != 1 else ''}")
        self.broken = [b for b in self.broken if b not in issues]
        self.rescan()

    def _fix(self, i: health.Issue) -> None:
        w = self.w
        lib, pls, pb, actions = w.library, w.playlists, w.playback, w.actions
        if i.kind == health.ORPHAN and i.track is not None:
            lib.add([i.track])
        elif i.kind == health.MISSING:
            key = i.keys[0]
            t = lib.get(key)
            if t is not None and t.source == LOCAL:
                lib.remove([key])
            else:
                lib.set_local(key, "")
                pls.set_local(key, "")
                pb.forget_local(key)
        elif i.kind == health.DUPLICATE:
            drop = [lib.get(k) for k in i.keys[1:] if lib.get(k)]
            keep = lib.get(i.keys[0])
            keep_file = os.path.normcase(keep.local_path) if keep and keep.local_path else ""
            # the extra uploads' downloads would only turn up again as strays
            actions.delete_downloads([t for t in drop if t.local_path and os.path.normcase(t.local_path) != keep_file])
            lib.remove([t.key for t in drop])
        elif i.kind == health.EXTRA_COPY:
            playing = pb.current.local_path if pb.current and pb.playing_local else ""
            for p in i.paths:
                if playing and os.path.normcase(p) == os.path.normcase(playing):
                    continue
                os.remove(p)
        elif i.kind == health.BROKEN:
            lib.remove(i.keys)
