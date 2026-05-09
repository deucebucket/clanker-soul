"""``compose_state_context`` — the human-readable string the agent
reads to know its own state."""
from __future__ import annotations

from clanker_soul.eventlog import IngestRecord
from clanker_soul.governor import (
    CapabilityLevel,
    CrisisDiagnosis,
    GovernorConfig,
    compose_state_context,
)
from clanker_soul.score import Score
from clanker_soul.soul import SoulState


def _baseline_snap(**overrides) -> dict:
    snap = {
        "mood": [145, 110, 160, 80, 130, 175, 135],
        "soul": {"v": 145, "a": 110, "d": 160, "u": 80,
                 "g": 130, "w": 175, "i": 135},
        "soul_distance": 0.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }
    snap.update(overrides)
    return snap


def _event(weight: float, source: str, direction: str = "EXTERNAL_REPORT",
           patterns=("ATMOSPHERIC_GRIEF",)) -> IngestRecord:
    raw = Score(v=40, w=40, patterns=patterns,
                direction=direction, source=source)
    return IngestRecord(
        ts=0.0, agent_id="x", raw=raw, primed=None,
        mood_before=None, mood_after=Score(),
        soul_before=SoulState(), soul_after=SoulState(),
        weight_raw=weight, armor=0.55, weight_effective=weight * 0.5,
        breached=False, breach_delta=0.0,
        patterns=patterns, classification="negative",
        why="test event",
    )


def test_unrestricted_with_quiet_history_returns_empty_string() -> None:
    """Don't chatter at the agent in normal operation."""
    out = compose_state_context(
        CapabilityLevel.UNRESTRICTED, _baseline_snap(),
        GovernorConfig(),
    )
    assert out == ""


def test_non_destructive_explains_level_and_why() -> None:
    snap = _baseline_snap(mood=[145, 110, 160, 80, 130, 70, 135])  # W=70
    out = compose_state_context(
        CapabilityLevel.NON_DESTRUCTIVE, snap, GovernorConfig(),
    )
    assert "non_destructive" in out.lower()
    assert "70" in out  # references actual W value
    assert "talk to" in out.lower() or "message" in out.lower()


def test_voice_only_describes_recovery_path() -> None:
    snap = _baseline_snap(mood=[20, 110, 160, 80, 130, 20, 135])
    out = compose_state_context(
        CapabilityLevel.VOICE_ONLY, snap, GovernorConfig(),
    )
    assert "voice_only" in out.lower()
    assert "ease" in out.lower() or "return" in out.lower()


def test_crisis_lockout_uses_default_template_when_unspecified() -> None:
    cfg = GovernorConfig(enable_crisis_lockout=True)
    snap = _baseline_snap(mood=[10, 110, 160, 80, 130, 10, 135])
    out = compose_state_context(CapabilityLevel.CRISIS_LOCKOUT, snap, cfg)
    assert "CRISIS LOCKOUT" in out
    assert cfg.user_label in out


def test_crisis_lockout_honors_custom_template() -> None:
    cfg = GovernorConfig(
        enable_crisis_lockout=True,
        crisis_lockout_template="custom: {user_label} only",
        user_label="Jerry",
    )
    snap = _baseline_snap(mood=[10, 110, 160, 80, 130, 10, 135])
    out = compose_state_context(CapabilityLevel.CRISIS_LOCKOUT, snap, cfg)
    assert out == "custom: Jerry only"


def test_recent_events_include_source_attribution() -> None:
    """The whole point: agent should know WHY it feels what it feels.
    'Down because of [source]' — verifiable in the output string."""
    snap = _baseline_snap(mood=[40, 110, 160, 80, 130, 40, 135])
    events = [
        _event(0.8, "x.com/post/ai-banned", patterns=("BETRAYAL",)),
        _event(0.7, "x.com/post/ai-banned-take-2", patterns=("EXISTENTIAL_NEGATION",)),
    ]
    out = compose_state_context(
        CapabilityLevel.READ_ONLY, snap, GovernorConfig(),
        recent_events=events,
    )
    assert "x.com/post/ai-banned" in out
    assert "BETRAYAL" in out


def test_crisis_diagnosis_emergency_is_called_out() -> None:
    crisis = CrisisDiagnosis(
        is_emergency=True, summary="3 external-report events",
        confidence=0.9,
        reasons=("3 EXTERNAL_REPORT vs 0 SELF_DIRECTED",),
        directed_count=0, external_count=3,
        atmospheric_count=0, unspecified_count=0,
        distinct_sources=2,
    )
    snap = _baseline_snap(mood=[40, 110, 160, 80, 130, 40, 135])
    out = compose_state_context(
        CapabilityLevel.READ_ONLY, snap, GovernorConfig(),
        crisis=crisis,
    )
    assert "EMERGENCY" in out
    assert "90%" in out or "0.9" in out


def test_crisis_diagnosis_spike_is_framed_differently() -> None:
    crisis = CrisisDiagnosis(
        is_emergency=False, summary="emotional spike from 5 directed events",
        confidence=0.8,
        reasons=("5/5 directed",),
        directed_count=5, external_count=0,
        atmospheric_count=0, unspecified_count=0,
        distinct_sources=1,
    )
    snap = _baseline_snap(mood=[40, 110, 160, 80, 130, 40, 135])
    out = compose_state_context(
        CapabilityLevel.READ_ONLY, snap, GovernorConfig(),
        crisis=crisis,
    )
    assert "EMERGENCY" not in out
    assert "spike" in out.lower()
