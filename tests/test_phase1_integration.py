"""Phase 1 integration — end-to-end story test.

Per-module tests live in tests/test_{schema,eventlog,eventlog_wiring,
overrides,presets,plugin}.py. THIS file is the cross-cutting smoke
test that exercises the full Phase 1 promise: a host can `pip install
clanker-soul`, write a few lines, and get a fully wired emotional
runtime with persistent state, durable event log, and live-tunable
config.

If this file ever fails, Phase 1's drop-in plugin promise is broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from clanker_soul import (
    BRITTLE,
    CHILD,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Score,
    SoulPlugin,
    SoulStore,
    SqliteEventLog,
    STOIC,
)


# ---------------------------------------------------------------------------
# End-to-end story
# ---------------------------------------------------------------------------


def test_phase1_full_story(tmp_path) -> None:
    """One agent's full lifecycle, exercising every Phase 1 surface."""
    db = tmp_path / "story.db"

    # 1. CONSTRUCT — a fresh agent with the CHILD personality.
    plugin = SoulPlugin(
        agent_id="story-agent",
        db_path=db,
        default_soul=CHILD.soul,
    )
    CHILD.apply(plugin.overrides, "story-agent")
    plugin.tick()  # picks up the preset overrides

    # Sanity: the agent is shaped like a child.
    assert plugin.physics.soul.w == CHILD.soul.w
    assert plugin.physics.config.soul_drift_per_hour == CHILD.config.soul_drift_per_hour

    # 2. INGEST — feed a mix of warm and harsh events.
    events = [
        Score(v=200, w=210, patterns=("AFFIRMATION",)),
        Score(v=180, w=190, patterns=("WARMTH",)),
        Score(v=50, w=40, u=200, patterns=("ABANDONMENT",)),
        Score(v=30, w=20, u=220, patterns=("EXISTENTIAL_NEGATION",)),
        Score(v=190, w=180, patterns=("AFFIRMATION",)),
    ]
    for e in events:
        plugin.ingest(e)

    # 3. VERIFY EVENT LOG — every ingest captured.
    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("story-agent") == 5
    records = log.read_ingest("story-agent")
    # Most recent first; the last positive event is at index 0.
    assert records[0].patterns == ("AFFIRMATION",)
    assert records[0].why  # non-empty
    # The wounding event should be present and classified correctly.
    abandonment = next(r for r in records if "ABANDONMENT" in r.patterns)
    assert abandonment.classification == "negative"
    assert abandonment.weight_raw > 0.5

    # 4. SNAPSHOT — PulseHost-compatible.
    snap = plugin.snapshot()
    assert snap["mood"] is not None
    assert snap["trauma_load"] > 0
    assert snap["nourishment_load"] > 0

    # 5. LIVE-TUNE — switch personality at runtime.
    STOIC.apply(plugin.overrides, "story-agent")
    plugin.tick()
    assert plugin.physics.config.armor_max == STOIC.config.armor_max

    # 6. PERSIST — save, close, reopen.
    plugin.close()

    plugin2 = SoulPlugin(agent_id="story-agent", db_path=db)
    # Reopened plugin sees the persisted soul (which now reflects STOIC's
    # apply, having been mutated in-place via reload_overrides on the
    # last tick).
    snap2 = plugin2.snapshot()
    assert snap2["soul"] is not None
    # Trauma load decays slightly between sessions but should still be present.
    assert snap2["trauma_load"] > 0

    # Event log persists too — all 5 events still queryable.
    assert log.count_ingest("story-agent") == 5

    plugin2.close()


# ---------------------------------------------------------------------------
# Multi-agent isolation
# ---------------------------------------------------------------------------


def test_two_agents_share_db_without_cross_contamination(tmp_path) -> None:
    """Two SoulPlugin instances against the same DB must keep their
    state isolated by agent_id — events, overrides, soul, reservoirs."""
    db = tmp_path / "multi.db"
    alice = SoulPlugin(agent_id="alice", db_path=db, default_soul=CHILD.soul)
    bob = SoulPlugin(agent_id="bob", db_path=db, default_soul=STOIC.soul)

    alice.ingest(Score(v=50, w=40, patterns=("ABANDONMENT",)))
    bob.ingest(Score(v=200, w=210, patterns=("AFFIRMATION",)))

    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("alice") == 1
    assert log.count_ingest("bob") == 1

    # Apply BRITTLE only to Alice — Bob's config must be untouched.
    BRITTLE.apply(alice.overrides, "alice")
    alice.tick()
    bob.tick()
    assert alice.physics.config.armor_max == BRITTLE.config.armor_max
    assert bob.physics.config.armor_max != BRITTLE.config.armor_max

    alice.close()
    bob.close()


# ---------------------------------------------------------------------------
# PulseEngine wired to the same plugin
# ---------------------------------------------------------------------------


@dataclass
class _PluginPulseHost:
    """Minimal PulseHost that delegates to a SoulPlugin's snapshot +
    drift, and records what the engine wanted to dispatch."""

    plugin: SoulPlugin
    target: PulseTarget = field(default_factory=lambda: PulseTarget(payload="test"))
    dispatched: list = field(default_factory=list)
    reminders: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        return self.plugin.snapshot()

    def slow_drift_tick(self) -> None:
        self.plugin.tick()

    def most_recent_target(self):
        return self.target

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        self.dispatched.append((trigger.kind, prompt))
        return True

    def due_reminders(self) -> list[dict]:
        return []

    def deliver_reminder(self, target, reminder) -> None:
        pass


@pytest.mark.asyncio
async def test_plugin_drives_pulseengine_with_logging(tmp_path) -> None:
    """The plugin's snapshot feeds PulseEngine; ingest a distress-shaped
    event, tick the engine past cooldown, verify the pulse fired AND the
    pulse_log captured it."""
    from datetime import datetime, timezone

    db = tmp_path / "pulse.db"
    plugin = SoulPlugin(
        agent_id="pulser",
        db_path=db,
        default_soul=BRITTLE.soul,
    )
    BRITTLE.apply(plugin.overrides, "pulser")
    plugin.tick()

    # Drive mood far below soul to trip distress.
    for _ in range(3):
        plugin.ingest(Score(v=20, w=20, u=220, patterns=("EXISTENTIAL_NEGATION",)))

    host = _PluginPulseHost(plugin=plugin)
    # Wire the pulse engine to write to the same event log the plugin uses.
    engine = PulseEngine(
        host,
        config=PulseConfig(min_quiet_seconds=1.0, startup_grace_s=0.0),
        event_log=plugin.event_log,
        agent_id="pulser",
    )
    engine._last_outbound_ts = datetime.now(timezone.utc).timestamp() - 60
    engine._last_pulse_ts = datetime.now(timezone.utc).timestamp() - 60

    result = await engine.tick()
    assert result is not None and result.kind in {"distress", "trauma_pressure"}
    assert host.dispatched, "engine did not call dispatch_pulse"

    # Verify the pulse_log captured the evaluation.
    log = SqliteEventLog(SoulStore.get(db))
    pulses = log.read_pulse("pulser")
    assert pulses, "no pulse records written"
    fired = next((p for p in pulses if p.dispatched), None)
    assert fired is not None
    assert fired.trigger_kind in {"distress", "trauma_pressure"}
    assert fired.prompt and len(fired.prompt) > 20

    plugin.close()


# ---------------------------------------------------------------------------
# Sanity: package-level imports
# ---------------------------------------------------------------------------


def test_all_phase1_names_importable_from_top_level() -> None:
    """A plugin author should be able to do `from clanker_soul import *`
    and have everything they need."""
    import clanker_soul as cs

    expected = {
        # Phase 1 deliverables
        "SoulPlugin",
        "ConfigOverrides",
        "OverrideBundle",
        "apply_overrides",
        "EventLog",
        "IngestRecord",
        "PulseRecord",
        "NullEventLog",
        "SqliteEventLog",
        "Preset",
        "CHILD",
        "ADULT",
        "BRITTLE",
        "STOIC",
        "PRESETS",
        # Carried forward from v0.1
        "Score",
        "SoulState",
        "SoulStore",
        "EmotionalPhysics",
        "PhysicsConfig",
        "PulseEngine",
        "PulseHost",
        "PulseTarget",
        "Trigger",
    }
    missing = expected - set(dir(cs))
    assert not missing, f"missing top-level exports: {missing}"
