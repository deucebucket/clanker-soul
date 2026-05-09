"""Trauma + Nourishment reservoirs — pattern-keyed accumulators with
14-day half-life decay.

The reservoirs let the engine distinguish "the same wound poked again"
from "many unrelated bad days." Each pattern (engine structure name)
has its own decaying weight; ``load()`` returns the decayed sum across
all patterns. Capped per-entry to prevent any single runaway pattern
from dominating the model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


# After 14 days of silence, accumulated trauma/nourishment for a
# pattern is halved. Keeps the reservoir from ballooning while still
# letting "this happens every week" stack.
RESERVOIR_HALF_LIFE_S = 14 * 24 * 3600

# Per-entry cap so no single runaway pattern can dominate the model.
RESERVOIR_CAP = 1000.0


def _decay_factor(elapsed_s: float, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> float:
    """Multiplicative decay over ``elapsed_s`` seconds with given half-life."""
    if elapsed_s <= 0:
        return 1.0
    return math.exp(-0.6931 * elapsed_s / half_life_s)


@dataclass
class _ReservoirEntry:
    weight: float
    last_update: float  # unix ts seconds


class TraumaReservoir:
    """Per-pattern accumulator of negative-weighted events.

    Each pattern has an independent decaying weight. ``add`` deposits a
    hit; ``load`` returns the current decayed value summed across all
    patterns."""

    def __init__(self, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> None:
        self._entries: dict[str, _ReservoirEntry] = {}
        self._half_life = half_life_s

    def add(self, pattern: str, weight: float, *, now_ts: float | None = None) -> None:
        if weight <= 0 or not pattern:
            return
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        entry = self._entries.get(pattern)
        if entry is None:
            self._entries[pattern] = _ReservoirEntry(
                weight=min(weight, RESERVOIR_CAP),
                last_update=now,
            )
            return
        decayed = entry.weight * _decay_factor(now - entry.last_update, self._half_life)
        entry.weight = min(decayed + weight, RESERVOIR_CAP)
        entry.last_update = now

    def by_pattern(self, *, now_ts: float | None = None) -> dict[str, float]:
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        out: dict[str, float] = {}
        for pat, entry in self._entries.items():
            decayed = entry.weight * _decay_factor(now - entry.last_update, self._half_life)
            if decayed > 0.01:
                out[pat] = round(decayed, 4)
        return out

    def load(self, *, now_ts: float | None = None) -> float:
        return sum(self.by_pattern(now_ts=now_ts).values())

    def to_dict(self) -> dict:
        return {
            pat: {"weight": e.weight, "last_update": e.last_update}
            for pat, e in self._entries.items()
        }

    @classmethod
    def from_dict(cls, data: dict, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> "TraumaReservoir":
        r = cls(half_life_s=half_life_s)
        for pat, e in (data or {}).items():
            r._entries[pat] = _ReservoirEntry(
                weight=float(e.get("weight", 0)),
                last_update=float(e.get("last_update", 0)),
            )
        return r


class NourishmentReservoir(TraumaReservoir):
    """Positive analog. Same mechanics, semantically distinct.

    Subclasses ``TraumaReservoir`` so the math stays in one place but
    the type is structurally different — code that branches on
    ``isinstance(x, NourishmentReservoir)`` works correctly."""

    @classmethod
    def from_dict(
        cls, data: dict, half_life_s: float = RESERVOIR_HALF_LIFE_S
    ) -> "NourishmentReservoir":
        r = cls(half_life_s=half_life_s)
        for pat, e in (data or {}).items():
            r._entries[pat] = _ReservoirEntry(
                weight=float(e.get("weight", 0)),
                last_update=float(e.get("last_update", 0)),
            )
        return r


__all__ = [
    "RESERVOIR_HALF_LIFE_S",
    "RESERVOIR_CAP",
    "TraumaReservoir",
    "NourishmentReservoir",
]
