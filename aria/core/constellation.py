"""Music Constellation: the neighbourhood of an artist or a song.

Edges come from two places:
- YouTube's music radio (artists whose songs keep appearing next to this one), and
- the user's own listening (artists played around the same time, songs they saved).
Everything here is blocking network/CPU work; call it from a background task.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from aria.core import textnorm
from aria.core.models import Track
from aria.core.recommend import pick_related

ARTIST, SONG = "artist", "song"


@dataclass
class Node:
    kind: str                     # artist | song
    name: str                     # artist name, or song title
    subtitle: str = ""            # song: artist
    thumb: str = ""
    weight: float = 1.0           # 0..1, drives size / distance
    track: Track | None = None    # songs (and an artist's sample song)
    saved: bool = False           # in the user's library
    together: bool = False        # artist the user often plays alongside the centre
    tags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.kind}:{textnorm.artist_key(self.name) if self.kind == ARTIST else (self.track.key if self.track else self.name)}"


@dataclass
class Galaxy:
    center: Node
    nodes: list[Node]


def listened_together(history: list[Track], artist: str, window: int = 3) -> Counter:
    """Artists played within ``window`` songs of ``artist`` in the user's history."""
    key = textnorm.artist_key(artist)
    names = [textnorm.artist_of(t.title, t.artist) for t in history]
    keys = [textnorm.artist_key(n) for n in names]
    out: Counter = Counter()
    for i, k in enumerate(keys):
        if k != key:
            continue
        for j in range(max(0, i - window), min(len(keys), i + window + 1)):
            if keys[j] and keys[j] != key:
                out[names[j]] += 1
    return out


def _same_artist(name: str, track: Track) -> bool:
    want = textnorm.artist_key(name)
    got = textnorm.artist_key(textnorm.artist_of(track.title, track.artist))
    return bool(want) and (want == got or want in got or got in want)


def _merge_alias(name: str, known) -> str:
    """"米津玄師" and "米津玄師 Kenshi Yonezu" are one artist: reuse the name already seen."""
    k = textnorm.artist_key(name)
    for other in known:
        o = textnorm.artist_key(other)
        if len(min(k, o, key=len)) >= 2 and (k.startswith(o) or o.startswith(k)):
            return other
    return name


def explore_artist(youtube, name: str, library: list[Track], history: list[Track]) -> Galaxy:
    results = youtube.search(name, limit=20)
    works: list[Track] = []
    songs: list[textnorm.Song] = []
    for t in results:
        if (not _same_artist(name, t) or textnorm.is_variant(t.title)
                or textnorm.is_compilation(t.title, t.duration)):
            continue
        s = textnorm.Song(t.title, t.artist)
        if any(s.same(o) for o in songs):
            continue
        songs.append(s)
        works.append(t)
        if len(works) >= 7:
            break
    if not works:
        works = results[:5]

    related: Counter = Counter()
    samples: dict[str, Track] = {}
    # The artist's own channels: uploads there under another name (Yorushika /
    # ヨルシカ) are the same artist, not a neighbour.
    own_channels = {textnorm.artist_key(textnorm.clean_channel(t.artist)) for t in works}
    # Radios of the top songs start with the artist's own catalogue; neighbours
    # appear further down, so read deep and from more than one seed.
    with ThreadPoolExecutor(2) as ex:
        radios = list(ex.map(lambda w: youtube.mix(w.id, limit=50), works[:2]))
    pool = [t for radio in radios for t in radio]
    if pool:
        for t in pool:
            artist = textnorm.artist_of(t.title, t.artist)
            k = textnorm.artist_key(artist)
            if (not k or k == textnorm.artist_key(name) or _same_artist(name, t)
                    or textnorm.artist_key(textnorm.clean_channel(t.artist)) in own_channels):
                continue
            artist = _merge_alias(artist, related)
            related[artist] += 1
            samples.setdefault(artist, t)

    together = listened_together(history, name)
    for artist, n in together.items():
        related[artist] += n * 2              # the user's own habits weigh more

    saved = [t for t in library if _same_artist(name, t)]
    saved_keys = {t.key for t in saved}

    nodes: list[Node] = []
    top = max(related.values(), default=1)
    for artist, n in related.most_common(9):
        sample = samples.get(artist)
        nodes.append(Node(ARTIST, artist, thumb=sample.thumbnail if sample else "", weight=n / top,
                          track=sample, together=artist in together))
    for i, t in enumerate(works):
        nodes.append(Node(SONG, t.title, subtitle=name, thumb=t.thumbnail, weight=1 - i / 10, track=t,
                          saved=t.key in saved_keys))
    shown = {n.track.key for n in nodes if n.track}
    for t in saved[:5]:
        if t.key not in shown:
            nodes.append(Node(SONG, t.title, subtitle=name, thumb=t.thumbnail, weight=0.6, track=t, saved=True))

    center_thumb = works[0].thumbnail if works else ""
    return Galaxy(Node(ARTIST, name, thumb=center_thumb, track=works[0] if works else None), nodes)


def explore_song(youtube, recommender, track: Track, library: list[Track]) -> Galaxy:
    vid = recommender.youtube_seed(track)
    pool = youtube.mix(vid, limit=30) if vid else []
    picks = pick_related(pool, [track], 10)
    saved_keys = {t.key for t in library}
    artist = textnorm.artist_of(track.title, track.artist) or track.artist
    nodes = [Node(ARTIST, artist, thumb=track.thumbnail, weight=1.0, track=track)]
    for i, t in enumerate(picks):
        nodes.append(Node(SONG, t.title, subtitle=textnorm.artist_of(t.title, t.artist), thumb=t.thumbnail,
                          weight=1 - i / 12, track=t, saved=t.key in saved_keys))
    return Galaxy(Node(SONG, track.title, subtitle=artist, thumb=track.thumbnail, track=track,
                       saved=track.key in saved_keys), nodes)
