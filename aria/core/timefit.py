"""Time-Fitted Playlist: pick songs whose total length lands just inside a time budget.

It is a subset-sum: with integer seconds, a big-int bitset per song records which
totals are reachable, so a few hundred songs and a few hours resolve instantly.
Cross-fades overlap the songs a little; that is folded in by shortening every
song (and the budget) by the fade length. The music ends with a song's natural
end, never mid-song.
"""

from __future__ import annotations

import random

from aria.core.models import Track

MIN_SONG = 30                 # seconds; shorter clips are skipped


def fit(tracks: list[Track], seconds: float, overlap: float = 0.0, keep_order: bool = False,
        rng: random.Random | None = None) -> list[Track]:
    """Songs totalling as close to ``seconds`` as possible without going over.
    ``overlap``: seconds lost at every change-over (cross-fade).
    ``keep_order``: keep the input order (e.g. the queue) instead of mixing it up."""
    rng = rng or random.Random()
    seen: set[str] = set()
    pool: list[Track] = []
    for t in tracks:
        if t.key not in seen and t.duration >= MIN_SONG:
            seen.add(t.key)
            pool.append(t)
    if not pool or seconds <= 0:
        return []
    order = list(range(len(pool)))
    if not keep_order:
        rng.shuffle(order)
    ov = max(0.0, overlap)
    # n songs play for sum(d) - (n-1)·ov  =  sum(d - ov) + ov
    budget = int(seconds - ov)
    sizes = [max(1, int(round(pool[i].duration - ov))) for i in order]
    if budget <= 0:
        return []

    mask = (1 << (budget + 1)) - 1
    reach = [1]                                  # reach[k]: totals reachable with the first k songs
    for s in sizes:
        reach.append((reach[-1] | (reach[-1] << s)) & mask)
    best = reach[-1].bit_length() - 1
    if best <= 0:
        return []
    picked: list[int] = []
    total = best
    for k in range(len(sizes), 0, -1):
        if (reach[k - 1] >> total) & 1:
            continue                             # reachable without song k
        picked.append(order[k - 1])
        total -= sizes[k - 1]
    picked.reverse()
    if keep_order:
        picked.sort()
    return [pool[i] for i in picked]


def playing_time(tracks: list[Track], overlap: float = 0.0) -> float:
    if not tracks:
        return 0.0
    return sum(t.duration for t in tracks) - overlap * (len(tracks) - 1)


def parse_duration(text: str) -> int | None:
    """"31:25" → 1885, "45" → 2700 (minutes), "1:05:00" → 3900. None when unreadable."""
    parts = text.strip().replace("：", ":").split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not nums or any(n < 0 for n in nums) or len(nums) > 3:
        return None
    if len(nums) == 1:
        secs = nums[0] * 60
    elif len(nums) == 2:
        secs = nums[0] * 60 + nums[1]
    else:
        secs = nums[0] * 3600 + nums[1] * 60 + nums[2]
    return int(secs) if 60 <= secs <= 24 * 3600 else None
