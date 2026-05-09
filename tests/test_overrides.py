"""ConfigOverrides — live-tunable PhysicsConfig + SoulState.

Verifies the partial-merge semantics: the UI can write one knob at a
time, removing a knob reverts it to the constructor default, and other
fields (including drift-modified soul fields) are not disturbed.
"""
from __future__ import annotations

import pytest

from clanker_soul import (
    EmotionalPhysics,
    PhysicsConfig,
    Score,
    SoulState,
    SoulStore,
)
from clanker_soul.overrides import (
    ConfigOverrides,
    OverrideBundle,
    apply_overrides,
)


# ---------------------------------------------------------------------------
# Pure apply_overrides
# ---------------------------------------------------------------------------


def test_apply_overrides_empty_returns_inputs_unchanged() -> None:
    config = PhysicsConfig(blend_alpha=0.55)
    soul = SoulState(v=145, w=175)
    new_config, new_soul = apply_overrides(
        config, soul, OverrideBundle(physics={}, soul={}),
    )
    assert new_config.blend_alpha == 0.55
    assert new_soul.v == 145 and new_soul.w == 175


def test_apply_overrides_partial_physics() -> None:
    config = PhysicsConfig(blend_alpha=0.55, armor_max=0.55)
    soul = SoulState()
    new_config, _ = apply_overrides(
        config, soul, OverrideBundle(physics={"blend_alpha": 0.7}, soul={}),
    )
    assert new_config.blend_alpha == 0.7
    assert new_config.armor_max == 0.55  # unchanged


def test_apply_overrides_partial_soul() -> None:
    config = PhysicsConfig()
    soul = SoulState(v=145, w=175, d=160)
    _, new_soul = apply_overrides(
        config, soul, OverrideBundle(physics={}, soul={"v": 200}),
    )
    assert new_soul.v == 200
    assert new_soul.w == 175 and new_soul.d == 160


def test_apply_overrides_unknown_keys_logged_and_ignored(caplog) -> None:
    config = PhysicsConfig()
    soul = SoulState()
    new_config, new_soul = apply_overrides(
        config, soul,
        OverrideBundle(physics={"nonexistent_field": 0.5},
                       soul={"also_nonexistent": 99}),
    )
    # No exception raised; original values intact.
    assert new_config.blend_alpha == config.blend_alpha
    assert new_soul.v == soul.v
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("nonexistent_field" in r.message for r in warnings)
    assert any("also_nonexistent" in r.message for r in warnings)


# ---------------------------------------------------------------------------
# ConfigOverrides storage round-trip
# ---------------------------------------------------------------------------


def test_overrides_get_returns_empty_for_unknown_agent(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    bundle = overrides.get("never-set")
    assert bundle.physics == {} and bundle.soul == {}


def test_overrides_set_and_get_round_trip(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("agent-1", physics={"blend_alpha": 0.7}, soul={"w": 200})
    bundle = overrides.get("agent-1")
    assert bundle.physics == {"blend_alpha": 0.7}
    assert bundle.soul == {"w": 200}


def test_overrides_update_merges_with_existing(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("agent-1", physics={"blend_alpha": 0.7}, soul={"w": 200})
    # update only adds a new field; existing ones preserved
    overrides.update("agent-1", physics={"armor_max": 0.8})
    bundle = overrides.get("agent-1")
    assert bundle.physics == {"blend_alpha": 0.7, "armor_max": 0.8}
    assert bundle.soul == {"w": 200}


def test_overrides_clear_removes_row(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("agent-1", physics={"blend_alpha": 0.7}, soul={})
    overrides.clear("agent-1")
    bundle = overrides.get("agent-1")
    assert bundle.physics == {} and bundle.soul == {}


# ---------------------------------------------------------------------------
# EmotionalPhysics.reload_overrides
# ---------------------------------------------------------------------------


def test_reload_overrides_applies_partial_physics(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("x", physics={"blend_alpha": 0.8}, soul={})

    physics = EmotionalPhysics(
        soul=SoulState(),
        config=PhysicsConfig(blend_alpha=0.55, armor_max=0.55),
        overrides=overrides, agent_id="x",
    )
    assert physics.config.blend_alpha == 0.55  # not yet applied
    physics.reload_overrides()
    assert physics.config.blend_alpha == 0.8
    assert physics.config.armor_max == 0.55  # untouched


def test_reload_overrides_applies_soul_field(tmp_path) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("x", physics={}, soul={"w": 220})

    physics = EmotionalPhysics(
        soul=SoulState(v=145, w=175),
        overrides=overrides, agent_id="x",
    )
    physics.reload_overrides()
    assert physics.soul.w == 220
    assert physics.soul.v == 145  # untouched


def test_reload_overrides_removed_field_reverts_to_constructor(tmp_path) -> None:
    """Setting an override then clearing it must revert that field to
    its constructor value, NOT leave the override in place."""
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)

    physics = EmotionalPhysics(
        soul=SoulState(v=145, w=175),
        config=PhysicsConfig(blend_alpha=0.55),
        overrides=overrides, agent_id="x",
    )
    overrides.set("x", physics={"blend_alpha": 0.9}, soul={"w": 220})
    physics.reload_overrides()
    assert physics.config.blend_alpha == 0.9 and physics.soul.w == 220

    # Now remove the overrides — both fields should revert.
    overrides.set("x", physics={}, soul={})
    physics.reload_overrides()
    assert physics.config.blend_alpha == 0.55
    assert physics.soul.w == 175


def test_reload_overrides_does_not_clobber_drifted_soul_fields(tmp_path) -> None:
    """If soul.v drifts during operation and the user has only
    overridden soul.w, calling reload_overrides must NOT reset soul.v
    back to its constructor value."""
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("x", physics={}, soul={"w": 200})

    physics = EmotionalPhysics(
        soul=SoulState(v=145, w=175),
        overrides=overrides, agent_id="x",
    )
    physics.reload_overrides()
    assert physics.soul.w == 200

    # Simulate drift on V (different field than the one overridden).
    physics.soul.v = 130

    # Reload again — soul.v should stay drifted, soul.w stays overridden.
    physics.reload_overrides()
    assert physics.soul.v == 130, "drift on un-overridden field was clobbered"
    assert physics.soul.w == 200


def test_reload_overrides_no_overrides_provider_is_noop() -> None:
    """Construction without an overrides provider — reload_overrides
    must be safe to call (and a no-op)."""
    physics = EmotionalPhysics(soul=SoulState())
    physics.reload_overrides()  # must not raise
    assert physics.soul.v == 145


def test_reload_overrides_ignores_unknown_keys(tmp_path, caplog) -> None:
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)
    overrides.set("x", physics={"new_field_in_v3": 0.5}, soul={})

    physics = EmotionalPhysics(
        soul=SoulState(),
        config=PhysicsConfig(blend_alpha=0.55),
        overrides=overrides, agent_id="x",
    )
    physics.reload_overrides()
    # Existing fields untouched; warning logged.
    assert physics.config.blend_alpha == 0.55
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("new_field_in_v3" in r.message for r in warnings)


def test_reload_overrides_ingest_picks_up_new_blend_alpha(tmp_path) -> None:
    """End-to-end: reload an override, then ingest, and observe the
    physics behaved according to the overridden config."""
    store = SoulStore(tmp_path / "co.db")
    overrides = ConfigOverrides(store)

    # Construct with a tiny blend_alpha first.
    physics = EmotionalPhysics(
        soul=SoulState(v=145, w=175),
        config=PhysicsConfig(blend_alpha=0.05),
        overrides=overrides, agent_id="x",
    )
    physics.ingest(Score(v=10, w=10))
    barely_moved = physics.mood

    # Now override to a much stronger blend.
    overrides.set("x", physics={"blend_alpha": 0.9}, soul={})
    physics.reload_overrides()
    physics.ingest(Score(v=10, w=10))
    much_moved = physics.mood

    assert barely_moved is not None and much_moved is not None
    # Stronger blend → mood pulled further from soul on the same hit.
    assert much_moved.v < barely_moved.v
