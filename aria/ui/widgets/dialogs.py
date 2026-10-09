"""Alert sheet (§4.12): icon left, title3 + one secondary line, buttons right.
Dangerous confirmations default to Cancel so Enter never destroys anything."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aria.ui import icons
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, label


def confirm(parent: QWidget, title: str, text: str, action: str, danger: bool = False) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dlg.setMinimumWidth(420)
    dlg.setMaximumWidth(560)

    root = QHBoxLayout(dlg)
    root.setContentsMargins(22, 20, 22, 18)
    root.setSpacing(14)

    icon = QLabel()
    icon.setPixmap(icons.pixmap("alert" if danger else "info",
                                theme.color("red" if danger else "accent"), 30))
    icon.setFixedSize(30, 30)
    root.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

    col = QVBoxLayout()
    col.setSpacing(6)
    col.addWidget(label(title, "Title3"))
    body = label(text, "Secondary")
    body.setWordWrap(True)
    col.addWidget(body)
    col.addSpacing(8)

    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    buttons.addStretch(1)
    cancel = Button("取消")
    ok = Button(action, "destructive" if danger else "primary")
    cancel.clicked.connect(dlg.reject)
    ok.clicked.connect(dlg.accept)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    col.addLayout(buttons)
    root.addLayout(col, 1)

    (cancel if danger else ok).setDefault(True)
    (cancel if danger else ok).setFocus()
    ok.setAutoDefault(not danger)
    return dlg.exec() == QDialog.DialogCode.Accepted
