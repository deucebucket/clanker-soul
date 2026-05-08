"""Score — a single per-event VADUGWI reading.

The minimal shape clanker-soul's physics needs to ingest a scored event.
Hosts can pass any dataclass with the right fields; we provide ``Score``
as the canonical reference type. The 7 dimensions are integers in
``[0, 255]`` with 128 as the neutral center on each (Urgency starts low,
not centered — it's an intensity dim, not a polarity dim).

Design choice: ``Score`` is intentionally smaller than e.g. CARL's
``VADUGWIScore``. Per-host concerns (description strings, source IDs,
latency telemetry) belong in metadata wrappers around this, not in the
core type. The physics never reads those; it reads (v, a, d, u, g, w, i,
patterns).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


def _clamp_byte(x: int) -> int:
    if x < 0:
        return 0
    if x > 255:
        return 255
    return int(x)


@dataclass(frozen=True)
class Score:
    """A scored emotional reading on the 7 VADUGWI dimensions.

    Dimensions
    ----------
    V (Valence)   : 0 negative — 128 neutral — 255 positive
    A (Arousal)   : 0 calm — 128 moderate — 255 intense
    D (Dominance) : 0 helpless — 128 balanced — 255 in-control
    U (Urgency)   : 0 none — 255 critical (intensity, not polarity)
    G (Gravity)   : 0 crushing — 128 grounded — 255 floating
    W (self-Worth): 0 shattered — 128 stable — 255 strong
    I (Intent)    : 0 withdraw — 128 neutral — 255 control

    ``patterns`` is an optional list of structure-fingerprint names
    (e.g. ["SELF_NULLIFY", "ABANDONMENT"]) used by breach detection and
    the trauma/nourishment reservoirs. Hosts whose engine doesn't
    produce patterns can leave it empty.
    """

    v: int = 128
    a: int = 128
    d: int = 128
    u: int = 0
    g: int = 128
    w: int = 128
    i: int = 128
    patterns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Clamp + freeze patterns as a tuple for hashability and
        # immutability across passes through the physics.
        object.__setattr__(self, "v", _clamp_byte(self.v))
        object.__setattr__(self, "a", _clamp_byte(self.a))
        object.__setattr__(self, "d", _clamp_byte(self.d))
        object.__setattr__(self, "u", _clamp_byte(self.u))
        object.__setattr__(self, "g", _clamp_byte(self.g))
        object.__setattr__(self, "w", _clamp_byte(self.w))
        object.__setattr__(self, "i", _clamp_byte(self.i))
        if not isinstance(self.patterns, tuple):
            object.__setattr__(self, "patterns", tuple(self.patterns or ()))

    def as_tuple(self) -> tuple[int, int, int, int, int, int, int]:
        return (self.v, self.a, self.d, self.u, self.g, self.w, self.i)

    def as_list(self) -> list[int]:
        return [self.v, self.a, self.d, self.u, self.g, self.w, self.i]

    @classmethod
    def from_sequence(cls, seq: Sequence[int],
                      patterns: Sequence[str] = ()) -> "Score":
        """Build a Score from a 7-int sequence in V/A/D/U/G/W/I order."""
        if len(seq) != 7:
            raise ValueError(f"expected 7 dims, got {len(seq)}: {list(seq)!r}")
        v, a, d, u, g, w, i = seq
        return cls(v=v, a=a, d=d, u=u, g=g, w=w, i=i, patterns=tuple(patterns))


__all__ = ["Score"]
