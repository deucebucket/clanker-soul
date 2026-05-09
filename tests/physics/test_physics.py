"""EmotionalPhysics — mood, soul-armor, dim-resilience, mood-prime, breach."""

from __future__ import annotations

import pytest

from clanker_soul import (
    EmotionalPhysics,
    PhysicsConfig,
    Score,
    SoulState,
    dim_resilience,
    mood_prime_score,
    soul_armor,
    soul_distance,
)


def _physics(soul: SoulState | None = None, cfg: PhysicsConfig | None = None) -> EmotionalPhysics:
    return EmotionalPhysics(soul=soul or SoulState(), config=cfg or PhysicsConfig())


def test_first_ingest_seeds_mood_close_to_score() -> None:
    p = _physics()
    s = Score(v=200, w=200, d=160)
    p.ingest(s)
    assert p.mood is not None
    # First event blends with default-mood (soul-projected); we don't
    # require equality, only that it moved meaningfully toward the score.
    assert p.mood.v > 145


def test_soul_drift_runs_without_event() -> None:
    """soul_drift is the slow bookkeeping pass — it should run cleanly
    when called between events and return a dict describing what moved."""
    p = _physics(SoulState(v=160, w=180))
    out = p.soul_drift(now_ts=0.0)
    assert isinstance(out, dict)


def test_negative_event_loads_trauma_reservoir() -> None:
    p = _physics()
    p.ingest(Score(v=40, a=180, d=70, u=180, g=80, w=50, i=110, patterns=("ABANDONMENT",)))
    assert p.trauma.load() > 0


def test_positive_event_loads_nourishment_reservoir() -> None:
    p = _physics()
    p.ingest(Score(v=220, a=140, d=170, u=20, g=160, w=210, i=160, patterns=("AFFIRMATION",)))
    # Nourishment OR mood should reflect a positive movement; we only
    # require that the positive event moved *something* in the right
    # direction in at least one of the two channels.
    assert p.nourishment.load() > 0 or (p.mood is not None and p.mood.v > 145)


def test_dim_resilience_cushions_small_hit_but_not_large() -> None:
    """The whole point: small bumps get absorbed, big ones still land."""
    soul = SoulState(v=160, w=180, d=160)
    p1 = _physics(soul)
    p1.ingest(Score(v=140, w=160, d=140))  # ~20-pt drop on each
    cushioned = p1.mood.w

    p2 = _physics(soul)
    p2.ingest(Score(v=40, w=40, d=40))  # ~140-pt drop
    landed = p2.mood.w

    # Cushioned event ends up much closer to soul than landed event.
    assert cushioned > landed + 30


def test_mood_prime_carries_forward_emotional_context() -> None:
    """If mood is already up, the next score gets read slightly more
    positively. This is the actual context-carrying piece."""
    happy_mood = Score(v=200, a=128, d=160, u=0, g=140, w=200, i=150)
    primed = mood_prime_score(Score(v=128, w=128), happy_mood, factor=0.1)
    # 0.1 * (200-128) = +7.2 on V, +7.2 on W
    assert primed.v > 128
    assert primed.w > 128

    sad_mood = Score(v=60, a=128, d=80, u=0, g=110, w=70, i=110)
    primed_sad = mood_prime_score(Score(v=128, w=128), sad_mood, factor=0.1)
    assert primed_sad.v < 128
    assert primed_sad.w < 128


def test_mood_prime_zero_factor_is_identity() -> None:
    happy = Score(v=200, w=200)
    primed = mood_prime_score(Score(v=128, w=128), happy, factor=0.0)
    assert primed.v == 128 and primed.w == 128


def test_soul_armor_increases_with_w_d_g() -> None:
    weak = SoulState(v=128, w=80, d=80, g=80)
    strong = SoulState(v=128, w=200, d=200, g=180)
    assert soul_armor(strong) > soul_armor(weak)


def test_soul_distance_zero_when_mood_matches_soul() -> None:
    soul = SoulState(v=145, d=160, g=130, w=175)
    matched_mood = Score(v=145, a=128, d=160, u=0, g=130, w=175, i=135)
    assert soul_distance(matched_mood, soul) < 1.0


def test_dim_resilience_returns_per_dim_pulls_capped_at_max() -> None:
    soul = SoulState(v=255, a=255, d=255, u=255, g=255, w=255, i=255)
    pulls = dim_resilience(soul, dim_resilience_max=0.5)
    assert isinstance(pulls, tuple) and len(pulls) == 7
    for p in pulls:
        assert 0.0 <= p <= 0.5
    # At max soul values, every pull should saturate at the cap.
    assert all(p == pytest.approx(0.5) for p in pulls)


def test_back_to_back_heavy_hits_eventually_breach() -> None:
    """The breach mechanic — repeated heavy events during an unhealed
    wound bypass the slow filter and leak into Soul itself."""
    cfg = PhysicsConfig()
    soul = SoulState(v=160, w=180, d=160)
    p = _physics(soul, cfg)
    starting_soul_v = p.soul.v
    for _ in range(8):
        p.ingest(Score(v=10, w=10, d=20, patterns=("EXISTENTIAL_NEGATION",)))
    # Soul itself should have moved (breach), not just mood.
    assert p.soul.v < starting_soul_v
