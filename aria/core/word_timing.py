"""Word-by-Word Everything: estimated word times for lyrics that are only timed line by line.

The speech model (Forced Alignment) listens to the song and tells where each word falls; the
lines keep the times they came with (see ``lyrics.merge_words``). It's heavy work, so it's
kept apart from everything else: one song at a time, on the analysis worker (idle priority), in
the "bulk" lane — the song playing first, downloads after it. The result is remembered per song
(and per file), and a downloaded song gets it written into its .lrc.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from aria.core import tasks
from aria.core.models import Track

log = logging.getLogger("lyrics")


class WordTimer(QObject):
    done = Signal(str, object)           # song key, the lyrics with word times
    busy_changed = Signal(str)           # the song being worked on ("" = none)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._queue: list[tuple[Track, object, str, bool]] = []
        self.busy = ""

    def ready(self) -> bool:
        from aria.core import aligner
        return bool(self.settings["lyrics_words_all"]) and aligner.engine_available() \
            and aligner.installed(self.settings["align_model"])

    def wanted(self, lyr) -> bool:
        """Lyrics timed by line only (and the setting on, a model there)."""
        from aria.core import lyrics
        return lyr is not None and lyr.sync == lyrics.SYNCED and not lyr.word_timed and self.ready()

    def request(self, track: Track, lyr, path: str, front: bool = False, temp: bool = False) -> None:
        """Estimate ``track``'s word times from the audio at ``path`` (a temporary copy: ``temp``)."""
        if not self.wanted(lyr) or not path:
            return
        if track.key == self.busy or any(t.key == track.key for t, *_ in self._queue):
            return                                           # one job per song
        job = (track, lyr, path, temp)
        if front:
            self._queue.insert(0, job)
        else:
            self._queue.append(job)
        if not self.busy:
            self._next()

    def _next(self) -> None:
        if not self._queue:
            self.busy = ""
            self.busy_changed.emit("")
            return
        track, lyr, path, temp = self._queue.pop(0)
        self.busy = track.key
        self.busy_changed.emit(track.key)
        from aria.core import aligner, langid, lyrics
        lines = [s for _t, s in lyr.lines]
        lang = aligner.WHISPER_LANG.get(langid.detect(" ".join(w for w in lines[:24] if w)).lang)
        model = self.settings["align_model"]

        def work():
            return aligner.worker().submit(aligner.align_file, path, lines, model, lang).result(timeout=1800)

        def finish(result):
            merged = None
            if result:
                result = dict(result, temp=True) if temp else result
                merged = lyrics.save_word_estimate(track, lyr, result)
                if merged.words_estimated:
                    log.info("Word timing · “%s” · %d%% of lines", track.title, round(100 * result.get("confidence", 0)))
                    if track.local_path and not temp:
                        lyrics.write_beside(track, merged, replace=True)     # into the song's .lrc (Aria's own)
                else:
                    merged = None
            if merged is None:
                log.warning("Couldn’t time the words of “%s”", track.title)
            self.done.emit(track.key, merged)
            self._next()

        def failed(exc):
            log.warning("Word timing failed for “%s”: %s", track.title, exc)
            finish(None)

        tasks.run(work, finish, failed, lane="bulk")
