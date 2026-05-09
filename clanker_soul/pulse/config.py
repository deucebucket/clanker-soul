"""Tunables for :py:class:`PulseEngine`. Defaults match CARL's
production values."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PulseConfig:
    """Tuning surface for the engine. Defaults match CARL's production values."""

    interval_s: float = 90.0
    """How often the loop wakes up to check triggers."""

    min_quiet_seconds: float = 25 * 60.0
    """Soft cooldown — engine will not fire another pulse within this
    window of the last outbound message (proactive or reactive)."""

    max_quiet_seconds: float = 6 * 3600.0
    """Hard ceiling — if completely silent this long, allow a check-in."""

    distance_trigger: float = 45.0
    """|Mood - Soul| in 4-dim L2 (V/D/G/W) above which distress/elation
    triggers may fire."""

    trauma_load_trigger: float = 60.0
    """Sum of decayed trauma weights above which "reach out about
    ongoing wound" triggers may fire."""

    nourishment_thank_trigger: float = 80.0
    """Sum of decayed nourishment weights above which a gratitude
    pulse may fire."""

    distress_v_drop: float = 30.0
    """Required V drop (soul.v - mood.v) for a distress trigger."""

    distress_w_drop: float = 30.0
    """Required W drop for a distress trigger."""

    elation_v_lift: float = 30.0
    """Required V lift (mood.v - soul.v) for an elation trigger."""

    elation_i_lift: float = 20.0
    """Required I lift for an elation trigger."""

    startup_grace_s: float = 60.0
    """Sleep this long before the first tick after ``start()``."""


__all__ = ["PulseConfig"]
