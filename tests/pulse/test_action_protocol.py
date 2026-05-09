"""Tests for PulseAction / ActionOutcome / dispatch_action (M1.1).

Covers:
- PulseAction dataclass + validation
- ActionOutcome shape
- Backwards-compat shim: legacy dispatch_pulse host still works
- Modern dispatch_action host receives PulseAction
- Outcome consequences auto-ingest into physics when provided
- Consequences dropped (with warning) when physics is not provided
- Mixed host (both methods) → dispatch_action takes precedence
- Non-DM action kinds reject cleanly on legacy hosts
"""

from __future__ import annotations

import asyncio

import pytest

from clanker_soul import (
    ACTION_KINDS,
    ActionOutcome,
    EmotionalPhysics,
    PhysicsConfig,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Score,
    SoulState,
    Trigger,
)


# ---------------------------------------------------------------------------
# PulseAction validation
# ---------------------------------------------------------------------------


def _trigger() -> Trigger:
    return Trigger(
        kind="distress", soul={"v": 145, "w": 175}, mood=[40, 110, 100, 200, 130, 30, 100]
    )


def test_pulse_action_accepts_known_kinds() -> None:
    for kind in ACTION_KINDS:
        action = PulseAction(
            kind=kind,
            trigger=_trigger(),
            target=None,
            prompt="hi",
        )
        assert action.kind == kind


def test_pulse_action_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="ACTION_KINDS"):
        PulseAction(kind="invent_action", trigger=_trigger(), target=None, prompt="x")


def test_action_kinds_includes_all_six() -> None:
    expected = {
        "direct_message",
        "post_public",
        "comment_reply",
        "browse_topic",
        "withdraw",
        "tool_invocation",
    }
    assert ACTION_KINDS == expected


# ---------------------------------------------------------------------------
# Test hosts
# ---------------------------------------------------------------------------


class _BaseHost:
    """Implements snapshot/drift/target/reminders so we can focus tests
    on the dispatch surface."""

    def __init__(self, soul: SoulState | None = None, mood: list[int] | None = None) -> None:
        self.soul = soul or SoulState(v=145, w=175)
        self.mood = mood or [40, 110, 100, 200, 130, 30, 100]
        self.target = PulseTarget(payload={"channel": "test"})

    def snapshot(self) -> dict:
        return {
            "soul": {
                "v": self.soul.v,
                "a": self.soul.a,
                "d": self.soul.d,
                "u": self.soul.u,
                "g": self.soul.g,
                "w": self.soul.w,
                "i": self.soul.i,
            },
            "mood": self.mood,
            "soul_distance": 60.0,  # > distance_trigger so trigger fires
            "trauma_load": 5.0,
            "nourishment_load": 0.0,
        }

    def slow_drift_tick(self) -> None:
        pass

    def most_recent_target(self) -> PulseTarget | None:
        return self.target

    def due_reminders(self) -> list[dict]:
        return []

    def deliver_reminder(self, target, reminder) -> None:
        pass


class LegacyHost(_BaseHost):
    """Only implements dispatch_pulse — the v0.1 path."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        self.calls.append((target, trigger.kind, prompt))
        return True


class ModernHost(_BaseHost):
    """Only implements dispatch_action — the new path. Returns
    consequences scored from a hypothetical real-world result."""

    def __init__(self, consequences: tuple[Score, ...] = ()) -> None:
        super().__init__()
        self.calls: list[PulseAction] = []
        self._consequences = consequences

    def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.calls.append(action)
        return ActionOutcome(delivered=True, consequences=self._consequences)


class MixedHost(_BaseHost):
    """Implements both — dispatch_action should be preferred."""

    def __init__(self) -> None:
        super().__init__()
        self.action_calls: list[PulseAction] = []
        self.pulse_calls: list[tuple] = []

    def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.action_calls.append(action)
        return ActionOutcome(delivered=True)

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        self.pulse_calls.append((target, trigger.kind, prompt))
        return True


class FailingHost(_BaseHost):
    """dispatch_action raises — engine should soft-fail and not crash."""

    def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        raise RuntimeError("boom")


# Force distress-trigger conditions on every test host.
_LOW_MOOD_CFG = PulseConfig(
    min_quiet_seconds=0.0,
    distress_v_drop=15.0,
    distress_w_drop=15.0,
    distance_trigger=20.0,
)


# ---------------------------------------------------------------------------
# Backwards compat: legacy host still works
# ---------------------------------------------------------------------------


async def test_legacy_dispatch_pulse_still_works() -> None:
    host = LegacyHost()
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)
    engine.note_outbound()  # suppress long_silence-since-epoch
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "distress"
    assert len(host.calls) == 1


# ---------------------------------------------------------------------------
# Modern dispatch_action receives the full PulseAction
# ---------------------------------------------------------------------------


async def test_modern_dispatch_action_called_with_pulse_action() -> None:
    host = ModernHost()
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)
    engine.note_outbound()  # suppress long_silence-since-epoch
    trigger = await engine.tick()
    assert trigger is not None
    assert len(host.calls) == 1
    action = host.calls[0]
    assert isinstance(action, PulseAction)
    assert action.kind == "direct_message"  # default for legacy DM flow
    assert action.trigger.kind == "distress"
    assert action.target is host.target


async def test_mixed_host_prefers_dispatch_action() -> None:
    host = MixedHost()
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)
    engine.note_outbound()
    await engine.tick()
    assert len(host.action_calls) == 1
    assert len(host.pulse_calls) == 0  # legacy path NOT taken


async def test_dispatch_action_failure_does_not_crash_engine() -> None:
    host = FailingHost()
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)
    # Should not raise.
    trigger = await engine.tick()
    assert trigger is None or trigger.kind == "distress"  # tick survives


# ---------------------------------------------------------------------------
# Outcome consequences auto-ingest into physics
# ---------------------------------------------------------------------------


async def test_consequences_ingest_into_physics_when_provided(tmp_path) -> None:
    physics = EmotionalPhysics(
        soul=SoulState(v=145, w=175),
        config=PhysicsConfig(),
    )
    consequences = (
        Score(v=40, w=40, patterns=("RATIO",)),  # post got dunked on
    )
    host = ModernHost(consequences=consequences)

    # Drive mood low so distress fires.
    physics.ingest(Score(v=40, w=40, patterns=("ABANDONMENT",)))
    mood_before = physics.mood
    assert mood_before is not None

    engine = PulseEngine(host, config=_LOW_MOOD_CFG, physics=physics)
    engine.note_outbound()
    await engine.tick()
    # The consequence Score was ingested → mood moved further or stayed
    # heavy. We assert the physics layer SAW the consequence by checking
    # that the most recent ingest tick has the RATIO pattern.
    last = physics.last_tick
    assert last is not None
    assert "RATIO" in last.patterns


async def test_consequences_dropped_with_warning_when_physics_absent(caplog) -> None:
    consequences = (Score(v=40, w=40, patterns=("RATIO",)),)
    host = ModernHost(consequences=consequences)
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)  # no physics= kwarg
    engine.note_outbound()

    with caplog.at_level("WARNING"):
        await engine.tick()
    msgs = [r.message for r in caplog.records]
    assert any("dropping" in m.lower() and "consequences" in m.lower() for m in msgs)


async def test_empty_consequences_is_a_noop() -> None:
    host = ModernHost(consequences=())  # nothing to ingest
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)  # no physics needed
    engine.note_outbound()
    # Should not warn, should not error.
    await engine.tick()
    assert len(host.calls) == 1


# ---------------------------------------------------------------------------
# Non-DM actions on legacy hosts
# ---------------------------------------------------------------------------


def test_legacy_host_cannot_handle_non_dm_action() -> None:
    """A host that only has dispatch_pulse can only serve direct_message
    actions. Non-DM actions return delivered=False without raising."""
    host = LegacyHost()
    engine = PulseEngine(host, config=_LOW_MOOD_CFG)

    action = PulseAction(
        kind="post_public",
        trigger=_trigger(),
        target=host.target,
        prompt="ok",
    )
    outcome = asyncio.run(engine._dispatch_action_via_host(action))
    assert outcome is not None
    assert outcome.delivered is False
    assert outcome.note == "legacy_host_no_action_support"
    assert host.calls == []  # never tried the legacy path


# ---------------------------------------------------------------------------
# ActionOutcome shape
# ---------------------------------------------------------------------------


def test_action_outcome_defaults_empty_consequences() -> None:
    o = ActionOutcome(delivered=True)
    assert o.consequences == ()
    assert o.note is None


def test_action_outcome_carries_consequences_and_note() -> None:
    s = Score(v=200, w=200, patterns=("AFFIRMATION",))
    o = ActionOutcome(
        delivered=True,
        consequences=(s,),
        note="post got 50 likes",
    )
    assert o.consequences == (s,)
    assert o.note == "post got 50 likes"
