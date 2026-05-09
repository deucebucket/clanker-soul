"""Personality presets — child / adult / brittle / stoic."""
from __future__ import annotations


from clanker_soul import (
    EmotionalPhysics,
    PhysicsConfig,
    Score,
    SoulState,
    SoulStore,
    soul_armor,
)
from clanker_soul.overrides import ConfigOverrides
from clanker_soul.presets import (
    ADULT,
    ALL,
    BRITTLE,
    CHILD,
    Preset,
    STOIC,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_all_four_presets_exist() -> None:
    assert {"child", "adult", "brittle", "stoic"} <= set(ALL.keys())


def test_each_preset_has_name_and_description() -> None:
    for preset in ALL.values():
        assert isinstance(preset, Preset)
        assert preset.name and isinstance(preset.name, str)
        assert preset.description and len(preset.description) > 20


def test_preset_lookup_by_name() -> None:
    assert ALL["child"] is CHILD
    assert ALL["adult"] is ADULT
    assert ALL["brittle"] is BRITTLE
    assert ALL["stoic"] is STOIC


# ---------------------------------------------------------------------------
# Personality fingerprints
# ---------------------------------------------------------------------------


def test_child_has_lower_armor_than_adult() -> None:
    """The whole point of CHILD: low W, low D — events land hard."""
    assert soul_armor(CHILD.soul) < soul_armor(ADULT.soul)


def test_stoic_has_higher_armor_than_adult() -> None:
    assert soul_armor(STOIC.soul) > soul_armor(ADULT.soul)


def test_brittle_has_low_armor_max_config() -> None:
    """BRITTLE turns armor cap WAY down so resilience math can't save it."""
    assert BRITTLE.config.armor_max < ADULT.config.armor_max
    assert BRITTLE.config.dim_resilience_max < ADULT.config.dim_resilience_max


def test_stoic_has_high_armor_max_config() -> None:
    assert STOIC.config.armor_max > ADULT.config.armor_max


def test_brittle_breaches_more_easily() -> None:
    """Lower breach_threshold means smaller mood-vs-soul gaps trigger
    soul leak on heavy events."""
    assert BRITTLE.config.breach_threshold < ADULT.config.breach_threshold


# ---------------------------------------------------------------------------
# Behavioral divergence under identical input
# ---------------------------------------------------------------------------


def test_brittle_and_stoic_diverge_under_identical_events() -> None:
    """Critical: same event stream, two different presets, different
    final state. Without this, the presets are decorative."""
    brittle = EmotionalPhysics(
        soul=SoulState(**_soul_dict(BRITTLE.soul)),
        config=PhysicsConfig(**_config_dict(BRITTLE.config)),
    )
    stoic = EmotionalPhysics(
        soul=SoulState(**_soul_dict(STOIC.soul)),
        config=PhysicsConfig(**_config_dict(STOIC.config)),
    )

    events = [
        Score(v=40, w=40, u=200, patterns=("ABANDONMENT",)),
        Score(v=30, w=30, u=180, patterns=("EXISTENTIAL_NEGATION",)),
        Score(v=200, w=200, patterns=("AFFIRMATION",)),
        Score(v=20, w=20, u=220, patterns=("BETRAYAL",)),
    ]
    for e in events:
        brittle.ingest(e)
        stoic.ingest(e)

    assert brittle.mood is not None and stoic.mood is not None
    # STOIC stays closer to its soul — V should be much higher than BRITTLE's.
    assert stoic.mood.v - brittle.mood.v >= 15, (
        f"STOIC mood.v={stoic.mood.v}, BRITTLE mood.v={brittle.mood.v} — "
        "presets did not produce distinguishable behavior"
    )


def _soul_dict(soul: SoulState) -> dict:
    """Helper: extract just the personality fields from a SoulState."""
    return {
        "v": soul.v, "a": soul.a, "d": soul.d, "u": soul.u,
        "g": soul.g, "w": soul.w, "i": soul.i,
    }


def _config_dict(config: PhysicsConfig) -> dict:
    from dataclasses import asdict
    return asdict(config)


# ---------------------------------------------------------------------------
# Preset.apply()
# ---------------------------------------------------------------------------


def test_apply_writes_full_override_bundle(tmp_path) -> None:
    store = SoulStore(tmp_path / "p.db")
    overrides = ConfigOverrides(store)
    BRITTLE.apply(overrides, "agent-1")
    bundle = overrides.get("agent-1")
    # All physics fields override-set
    assert "blend_alpha" in bundle.physics
    assert "armor_max" in bundle.physics
    assert bundle.physics["armor_max"] == BRITTLE.config.armor_max
    # Personality soul fields override-set
    for f in ("v", "a", "d", "u", "g", "w", "i"):
        assert f in bundle.soul
    # Bookkeeping fields NOT in override (those drift independently)
    assert "last_drift_ts" not in bundle.soul
    assert "last_save_ts" not in bundle.soul


def test_apply_then_reload_changes_running_engine(tmp_path) -> None:
    store = SoulStore(tmp_path / "p.db")
    overrides = ConfigOverrides(store)
    physics = EmotionalPhysics(
        soul=SoulState(),  # adult defaults
        config=PhysicsConfig(),
        overrides=overrides, agent_id="x",
    )
    assert physics.soul.w == 175  # ADULT default

    CHILD.apply(overrides, "x")
    physics.reload_overrides()
    assert physics.soul.w == CHILD.soul.w
    assert physics.soul.d == CHILD.soul.d
    assert physics.config.armor_max == CHILD.config.armor_max


def test_switching_presets_replaces_overrides(tmp_path) -> None:
    """Switching from BRITTLE to STOIC must REPLACE the overrides, not
    merge them. Otherwise BRITTLE-specific knobs would linger."""
    store = SoulStore(tmp_path / "p.db")
    overrides = ConfigOverrides(store)
    BRITTLE.apply(overrides, "x")
    STOIC.apply(overrides, "x")

    bundle = overrides.get("x")
    # Should reflect STOIC values, not BRITTLE values.
    assert bundle.physics["armor_max"] == STOIC.config.armor_max
    assert bundle.soul["w"] == STOIC.soul.w
