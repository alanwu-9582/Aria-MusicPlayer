"""Console: live log plus a command line for power users."""

from __future__ import annotations

import html
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

from aria import log as applog
from aria.ui.pages.base import Page
from aria.ui.theme import font, theme
from aria.ui.widgets.controls import Button
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import reserve_scrollbar

LEVEL_TOKEN = {logging.DEBUG: "tertiary", logging.INFO: "label", logging.WARNING: "orange",
               logging.ERROR: "red", logging.CRITICAL: "red"}


class ConsolePage(Page):
    def __init__(self, bus: applog.LogBus, execute):
        super().__init__("主控台")
        self.bus = bus
        self.execute = execute
        self._history: list[str] = []
        self._hpos = 0

        clear = Button("清除", "borderless", icon="trash", tooltip="清除畫面上的紀錄")
        clear.clicked.connect(self.clear)
        self.header.addWidget(clear)

        bar = self.toolbar()
        self.level = Segmented(["全部", "警告"], compact=True, tooltips=["顯示全部紀錄", "只顯示警告與錯誤"])
        self.level.changed.connect(lambda _i: self.rebuild())
        bar.addWidget(self.level)
        bar.addStretch(1)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.view.setFont(font("caption", mono=True))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.view.setObjectName("Card")
        self.view.document().setDocumentMargin(12)
        reserve_scrollbar(self.view)
        self.root.addWidget(self.view, 1)

        self.prompt = QLineEdit()
        self.prompt.setFont(font("callout", mono=True))
        self.prompt.setPlaceholderText("指令（help）")
        self.prompt.setToolTip("輸入指令後按 Enter；↑ ↓ 叫出先前的指令。例如：play 晴天、vol 50、auto off")
        self.prompt.returnPressed.connect(self._run)
        self.prompt.installEventFilter(self)
        self.root.addWidget(self.prompt)

        bus.record.connect(self._append_record)
        theme.changed.connect(self.rebuild)
        self.rebuild()

    # ---- log -----------------------------------------------------------------

    def _line(self, color: str, text: str) -> str:
        return f'<span style="color:{color}; white-space:pre-wrap">{html.escape(text)}</span>'

    def _format(self, r: applog.LogRecord) -> str:
        stamp = self._line(theme.hex("tertiary"), applog.stamp(r.time) + "  ")
        return stamp + self._line(theme.hex(LEVEL_TOKEN.get(r.level, "label")), r.message)

    def _visible(self, r: applog.LogRecord) -> bool:
        return self.level.index() == 0 or r.level >= logging.WARNING

    def _append_record(self, r: applog.LogRecord) -> None:
        if self._visible(r):
            self._append(self._format(r))

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

    def clear(self) -> None:
        self.bus.history.clear()
        self.view.clear()

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
            out = f"錯誤：{exc}"
        if out == "\f":
            self.clear()
        elif out:
            for line in str(out).splitlines():
                self._append(self._line(theme.hex("secondary"), "  " + line))

    def eventFilter(self, obj, e):
        if obj is self.prompt and e.type() == e.Type.KeyPress and self._history:
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
