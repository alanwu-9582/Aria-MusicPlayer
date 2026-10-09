"""Auto-recommendation: *related* songs, never the same song again.

Recommendations always come from YouTube (its music radio is the best free
"related songs" source). A seed from another platform is first matched to a
YouTube video. Candidates are then filtered so that anything already played or
queued — including another singer's version of it — is dropped: another
singer performing the same song counts as "the same", not "related".

Originals come first: derivative uploads (lyric videos, sped-up edits, fan
videos, remixes…) are dropped, and covers or live versions of *other* songs
are allowed but ranked after original recordings.

The radio order is then nudged by the listener's own taste: artists they
often play alongside the seeds, artists in their library, artists several seed
radios agree on — and by the Discovery dial, which leans towards familiar
artists or towards ones they've never played.
"""

from __future__ import annotations

import collections
import logging
import re
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

    def fetch(self, seeds: list[Track], history: list[Track] | None = None,
              library: list[Track] | None = None) -> list[list[Track]]:
        """Blocking. One radio per seed (most recent seed first), plus up to two
        "bridge" radios when the seeds' radios hardly leave their artist."""
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
        bridges = self._bridges(seeds, pools, history or [], library or [])
        if bridges:
            # Some radios are one artist all the way down; a related artist's radio opens them up.
            with ThreadPoolExecutor(2) as ex:
                pools += [p for p in ex.map(radio, bridges) if p]
        return pools

    def _bridges(self, seeds: list[Track], pools: list[list[Track]], history: list[Track],
                 library: list[Track]) -> list[Track]:
        """Up to two songs by *other* artists to branch out from, when the radios lack variety:
        from the radios themselves, YouTube's regular mix, then the listener's own music —
        artists usually played alongside these first."""
        mine = {artist_id(s) for s in seeds}
        others = {artist_id(t) for p in pools for t in p} - mine
        if len(others) >= MIN_ARTISTS or not seeds:
            return []
        extra: list[Track] = []
        vid = self.youtube_seed(seeds[0])
        if vid:
            try:
                extra = self.youtube.mix(vid, limit=40, music=False)
            except Exception as exc:
                log.info("No YouTube mix for variety: %s", exc)
        together = listened_together(history, mine)
        own = sorted(history[::-1] + library, key=lambda t: -together[artist_id(t)])
        out, seen = [], set(mine)
        for t in [t for p in pools for t in p] + extra + own:
            a = artist_id(t)
            if a in seen or textnorm.is_derivative(t.title) or textnorm.is_compilation(t.title, t.duration):
                continue
            seen.add(a)
            out.append(t)
            if len(out) == 2:
                break
        return out

    def recommend(self, seeds: list[Track], exclude: list[Track], count: int = 10, **taste) -> list[Track]:
        """Blocking. ``seeds``: most recent first. ``exclude``: everything already heard or queued."""
        pools = self.fetch(seeds, taste.get("history"), taste.get("library"))
        return assemble(pools, seeds, exclude, count, **taste)


def assemble(pools: list[list[Track]], seeds: list[Track], exclude: list[Track], count: int,
             history: list[Track] | None = None, library: list[Track] | None = None,
             played: set[str] | None = None, discovery: float = 0.5) -> list[Track]:
    """Radios → a ranked, filtered list. Pure: re-run it to re-rank without refetching."""
    candidates = rank_by_taste(_weave(pools), pools, seeds, history or [], library or [],
                               played or set(), discovery)
    return pick_related(candidates, seeds + exclude, count)


# ---- taste ---------------------------------------------------------------------

_OWN_CHANNEL = re.compile(r"official|-\s*topic\s*$|vevo\s*$", re.IGNORECASE)
_LABEL = re.compile(r"records?|music|entertainment|label|唱片|音樂|音乐|レコード", re.IGNORECASE)


def artist_id(track: Track) -> str:
    """Grouping key for a track's performer. An artist's own channel ("ヨルシカ / n-buna Official",
    "… - Topic") names them whatever the titles say ("Yorushika - …", "ヨルシカ - …"); on a
    label's channel the performer is parsed from the title instead."""
    chan = track.artist
    if chan and _OWN_CHANNEL.search(chan) and not _LABEL.search(textnorm.clean_channel(chan)):
        return textnorm.artist_key(textnorm.clean_channel(chan))
    return textnorm.artist_key(textnorm.artist_of(track.title, chan) or chan)


def listened_together(history: list[Track], artists: set[str], window: int = 3) -> collections.Counter:
    """How often each artist was played within ``window`` songs of any of ``artists``."""
    keys = [artist_id(t) for t in history]
    out: collections.Counter = collections.Counter()
    for i, k in enumerate(keys):
        if k not in artists:
            continue
        for j in range(max(0, i - window), min(len(keys), i + window + 1)):
            if keys[j] and keys[j] not in artists:
                out[keys[j]] += 1
    return out


MIN_ARTISTS = 5              # fewer other artists than this in the radios → branch out

# Bonuses on top of the radio's own order, which spans 0..1.
TOGETHER_WEIGHT = 0.40       # played next to the seed artists
FAMILIAR_WEIGHT = 0.30       # saved / often played artists
KNOWN_WEIGHT = 0.12          # this very song is saved or was played before
NOVELTY_WEIGHT = 0.45        # an artist never saved or played
AGREEMENT_WEIGHT = 0.20      # several seed radios suggest the artist
ORIGINAL_WEIGHT = 0.15       # official upload ↑, cover / live ↓


def rank_by_taste(candidates: list[Track], pools: list[list[Track]], seeds: list[Track],
                  history: list[Track], library: list[Track], played: set[str] | None = None,
                  discovery: float = 0.5) -> list[Track]:
    """Reorder the woven radio. ``discovery`` 0 = familiar … 1 = unexpected.
    At the middle the radio's own order still dominates."""
    if not candidates:
        return candidates
    d = max(0.0, min(1.0, discovery))
    lean = 1 - 2 * d             # +1 familiar … 0 neutral … -1 unexpected
    seed_artists = {artist_id(t) for t in seeds}
    together = listened_together(history, seed_artists)
    saved = collections.Counter(artist_id(t) for t in library)
    heard = collections.Counter(artist_id(t) for t in history)
    known = {t.key for t in library} | {t.key for t in history} | (played or set())
    agreement: collections.Counter = collections.Counter()
    for pool in pools:
        for a in {artist_id(t) for t in pool}:
            agreement[a] += 1
    top_together = max(together.values(), default=0) or 1
    top_saved = max(saved.values(), default=0) or 1
    top_heard = max(heard.values(), default=0) or 1
    n = len(candidates)

    def score(item) -> float:
        i, t = item
        a = artist_id(t)
        familiar = 0.6 * min(1.0, saved[a] / top_saved) + 0.4 * min(1.0, heard[a] / top_heard)
        novel = not familiar and not together[a] and a not in seed_artists
        # Leaning towards the unexpected also loosens the radio's order a little.
        s = (1 - i / n) * (1 - 0.4 * d)
        s += (1 - d) * TOGETHER_WEIGHT * together[a] / top_together
        s += lean * (FAMILIAR_WEIGHT * familiar + KNOWN_WEIGHT * (t.key in known))
        s += max(0.0, -lean) * NOVELTY_WEIGHT * novel
        if len(pools) > 1 and agreement[a] > 1:
            s += AGREEMENT_WEIGHT * (agreement[a] - 1) / (len(pools) - 1)
        s += ORIGINAL_WEIGHT * textnorm.originality(t.title, t.artist)
        return s

    return [t for _i, t in sorted(enumerate(candidates), key=score, reverse=True)]


def _weave(pools: list[list[Track]]) -> list[Track]:
    """Merge seed pools; the newest seed (the current song) gets two picks per round."""
    if not pools:
        return []
    head = pools[0]
    rounds = [head[0::2], head[1::2], *pools[1:]]
    return [t for group in zip_longest(*rounds) for t in group if t]


def pick_related(candidates: list[Track], exclude: list[Track], count: int) -> list[Track]:
    """Filter candidates down to related-but-different songs, keeping artist variety.
    The first upload of a song wins, so ranking originals first keeps their versions."""
    seen_ids = {t.id for t in exclude} | {t.match_id for t in exclude if t.match_id}
    known = [textnorm.Song(t.title, t.artist) for t in exclude]
    per_artist: collections.Counter[str] = collections.Counter()
    cap = max(2, count // 5)
    picked: list[Track] = []
    deferred: list[Track] = []

    for cand in candidates:
        if (cand.id in seen_ids or textnorm.is_derivative(cand.title)
                or textnorm.is_compilation(cand.title, cand.duration)):
            continue
        song = textnorm.Song(cand.title, cand.artist)
        if any(song.same(k) for k in known):
            continue
        seen_ids.add(cand.id)
        known.append(song)
        artist = artist_id(cand)
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
        last = artist_id(out[-1]) if out else None
        i = next((k for k, t in enumerate(rest) if artist_id(t) != last), 0)
        out.append(rest.pop(i))
    return out
