"""Trigger + target dataclasses for :py:class:`PulseEngine`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Trigger:
    """A reason the engine wants to fire a pulse, with state attached.

    ``kind`` is one of:
      - ``distress``        : mood far below soul on V/W
      - ``elation``         : mood far above soul on V with I-lift
      - ``trauma_pressure`` : sustained negative pattern accumulation
      - ``gratitude``       : sustained nourishment > trauma * 2
      - ``long_silence``    : quiet for > max_quiet_seconds
    """

    kind: str
    soul: dict
    mood: list[int] | None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "soul": self.soul, "mood": self.mood, **self.metrics}


@dataclass(frozen=True)
class PulseTarget:
    """An opaque address for "where this pulse should go."

    The engine never inspects this — it's passed back to the host's
    ``dispatch_pulse``. Hosts can put a channel id, a recipient meta
    dict, a user id, anything."""

    payload: Any


__all__ = ["Trigger", "PulseTarget"]
