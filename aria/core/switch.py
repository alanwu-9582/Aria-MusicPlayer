"""Switch Source: move saved songs between YouTube and Spotify.

YouTube → Spotify looks the song up on the web (Spotify has no public search without an
account), then checks each candidate against Spotify's own page — the title must be the same
song and the length close — before accepting it. The Spotify track keeps playing the very
video it came from, so the sound and any download stay as they were.

Spotify → YouTube uses the match Aria already plays (or finds one the usual way).

Any copy of a song can be switched — saved, queued, recommended, in the history or playing
right now (that one carries on without a break). Everything the song carries moves with it:
its place in the library, playlists, queue and history, the download, listening stats and
the measured BPM.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal

from aria.core import tasks, textnorm
from aria.core.models import SOURCE_LABELS, SPOTIFY, YOUTUBE, Track

log = logging.getLogger("switch")

MAX_CANDIDATES = 4
PAUSE = 0.8                     # seconds between songs: a web search per song, politely spaced


def target_of(track: Track) -> str | None:
    """Where a song can be switched to, if anywhere."""
    return SPOTIFY if track.source == YOUTUBE else YOUTUBE if track.source == SPOTIFY else None


# ---- matching (blocking: run off the GUI thread) ---------------------------------------

def _song(track: Track) -> tuple[str, str]:
    """(song name, artist) behind a video title (the listener's corrections first)."""
    from aria.core import identity
    ident = identity.of(track)
    return ident.title, ident.artist or identity.clean_uploader(track.artist)


def _spotify_candidates(name: str, artist: str) -> list[str]:
    from aria import providers
    for q in (f"{artist} {name} spotify", f"{name} {artist} open.spotify.com track", f"{name} spotify"):
        found = providers.spotify.find_links(q, "track")
        if found:
            return found[:MAX_CANDIDATES]
    return []


def _score(want: Track, name: str, artist: str, cand: Track) -> float | None:
    """How well a Spotify track fits the video; None when it's clearly another recording."""
    same = textnorm.same_song(want.title, want.artist, cand.title, cand.artist) \
        or textnorm.similarity(name, cand.title) >= 0.85
    if not same:
        return None
    if textnorm.is_variant(cand.title) != textnorm.is_variant(want.title):
        return None                                     # a remix / live take isn't the original
    score = 3.0
    if artist and (textnorm.similarity(artist, cand.artist) > 0.6
                   or textnorm.artist_key(cand.artist.split(",")[0]) in textnorm.artist_key(want.title + want.artist)):
        score += 1.5
    if want.duration and cand.duration:
        gap = abs(want.duration - cand.duration)
        if gap > 25:                                    # a music video with a long intro, or another edit
            return None
        score -= gap / 10
    return score


def find_spotify(track: Track) -> Track | None:
    from aria import providers
    name, artist = _song(track)
    best, best_score = None, None
    for sid in _spotify_candidates(name, artist):
        try:
            cand = providers.spotify.parse(f"https://open.spotify.com/track/{sid}")[0]
        except Exception:
            log.debug("  ✗ %s unreadable", sid)
            continue
        s = _score(track, name, artist, cand)
        if s is None:
            log.debug("  ✗ “%s” · %s", cand.title, cand.artist)
        else:
            log.debug("  ✓ “%s” · %s · %.1f", cand.title, cand.artist, s)
        if s is not None and (best_score is None or s > best_score):
            best, best_score = cand, s
    if best is None:
        return None
    best.match_id = track.id                            # keeps playing the same video
    return best


def find_youtube(track: Track) -> Track | None:
    from aria import providers
    from aria.providers.youtube import watch_url
    m = providers.spotify.youtube_match(track)
    if not m.url or not m.thumbnail:
        m = providers.youtube.parse(watch_url(m.id))[0]
    return m


def find(track: Track) -> Track | None:
    return find_spotify(track) if track.source == YOUTUBE else find_youtube(track)


# ---- the switch itself ------------------------------------------------------------------

class SourceSwitcher(QObject):
    """Switches songs one after another in the background; the library changes on the GUI thread."""

    progress = Signal(int, int, str)      # done, total, title
    finished = Signal(int, list, str)     # switched, titles not found, target source
    switched = Signal(str, object)        # old key, the new track (lists showing the old one update)

    def __init__(self, library, playlists, playback):
        super().__init__()
        self.library = library
        self.playlists = playlists
        self.pb = playback
        self._todo: list[Track] = []
        self._total = 0
        self._switched = 0
        self._missing: list[str] = []
        self._target = ""

    @property
    def busy(self) -> bool:
        return bool(self._todo) or self._total > 0

    def start(self, tracks: list[Track], target: str) -> int:
        """Queue songs for switching to ``target``; returns how many can be."""
        todo = [t for t in tracks if target_of(t) == target]
        if not todo or self.busy:
            return 0
        self._todo, self._total, self._switched, self._missing, self._target = list(todo), len(todo), 0, [], target
        log.info("→ %s · %d song%s", SOURCE_LABELS[target], len(todo), "s" if len(todo) != 1 else "")
        self._next()
        return len(todo)

    def _next(self) -> None:
        if not self._todo:
            total, self._total = self._total, 0
            log.info("Switch done · %d/%d", self._switched, total)
            self.finished.emit(self._switched, self._missing, self._target)
            return
        t = self._todo.pop(0)
        self.progress.emit(self._total - len(self._todo) - 1, self._total, t.title)
        first = len(self._todo) == self._total - 1

        def work():
            if not first:
                time.sleep(PAUSE)
            return find(t)

        def done(new):
            try:
                if new is None:
                    log.warning("No %s match · “%s”", SOURCE_LABELS[self._target], t.title)
                    self._missing.append(t.title)
                else:
                    self.apply(t, new)
                    self._switched += 1
            except Exception as exc:                  # one song going wrong never stalls the rest
                log.error("Couldn’t switch “%s”: %s", t.title, exc)
                self._missing.append(t.title)
            finally:
                self._next()

        def failed(exc):
            log.warning("Couldn’t switch “%s”: %s", t.title, exc)
            self._missing.append(t.title)
            self._next()

        tasks.run(work, done, failed)

    def apply(self, old: Track, new: Track) -> None:
        """Put ``new`` everywhere ``old`` was, keeping what belongs to the song."""
        old_key, old_label = old.key, old.source_label    # ``old`` may be the playing song, changed in place
        saved = self.library.get(old_key) or old
        new.local_path = saved.local_path or old.local_path
        new.added_at = saved.added_at
        log.info("“%s” %s → %s", new.title, old_label, new.source_label)
        if self.pb.listening is not None:
            self.pb.listening.rekey(old_key, new)
        if self.pb.tempo is not None:
            self.pb.tempo.rekey(old_key, new.key)
        from aria.core import identity
        identity.overrides().move(old_key, new.key)        # corrected song info goes with the song
        self.library.replace(old_key, new)
        self.playlists.replace(old_key, new)
        self.pb.replace_track(old_key, new)
        self.switched.emit(old_key, new)
