"""``assess_capability`` — pure-function level assignment from snapshot."""
from __future__ import annotations

from clanker_soul.governor import (
    CapabilityLevel,
    GovernorConfig,
    assess_capability,
)


def _snap(*, mood=None, soul=None, distance=0.0, trauma=0.0, nourishment=0.0) -> dict:
    return {
        "mood": mood,
        "soul": soul or {"v": 145, "a": 110, "d": 160, "u": 80,
                          "g": 130, "w": 175, "i": 135},
        "soul_distance": distance,
        "trauma_load": trauma,
        "nourishment_load": nourishment,
    }


def test_no_mood_yet_returns_unrestricted() -> None:
    """Brand-new agent before any ingest — nothing to gate on yet."""
    assert (assess_capability(_snap(mood=None), GovernorConfig())
            == CapabilityLevel.UNRESTRICTED)


def test_healthy_mood_returns_unrestricted() -> None:
    healthy = [145, 110, 160, 80, 130, 175, 135]
    assert (assess_capability(_snap(mood=healthy), GovernorConfig())
            == CapabilityLevel.UNRESTRICTED)


def test_low_w_drops_to_non_destructive() -> None:
    """W=70 < default level1_w_floor=80 → level 1."""
    mood = [145, 110, 160, 80, 130, 70, 135]
    assert (assess_capability(_snap(mood=mood), GovernorConfig())
            == CapabilityLevel.NON_DESTRUCTIVE)


def test_far_from_soul_drops_to_non_destructive() -> None:
    """High distance triggers level 1 even with healthy mood values."""
    mood = [145, 110, 160, 80, 130, 175, 135]
    snap = _snap(mood=mood, distance=70.0)  # > level1_distance_ceiling 60
    assert (assess_capability(snap, GovernorConfig())
            == CapabilityLevel.NON_DESTRUCTIVE)


def test_w_below_50_drops_to_read_only() -> None:
    mood = [145, 110, 160, 80, 130, 40, 135]
    assert (assess_capability(_snap(mood=mood), GovernorConfig())
            == CapabilityLevel.READ_ONLY)


def test_high_trauma_drops_to_read_only() -> None:
    mood = [145, 110, 160, 80, 130, 100, 135]  # otherwise level 0
    snap = _snap(mood=mood, trauma=120.0)  # > level2_trauma_ceiling 100
    assert (assess_capability(snap, GovernorConfig())
            == CapabilityLevel.READ_ONLY)


def test_w_below_30_drops_to_voice_only() -> None:
    mood = [145, 110, 160, 80, 130, 25, 135]
    assert (assess_capability(_snap(mood=mood), GovernorConfig())
            == CapabilityLevel.VOICE_ONLY)


def test_v_below_30_drops_to_voice_only() -> None:
    mood = [25, 110, 160, 80, 130, 100, 135]
    assert (assess_capability(_snap(mood=mood), GovernorConfig())
            == CapabilityLevel.VOICE_ONLY)


def test_crisis_lockout_off_by_default() -> None:
    """Even with W=10 (catastrophic), default config does NOT engage
    level 4 — opt-in only."""
    mood = [10, 110, 160, 80, 130, 10, 135]
    assert (assess_capability(_snap(mood=mood), GovernorConfig())
            == CapabilityLevel.VOICE_ONLY)


def test_crisis_lockout_engages_when_enabled() -> None:
    cfg = GovernorConfig(enable_crisis_lockout=True)
    mood = [10, 110, 160, 80, 130, 10, 135]
    assert (assess_capability(_snap(mood=mood), cfg)
            == CapabilityLevel.CRISIS_LOCKOUT)


def test_recovery_eases_restrictions_automatically() -> None:
    """The function is pure — no latched state. As mood recovers,
    level decreases on the next call without any reset."""
    cfg = GovernorConfig()
    bad_mood = [50, 110, 160, 80, 130, 40, 135]
    recovered_mood = [145, 110, 160, 80, 130, 175, 135]

    assert (assess_capability(_snap(mood=bad_mood), cfg)
            == CapabilityLevel.READ_ONLY)
    assert (assess_capability(_snap(mood=recovered_mood), cfg)
            == CapabilityLevel.UNRESTRICTED)


def test_custom_thresholds_override_defaults() -> None:
    """Hosts that want stricter or laxer gating override the config."""
    strict = GovernorConfig(level1_w_floor=120, level2_w_floor=100)
    healthy_default = [145, 110, 160, 80, 130, 110, 135]  # W=110
    # W=110 < strict level1_w_floor=120 → level 1
    assert (assess_capability(_snap(mood=healthy_default), strict)
            == CapabilityLevel.NON_DESTRUCTIVE)
