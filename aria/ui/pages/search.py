"""Search: one field for keywords or links (YouTube, Bilibili, Spotify, SoundCloud, …)."""

from __future__ import annotations

import logging

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLineEdit

from aria import providers
from aria.core import tasks
from aria.ui import icons
from aria.ui.pages.base import Page
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, label
from aria.ui.widgets.segmented import Segmented
from aria.ui.widgets.tracklist import RowAction, TrackListView

log = logging.getLogger(__name__)

SOURCES = providers.SEARCHABLE                       # youtube, bilibili, soundcloud
SOURCE_NAMES = ["YouTube", "Bilibili", "SoundCloud"]


class SearchPage(Page):
    def __init__(self, library, actions, settings):
        super().__init__("Search")
        self.library = library
        self.actions = actions
        self.settings = settings
        self._latest = tasks.Latest()
        self._from_link = False

        bar = self.toolbar()
        self.field = QLineEdit()
        self.field.setPlaceholderText("Songs, artists or a link")
        self.field.setClearButtonEnabled(True)
        self._search_icon = QAction(self.field)
        self.field.addAction(self._search_icon, QLineEdit.ActionPosition.LeadingPosition)
        self.field.returnPressed.connect(self.run)
        self.field.textChanged.connect(lambda _t: self.field.setProperty("error", False) or self._repolish())
        bar.addWidget(self.field, 1)

        self.source = Segmented(SOURCE_NAMES, compact=True)
        current = settings["search_source"]
        self.source.set_index(SOURCES.index(current) if current in SOURCES else 0)
        self.source.changed.connect(self._source_changed)
        bar.addWidget(self.source)

        self.status = label("", "Caption")
        self.add_all = Button("Add All to Queue", "borderless", icon="queue-add")
        self.add_all.clicked.connect(lambda: self.actions.enqueue(self.results.tracks()))
        self.save_all = Button("Save All", "borderless", icon="heart")
        self.save_all.clicked.connect(lambda: self.actions.save(self.results.tracks()))
        row = self.toolbar()
        row.addWidget(self.status)
        row.addStretch(1)
        row.addWidget(self.add_all)
        row.addWidget(self.save_all)

        self.results = TrackListView("search", "Search for music", "Type a name, or paste a song or playlist link.")
        saved = lambda t: t.key in self.library  # noqa: E731
        self.results.actions = [
            RowAction("queue-add", "Add to Queue", lambda r: actions.enqueue([self.results.tracks()[r]])),
            RowAction("heart", "Save", lambda r: actions.toggle_saved(self.results.tracks()[r]), saved),
        ]
        self.results.activated_row.connect(lambda r: actions.play([self.results.tracks()[r]]))
        self.results.context_requested.connect(
            lambda rows, pos: actions.menu(self, [self.results.tracks()[r] for r in rows]).exec(pos))
        library.changed.connect(self.results.viewport().update)
        self.root.addWidget(self.results, 1)

        theme.changed.connect(self._retint)
        self._retint()
        self._update_bar()

    def _retint(self) -> None:
        self._search_icon.setIcon(icons.icon("search", theme.color("tertiary"), 15))

    def _repolish(self) -> None:
        self.field.style().unpolish(self.field)
        self.field.style().polish(self.field)

    def _source_changed(self, i: int) -> None:
        self.settings["search_source"] = SOURCES[i]
        if self.field.text().strip() and not providers.is_link(self.field.text()):
            self.run()

    def focus(self) -> None:
        self.field.setFocus()
        self.field.selectAll()

    def on_shown(self) -> None:
        if not self.field.text():
            self.field.setFocus()

    def run(self, text: str | None = None) -> None:
        query = (text if text is not None else self.field.text()).strip()
        if text is not None:
            self.field.setText(query)
        if not query:
            return
        ticket = self._latest.next()
        is_link = self._from_link = providers.is_link(query)
        source = SOURCES[self.source.index()]
        self.status.setText("Reading link…" if is_link else "Searching…")
        self.add_all.hide()
        self.save_all.hide()
        log.info("%s: %s", "Link" if is_link else f"Search {SOURCE_NAMES[self.source.index()]}", query)

        def work():
            return providers.parse(query) if is_link else providers.search(source, query, 25)

        def done(tracks):
            if not self._latest.is_current(ticket):
                return
            self.results.set_tracks(tracks)
            self.results.scrollToTop()
            if tracks and TrackListView.warm_hook:
                TrackListView.warm_hook(tracks[0])      # the likeliest pick
            log.info("Found %d", len(tracks))
            self._update_bar()

        def failed(exc):
            if not self._latest.is_current(ticket):
                return
            log.error("Search failed: %s", exc)
            self.field.setProperty("error", True)
            self._repolish()
            self.results.set_tracks([])
            self.status.setText("Nothing found" if isinstance(exc, ValueError) else "Couldn’t connect")

        tasks.run(work, done, failed)

    def _update_bar(self) -> None:
        n = len(self.results.tracks())
        self.status.setText(f"{n:,} result{'s' if n != 1 else ''}" if n else "")
        multi = n > 1 and self._from_link        # a playlist / album link
        self.add_all.setVisible(multi)
        self.save_all.setVisible(multi)
