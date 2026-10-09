"""Smart Collections: lists defined by rules that keep themselves up to date.

A rule is a small dict ``{"field": ..., "value": ...}``. Rules are evaluated
against the library (or, with scope "all", everything ever played) using the
listening stats, so the collection follows what the user saves and plays.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field

from aria.core import textnorm
from aria.core.models import LOCAL, SOURCE_LABELS, Track

DAY = 86400

# field → (label, value kind, default value)
FIELDS: dict[str, tuple[str, str, object]] = {
    "added_within": ("Saved in the last", "days", 30),
    "not_finished": ("Not listened to the end yet", "none", True),
    "played_at_least": ("Played at least", "times", 3),
    "never_played": ("Never played", "none", True),
    "not_played_for": ("Not played in", "days", 60),
    "downloaded": ("Downloaded", "none", True),
    "language": ("Language", "language", "ja"),
    "source": ("Source", "source", "youtube"),
    "artist": ("Artist contains", "text", ""),
    "title": ("Title contains", "text", ""),
    "longer_than": ("Longer than", "minutes", 5),
    "shorter_than": ("Shorter than", "minutes", 4),
}

PRESETS: list[tuple[str, list[dict]]] = [
    ("Saved in the Last 30 Days", [{"field": "added_within", "value": 30}]),
    ("Saved, Not Finished Yet", [{"field": "not_finished", "value": True}]),
    ("Downloaded Japanese Music", [{"field": "downloaded", "value": True}, {"field": "language", "value": "ja"}]),
]


@dataclass
class SmartCollection:
    name: str
    rules: list[dict] = field(default_factory=list)
    match: str = "all"                      # all | any
    scope: str = "library"                  # library | all (also songs only played, never saved)
    id: str = field(default_factory=lambda: "s" + uuid.uuid4().hex[:11])
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "rules": self.rules, "match": self.match,
                "scope": self.scope, "created": self.created}

    @classmethod
    def from_dict(cls, d: dict) -> SmartCollection:
        return cls(name=d.get("name", "Smart Collection"), rules=list(d.get("rules", [])),
                   match=d.get("match", "all"), scope=d.get("scope", "library"),
                   id=d.get("id") or "s" + uuid.uuid4().hex[:11], created=d.get("created", 0))


def describe(rule: dict) -> str:
    """Short text for a rule: "Saved in the last 30 days"."""
    f = rule.get("field", "")
    label, kind, _default = FIELDS.get(f, (f, "none", None))
    v = rule.get("value")
    if kind == "days":
        return f"{label} {v} day{'s' if v != 1 else ''}"
    if kind == "times":
        return f"{label} {v} time{'s' if v != 1 else ''}"
    if kind == "minutes":
        return f"{label} {v} min"
    if kind == "language":
        return f"{label}: {textnorm.LANGUAGES.get(v, v)}"
    if kind == "source":
        return f"{label}: {SOURCE_LABELS.get(v, v)}"
    if kind == "text":
        return f"{label} “{v}”"
    return label


def _matches(rule: dict, t: Track, saved_at: float | None, stat, now: float) -> bool:
    f, v = rule.get("field"), rule.get("value")
    if f == "added_within":
        return saved_at is not None and now - saved_at <= float(v) * DAY
    if f == "not_finished":
        return not (stat and stat.finished)
    if f == "played_at_least":
        return bool(stat) and stat.plays >= int(v)
    if f == "never_played":
        return not stat or not stat.plays
    if f == "not_played_for":
        return not stat or now - stat.last >= float(v) * DAY
    if f == "downloaded":
        has = t.source == LOCAL or bool(t.local_path and os.path.exists(t.local_path))
        return has == bool(v)
    if f == "language":
        return textnorm.language_of(t.title, t.artist) == v
    if f == "source":
        return t.source == v
    if f == "artist":
        who = f"{t.artist} {textnorm.artist_of(t.title, t.artist)}".casefold()
        return str(v).strip().casefold() in who
    if f == "title":
        return str(v).casefold() in t.title.casefold()
    if f == "longer_than":
        return t.duration > float(v) * 60
    if f == "shorter_than":
        return 0 < t.duration < float(v) * 60
    return True


def evaluate(sc: SmartCollection, library: list[Track], played: list[Track], stats: dict) -> list[Track]:
    """The songs that match right now: saved songs newest first, then (scope "all") played ones."""
    now = time.time()
    pool = [(t, t.added_at) for t in library]
    if sc.scope == "all":
        have = {t.key for t in library}
        extra = [t for t in played if t.key not in have]
        extra.sort(key=lambda t: -(stats[t.key].last if t.key in stats else 0))
        pool += [(t, None) for t in extra]
    rules = [r for r in sc.rules if r.get("field") in FIELDS]
    out = []
    for t, saved_at in pool:
        stat = stats.get(t.key)
        hits = (_matches(r, t, saved_at, stat, now) for r in rules)
        if not rules or (all(hits) if sc.match == "all" else any(hits)):
            out.append(t)
    return out
