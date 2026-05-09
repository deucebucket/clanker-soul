"""SoulPlugin — the documented one-call drop-in entry point.

Verifies the full lifecycle: construct, ingest, tick, save, close,
reopen — state must persist; events must be logged; presets must
apply via the plugin's bundled overrides provider.
"""
from __future__ import annotations

import pytest

from clanker_soul import (
    BRITTLE,
    CHILD,
    Score,
    SoulState,
    SoulStore,
    STOIC,
    SqliteEventLog,
)
from clanker_soul.plugin import SoulPlugin


# ---------------------------------------------------------------------------
# Hello world
# ---------------------------------------------------------------------------


def test_four_line_hello_world(tmp_path) -> None:
    """The doc-promised one-call drop-in: construct, ingest, tick, save.
    Every line should just work."""
    plugin = SoulPlugin(agent_id="agent-1", db_path=tmp_path / "soul.db")
    plugin.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
    plugin.tick()
    plugin.save()
    plugin.close()


def test_context_manager_auto_saves(tmp_path) -> None:
    db = tmp_path / "ctx.db"
    with SoulPlugin(agent_id="ctx-agent", db_path=db) as plugin:
        plugin.ingest(Score(v=200, w=210, patterns=("AFFIRMATION",)))
    # Reopen and verify the soul + mood survived.
    with SoulPlugin(agent_id="ctx-agent", db_path=db) as p2:
        snap = p2.snapshot()
    assert snap["nourishment_load"] > 0


# ---------------------------------------------------------------------------
# Snapshot shape (PulseHost compat)
# ---------------------------------------------------------------------------


def test_snapshot_returns_pulsehost_shape(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "s.db")
    plugin.ingest(Score(v=80, w=60, patterns=("ABANDONMENT",)))
    snap = plugin.snapshot()
    assert "soul" in snap and isinstance(snap["soul"], dict)
    assert "mood" in snap and isinstance(snap["mood"], list)
    assert "soul_distance" in snap
    assert "trauma_load" in snap and snap["trauma_load"] > 0
    assert "nourishment_load" in snap


def test_snapshot_mood_none_before_first_ingest(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "s.db")
    snap = plugin.snapshot()
    assert snap["mood"] is None


# ---------------------------------------------------------------------------
# State persistence across close/reopen
# ---------------------------------------------------------------------------


def test_state_survives_close_and_reopen(tmp_path) -> None:
    """5 events in, close, reopen with same agent_id — all 5 events
    must be queryable in the log AND soul/reservoirs must round-trip."""
    db = tmp_path / "persist.db"
    p1 = SoulPlugin(agent_id="persist", db_path=db)
    for i in range(5):
        p1.ingest(Score(v=80, w=50 + i, patterns=("ABANDONMENT",)))
    pre_close_trauma = p1._physics.trauma.load()
    p1.close()

    p2 = SoulPlugin(agent_id="persist", db_path=db)
    assert p2._physics.trauma.load() == pytest.approx(pre_close_trauma, rel=0.01)
    # Event log preserved across reopen.
    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("persist") == 5


def test_event_log_disabled_writes_no_rows(tmp_path) -> None:
    db = tmp_path / "nolog.db"
    plugin = SoulPlugin(
        agent_id="quiet", db_path=db, event_log=False,
    )
    for _ in range(3):
        plugin.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",)))
    plugin.close()
    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("quiet") == 0


# ---------------------------------------------------------------------------
# Default soul on first run
# ---------------------------------------------------------------------------


def test_default_soul_used_when_no_saved_state(tmp_path) -> None:
    plugin = SoulPlugin(
        agent_id="newborn", db_path=tmp_path / "n.db",
        default_soul=SoulState(v=200, w=210, d=180),
    )
    snap = plugin.snapshot()
    assert snap["soul"]["v"] == 200
    assert snap["soul"]["w"] == 210


def test_default_soul_ignored_when_state_exists(tmp_path) -> None:
    """Once the agent has saved state, the default is irrelevant —
    persisted soul wins."""
    db = tmp_path / "exists.db"
    p1 = SoulPlugin(
        agent_id="returning", db_path=db,
        default_soul=SoulState(v=180, w=180),
    )
    p1.close()  # writes the default-soul row to disk

    # Open again with a different default — the saved value wins.
    p2 = SoulPlugin(
        agent_id="returning", db_path=db,
        default_soul=SoulState(v=50, w=50),
    )
    snap = p2.snapshot()
    assert snap["soul"]["v"] == 180
    assert snap["soul"]["w"] == 180


# ---------------------------------------------------------------------------
# Preset application via plugin
# ---------------------------------------------------------------------------


def test_preset_apply_then_tick_changes_running_engine(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "p.db")
    # ADULT defaults
    assert plugin._physics.soul.w == 175

    CHILD.apply(plugin.overrides, "x")
    plugin.tick()  # tick reloads overrides
    assert plugin._physics.soul.w == CHILD.soul.w


def test_switching_presets_via_plugin(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "p.db")
    BRITTLE.apply(plugin.overrides, "x")
    plugin.tick()
    assert plugin._physics.config.armor_max == BRITTLE.config.armor_max

    STOIC.apply(plugin.overrides, "x")
    plugin.tick()
    assert plugin._physics.config.armor_max == STOIC.config.armor_max


# ---------------------------------------------------------------------------
# Compositional usage as a PulseHost
# ---------------------------------------------------------------------------


def test_plugin_ingest_returns_physics_tick(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "p.db")
    tick = plugin.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",)))
    assert tick is not None
    assert tick.weight_raw > 0
