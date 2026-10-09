"""Auto-recommendation: *related* songs, never the same song again.

Recommendations always come from YouTube (its music radio is the best free
"related songs" source). A seed from another platform is first matched to a
YouTube video. Candidates are then filtered so that covers, live cuts, remixes
or re-uploads of anything already played or queued are dropped — another
singer performing the same song counts as "the same", not "related".
"""

from __future__ import annotations

import collections
import logging
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest

from aria.core import textnorm
from aria.core.models import SPOTIFY, YOUTUBE, Track

log = logging.getLogger(__name__)


class Recommender:
    def __init__(self, youtube, spotify):
        self.youtube = youtube
        self.spotify = spotify
        self._seed_cache: dict[str, str | None] = {}

    def youtube_seed(self, track: Track) -> str | None:
        """YouTube video id standing for ``track``."""
        if track.source == YOUTUBE:
            return track.id
        if track.key in self._seed_cache:
            return self._seed_cache[track.key]
        vid = None
        try:
            if track.source == SPOTIFY:
                vid = self.spotify.youtube_match(track).id
            else:
                query = f"{textnorm.core_title(track.title, track.artist)} {track.title}".strip()
                me = textnorm.Song(track.title, track.artist)
                results = self.youtube.search(query[:120], limit=5)
                # Prefer an upload of the same song that isn't a cover/live version.
                exact = [r for r in results if me.same(textnorm.Song(r.title, r.artist))]
                pick = next((r for r in exact if not textnorm.is_variant(r.title)), None) \
                    or (exact[0] if exact else results[0] if results else None)
                vid = pick.id if pick else None
        except Exception as exc:
            log.warning("No recommendation seed for %s: %s", track.title, exc)
        self._seed_cache[track.key] = vid
        return vid

    def recommend(self, seeds: list[Track], exclude: list[Track], count: int = 10) -> list[Track]:
        """Blocking. ``seeds``: most recent first. ``exclude``: everything already heard or queued."""
        def radio(seed: Track) -> list[Track]:
            vid = self.youtube_seed(seed)
            if not vid:
                return []
            try:
                return self.youtube.mix(vid, limit=40)
            except Exception as exc:
                log.warning("Couldn’t fetch recommendations: %s", exc)
                return []

        with ThreadPoolExecutor(4) as ex:
            pools = [p for p in ex.map(radio, seeds[:5]) if p]
        return pick_related(_weave(pools), seeds + exclude, count)


def _weave(pools: list[list[Track]]) -> list[Track]:
    """Merge seed pools; the newest seed (the current song) gets two picks per round."""
    if not pools:
        return []
    head = pools[0]
    rounds = [head[0::2], head[1::2], *pools[1:]]
    return [t for group in zip_longest(*rounds) for t in group if t]


def pick_related(candidates: list[Track], exclude: list[Track], count: int) -> list[Track]:
    """Filter candidates down to related-but-different songs, keeping artist variety."""
    seen_ids = {t.id for t in exclude} | {t.match_id for t in exclude if t.match_id}
    known = [textnorm.Song(t.title, t.artist) for t in exclude]
    per_artist: collections.Counter[str] = collections.Counter()
    cap = max(2, count // 5)
    picked: list[Track] = []
    deferred: list[Track] = []

    for cand in candidates:
        if cand.id in seen_ids or textnorm.is_variant(cand.title):
            continue
        song = textnorm.Song(cand.title, cand.artist)
        if any(song.same(k) for k in known):
            continue
        seen_ids.add(cand.id)
        known.append(song)
        artist = cand.artist.casefold()
        if per_artist[artist] >= cap:
            deferred.append(cand)          # keep as filler if variety runs out
            continue
        per_artist[artist] += 1
        picked.append(cand)
        if len(picked) >= count:
            return _spread(picked)
    return _spread((picked + deferred)[:count])


def _spread(tracks: list[Track]) -> list[Track]:
    """Reorder so the same artist rarely plays twice in a row (order otherwise kept)."""
    rest = list(tracks)
    out: list[Track] = []
    while rest:
        last = out[-1].artist.casefold() if out else None
        i = next((k for k, t in enumerate(rest) if t.artist.casefold() != last), 0)
        out.append(rest.pop(i))
    return out
