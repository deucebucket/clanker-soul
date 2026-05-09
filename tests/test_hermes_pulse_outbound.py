"""Tests for the hermes plugin's pulse-outbound mode (#44 / M2).

The plugin runs PulseEngine in a daemon thread with its own asyncio
loop. Tests here exercise:

- pulse_outbound disabled by default (no thread, behavior unchanged)
- env var activation + programmatic activation
- Dispatcher resolution from CLANKER_SOUL_PULSE_DISPATCH
- The runner cleanly starts + stops + survives multiple cycles
- A configured dispatcher gets called when triggers fire on synthetic
  state, and consequences feed back into physics
- sync_turn calls note_outbound on the runner

All tests use synthetic dispatchers; no LLM, no network.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import List

import pytest

# Add integrations/hermes/ to sys.path so we can load the plugin without
# needing it symlinked into a hermes-agent tree.
_PLUGIN_DIR = Path(__file__).parent.parent / "integrations" / "hermes"
sys.path.insert(0, str(_PLUGIN_DIR))

# Reload of cached modules from prior tests in this run.
for _k in list(sys.modules):
    if _k in ("scorer", "pulse_runner", "clanker_soul_hermes_plugin"):
        sys.modules.pop(_k, None)

# Load the plugin via spec_from_file_location so relative imports resolve.
_pkg_spec = importlib.util.spec_from_file_location(
    "clanker_soul_hermes_plugin", str(_PLUGIN_DIR / "__init__.py"),
    submodule_search_locations=[str(_PLUGIN_DIR)],
)
_pkg = importlib.util.module_from_spec(_pkg_spec)
sys.modules["clanker_soul_hermes_plugin"] = _pkg
_pkg_spec.loader.exec_module(_pkg)

ClankerSoulMemoryProvider = _pkg.ClankerSoulMemoryProvider
PulseRunner = _pkg.PulseRunner

from clanker_soul import (
    ActionOutcome,
    PulseAction,
    PulseConfig,
    PulseTarget,
    Score,
    SoulPlugin,
)


# ---------------------------------------------------------------------------
# Default — no pulse_outbound
# ---------------------------------------------------------------------------


def test_provider_default_does_not_start_runner(tmp_path, monkeypatch) -> None:
    """No env var, no programmatic dispatcher → no runner started.
    Behavior identical to v0.10.0."""
    monkeypatch.delenv("CLANKER_SOUL_PULSE_OUTBOUND", raising=False)
    monkeypatch.delenv("CLANKER_SOUL_PULSE_DISPATCH", raising=False)
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    assert p._pulse_runner is None
    p.shutdown()


# ---------------------------------------------------------------------------
# Env-var activation
# ---------------------------------------------------------------------------


def test_env_var_activates_runner(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLANKER_SOUL_PULSE_OUTBOUND", "1")
    monkeypatch.delenv("CLANKER_SOUL_PULSE_DISPATCH", raising=False)
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    assert p._pulse_runner is not None
    assert p._pulse_runner._thread is not None
    assert p._pulse_runner._thread.is_alive()
    p.shutdown()
    # After shutdown, runner is gone.
    assert p._pulse_runner is None


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "ON"])
def test_env_var_truthy_values(tmp_path, monkeypatch, flag) -> None:
    monkeypatch.setenv("CLANKER_SOUL_PULSE_OUTBOUND", flag)
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    assert p._pulse_runner is not None
    p.shutdown()


@pytest.mark.parametrize("flag", ["0", "false", "no", "off", ""])
def test_env_var_falsy_values(tmp_path, monkeypatch, flag) -> None:
    monkeypatch.setenv("CLANKER_SOUL_PULSE_OUTBOUND", flag)
    monkeypatch.delenv("CLANKER_SOUL_PULSE_DISPATCH", raising=False)
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    assert p._pulse_runner is None
    p.shutdown()


# ---------------------------------------------------------------------------
# Programmatic dispatcher
# ---------------------------------------------------------------------------


def test_set_pulse_dispatcher_activates_runner(tmp_path, monkeypatch) -> None:
    """Calling set_pulse_dispatcher BEFORE initialize → runner starts."""
    monkeypatch.delenv("CLANKER_SOUL_PULSE_OUTBOUND", raising=False)
    monkeypatch.delenv("CLANKER_SOUL_PULSE_DISPATCH", raising=False)

    received: List[PulseAction] = []

    def my_dispatcher(action: PulseAction) -> ActionOutcome:
        received.append(action)
        return ActionOutcome(delivered=True)

    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.set_pulse_dispatcher(my_dispatcher)
    p.initialize(session_id="alice")
    assert p._pulse_runner is not None
    p.shutdown()


# ---------------------------------------------------------------------------
# CLANKER_SOUL_PULSE_DISPATCH env var resolution
# ---------------------------------------------------------------------------


# Define a module-level dispatcher we can point the env var at.
_resolved_calls: List[PulseAction] = []


def _module_level_dispatcher(action: PulseAction) -> ActionOutcome:
    _resolved_calls.append(action)
    return ActionOutcome(delivered=True, note="from-env-import")


def test_dispatch_env_var_resolves_module_callable(monkeypatch) -> None:
    from pulse_runner import _resolve_dispatcher_from_env

    monkeypatch.setenv(
        "CLANKER_SOUL_PULSE_DISPATCH",
        "tests.test_hermes_pulse_outbound:_module_level_dispatcher",
    )
    cb = _resolve_dispatcher_from_env()
    assert cb is _module_level_dispatcher


def test_dispatch_env_var_unset_returns_none(monkeypatch) -> None:
    from pulse_runner import _resolve_dispatcher_from_env
    monkeypatch.delenv("CLANKER_SOUL_PULSE_DISPATCH", raising=False)
    assert _resolve_dispatcher_from_env() is None


def test_dispatch_env_var_bad_format_warns(monkeypatch, caplog) -> None:
    from pulse_runner import _resolve_dispatcher_from_env
    monkeypatch.setenv("CLANKER_SOUL_PULSE_DISPATCH", "no_colon_here")
    with caplog.at_level("WARNING"):
        cb = _resolve_dispatcher_from_env()
    assert cb is None
    assert any("missing ':'" in r.message for r in caplog.records)


def test_dispatch_env_var_unimportable_warns(monkeypatch, caplog) -> None:
    from pulse_runner import _resolve_dispatcher_from_env
    monkeypatch.setenv(
        "CLANKER_SOUL_PULSE_DISPATCH",
        "no.such.module:nope",
    )
    with caplog.at_level("WARNING"):
        cb = _resolve_dispatcher_from_env()
    assert cb is None


# ---------------------------------------------------------------------------
# Runner direct tests (no provider)
# ---------------------------------------------------------------------------


def test_runner_lifecycle_start_stop(tmp_path) -> None:
    """A bare PulseRunner over a SoulPlugin starts a thread + loop and
    cleanly stops both."""
    db = tmp_path / "ts.db"
    with SoulPlugin(agent_id="alice", db_path=db) as plugin:
        runner = PulseRunner(plugin=plugin)
        runner.start()
        assert runner._thread is not None
        assert runner._thread.is_alive()
        # Engine should be set up by now (start() waits up to 2s for
        # the ready event).
        assert runner.engine is not None
        runner.stop()
        # After stop, the thread is gone.
        assert runner._thread is None
        assert runner.engine is None


def test_runner_start_is_idempotent(tmp_path) -> None:
    db = tmp_path / "ts.db"
    with SoulPlugin(agent_id="alice", db_path=db) as plugin:
        runner = PulseRunner(plugin=plugin)
        runner.start()
        first_thread = runner._thread
        runner.start()  # no-op
        assert runner._thread is first_thread
        runner.stop()


def test_runner_stop_without_start_is_safe(tmp_path) -> None:
    db = tmp_path / "ts.db"
    with SoulPlugin(agent_id="alice", db_path=db) as plugin:
        runner = PulseRunner(plugin=plugin)
        runner.stop()  # never started; should not raise


def test_runner_default_dispatcher_is_noop(tmp_path) -> None:
    """When no dispatcher is configured, the no-op dispatcher should
    be installed and dispatch_action returns delivered=False."""
    from pulse_runner import _NoOpDispatcher

    dispatcher = _NoOpDispatcher()
    trigger = _make_trigger("distress")
    action = PulseAction(
        kind="direct_message", trigger=trigger,
        target=PulseTarget(payload="x"), prompt="test",
    )
    out = dispatcher(action)
    assert out.delivered is False
    assert out.note == "no_dispatcher_configured"


# ---------------------------------------------------------------------------
# Dispatcher gets called when engine fires
# ---------------------------------------------------------------------------


def _make_trigger(kind: str):
    from clanker_soul import Trigger
    return Trigger(kind=kind, soul={"v": 145, "w": 175}, mood=[40]*7)


def test_runner_dispatches_synthetic_pulse(tmp_path, monkeypatch) -> None:
    """Drive the SoulPlugin into distress, run a single tick on the
    runner's engine, verify the dispatcher fires."""
    received: List[PulseAction] = []

    def my_dispatcher(action: PulseAction) -> ActionOutcome:
        received.append(action)
        return ActionOutcome(delivered=True)

    db = tmp_path / "ts.db"
    with SoulPlugin(agent_id="alice", db_path=db) as plugin:
        # Wound the soul → mood drops far from soul.
        for _ in range(10):
            plugin.ingest(Score(
                v=40, w=40, u=200,
                patterns=("ABANDONMENT",),
                direction="SELF_DIRECTED",
            ))

        runner = PulseRunner(
            plugin=plugin,
            dispatcher=my_dispatcher,
            pulse_config=PulseConfig(
                min_quiet_seconds=0.0,
                distress_v_drop=15.0,
                distress_w_drop=15.0,
                distance_trigger=20.0,
            ),
            target_factory=lambda: PulseTarget(payload="x"),
        )
        runner.start()
        # Suppress long_silence (idle since epoch).
        runner.note_outbound()
        # Drive a single tick from the runner's loop, synchronously.
        # We schedule a coroutine onto the loop and wait for it.
        import asyncio
        engine = runner.engine
        assert engine is not None
        loop = runner._loop
        assert loop is not None
        future = asyncio.run_coroutine_threadsafe(engine.tick(), loop)
        trigger = future.result(timeout=5.0)
        runner.stop()

        assert trigger is not None
        assert trigger.kind == "distress"
        assert len(received) == 1
        assert received[0].kind == "direct_message"
        assert received[0].trigger.kind == "distress"


def test_runner_consequences_feed_back_into_soul(tmp_path) -> None:
    """When the dispatcher returns ActionOutcome.consequences, those
    Score events are auto-ingested by the engine. The soul should
    register the new pattern in its physics state."""

    def angry_response_dispatcher(action: PulseAction) -> ActionOutcome:
        # Pretend the agent's distress message got a hostile reply.
        return ActionOutcome(
            delivered=True,
            consequences=(
                Score(v=40, w=30, patterns=("HOSTILE_RESPONSE",)),
            ),
        )

    db = tmp_path / "ts.db"
    with SoulPlugin(agent_id="alice", db_path=db) as plugin:
        for _ in range(10):
            plugin.ingest(Score(
                v=40, w=40, u=200,
                patterns=("ABANDONMENT",),
                direction="SELF_DIRECTED",
            ))

        runner = PulseRunner(
            plugin=plugin,
            dispatcher=angry_response_dispatcher,
            pulse_config=PulseConfig(
                min_quiet_seconds=0.0,
                distress_v_drop=15.0,
                distress_w_drop=15.0,
                distance_trigger=20.0,
            ),
            target_factory=lambda: PulseTarget(payload="x"),
        )
        runner.start()
        runner.note_outbound()

        import asyncio
        engine = runner.engine
        assert engine is not None
        loop = runner._loop
        assert loop is not None
        future = asyncio.run_coroutine_threadsafe(engine.tick(), loop)
        future.result(timeout=5.0)
        runner.stop()

        # The HOSTILE_RESPONSE pattern should now appear in the
        # physics last_tick — the engine ingested the consequence.
        last = plugin.physics.last_tick
        assert last is not None
        assert "HOSTILE_RESPONSE" in last.patterns


# ---------------------------------------------------------------------------
# Provider sync_turn → runner.note_outbound
# ---------------------------------------------------------------------------


def test_sync_turn_notes_outbound_to_runner(tmp_path, monkeypatch) -> None:
    """sync_turn (called after each turn ships) must mark the runner's
    outbound timestamp so cooldown covers reactive replies."""
    monkeypatch.delenv("CLANKER_SOUL_PULSE_OUTBOUND", raising=False)

    def dispatcher(action: PulseAction) -> ActionOutcome:
        return ActionOutcome(delivered=True)

    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.set_pulse_dispatcher(dispatcher)
    p.initialize(session_id="alice")
    assert p._pulse_runner is not None
    runner = p._pulse_runner

    # Verify note_outbound is wired by checking _last_outbound_ts on
    # the engine before and after.
    engine = runner.engine
    assert engine is not None
    before = engine._last_outbound_ts
    p.sync_turn(user_content="hi", assistant_content="hello back")
    # note_outbound is scheduled via call_soon_threadsafe; give the
    # loop a beat to process.
    time.sleep(0.05)
    after = engine._last_outbound_ts
    assert after > before

    p.shutdown()
