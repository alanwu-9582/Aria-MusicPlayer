"""Console: live log plus a command line for power users.

One message, one line: long ones are shortened to the width of the view (and re-fitted when
it changes). Colour carries the meaning — the source of each line has its own colour, and
inside a line song names, services, numbers and marks (✓ ✗ → ▶) stand out.
"""

from __future__ import annotations

import html
import logging
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics, QTextCursor
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

from aria import log as applog
from aria.ui.pages.base import Page
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import Button, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import reserve_scrollbar

LEVEL_TOKEN = {logging.DEBUG: "tertiary", logging.INFO: "secondary", logging.WARNING: "orange",
               logging.ERROR: "red", logging.CRITICAL: "red"}
TAG_TOKEN = {"activity": "blue", "playback": "blue", "player": "blue", "switch": "purple", "library": "green",
             "lists": "green", "tempo": "teal", "notice": "accent", "settings": "gray", "recommend": "teal",
             "balance": "teal", "devices": "teal", "snapshot": "green", "search": "accent", "commands": "accent"}
SOURCE_TOKEN = {"YouTube": "red", "Spotify": "green", "Bilibili": "blue", "SoundCloud": "orange", "Local": "gray"}
TAG_WIDTH = 9
# pieces of a message that get their own colour
_PIECES = re.compile(r"(?P<title>“[^”]*”?)|(?P<src>\b(?:YouTube|Spotify|Bilibili|SoundCloud|Local)\b)"
                     r"|(?P<ok>✓)|(?P<no>✗)|(?P<mark>[→▶‖♥↓+](?=\s|$))"
                     r"|(?P<num>[+-]?\d+(?::\d{2}){1,2}|[+-]?\d+(?:\.\d+)?(?:\s?(?:%|BPM|s\b|MB|KB|GB))?|\d+/\d+)")


class ConsolePage(Page):
    def __init__(self, bus: applog.LogBus, execute):
        super().__init__("Console")
        self.bus = bus
        self.execute = execute
        self._history: list[str] = []
        self._hpos = 0

        clear = Button("Clear", "borderless", icon="trash")
        clear.clicked.connect(self.clear)
        self.header.addWidget(clear)

        bar = self.toolbar()
        self.level = Segmented(["All", "Warnings"], compact=True)
        self.level.changed.connect(lambda _i: self.rebuild())
        bar.addWidget(self.level)
        bar.addStretch(1)
        self.count = label("", "Secondary")
        bar.addWidget(self.count)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.view.setFont(font("caption", mono=True))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(150)
        self._refit.timeout.connect(self.rebuild)
        self.view.installEventFilter(self)
        self.view.setObjectName("Card")
        self.view.document().setDocumentMargin(12)
        reserve_scrollbar(self.view)
        self.root.addWidget(self.view, 1)

        self.prompt = QLineEdit()
        self.prompt.setFont(font("callout", mono=True))
        self.prompt.setPlaceholderText("Command (help)")
        self.prompt.returnPressed.connect(self._run)
        self.prompt.installEventFilter(self)
        self.root.addWidget(self.prompt)

        bus.record.connect(self._append_record)
        bus.cleared.connect(self._auto_cleared)
        theme.changed.connect(self.rebuild)
        self.rebuild()

    # ---- log -----------------------------------------------------------------

    def _line(self, color: str, text: str) -> str:
        return f'<span style="color:{color}; white-space:pre">{html.escape(text)}</span>'

    @staticmethod
    def _tag(name: str) -> str:
        """Short source of a message: "aria.core.playback" → "playback"."""
        tag = name.rsplit(".", 1)[-1]
        return {"root": "aria", "__main__": "aria", "main_window": "aria", "app": "aria"}.get(tag, tag)[:TAG_WIDTH]

    def _fit(self, prefix: str, message: str) -> str:
        """The message shortened so the whole line fits the view."""
        message = " ".join(message.split())             # never more than one line
        fm = QFontMetrics(self.view.font())
        room = self.view.viewport().width() - 2 * int(self.view.document().documentMargin()) - 8
        room -= fm.horizontalAdvance(prefix)
        return fm.elidedText(message, Qt.TextElideMode.ElideRight, max(room, 80))

    def _colored(self, message: str, base: str) -> str:
        out, pos = [], 0
        for m in _PIECES.finditer(message):
            if m.start() > pos:
                out.append(self._line(base, message[pos:m.start()]))
            kind = m.lastgroup
            text = m.group()
            token = {"title": "label", "ok": "green", "no": "red", "mark": "accent", "num": "teal"}.get(kind)
            color = theme.hex(SOURCE_TOKEN.get(text, "label")) if kind == "src" else theme.hex(token)
            out.append(self._line(color, text))
            pos = m.end()
        out.append(self._line(base, message[pos:]))
        return "".join(out)

    def _format(self, r: applog.LogRecord) -> str:
        tag = self._tag(r.name)
        stamp_text, tag_text = applog.stamp(r.time) + "  ", f"{tag:<{TAG_WIDTH}} "
        message = self._fit(stamp_text + tag_text, r.message)
        base = theme.hex(LEVEL_TOKEN.get(r.level, "secondary"))
        return (self._line(theme.hex("tertiary"), stamp_text)
                + self._line(theme.hex(TAG_TOKEN.get(tag, "tertiary")), tag_text)
                + self._colored(message, base))

    def _visible(self, r: applog.LogRecord) -> bool:
        return self.level.index() == 0 or r.level >= logging.WARNING

    def _append_record(self, r: applog.LogRecord) -> None:
        if self._visible(r):
            self._append(self._format(r))
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.bus.history)
        limit = self.bus.limit
        self.count.setText(f"{n} / {limit}" if limit else f"{n} message{'s' if n != 1 else ''}")

    def _auto_cleared(self, n: int) -> None:
        self.view.clear()
        self._append(self._line(theme.hex("tertiary"), f"— Cleared automatically after {n} messages —"))

    def _append(self, html_line: str) -> None:
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.view.appendHtml(html_line)
        if at_bottom:
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())

    def rebuild(self) -> None:
        self.view.clear()
        lines = [self._format(r) for r in self.bus.history if self._visible(r)]
        if lines:
            self.view.appendHtml("<br>".join(lines))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        self._update_count()

    def clear(self) -> None:
        self.bus.history.clear()
        self.view.clear()
        self._update_count()

    # ---- commands ------------------------------------------------------------

    def _run(self) -> None:
        cmd = self.prompt.text().strip()
        if not cmd:
            return
        self.prompt.clear()
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
        self._hpos = len(self._history)
        self._append(self._line(theme.hex("accent"), f"› {cmd}"))
        try:
            out = self.execute(cmd)
        except Exception as exc:
            out = f"Error: {exc}"
        if out == "\f":
            self.clear()
        elif out:
            for line in str(out).splitlines():
                self._append(self._line(theme.hex("secondary"), "  " + line))

    def eventFilter(self, obj, e):
        if obj is self.view and e.type() == e.Type.Resize:
            self._refit.start()                         # lines re-fit the new width
        if obj is getattr(self, "prompt", None) and e.type() == e.Type.KeyPress and self._history:
            if e.key() == Qt.Key.Key_Up:
                self._hpos = max(0, self._hpos - 1)
                self.prompt.setText(self._history[self._hpos])
                return True
            if e.key() == Qt.Key.Key_Down:
                self._hpos = min(len(self._history), self._hpos + 1)
                self.prompt.setText(self._history[self._hpos] if self._hpos < len(self._history) else "")
                return True
        return super().eventFilter(obj, e)

    def on_shown(self) -> None:
        self.prompt.setFocus()
