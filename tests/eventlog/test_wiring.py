"""EventLog wiring into EmotionalPhysics.ingest and PulseEngine.tick.

Backward-compatibility constraint: every existing test in tests/ must
keep passing without modification. New behavior is opt-in via the
``event_log`` + ``agent_id`` constructor params.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from clanker_soul import (
    EmotionalPhysics,
    IngestRecord,
    PhysicsConfig,
    PulseConfig,
    PulseEngine,
    PulseRecord,
    PulseTarget,
    Score,
    SoulState,
)


# ---------------------------------------------------------------------------
# In-memory capturing sink
# ---------------------------------------------------------------------------


class CapturingEventLog:
    """Records every call. Satisfies the ``EventLog`` protocol."""

    def __init__(self) -> None:
        self.ingests: list[IngestRecord] = []
        self.pulses: list[PulseRecord] = []

    def log_ingest(self, record: IngestRecord) -> None:
        self.ingests.append(record)

    def log_pulse(self, record: PulseRecord) -> None:
        self.pulses.append(record)


class RaisingEventLog:
    """A faulty sink that raises on every call. Used to verify the
    physics soft-fail invariant — log failures must not propagate."""

    def log_ingest(self, record: IngestRecord) -> None:  # noqa: ARG002
        raise RuntimeError("simulated log failure")

    def log_pulse(self, record: PulseRecord) -> None:  # noqa: ARG002
        raise RuntimeError("simulated log failure")


# ---------------------------------------------------------------------------
# Physics — basic logging
# ---------------------------------------------------------------------------


def test_physics_logs_ingest_when_event_log_provided() -> None:
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(),
        config=PhysicsConfig(),
        event_log=sink,
        agent_id="agent-1",
    )
    physics.ingest(Score(v=80, a=160, d=70, u=180, g=80, w=50, i=110, patterns=("ABANDONMENT",)))
    assert len(sink.ingests) == 1
    rec = sink.ingests[0]
    assert rec.agent_id == "agent-1"
    assert rec.raw.patterns == ("ABANDONMENT",)
    assert rec.mood_before is None  # first-ever ingest has no prior mood
    assert rec.mood_after is not None
    assert rec.weight_raw > 0
    assert rec.weight_effective > 0
    assert rec.classification == "negative"
    assert rec.why  # non-empty


def test_physics_records_mood_before_on_subsequent_ingest() -> None:
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(),
        event_log=sink,
        agent_id="x",
    )
    physics.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
    physics.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",)))
    assert len(sink.ingests) == 2
    second = sink.ingests[1]
    # mood_before on second ingest is the mood that resulted from ingest #1
    assert second.mood_before is not None
    assert second.mood_before.v != 145  # moved away from default soul.v


def test_physics_records_soul_movement_across_breach_sequence() -> None:
    """A sustained breach sequence visibly moves soul over the run.
    Per-record deltas can round to zero on int-clamped fields, but the
    cumulative effect must be observable in the log."""
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(v=160, w=180, d=160),
        event_log=sink,
        agent_id="x",
    )
    for _ in range(8):
        physics.ingest(Score(v=10, w=10, d=20, patterns=("EXISTENTIAL_NEGATION",)))
    assert any(r.breached for r in sink.ingests), "expected breach to fire"
    first_soul = sink.ingests[0].soul_before
    last_soul = sink.ingests[-1].soul_after
    assert first_soul.v != last_soul.v or first_soul.w != last_soul.w or first_soul.g != last_soul.g


def test_physics_with_no_event_log_does_not_log() -> None:
    """The ``event_log=None`` path must keep existing behavior — no
    record emitted, no agent_id required."""
    physics = EmotionalPhysics(soul=SoulState())
    tick = physics.ingest(Score(v=200, w=200))
    assert tick is not None  # physics still works


def test_physics_requires_agent_id_when_event_log_given() -> None:
    """Logging needs an agent_id to scope rows. Construction must
    fail loudly rather than silently logging with agent_id=None."""
    with pytest.raises(ValueError):
        EmotionalPhysics(
            soul=SoulState(),
            event_log=CapturingEventLog(),
            # agent_id missing
        )


def test_physics_log_failure_does_not_crash_ingest() -> None:
    """A raising EventLog impl must not propagate into physics. Soft-fail
    is defense in depth — SqliteEventLog catches internally, but custom
    impls might not, and physics must be robust either way."""
    physics = EmotionalPhysics(
        soul=SoulState(),
        event_log=RaisingEventLog(),
        agent_id="x",
    )
    tick = physics.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",)))
    assert tick is not None  # physics returned normally despite logger raising


# ---------------------------------------------------------------------------
# Physics — why_text
# ---------------------------------------------------------------------------


def test_why_text_references_patterns_and_weight() -> None:
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(),
        event_log=sink,
        agent_id="x",
    )
    physics.ingest(Score(v=40, w=40, u=200, patterns=("ABANDONMENT",)))
    why = sink.ingests[0].why
    assert "ABANDONMENT" in why
    # the why string must reference the actual weight number
    assert "weight" in why.lower()


def test_why_text_marks_breach_when_breach_fired() -> None:
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(v=160, w=180, d=160),
        event_log=sink,
        agent_id="x",
    )
    for _ in range(8):
        physics.ingest(Score(v=10, w=10, d=20, patterns=("EXISTENTIAL_NEGATION",)))
    breach_whys = [r.why for r in sink.ingests if r.breached]
    assert breach_whys
    assert any("BREACH" in w for w in breach_whys)


# ---------------------------------------------------------------------------
# Physics — primed score logging
# ---------------------------------------------------------------------------


def test_physics_logs_primed_when_raw_kwarg_provided() -> None:
    """Hosts that apply ``mood_prime_score`` themselves can pass the
    pre-prime score via the ``raw=`` kwarg, and the log will record both."""
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(),
        event_log=sink,
        agent_id="x",
    )
    raw = Score(v=128, w=128, patterns=("AMBIGUOUS",))
    primed = Score(v=140, w=135, patterns=("AMBIGUOUS",))
    physics.ingest(primed, raw=raw)
    rec = sink.ingests[0]
    assert rec.raw.v == 128  # the pre-prime score
    assert rec.primed is not None and rec.primed.v == 140


def test_physics_primed_is_none_when_no_raw_kwarg() -> None:
    sink = CapturingEventLog()
    physics = EmotionalPhysics(
        soul=SoulState(),
        event_log=sink,
        agent_id="x",
    )
    physics.ingest(Score(v=128, w=128))
    assert sink.ingests[0].primed is None


# ---------------------------------------------------------------------------
# Pulse — every evaluation logged
# ---------------------------------------------------------------------------


@dataclass
class FakeHost:
    snap: dict = field(default_factory=dict)
    target: PulseTarget | None = field(default_factory=lambda: PulseTarget(payload="default"))
    drift_ticks: int = 0
    dispatched: list = field(default_factory=list)
    dispatch_returns: bool = True
    reminders: list[dict] = field(default_factory=list)
    delivered: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        return self.snap

    def slow_drift_tick(self) -> None:
        self.drift_ticks += 1

    def most_recent_target(self) -> PulseTarget | None:
        return self.target

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        self.dispatched.append((trigger, prompt))
        return self.dispatch_returns

    def due_reminders(self) -> list[dict]:
        out = self.reminders
        self.reminders = []
        return out

    def deliver_reminder(self, target, reminder) -> None:
        self.delivered.append(reminder)


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


def _seed_clock_past_cooldown(engine, gap: float = 30 * 60.0 + 60.0) -> None:
    now = datetime.now(timezone.utc).timestamp()
    engine._last_outbound_ts = now - gap
    engine._last_pulse_ts = now - gap


@pytest.mark.asyncio
async def test_pulse_logs_no_trigger_at_baseline() -> None:
    sink = CapturingEventLog()
    host = FakeHost(snap=_baseline_snap())
    engine = PulseEngine(host, event_log=sink, agent_id="x")
    engine.note_outbound()
    await engine.tick()
    assert len(sink.pulses) == 1
    rec = sink.pulses[0]
    assert rec.trigger_kind is None
    assert rec.suppressed_reason == "no_trigger"
    assert rec.dispatched is False


@pytest.mark.asyncio
async def test_pulse_logs_distress_dispatch() -> None:
    sink = CapturingEventLog()
    host = FakeHost(
        snap=_baseline_snap(
            mood=[80, 110, 160, 80, 130, 100, 135],
            soul_distance=80.0,
        )
    )
    engine = PulseEngine(host, event_log=sink, agent_id="x")
    _seed_clock_past_cooldown(engine)
    await engine.tick()
    assert len(sink.pulses) == 1
    rec = sink.pulses[0]
    assert rec.trigger_kind == "distress"
    assert rec.suppressed_reason is None
    assert rec.dispatched is True
    assert rec.target_present is True
    assert rec.prompt and "distress" in rec.prompt.lower()


@pytest.mark.asyncio
async def test_pulse_logs_cooldown_suppression() -> None:
    sink = CapturingEventLog()
    host = FakeHost(
        snap=_baseline_snap(
            mood=[80, 110, 160, 80, 130, 100, 135],
            soul_distance=80.0,
        )
    )
    engine = PulseEngine(
        host,
        config=PulseConfig(min_quiet_seconds=999_999.0, max_quiet_seconds=10**12),
        event_log=sink,
        agent_id="x",
    )
    _seed_clock_past_cooldown(engine, gap=999_999.0 + 60.0)
    await engine.tick()  # fires
    await engine.tick()  # suppressed by cooldown
    assert len(sink.pulses) == 2
    fired, suppressed = sink.pulses[1], sink.pulses[0]
    # most recent first by insertion order isn't guaranteed; identify by reason
    fired = next(r for r in sink.pulses if r.dispatched)
    suppressed = next(r for r in sink.pulses if r.suppressed_reason == "cooldown")
    assert fired.trigger_kind == "distress"
    assert suppressed.trigger_kind == "distress"
    assert suppressed.dispatched is False


@pytest.mark.asyncio
async def test_pulse_logs_no_target_suppression() -> None:
    sink = CapturingEventLog()
    host = FakeHost(
        snap=_baseline_snap(
            mood=[80, 110, 160, 80, 130, 100, 135],
            soul_distance=80.0,
        ),
        target=None,
    )
    engine = PulseEngine(host, event_log=sink, agent_id="x")
    _seed_clock_past_cooldown(engine)
    await engine.tick()
    assert len(sink.pulses) == 1
    rec = sink.pulses[0]
    assert rec.trigger_kind == "distress"
    assert rec.suppressed_reason == "no_target"
    assert rec.target_present is False
    assert rec.dispatched is False


@pytest.mark.asyncio
async def test_pulse_log_failure_does_not_crash_tick() -> None:
    """A faulty event log must not propagate into the engine — tick
    must still return its normal result."""
    host = FakeHost(
        snap=_baseline_snap(
            mood=[80, 110, 160, 80, 130, 100, 135],
            soul_distance=80.0,
        )
    )
    engine = PulseEngine(host, event_log=RaisingEventLog(), agent_id="x")
    _seed_clock_past_cooldown(engine)
    result = await engine.tick()
    assert result is not None
    assert result.kind == "distress"


@pytest.mark.asyncio
async def test_pulse_with_no_event_log_keeps_existing_behavior() -> None:
    """Existing PulseEngine(host) construction without event_log must
    still work and not log anything."""
    host = FakeHost(snap=_baseline_snap())
    engine = PulseEngine(host)  # no event_log
    engine.note_outbound()
    result = await engine.tick()
    assert result is None  # baseline → no trigger
    assert host.drift_ticks == 1


@pytest.mark.asyncio
async def test_pulse_requires_agent_id_when_event_log_given() -> None:
    with pytest.raises(ValueError):
        PulseEngine(FakeHost(), event_log=CapturingEventLog())
