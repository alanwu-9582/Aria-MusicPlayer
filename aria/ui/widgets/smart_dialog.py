"""Rule editor for a Smart Collection: name, rules, and a live count of matching songs."""

from __future__ import annotations

import copy

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from aria.core import smart, textnorm
from aria.core.models import SOURCE_LABELS
from aria.ui.widgets.controls import Button, IconButton, MenuButton, hbox, label

FIELD_KEYS = list(smart.FIELDS)
UNITS = {"days": "days", "times": "times", "minutes": "min"}
LANG_KEYS = list(textnorm.LANGUAGES)
SOURCE_KEYS = ["youtube", "bilibili", "spotify", "soundcloud", "local"]


class RuleRow(QWidget):
    def __init__(self, rule: dict, on_change, on_remove):
        super().__init__()
        self.on_change = on_change
        self.rule = dict(rule)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.field = MenuButton([smart.FIELDS[k][0] for k in FIELD_KEYS],
                                FIELD_KEYS.index(rule["field"]) if rule.get("field") in FIELD_KEYS else 0)
        self.field.setMinimumWidth(220)
        self.field.changed.connect(self._field_changed)
        self.row.addWidget(self.field)
        self.value_box = QHBoxLayout()
        self.value_box.setSpacing(6)
        self.row.addLayout(self.value_box, 1)
        remove = IconButton("close", "Remove Rule", tone="secondary")
        remove.clicked.connect(lambda: on_remove(self))
        self.row.addWidget(remove)
        self._build_value()

    def _field_changed(self, i: int) -> None:
        f = FIELD_KEYS[i]
        self.rule = {"field": f, "value": smart.FIELDS[f][2]}
        self._build_value()
        self.on_change()

    def _clear_value(self) -> None:
        while self.value_box.count():
            item = self.value_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build_value(self) -> None:
        self._clear_value()
        f = self.rule["field"]
        _label, kind, default = smart.FIELDS[f]
        v = self.rule.get("value", default)
        if kind in UNITS:
            edit = QLineEdit(str(v))
            edit.setValidator(QIntValidator(0, 9999, edit))
            edit.setFixedWidth(70)
            edit.textChanged.connect(lambda t: self._set(int(t) if t.isdigit() else 0))
            self.value_box.addWidget(edit)
            self.value_box.addWidget(label(UNITS[kind], "Secondary"))
        elif kind == "text":
            edit = QLineEdit(str(v))
            edit.setPlaceholderText("Text")
            edit.textChanged.connect(self._set)
            self.value_box.addWidget(edit, 1)
        elif kind == "language":
            pick = MenuButton([textnorm.LANGUAGES[k] for k in LANG_KEYS], LANG_KEYS.index(v) if v in LANG_KEYS else 0)
            pick.changed.connect(lambda i: self._set(LANG_KEYS[i]))
            self.value_box.addWidget(pick)
        elif kind == "source":
            pick = MenuButton([SOURCE_LABELS[k] for k in SOURCE_KEYS], SOURCE_KEYS.index(v) if v in SOURCE_KEYS else 0)
            pick.changed.connect(lambda i: self._set(SOURCE_KEYS[i]))
            self.value_box.addWidget(pick)
        self.value_box.addStretch(1)
        self.rule["value"] = v

    def _set(self, v) -> None:
        self.rule["value"] = v
        self.on_change()


class SmartDialog(QDialog):
    def __init__(self, parent, sc: smart.SmartCollection | None, count_matches):
        super().__init__(parent)
        self.setWindowTitle(" ")
        self.setMinimumWidth(560)
        self.sc = copy.deepcopy(sc) if sc else smart.SmartCollection(name="", rules=[{"field": "added_within", "value": 30}])
        self.count_matches = count_matches
        self.rows: list[RuleRow] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(12)
        col.addWidget(label("Edit Smart Collection" if sc else "New Smart Collection", "Title2"))
        self.name = QLineEdit(self.sc.name)
        self.name.setPlaceholderText("Name")
        col.addWidget(self.name)
        self.match = MenuButton(["Match all rules", "Match any rule"], 0 if self.sc.match == "all" else 1)
        self.scope = MenuButton(["Library", "Library and played songs"], 0 if self.sc.scope == "library" else 1)
        self.match.changed.connect(lambda _i: self._changed())
        self.scope.changed.connect(lambda _i: self._changed())
        col.addLayout(hbox(self.match, self.scope, None))
        self.rules_box = QVBoxLayout()
        self.rules_box.setSpacing(8)
        col.addLayout(self.rules_box)
        add = Button("Add Rule", "borderless", icon="plus")
        add.clicked.connect(lambda: self._add_row({"field": "language", "value": "ja"}, changed=True))
        col.addLayout(hbox(add, None))
        col.addSpacing(4)
        self.preview = label("", "Caption")
        cancel = Button("Cancel")
        self.ok = Button("Save" if sc else "Create", "primary")
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        self.ok.setDefault(True)
        col.addLayout(hbox(self.preview, None, cancel, self.ok))
        for r in self.sc.rules:
            self._add_row(r)
        self.name.textChanged.connect(lambda _t: self._validate())
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._update_preview)
        self._changed()
        self.name.setFocus()

    def _add_row(self, rule: dict, changed: bool = False) -> None:
        row = RuleRow(rule, self._changed, self._remove_row)
        self.rows.append(row)
        self.rules_box.addWidget(row)
        if changed:
            self._changed()

    def _remove_row(self, row: RuleRow) -> None:
        self.rows.remove(row)
        row.deleteLater()
        self._changed()

    def result_collection(self) -> smart.SmartCollection:
        self.sc.name = self.name.text().strip() or self._suggest_name()
        self.sc.rules = [dict(r.rule) for r in self.rows]
        self.sc.match = "all" if self.match.index() == 0 else "any"
        self.sc.scope = "library" if self.scope.index() == 0 else "all"
        return self.sc

    def _suggest_name(self) -> str:
        return " · ".join(smart.describe(r.rule) for r in self.rows[:2]) or "Smart Collection"

    def _changed(self) -> None:
        self._validate()
        self._preview_timer.start()

    def _validate(self) -> None:
        self.ok.setEnabled(bool(self.rows))

    def _update_preview(self) -> None:
        n = self.count_matches(self.result_collection())
        self.preview.setText(f"{n:,} song{'s' if n != 1 else ''} match")
        self.name.setPlaceholderText(self._suggest_name())


def edit_smart(parent, sc, count_matches) -> smart.SmartCollection | None:
    dlg = SmartDialog(parent, sc, count_matches)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.result_collection()
    return None

