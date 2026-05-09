"""SoulState — the persistent slow-moving emotional baseline.

Soul is the agent's "who they are right now" vector. It drifts on
days-to-weeks timescale via two pathways:

1. Slow drift: rolling mood mean pulls Soul toward sustained affect.
2. Breach: a heavy event during an unhealed wound (|Mood - Soul|
   already large) leaks straight into Soul, bypassing the slow filter.
   This is how back-to-back hits actually scar.

State is persisted via :py:class:`clanker_soul.soul.SoulStore`. Without
persistence the whole exercise is theater — every restart would reset
the agent to the starting baseline, defeating the point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SoulState:
    """The persistent baseline VADUGWI. 7 dims, each 0-255.

    Healthy starting baseline is *not* neutral 128 — most agents want a
    default leaning (mildly positive, grounded, in-control, strong
    sense of self-worth) so neutral conversation doesn't read as
    "depressed." Override these defaults per agent at construction time.
    """

    v: int = 145  # mild positive baseline
    a: int = 110  # slightly calm — not over-aroused
    d: int = 160  # in-control by default
    u: int = 80   # low background urgency
    g: int = 130  # slightly grounded
    w: int = 175  # strong self-worth — agent is allowed to think it's solid
    i: int = 135  # slight forward-intent
    last_drift_ts: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    last_save_ts: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    def as_tuple(self) -> tuple[int, int, int, int, int, int, int]:
        return (self.v, self.a, self.d, self.u, self.g, self.w, self.i)

    def to_dict(self) -> dict:
        return {
            "v": self.v, "a": self.a, "d": self.d, "u": self.u,
            "g": self.g, "w": self.w, "i": self.i,
            "last_drift_ts": self.last_drift_ts,
            "last_save_ts": self.last_save_ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SoulState":
        return cls(
            v=int(data.get("v", 145)),
            a=int(data.get("a", 110)),
            d=int(data.get("d", 160)),
            u=int(data.get("u", 80)),
            g=int(data.get("g", 130)),
            w=int(data.get("w", 175)),
            i=int(data.get("i", 135)),
            last_drift_ts=float(data.get("last_drift_ts", 0)),
            last_save_ts=float(data.get("last_save_ts", 0)),
        )


__all__ = ["SoulState"]
