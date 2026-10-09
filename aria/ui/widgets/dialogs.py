"""Alert sheet (§4.12): icon left, title3 + one secondary line, buttons right.
Dangerous confirmations default to Cancel so Enter never destroys anything."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from aria.ui import icons
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, CheckBox, label


def confirm(parent: QWidget, title: str, text: str, action: str, danger: bool = False,
            option: str | None = None) -> bool | tuple[bool, bool]:
    """Ask before acting. With ``option`` a checkbox is shown and (accepted, checked) is returned."""
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
    box = None
    if option:
        box = CheckBox(option)
        col.addWidget(box)
    col.addSpacing(8)

    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    buttons.addStretch(1)
    cancel = Button("Cancel")
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
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    return (accepted, bool(box and box.isChecked())) if option else accepted


def prompt(parent: QWidget, title: str, placeholder: str, action: str, initial: str = "") -> str | None:
    """One-field sheet: title2, field, Cancel / action (disabled until something is typed)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setFixedWidth(420)
    col = QVBoxLayout(dlg)
    col.setContentsMargins(22, 20, 22, 18)
    col.setSpacing(14)
    col.addWidget(label(title, "Title2"))
    field = QLineEdit(initial)
    field.setPlaceholderText(placeholder)
    field.selectAll()
    col.addWidget(field)
    buttons = QHBoxLayout()
    buttons.setSpacing(8)
    buttons.addStretch(1)
    cancel = Button("Cancel")
    ok = Button(action, "primary")
    cancel.clicked.connect(dlg.reject)
    ok.clicked.connect(dlg.accept)
    ok.setDefault(True)
    ok.setEnabled(bool(initial.strip()))
    field.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    col.addLayout(buttons)
    field.setFocus()
    if dlg.exec() == QDialog.DialogCode.Accepted and field.text().strip():
        return field.text().strip()
    return None
