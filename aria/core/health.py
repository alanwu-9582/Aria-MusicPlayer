"""Collection Health: find what's out of order in the library and suggest a fix.

The local checks are instant and run without the network:
- downloaded files that no saved song points to (suggest: add them to the library);
- saved songs whose downloaded file is gone (suggest: forget the file);
- the same song saved more than once (suggest: keep one);
- the same song downloaded more than once (suggest: delete the extra copies);
- lyric files (.lrc / .ttml) left without their song (suggest: delete them).
Checking that links still play needs the network, so it's a separate, slower step.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from aria import paths
from aria.core import textnorm
from aria.core.models import LOCAL, Track

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm", ".wma", ".mka"}
_ID_TAG = re.compile(r"\[([^\[\]]{6,40})\]\s*$")

ORPHAN, MISSING, DUPLICATE, EXTRA_COPY, BROKEN = "orphan", "missing", "duplicate", "extra_copy", "broken"
LYRICS_ONLY = "lyrics_only"
LYRIC_EXTS = {".lrc", ".ttml"}
GROUP_TITLES = {
    ORPHAN: "Downloaded but not in the Library",
    MISSING: "Missing downloaded files",
    DUPLICATE: "Saved more than once",
    EXTRA_COPY: "Downloaded more than once",
    BROKEN: "Links that no longer play",
    LYRICS_ONLY: "Lyrics without their song",
}
FIX_LABELS = {ORPHAN: "Delete", MISSING: "Forget File", DUPLICATE: "Keep One",
              EXTRA_COPY: "Delete Extra", BROKEN: "Remove", LYRICS_ONLY: "Delete"}


@dataclass
class Issue:
    kind: str
    title: str
    detail: str
    keys: list[str] = field(default_factory=list)      # library songs involved (for DUPLICATE: [keep, *drop])
    paths: list[str] = field(default_factory=list)     # files involved (for EXTRA_COPY: the ones to delete)

    @property
    def fix_label(self) -> str:
        return FIX_LABELS[self.kind]


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def audio_files(folder: Path | None = None) -> list[Path]:
    folder = folder or paths.AUDIO_DIR
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    except OSError:
        return []


def link_candidates(issue: Issue, library: list[Track], limit: int = 15) -> list[Track]:
    """Library songs a stray download may be: those without a download of their own first, then
    the closest names (and the same video id, when the file name has one)."""
    if not issue.paths:
        return []
    stem = Path(issue.paths[0]).stem
    m = _ID_TAG.search(stem)
    ident = m.group(1) if m else ""
    name = _ID_TAG.sub("", stem).strip() or stem
    from aria.core import identity

    def score(t: Track) -> tuple:
        title, artist = identity.display(t)
        has_file = bool(t.local_path and os.path.exists(t.local_path))
        sim = max(textnorm.similarity(name, title), textnorm.similarity(name, f"{artist} - {title}"),
                  textnorm.similarity(name, t.title))
        return (t.id == ident, not has_file, sim)
    return sorted((t for t in library if t.source != LOCAL), key=score, reverse=True)[:limit]


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def scan(library: list[Track], others: list[Track], folder: Path | None = None) -> list[Issue]:
    """Local checks. ``others``: songs held elsewhere (playlists, shelf, queue) whose files count as used."""
    issues: list[Issue] = []
    files = audio_files(folder)
    used = {_norm(t.local_path) for t in library + others if t.local_path}

    # the same download more than once (same "[id]", different files)
    by_id: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        m = _ID_TAG.search(f.stem)
        if m:
            by_id[m.group(1)].append(f)
    extra: set[str] = set()
    for ident, group in by_id.items():
        if len(group) < 2:
            continue
        keep = next((f for f in group if _norm(str(f)) in used), max(group, key=lambda f: f.stat().st_mtime))
        drop = [f for f in group if f != keep]
        extra.update(_norm(str(f)) for f in drop)
        size = sum(f.stat().st_size for f in drop)
        issues.append(Issue(EXTRA_COPY, keep.stem, f"{len(drop)} extra cop{'ies' if len(drop) != 1 else 'y'} · {_mb(size)}",
                            paths=[str(f) for f in drop]))

    # downloads no saved song points to
    for f in files:
        n = _norm(str(f))
        if n in used or n in extra:
            continue
        issues.append(Issue(ORPHAN, _ID_TAG.sub("", f.stem).strip() or f.stem,
                            f"{f.suffix[1:].upper()} · {_mb(f.stat().st_size)}", paths=[str(f)]))

    # lyric files whose song isn't there (deleted elsewhere, or never downloaded)
    stems = {_norm(str(f.with_suffix(""))) for f in files}
    try:
        lyric_files = sorted(p for p in (folder or paths.AUDIO_DIR).iterdir()
                             if p.is_file() and p.suffix.lower() in LYRIC_EXTS)
    except OSError:
        lyric_files = []
    for f in lyric_files:
        if _norm(str(f.with_suffix(""))) not in stems:
            issues.append(Issue(LYRICS_ONLY, _ID_TAG.sub("", f.stem).strip() or f.stem,
                                f"{f.suffix[1:].upper()} · no song file beside it", paths=[str(f)]))

    # saved songs whose file has gone
    for t in library:
        if t.local_path and not os.path.exists(t.local_path):
            what = "The file is gone; it's removed from the Library" if t.source == LOCAL else \
                "The file is gone; it will stream instead"
            issues.append(Issue(MISSING, t.title, what, keys=[t.key], paths=[t.local_path]))

    # the same song saved more than once (same performer, same song — not a cover by someone else)
    from aria.core.recommend import artist_id
    groups: list[list[Track]] = []
    songs: list[tuple[textnorm.Song, str, list[Track]]] = []
    for t in library:
        if t.source == LOCAL:
            continue
        song, who = textnorm.Song(t.title, t.artist), artist_id(t)
        twin = next((g for s, a, g in songs if a == who and s.same(song)), None)
        if twin is not None:
            twin.append(t)
        else:
            g = [t]
            songs.append((song, who, g))
            groups.append(g)
    for g in groups:
        if len(g) < 2:
            continue
        # keep the downloaded one, else the most original upload, else the first saved
        keep = max(g, key=lambda t: (bool(t.local_path and os.path.exists(t.local_path)),
                                     textnorm.originality(t.title, t.artist), -g.index(t)))
        drop = [t for t in g if t is not keep]
        issues.append(Issue(DUPLICATE, keep.title, f"Keep this one, remove {len(drop)} other upload{'s' if len(drop) != 1 else ''}",
                            keys=[keep.key] + [t.key for t in drop]))
    return issues


def check_links(tracks: list[Track], stream_fn, progress=None, workers: int = 4) -> list[Issue]:
    """Blocking, network. Songs whose link no longer resolves to something playable."""
    todo = [t for t in tracks if t.source != LOCAL]
    broken: list[Issue] = []
    done = 0

    def probe(t: Track):
        try:
            stream_fn(t)
            return None
        except Exception as exc:
            return t, str(exc).splitlines()[0][:120] if str(exc) else "Unavailable"

    with ThreadPoolExecutor(workers) as ex:
        for result in ex.map(probe, todo):
            done += 1
            if progress:
                progress(done, len(todo))
            if result:
                t, why = result
                has_file = bool(t.local_path and os.path.exists(t.local_path))
                note = " · the downloaded copy still plays" if has_file else ""
                broken.append(Issue(BROKEN, t.title, f"{why}{note}", keys=[t.key]))
    return broken
