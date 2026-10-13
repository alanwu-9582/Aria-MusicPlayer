"""Edit Song Info and Choose Lyrics: two small sheets for when Aria read a song wrong.

Edit Song Info shows what Aria detected (as placeholders) and keeps only what the listener
types — title, artist, series, version, language — per song, until Reset to Detected. Choose
Lyrics searches LRCLIB with an editable query and lists what it finds (synced or plain, length
against the song, language, the first lines); the pick is remembered for this song until Reset
to Automatic. Searching runs in the background; nothing here blocks playback.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout)

from aria.core import identity, langid, tasks
from aria.core.models import format_duration
from aria.ui.widgets.controls import Button, MenuButton, label

LANGS = [("", "Detect Automatically"), ("ja", "Japanese"), ("zh", "Chinese"), ("ko", "Korean"), ("en", "English")]


def _sheet(parent, title: str, width: int = 460) -> tuple[QDialog, QVBoxLayout]:
    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dlg.setMinimumWidth(width)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(22, 20, 22, 18)
    root.setSpacing(10)
    root.addWidget(label(title, "Title3"))
    return dlg, root


def edit_song_info(parent, track) -> bool:
    """True when the listener changed (or reset) this song's info."""
    detected = identity.parse(track.title, track.artist) if track.source != "spotify" else identity.of(track)
    mine = identity.overrides().get(track.key)
    dlg, root = _sheet(parent, "Edit Song Info")
    hint = label(f"From “{track.title}”" + (f" · {track.artist}" if track.artist else ""), "Caption")
    hint.setWordWrap(True)
    root.addWidget(hint)
    form = QFormLayout()
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    fields = {}
    for name, text, shown in (("title", "Title", detected.title), ("artist", "Artist", detected.artist),
                              ("franchise", "Series", detected.franchise), ("version", "Version", detected.version)):
        e = QLineEdit(mine.get(name, ""))
        e.setPlaceholderText(shown or "—")
        fields[name] = e
        form.addRow(label(text, "Secondary"), e)
    langs = MenuButton([n for _c, n in LANGS], next((i for i, (c, _n) in enumerate(LANGS) if c == mine.get("language")), 0))
    form.addRow(label("Language", "Secondary"), langs)
    root.addLayout(form)
    g = identity.of(track)
    if g.language and g.language_source != "user":
        root.addWidget(label(f"Detected language: {langid.NAMES.get(g.language, g.language)} "
                             f"({g.language_source}, {round(100 * g.language_confidence)} %)", "Caption"))
    row = QHBoxLayout()
    reset = Button("Reset to Detected", "borderless")
    reset.setEnabled(bool(mine))
    cancel, save = Button("Cancel"), Button("Save", "primary")
    row.addWidget(reset)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(save)
    root.addSpacing(6)
    root.addLayout(row)
    result = {"changed": False}

    def do_reset():
        identity.overrides().reset(track.key)
        result["changed"] = True
        dlg.accept()

    def do_save():
        values = {k: e.text() for k, e in fields.items()}
        values["language"] = LANGS[langs.index()][0]
        if values != {k: mine.get(k, "") for k in values}:
            identity.overrides().set(track.key, values)
            result["changed"] = True
        dlg.accept()
    reset.clicked.connect(do_reset)
    cancel.clicked.connect(dlg.reject)
    save.clicked.connect(do_save)
    save.setDefault(True)
    dlg.exec()
    return result["changed"]


def choose_lyrics(parent, track, chosen: bool) -> tuple[str, dict | None] | None:
    """("apply", candidate) / ("reset", None) / None (cancelled)."""
    from aria.core import lyrics as lyrics_mod
    dlg, root = _sheet(parent, "Choose Lyrics", 560)
    ident = identity.of(track)
    query = QLineEdit(f"{ident.title} {ident.artist}".strip())
    find = Button("Search", "secondary")
    bar = QHBoxLayout()
    bar.addWidget(query, 1)
    bar.addWidget(find)
    root.addLayout(bar)
    status = label("", "Caption")
    root.addWidget(status)
    results = QListWidget()
    results.setMinimumHeight(220)
    root.addWidget(results, 1)
    preview = label("", "Secondary")
    preview.setWordWrap(True)
    preview.setMinimumHeight(64)
    root.addWidget(preview)
    row = QHBoxLayout()
    reset = Button("Reset to Automatic", "borderless")
    reset.setEnabled(chosen)
    cancel, apply = Button("Cancel"), Button("Apply", "primary")
    apply.setEnabled(False)
    row.addWidget(reset)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(apply)
    root.addLayout(row)
    found: list[dict] = []
    ticket = tasks.Latest()
    out: dict = {"v": None}

    def run():
        q = query.text().strip()
        if not q:
            return
        n = ticket.next()
        status.setText("Searching…")
        results.clear()
        apply.setEnabled(False)

        def done(items):
            if not ticket.is_current(n) or not dlg.isVisible():
                return
            found[:] = sorted(items, key=lambda c: (c["romanized"], c["diff"] if c["diff"] is not None else 99,
                                                    not c["synced"]))
            for c in found:
                bits = ["Synced" if c["synced"] else "Plain"]
                if c["duration"]:
                    bits.append(format_duration(c["duration"]) + (f" (±{round(c['diff'])} s)" if c["diff"] is not None else ""))
                if c["lang"]:
                    bits.append(langid.NAMES.get(c["lang"], c["lang"]) + (" · Romanized" if c["romanized"] else ""))
                item = QListWidgetItem(f"{c['title']} — {c['artist']}\n{' · '.join(bits)}")
                results.addItem(item)
            status.setText(f"{len(found)} found" if found else "Nothing found — try another spelling")
            if found:
                results.setCurrentRow(0)

        tasks.run(lambda: lyrics_mod.search_lrclib(q, track), done,
                  lambda e: ticket.is_current(n) and status.setText(f"Couldn’t search: {e}"))

    def picked(row: int):
        ok = 0 <= row < len(found)
        apply.setEnabled(ok)
        preview.setText("\n".join(found[row]["preview"]) if ok else "")

    def do_apply():
        row = results.currentRow()
        if 0 <= row < len(found):
            out["v"] = ("apply", found[row])
            dlg.accept()

    def do_reset():
        out["v"] = ("reset", None)
        dlg.accept()
    find.clicked.connect(run)
    query.returnPressed.connect(run)
    results.currentRowChanged.connect(picked)
    results.itemDoubleClicked.connect(lambda _i: do_apply())
    apply.clicked.connect(do_apply)
    reset.clicked.connect(do_reset)
    cancel.clicked.connect(dlg.reject)
    run()
    dlg.exec()
    return out["v"]
