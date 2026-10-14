"""Edit Song Info and Choose Lyrics: two small sheets for when Aria read a song wrong.

Edit Song Info shows what Aria detected (as placeholders) and keeps only what the listener
types — title, artist, series, version, language — per song, until Reset to Detected.

Choose Lyrics searches LRCLIB with an editable query. Results on the left say what each is —
Word Synced, Line Synced or Plain, its length against the song's, its language; the lyrics of
the selected one fill the right side. For the song that's playing, the preview follows it — the
line being sung is lit — so a timeline can be checked by ear before it's applied. The pick is remembered for this song until Reset to Automatic. Searching
runs in the background; nothing here blocks playback.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)

from aria.core import identity, langid, tasks
from aria.core.models import format_duration
from aria.ui.theme import theme
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
        e = QLineEdit(mine.get(name, shown or ""))         # filled in: edit what's there
        fields[name] = (e, shown or "")
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
        # only what differs from the detected info is kept, so the rest follows better detection later
        values = {k: e.text().strip() for k, (e, auto) in fields.items() if e.text().strip() != auto.strip()}
        if LANGS[langs.index()][0]:
            values["language"] = LANGS[langs.index()][0]
        if values != {k: v for k, v in mine.items() if v}:
            identity.overrides().set(track.key, values)
            result["changed"] = True
        dlg.accept()
    reset.clicked.connect(do_reset)
    cancel.clicked.connect(dlg.reject)
    save.clicked.connect(do_save)
    save.setDefault(True)
    dlg.exec()
    return result["changed"]


def _pill(text: str, strong: bool) -> QLabel:
    """A small tag: the kind of lyrics (Word Synced stands out)."""
    tag = QLabel(text)
    fg = theme.color("accent" if strong else "secondary").name()
    bg = theme.color("accent_soft") if strong else theme.color("fill")
    tag.setStyleSheet(f"QLabel {{ color: {fg}; background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
                      f" border-radius: 4px; padding: 1px 6px; font-size: 11px; font-weight: 600; }}")
    return tag


def _result_row(c: dict) -> QWidget:
    """One result: name and artist, then its kind, length and language."""
    row = QWidget()
    lay = QVBoxLayout(row)
    lay.setContentsMargins(10, 7, 10, 7)
    lay.setSpacing(3)
    name = label(c["title"] or "—", "Callout")
    name.setToolTip(f"{c['title']} — {c['artist']}" + (f"\n{c['album']}" if c.get("album") else ""))
    lay.addWidget(name)
    who = label(" · ".join(x for x in (c["artist"], c.get("album")) if x), "Caption")
    lay.addWidget(who)
    facts = QHBoxLayout()
    facts.setSpacing(6)
    facts.addWidget(_pill(c["kind"], c["kind"] == "Word Synced"))
    bits = []
    if c["duration"]:
        bits.append(format_duration(c["duration"]) + (f" (±{round(c['diff'])} s)" if c["diff"] is not None else ""))
    if c["lang"]:
        bits.append(langid.NAMES.get(c["lang"], c["lang"]) + (" · Romanized" if c["romanized"] else ""))
    facts.addWidget(label(" · ".join(bits), "Caption"))
    facts.addStretch(1)
    lay.addLayout(facts)
    return row


def choose_lyrics(parent, track, chosen: bool, playback=None) -> tuple[str, dict | None] | None:
    """("apply", candidate) / ("reset", None) / None (cancelled)."""
    from aria.core import lyrics as lyrics_mod
    from aria.ui.widgets.lyrics_view import LyricsView
    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dlg.resize(900, 580)
    root = QHBoxLayout(dlg)
    root.setContentsMargins(22, 20, 22, 18)
    root.setSpacing(18)

    # ---- left: search and results
    left = QVBoxLayout()
    left.setSpacing(8)
    left.addWidget(label("Choose Lyrics", "Title3"))
    ident = identity.of(track)
    left.addWidget(label(f"For “{ident.title}”" + (f" · {ident.artist}" if ident.artist else ""), "Caption"))
    query = QLineEdit(ident.title)
    query.setPlaceholderText("Song name")
    find = Button("Search", "secondary")
    bar = QHBoxLayout()
    bar.setSpacing(6)
    bar.addWidget(query, 1)
    bar.addWidget(find)
    left.addLayout(bar)
    status = label("", "Caption")
    left.addWidget(status)
    results = QListWidget()
    results.setMinimumWidth(340)
    results.setSpacing(1)
    soft, hover = theme.color("accent_soft"), theme.color("fill")
    results.setStyleSheet(          # a soft wash for the picked row: its text stays as readable as the others'
        f"QListWidget::item {{ border-radius: 6px; }}"
        f"QListWidget::item:selected {{ background: rgba({soft.red()},{soft.green()},{soft.blue()},{soft.alpha()}); }}"
        f"QListWidget::item:hover:!selected {{ background: rgba({hover.red()},{hover.green()},{hover.blue()},{hover.alpha()}); }}")
    left.addWidget(results, 1)
    reset = Button("Reset to Automatic", "borderless")
    reset.setEnabled(chosen)
    left.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
    root.addLayout(left, 5)

    # ---- right: the preview
    right = QVBoxLayout()
    right.setSpacing(6)
    head = label("Preview", "Headline")
    right.addWidget(head)
    preview = LyricsView()
    preview.clickable = False                       # a preview: it follows the song, it doesn't steer it
    preview.setMinimumWidth(380)
    right.addWidget(preview, 1)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel, apply = Button("Cancel"), Button("Apply", "primary")
    apply.setEnabled(False)
    buttons.addWidget(cancel)
    buttons.addWidget(apply)
    right.addLayout(buttons)
    root.addLayout(right, 6)

    playing = playback is not None and playback.current is not None and playback.current.key == track.key
    offset = lyrics_mod.prefs().offset(track.key)
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
        preview.set_state("loading")

        def done(items):
            if not ticket.is_current(n) or not dlg.isVisible():
                return
            found[:] = sorted(items, key=lambda c: (c["romanized"], c["diff"] if c["diff"] is not None else 99,
                                                    {"Word Synced": 0, "Line Synced": 1}.get(c["kind"], 2)))
            for c in found:
                item = QListWidgetItem()
                row = _result_row(c)
                item.setSizeHint(QSize(row.sizeHint().width(), row.sizeHint().height() + 2))
                results.addItem(item)
                results.setItemWidget(item, row)
            status.setText(f"{len(found)} found" if found else "Nothing found — try another spelling")
            if found:
                results.setCurrentRow(0)
            else:
                preview.set_state("none")

        tasks.run(lambda: lyrics_mod.search_lrclib(q, track), done,
                  lambda e: ticket.is_current(n) and status.setText(f"Couldn’t search: {e}"))

    def picked(row: int):
        ok = 0 <= row < len(found)
        apply.setEnabled(ok)
        if not ok:
            return
        c = found[row]
        lyr = c["lyrics"]
        lyr.offset = offset
        preview.set_state("ready", lyr)
        head.setText(f"Preview · {c['kind']}")
        if playing and lyr.synced:
            preview.set_position(playback.player.position())

    def on_position(pos, _length):
        if playing and preview.lyrics is not None and preview.lyrics.synced:
            preview.set_position(pos)

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
    if playing:
        playback.position_changed.connect(on_position)
    run()
    dlg.exec()
    if playing:
        playback.position_changed.disconnect(on_position)
    return out["v"]
