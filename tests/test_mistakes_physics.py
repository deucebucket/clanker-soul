"""Physics routing + soul-drift branches for the mistakes/corrections
loop. M4 #97.

Covers:
- ``_classify`` returns ``mistake`` and ``correction`` for the new
  pattern sets, in the right priority order.
- ``_update_reservoirs`` routes mistakes to ``physics.mistakes`` and
  corrections to nourishment + relieve.
- ``soul_drift`` wears soul down when mistakes load is over floor and
  uplifts soul when correction load dominates (gated by the opt-in
  ``recovery_resilience_rate``).
"""

from __future__ import annotations

from clanker_soul import (
    CORRECTION_PATTERNS,
    HEAVY_PATTERNS,
    MISTAKE_PATTERNS,
    POSITIVE_PATTERNS,
    EmotionalPhysics,
    PhysicsConfig,
    Score,
    SoulState,
)


# ── disjointness invariants ────────────────────────────────────────────


def test_mistake_patterns_disjoint_from_heavy() -> None:
    assert MISTAKE_PATTERNS.isdisjoint(HEAVY_PATTERNS)


def test_mistake_patterns_disjoint_from_positive() -> None:
    assert MISTAKE_PATTERNS.isdisjoint(POSITIVE_PATTERNS)


def test_mistake_patterns_disjoint_from_correction() -> None:
    assert MISTAKE_PATTERNS.isdisjoint(CORRECTION_PATTERNS)


def test_correction_patterns_subset_of_positive() -> None:
    assert CORRECTION_PATTERNS <= POSITIVE_PATTERNS


# ── routing ────────────────────────────────────────────────────────────


def _physics() -> EmotionalPhysics:
    return EmotionalPhysics(soul=SoulState(), config=PhysicsConfig())


def test_mistake_pattern_routes_to_mistakes_not_trauma() -> None:
    p = _physics()
    s = Score(
        v=120,
        a=125,
        d=110,
        u=55,
        g=120,
        w=120,
        i=110,
        patterns=("TOOL_BAD_CALL",),
    )
    trauma_before = p.trauma.load()
    p.ingest(s)
    assert p.mistakes.load() > 0.0
    assert p.trauma.load() == trauma_before


def test_mistake_pattern_wins_over_heavy_pattern() -> None:
    """If a Score somehow carries both, mistake wins (deterministic
    pessimistic branch — host bug protection)."""
    p = _physics()
    s = Score(
        v=80,
        a=130,
        d=100,
        u=60,
        g=100,
        w=85,
        patterns=("TOOL_BAD_CALL", "BETRAYAL"),
    )
    trauma_before = p.trauma.load()
    p.ingest(s)
    assert p.mistakes.load() > 0.0
    assert p.trauma.load() == trauma_before


def test_tool_timeout_does_not_touch_mistakes() -> None:
    """No MISTAKE pattern → no mistake bucket. TOOL_TIMEOUT is just an
    annoyance, not a self-attributed error."""
    p = _physics()
    s = Score(
        v=118,
        a=138,
        d=115,
        u=60,
        g=122,
        w=128,
        i=115,
        patterns=("TOOL_TIMEOUT",),
    )
    p.ingest(s)
    assert p.mistakes.load() == 0.0


def test_classify_mistake_wins_over_VW_heuristic() -> None:
    """Even a Score with V=80, W=85 (would normally be 'negative')
    routes to mistake when it carries TOOL_BAD_CALL."""
    s = Score(v=80, a=120, d=100, u=60, g=100, w=85, patterns=("TOOL_BAD_CALL",))
    assert EmotionalPhysics._classify(s) == "mistake"


def test_correction_pattern_feeds_nourishment_and_relieves_mistakes() -> None:
    p = _physics()
    # First accumulate mistake weight.
    bad = Score(v=120, a=125, d=110, u=55, g=120, w=120, patterns=("TOOL_BAD_CALL",))
    for _ in range(3):
        p.ingest(bad)
    mistakes_before = p.mistakes.load()
    nourishment_before = p.nourishment.load()
    assert mistakes_before > 0.0

    fix = Score(v=160, a=100, d=170, u=30, g=135, w=150, patterns=("TOOL_FIX",))
    p.ingest(fix)
    # Mistakes relieved.
    assert p.mistakes.load() < mistakes_before
    # Nourishment grew (correction is also nourishment).
    assert p.nourishment.load() > nourishment_before


def test_zero_correction_relief_factor_disables_active_relief() -> None:
    cfg = PhysicsConfig()
    cfg.correction_relief_factor = 0.0
    p = EmotionalPhysics(soul=SoulState(), config=cfg)
    bad = Score(v=120, a=125, d=110, u=55, g=120, w=120, patterns=("TOOL_BAD_CALL",))
    for _ in range(3):
        p.ingest(bad)
    mistakes_before = p.mistakes.load()
    fix = Score(v=160, a=100, d=170, u=30, g=135, w=150, patterns=("TOOL_FIX",))
    p.ingest(fix)
    # No active relief — decay-only, so the value is within ~equal tolerance.
    assert abs(p.mistakes.load() - mistakes_before) < 0.5


def test_ambiguous_mistake_plus_correction_routes_to_mistake() -> None:
    """Host-bug protection: a Score with both pattern types routes to
    mistake (pessimistic branch wins)."""
    p = _physics()
    # Use stronger dims so event_weight clears the _update_reservoirs floor.
    s = Score(
        v=100,
        a=140,
        d=100,
        u=70,
        g=100,
        w=100,
        patterns=("TOOL_BAD_CALL", "TOOL_FIX"),
    )
    nourishment_before = p.nourishment.load()
    p.ingest(s)
    assert p.mistakes.load() > 0.0
    # Did not feed nourishment.
    assert p.nourishment.load() == nourishment_before


def test_classify_picks_mistake_over_correction_for_combined_patterns() -> None:
    """Pure classifier-level check — order: mistake before correction."""
    s = Score(v=140, a=120, d=140, u=40, g=130, w=130, patterns=("TOOL_BAD_CALL", "TOOL_FIX"))
    assert EmotionalPhysics._classify(s) == "mistake"


# ── soul-drift branches ────────────────────────────────────────────────


def test_no_mistake_drift_below_floor() -> None:
    p = _physics()
    p.soul.last_drift_ts = 0.0
    # Single small hit — load is below the default floor (50).
    p.mistakes.add("TOOL_BAD_CALL", weight=10.0, now_ts=1000.0)
    # Snapshot soul before drift so the mood-pull branch (which can move
    # soul independently when mood is unset, but here mood is None) is
    # isolated from the mistakes branch under test.
    w_before = p.soul.w
    v_before = p.soul.v
    p.soul_drift(now_ts=1000.0 + 24 * 3600.0)
    assert p.soul.w == w_before
    assert p.soul.v == v_before


def test_mistake_wear_drops_w_and_v_above_floor() -> None:
    p = _physics()
    p.soul.last_drift_ts = 0.0
    # Pile up enough to drive both W and V drops above the rounding
    # threshold. mistake_wounding_rate is deliberately small (0.0003);
    # short timespans round to 0 via _clamp's int rounding.
    p.mistakes.add("TOOL_BAD_CALL", weight=500.0, now_ts=1000.0)
    w_before = p.soul.w
    v_before = p.soul.v
    g_before = p.soul.g
    p.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    assert p.soul.w < w_before
    assert p.soul.v < v_before
    # G untouched — being-wrong doesn't bleed grounding.
    assert p.soul.g == g_before


def test_mistake_wear_weaker_than_trauma_wear() -> None:
    """For equal accumulated load, trauma's wounding_rate (0.0009) should
    produce more soul wear than mistake_wounding_rate (0.0003).
    Need a long span so both branches produce measurable integer drops."""
    p_mistake = EmotionalPhysics(soul=SoulState(), config=PhysicsConfig())
    p_trauma = EmotionalPhysics(soul=SoulState(), config=PhysicsConfig())
    p_mistake.soul.last_drift_ts = 0.0
    p_trauma.soul.last_drift_ts = 0.0

    p_mistake.mistakes.add("TOOL_BAD_CALL", weight=500.0, now_ts=1000.0)
    p_trauma.trauma.add("BETRAYAL", weight=500.0, now_ts=1000.0)

    w_m_before = p_mistake.soul.w
    w_t_before = p_trauma.soul.w
    p_mistake.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    p_trauma.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    mistake_drop = w_m_before - p_mistake.soul.w
    trauma_drop = w_t_before - p_trauma.soul.w
    assert mistake_drop > 0
    assert trauma_drop > mistake_drop, (
        f"trauma_drop={trauma_drop} should exceed mistake_drop={mistake_drop}"
    )


def test_default_recovery_resilience_rate_branch_does_not_fire() -> None:
    """Default PhysicsConfig has recovery_resilience_rate=0.0 — the
    resilience branch does NOT fire even when correction load
    dominates. Asserted via the report flag because the existing
    imbalance-healing branch can still lift W via the unrelated
    healing_rate path; this test is specifically about the new
    resilience branch's opt-in toggle."""
    cfg = PhysicsConfig()
    assert cfg.recovery_resilience_rate == 0.0
    p = EmotionalPhysics(soul=SoulState(), config=cfg)
    p.soul.last_drift_ts = 0.0
    p.nourishment.add("TOOL_FIX", weight=300.0, now_ts=1000.0)
    report = p.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    assert report.get("resilience_uplift") is False


def test_resilience_uplift_when_opted_in_and_corrections_dominate() -> None:
    cfg = PhysicsConfig()
    cfg.recovery_resilience_rate = 0.0003
    p = EmotionalPhysics(soul=SoulState(), config=cfg)
    p.soul.last_drift_ts = 0.0
    p.nourishment.add("TOOL_FIX", weight=500.0, now_ts=1000.0)
    # Mistakes load 0 → corrections dominate trivially.
    d_before = p.soul.d
    g_before = p.soul.g
    report = p.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    # D is the cleanest signal: the imbalance-healing branch only
    # touches W/V/G, so D moves only via the resilience branch.
    assert p.soul.d > d_before
    # G untouched by resilience (imbalance branch *can* lift it via
    # nourishment, so we accept ≥).
    assert p.soul.g >= g_before
    assert report.get("resilience_uplift") is True


def test_resilience_does_not_trigger_when_mistakes_dominate() -> None:
    """Gate condition: correction_load > mistakes_load. Without it,
    resilience does not build (D unchanged is the clean signal)."""
    cfg = PhysicsConfig()
    cfg.recovery_resilience_rate = 0.0003
    p = EmotionalPhysics(soul=SoulState(), config=cfg)
    p.soul.last_drift_ts = 0.0
    # Mistakes load exceeds correction load → correction NOT > mistakes
    # → no uplift.
    p.nourishment.add("TOOL_FIX", weight=200.0, now_ts=1000.0)
    p.mistakes.add("TOOL_BAD_CALL", weight=300.0, now_ts=1000.0)
    d_before = p.soul.d
    report = p.soul_drift(now_ts=1000.0 + 72 * 3600.0)
    # D only moves via resilience, so unchanged D = no uplift.
    assert p.soul.d == d_before
    assert report.get("resilience_uplift") is False


def test_by_pattern_filtered_returns_only_named_patterns() -> None:
    p = _physics()
    p.nourishment.add("TOOL_FIX", weight=100.0, now_ts=1000.0)
    p.nourishment.add("WARMTH", weight=50.0, now_ts=1000.0)
    correction_only = p.nourishment.by_pattern_filtered(CORRECTION_PATTERNS, now_ts=1000.0)
    total = p.nourishment.load(now_ts=1000.0)
    assert correction_only > 0.0
    assert correction_only < total
