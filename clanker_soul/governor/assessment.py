"""Capability-level assessment from current emotional state.

Pure function: ``assess_capability(snap, config) -> CapabilityLevel``.
Deterministic for the same input. Called every tick by the host
to decide which tools to expose.
"""
from __future__ import annotations

from clanker_soul.governor.levels import CapabilityLevel, GovernorConfig


def assess_capability(snap: dict, config: GovernorConfig) -> CapabilityLevel:
    """Translate a :py:meth:`SoulPlugin.snapshot` dict into a
    capability level.

    Higher level = more restricted. The function evaluates
    descending-severity gates in order; the most restrictive matching
    gate wins. Restrictions ease automatically as state recovers
    because the function is pure — there is no latched state."""
    mood = snap.get("mood")
    distance = snap.get("soul_distance") or 0.0
    trauma = snap.get("trauma_load") or 0.0

    # Without a mood reading (no events yet), treat as unrestricted —
    # the agent hasn't been touched yet so there's nothing to gate on.
    if mood is None:
        return CapabilityLevel.UNRESTRICTED

    # mood is [v, a, d, u, g, w, i]
    mood_v = mood[0]
    mood_w = mood[5]

    # Level 4: CRISIS_LOCKOUT (only if explicitly enabled)
    if (config.enable_crisis_lockout and (
        mood_w < config.crisis_lockout_w_floor
        or mood_v < config.crisis_lockout_v_floor
    )):
        return CapabilityLevel.CRISIS_LOCKOUT

    # Level 3: VOICE_ONLY
    if mood_w < config.level3_w_floor or mood_v < config.level3_v_floor:
        return CapabilityLevel.VOICE_ONLY

    # Level 2: READ_ONLY
    if mood_w < config.level2_w_floor or trauma > config.level2_trauma_ceiling:
        return CapabilityLevel.READ_ONLY

    # Level 1: NON_DESTRUCTIVE
    if (mood_w < config.level1_w_floor
        or mood_v < config.level1_v_floor
        or distance > config.level1_distance_ceiling):
        return CapabilityLevel.NON_DESTRUCTIVE

    return CapabilityLevel.UNRESTRICTED


__all__ = ["assess_capability"]
