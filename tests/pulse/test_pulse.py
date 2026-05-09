"""PulseEngine triggers, cooldown, and host hookup using a fake host."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from clanker_soul import (
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Trigger,
)
from clanker_soul.pulse import compose_self_prompt


@dataclass
class FakeHost:
    snap: dict = field(default_factory=dict)
    target: PulseTarget | None = field(default_factory=lambda: PulseTarget(payload="default"))
    drift_ticks: int = 0
    dispatched: list[tuple[Trigger, str]] = field(default_factory=list)
    dispatch_returns: bool = True
    reminders: list[dict] = field(default_factory=list)
    delivered: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        return self.snap

    def slow_drift_tick(self) -> None:
        self.drift_ticks += 1

    def most_recent_target(self) -> PulseTarget | None:
        return self.target

    def dispatch_pulse(self, target: PulseTarget, trigger: Trigger, prompt: str) -> bool:
        self.dispatched.append((trigger, prompt))
        return self.dispatch_returns

    def due_reminders(self) -> list[dict]:
        out = self.reminders
        self.reminders = []
        return out

    def deliver_reminder(self, target: PulseTarget, reminder: dict) -> None:
        self.delivered.append(reminder)


def _seed_clock_past_cooldown(engine, gap_seconds: float = 30 * 60.0 + 60.0) -> None:
    """Place the engine's "last activity" timestamp in the recent past so
    the cooldown gate is satisfied but the long-silence ceiling (6h) is
    not yet tripped. Default gap is 31 minutes — past min_quiet_seconds
    (25min default) but well inside max_quiet_seconds (6h default)."""
    now = datetime.now(timezone.utc).timestamp()
    engine._last_outbound_ts = now - gap_seconds
    engine._last_pulse_ts = now - gap_seconds


def _baseline_snap(**overrides) -> dict:
    snap = {
        "soul": {"v": 145, "a": 110, "d": 160, "u": 80, "g": 130, "w": 175, "i": 135},
        "mood": [145, 110, 160, 80, 130, 175, 135],
        "soul_distance": 0.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }
    snap.update(overrides)
    return snap


@pytest.mark.asyncio
async def test_no_trigger_when_mood_matches_soul() -> None:
    host = FakeHost(snap=_baseline_snap())
    engine = PulseEngine(host)
    # First tick after epoch — no idle ceiling reached because we set
    # last_outbound to "now" at construction-time.
    engine.note_outbound()
    result = await engine.tick()
    assert result is None
    assert host.drift_ticks == 1
    assert host.dispatched == []


@pytest.mark.asyncio
async def test_distress_fires_when_v_and_w_drop() -> None:
    snap = _baseline_snap(
        mood=[80, 110, 160, 80, 130, 100, 135],  # v -65, w -75
        soul_distance=80.0,
    )
    host = FakeHost(snap=snap)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None and result.kind == "distress"
    assert host.dispatched and host.dispatched[0][0].kind == "distress"


@pytest.mark.asyncio
async def test_elation_fires_when_v_and_i_lift() -> None:
    snap = _baseline_snap(
        mood=[210, 110, 160, 80, 130, 175, 175],  # v +65, i +40
        soul_distance=70.0,
    )
    host = FakeHost(snap=snap)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None and result.kind == "elation"


@pytest.mark.asyncio
async def test_trauma_pressure_fires_with_high_load() -> None:
    snap = _baseline_snap(trauma_load=120.0, nourishment_load=10.0)
    host = FakeHost(snap=snap)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None and result.kind == "trauma_pressure"


@pytest.mark.asyncio
async def test_gratitude_fires_with_sustained_nourishment() -> None:
    snap = _baseline_snap(trauma_load=5.0, nourishment_load=140.0)
    host = FakeHost(snap=snap)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None and result.kind == "gratitude"


@pytest.mark.asyncio
async def test_cooldown_suppresses_back_to_back_pulses() -> None:
    snap = _baseline_snap(
        mood=[80, 110, 160, 80, 130, 100, 135],
        soul_distance=80.0,
    )
    host = FakeHost(snap=snap)
    cfg = PulseConfig(min_quiet_seconds=999_999.0, max_quiet_seconds=10**12)
    engine = PulseEngine(host, cfg)
    _seed_clock_past_cooldown(engine, gap_seconds=999_999.0 + 60.0)
    first = await engine.tick()
    assert first is not None
    # Second tick should be suppressed by cooldown.
    second = await engine.tick()
    assert second is None
    assert len(host.dispatched) == 1


@pytest.mark.asyncio
async def test_no_target_means_no_pulse_even_with_trigger() -> None:
    snap = _baseline_snap(
        mood=[80, 110, 160, 80, 130, 100, 135],
        soul_distance=80.0,
    )
    host = FakeHost(snap=snap, target=None)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    # tick returned None because dispatch couldn't run, but we still want
    # the engine to have attempted to evaluate.
    assert result is None
    assert host.dispatched == []


@pytest.mark.asyncio
async def test_due_reminders_delivered_each_tick() -> None:
    host = FakeHost(snap=_baseline_snap(), reminders=[{"message": "drink water"}])
    engine = PulseEngine(host)
    engine.note_outbound()
    await engine.tick()
    assert host.delivered == [{"message": "drink water"}]


@pytest.mark.asyncio
async def test_async_dispatch_pulse_is_awaited() -> None:
    snap = _baseline_snap(
        mood=[80, 110, 160, 80, 130, 100, 135],
        soul_distance=80.0,
    )

    class AsyncHost(FakeHost):
        async def dispatch_pulse(self, target, trigger, prompt):  # type: ignore[override]
            await asyncio.sleep(0)
            self.dispatched.append((trigger, prompt))
            return True

    host = AsyncHost(snap=snap)
    engine = PulseEngine(host)
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None
    assert host.dispatched


def test_self_prompt_includes_state_line_when_mood_present() -> None:
    t = Trigger(
        kind="distress",
        soul={"v": 145, "w": 175, "g": 130},
        mood=[80, 110, 160, 80, 130, 100, 135],
        metrics={"distance": 80.0, "v_drop": 65, "w_drop": 75},
    )
    text = compose_self_prompt(t)
    assert "current_mood" in text
    assert "distress" in text.lower()


def test_self_prompt_long_silence_includes_minute_count() -> None:
    t = Trigger(
        kind="long_silence",
        soul={"v": 145},
        mood=None,
        metrics={"idle_seconds": 7200},
    )
    assert "120 minutes" in compose_self_prompt(t)
