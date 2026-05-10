"""``EmotionalPhysics.contemplate`` — synthetic mood-shift from a
``PromptFace.vadugwi_affinity``, without breach or reservoirs.

Covers the M4 contemplation primitive (#80): a thought is not an event.
Mood moves; soul reservoirs and breach do not.
"""

from __future__ import annotations

import pytest

from clanker_soul import (
    ContemplationResult,
    EmotionalPhysics,
    PhysicsConfig,
    PromptFace,
    Score,
    SoulState,
    VadugwiPredicate,
)


def _physics(soul: SoulState | None = None, cfg: PhysicsConfig | None = None) -> EmotionalPhysics:
    return EmotionalPhysics(soul=soul or SoulState(), config=cfg or PhysicsConfig())


def _face(
    *,
    affinity: tuple[int, int, int, int, int, int, int] | None = (60, 100, 90, 80, 200, 80, 110),
    fid: str = "test.face",
) -> PromptFace:
    return PromptFace(
        id=fid,
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="why am i like this?",
        vadugwi_affinity=affinity,
    )


def test_contemplate_returns_pre_post_delta_and_score() -> None:
    p = _physics()
    face = _face()
    result = p.contemplate(face)
    assert isinstance(result, ContemplationResult)
    assert len(result.pre_mood) == 7
    assert len(result.post_mood) == 7
    assert len(result.delta) == 7
    # Delta is post - pre per dim, exactly.
    for i, (pre, post, d) in enumerate(zip(result.pre_mood, result.post_mood, result.delta)):
        assert post - pre == d, f"dim {i}: post-pre={post - pre} but delta={d}"
    # Score carries the CONTEMPLATION pattern marker
    assert result.score.patterns == ("CONTEMPLATION",)


def test_contemplate_moves_mood_toward_affinity() -> None:
    # Soul-anchored start at default ~ (145, 110, 160, 80, 130, 175, 135)
    p = _physics()
    # Heavy face: V=60 (low), G=200 (high). Mood should move toward those.
    face = _face(affinity=(60, 100, 90, 80, 200, 80, 110))
    result = p.contemplate(face)
    # V moved down (toward 60 from default V=145)
    assert result.delta[0] < 0, (
        f"V should drop; pre={result.pre_mood[0]} post={result.post_mood[0]}"
    )
    # G moved up (toward 200 from default G=130)
    assert result.delta[4] > 0, (
        f"G should rise; pre={result.pre_mood[4]} post={result.post_mood[4]}"
    )


def test_contemplate_does_not_update_reservoirs() -> None:
    """A thought is not an event. Trauma and nourishment must stay flat."""
    p = _physics()
    trauma_before = p.trauma.load()
    nourishment_before = p.nourishment.load()
    # Heavy contemplation that *would* hit trauma if treated as ingest:
    face = _face(affinity=(40, 180, 50, 180, 230, 40, 100))
    p.contemplate(face)
    assert p.trauma.load() == trauma_before
    assert p.nourishment.load() == nourishment_before


def test_contemplate_does_not_breach_soul() -> None:
    """Even a heavy contemplation must not leak into Soul. Breach is
    reserved for real events."""
    soul_before = SoulState(v=145, a=110, d=160, u=80, g=130, w=175, i=135)
    p = _physics(
        soul=SoulState(**{f: getattr(soul_before, f) for f in ("v", "a", "d", "u", "g", "w", "i")})
    )
    # First put mood far from soul so a real ingest *would* breach.
    p.ingest(Score(v=40, a=180, d=70, u=180, g=80, w=50, i=110, patterns=("ABANDONMENT",)))
    soul_after_event = (p.soul.v, p.soul.w, p.soul.g)
    # Now contemplate something heavy with the breach-trigger pattern logic
    # (contemplation injects its own pattern, not HEAVY ones — extra safety):
    face = _face(affinity=(30, 200, 40, 200, 250, 30, 100))
    p.contemplate(face)
    # Soul V/W/G must not have moved from the contemplation alone.
    assert (p.soul.v, p.soul.w, p.soul.g) == soul_after_event


def test_contemplate_raises_when_affinity_missing() -> None:
    p = _physics()
    face = PromptFace(
        id="no.affinity",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="x",
    )
    with pytest.raises(ValueError, match="vadugwi_affinity is None"):
        p.contemplate(face)


def test_contemplate_first_call_uses_soul_anchor() -> None:
    """No prior mood -> pre_mood is the soul-anchored baseline."""
    soul = SoulState(v=160, a=120, d=170, u=70, g=140, w=190, i=140)
    p = _physics(soul=soul)
    assert p.mood is None  # confirm starting state
    face = _face(affinity=(80, 140, 100, 100, 180, 90, 130))
    result = p.contemplate(face)
    assert result.pre_mood == (160, 120, 170, 70, 140, 190, 140)


def test_contemplate_high_w_resilience_dampens_shift() -> None:
    """A high-W agent contemplating a heavy face moves less than a
    low-W agent contemplating the same face. Personality stays
    load-bearing."""
    cfg = PhysicsConfig()
    high_w = EmotionalPhysics(soul=SoulState(w=240, v=170, d=180, g=140), config=cfg)
    low_w = EmotionalPhysics(soul=SoulState(w=60, v=170, d=180, g=140), config=cfg)
    face = _face(affinity=(40, 180, 50, 200, 230, 40, 100))

    r_high = high_w.contemplate(face)
    r_low = low_w.contemplate(face)
    # Low-W agent should drop more in V than high-W agent.
    assert r_low.delta[0] < r_high.delta[0], (
        f"Expected high-W to be more cushioned. "
        f"high-W delta_V={r_high.delta[0]}, low-W delta_V={r_low.delta[0]}"
    )


def test_contemplate_weight_scale_attenuates() -> None:
    p_full = _physics()
    p_half = _physics()
    face = _face(affinity=(40, 150, 80, 100, 200, 80, 100))
    r_full = p_full.contemplate(face, weight_scale=1.0)
    r_half = p_half.contemplate(face, weight_scale=0.3)
    # Half-weight contemplation should move V less than full-weight.
    assert abs(r_half.delta[0]) < abs(r_full.delta[0]), (
        f"weight_scale=0.3 delta_V={r_half.delta[0]}, weight_scale=1.0 delta_V={r_full.delta[0]}"
    )


def test_contemplate_weight_scale_clamped_to_zero_floor() -> None:
    """Negative weight_scale should clamp to 0 (no shift, but no error)."""
    p = _physics()
    face = _face()
    pre_mood = p._mood_anchor().as_tuple()
    result = p.contemplate(face, weight_scale=-0.5)
    # No movement when weight is clamped to zero.
    assert result.delta == (0, 0, 0, 0, 0, 0, 0)
    assert result.post_mood == pre_mood


def test_contemplate_logs_no_event_record() -> None:
    """Contemplation must not write an IngestRecord to the event log —
    a thought is not an event. We verify by giving physics a fake
    event log and asserting it never gets called."""
    calls: list[str] = []

    class FakeLog:
        def log_ingest(self, _rec: object) -> None:
            calls.append("ingest")

        def log_pulse(self, _rec: object) -> None:
            calls.append("pulse")

    p = EmotionalPhysics(
        soul=SoulState(),
        config=PhysicsConfig(),
        event_log=FakeLog(),  # type: ignore[arg-type]
        agent_id="contemplate-test",
    )
    face = _face()
    p.contemplate(face)
    assert calls == [], f"contemplate should not write to event log; got {calls}"


def test_promptface_validates_affinity_length() -> None:
    with pytest.raises(ValueError, match="must be a 7-tuple"):
        PromptFace(
            id="bad.affinity",
            trigger_kinds=frozenset({"reflective_impulse"}),
            template="x",
            vadugwi_affinity=(60, 100, 90),  # type: ignore[arg-type]
        )


def test_promptface_validates_affinity_range() -> None:
    with pytest.raises(ValueError, match="must be in 0..255"):
        PromptFace(
            id="bad.range",
            trigger_kinds=frozenset({"reflective_impulse"}),
            template="x",
            vadugwi_affinity=(60, 100, 300, 80, 200, 80, 110),
        )


def test_promptface_affinity_is_optional() -> None:
    """Existing PromptFace constructions without affinity must still work."""
    face = PromptFace(
        id="no.affinity.is.fine",
        trigger_kinds=frozenset({"reflective_impulse"}),
        vadugwi_predicates=(VadugwiPredicate(dim="V", op=">=", value=160),),
        template="hi",
    )
    assert face.vadugwi_affinity is None
