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

    # ------------------------------------------------------------------
    # M1.2 — motivation triggers (#45)
    # ------------------------------------------------------------------

    share_v_lift: float = 15.0
    """V lift required for share_impulse. Lower than elation_v_lift —
    share is mood-leaning-positive, not full elation."""

    share_arousal_min: float = 130.0
    """Minimum arousal for share_impulse. Below this, the agent is
    feeling fine but not moved enough to want to broadcast."""

    share_nourishment_floor: float = 20.0
    """Nourishment load above which the share-impulse becomes
    plausible — distinguishes 'good vibes' from 'mid-conversation
    moment that happens to be positive'."""

    curiosity_arousal_min: float = 145.0
    """Minimum arousal for restless_curiosity. The agent has energy
    looking for somewhere to go."""

    curiosity_distance_max: float = 25.0
    """Mood-soul distance ABOVE which curiosity is overshadowed by
    something heavier. Keeps the trigger from firing during distress
    or elation."""

    curiosity_idle_min_seconds: float = 5 * 60.0
    """Minimum idle since last activity before curiosity considers
    firing — prevents firing mid-conversation."""

    argue_v_drop: float = 20.0
    """V drop (soul.v - mood.v) required for argue_impulse. Smaller
    than distress_v_drop — the agent isn't crashing, just irritated."""

    argue_arousal_min: float = 140.0
    """Minimum arousal for argue_impulse. Anger needs energy."""

    argue_intent_min: float = 145.0
    """Minimum intent (I) for argue_impulse. The agent feels both wronged
    AND like acting on it. Without intent, it's just rumination."""

    connect_v_min: float = 130.0
    """Minimum V mood for connect_impulse. The agent feels OK enough to
    want company; not from a place of need (that's distress)."""

    connect_idle_min_seconds: float = 90 * 60.0
    """Minimum idle since last activity before connect_impulse fires.
    Smaller than max_quiet_seconds — connect can fire before the
    long_silence force-fire kicks in."""

    connect_max_trauma: float = 30.0
    """Trauma load above which connect-impulse is suppressed (the
    agent should rest or vent, not seek company)."""

    withdraw_trauma_min: float = 50.0
    """Trauma load above which withdraw_impulse becomes plausible."""

    withdraw_w_max: int = 100
    """Mood W BELOW which the agent withdraws — soul-worth dipped
    enough that engagement isn't well-formed."""

    reflective_idle_min_seconds: float = 30 * 60.0
    """Minimum idle since last activity for reflective_impulse."""

    reflective_distance_min: float = 15.0
    """Minimum mood-soul distance for reflective_impulse — without
    distance there's nothing meaningful to reflect on."""

    reflective_max_trauma: float = 50.0
    """Trauma above which reflection gives way to heavier triggers
    (trauma_pressure, withdraw)."""

    caretake_self_w_min: int = 110
    """Minimum self W mood for caretake_impulse. Don't try to caretake
    when own well is dry — that's compulsive caretaking, not authentic
    concern."""

    startup_grace_s: float = 60.0
    """Sleep this long before the first tick after ``start()``."""


__all__ = ["PulseConfig"]
