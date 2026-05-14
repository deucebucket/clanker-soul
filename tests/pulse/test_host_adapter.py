"""Tests for the public PulseHostAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from clanker_soul import (
    ActionOutcome,
    PulseAction,
    PulseConfig,
    PulseDispatcher,
    PulseEngine,
    PulseHostAdapter,
    PulseTarget,
    SoulState,
    Trigger,
)


def _trigger() -> Trigger:
    return Trigger(
        kind="distress",
        soul={"v": 145, "w": 175},
        mood=[40, 110, 100, 200, 130, 30, 100],
    )


def _action() -> PulseAction:
    return PulseAction(
        kind="direct_message",
        trigger=_trigger(),
        target=PulseTarget(payload="room"),
        prompt="hello",
    )


class HostAdapterFixture(PulseHostAdapter):
    def __init__(self, *, dispatcher) -> None:
        super().__init__(dispatcher=dispatcher)
        self.target = PulseTarget(payload="room")
        self.drift_ticks = 0

    def snapshot(self) -> dict:
        soul = SoulState(v=145, w=175)
        return {
            "soul": {
                "v": soul.v,
                "a": soul.a,
                "d": soul.d,
                "u": soul.u,
                "g": soul.g,
                "w": soul.w,
                "i": soul.i,
            },
            "mood": [40, 110, 100, 200, 130, 30, 100],
            "soul_distance": 60.0,
            "trauma_load": 0.0,
            "nourishment_load": 0.0,
        }

    def slow_drift_tick(self) -> None:
        self.drift_ticks += 1

    def most_recent_target(self) -> PulseTarget | None:
        return self.target

    def due_reminders(self) -> list[dict]:
        return []

    def deliver_reminder(self, target: PulseTarget, reminder: dict) -> None:
        return None


def test_public_adapter_imports_from_top_level() -> None:
    assert PulseHostAdapter.__name__ == "PulseHostAdapter"


async def test_adapter_delegates_to_sync_callable() -> None:
    calls: list[PulseAction] = []

    def dispatcher(action: PulseAction) -> ActionOutcome:
        calls.append(action)
        return ActionOutcome(delivered=True, note="sync")

    host = HostAdapterFixture(dispatcher=dispatcher)
    out = await host.dispatch_action(_action())
    assert out.delivered is True
    assert out.note == "sync"
    assert calls == [_action()]


async def test_adapter_delegates_to_async_callable() -> None:
    dispatcher = AsyncMock(return_value=ActionOutcome(delivered=True, note="async"))
    host = HostAdapterFixture(dispatcher=dispatcher)
    out = await host.dispatch_action(_action())
    assert out.delivered is True
    assert out.note == "async"
    dispatcher.assert_awaited_once()


async def test_adapter_delegates_to_pulse_dispatcher_object() -> None:
    sender = AsyncMock(return_value=True)
    dispatcher = PulseDispatcher(signal_sender=sender)
    host = HostAdapterFixture(dispatcher=dispatcher)
    out = await host.dispatch_action(_action())
    assert out.delivered is True
    sender.assert_awaited_once_with(PulseTarget(payload="room"), "hello")


async def test_adapter_rejects_bad_dispatcher_return() -> None:
    host = HostAdapterFixture(dispatcher=lambda action: True)
    with pytest.raises(TypeError, match="expected ActionOutcome"):
        await host.dispatch_action(_action())


def test_base_adapter_requires_host_hooks() -> None:
    host = PulseHostAdapter(dispatcher=lambda action: ActionOutcome(delivered=True))
    with pytest.raises(NotImplementedError):
        host.snapshot()
    with pytest.raises(NotImplementedError):
        host.slow_drift_tick()
    with pytest.raises(NotImplementedError):
        host.most_recent_target()
    with pytest.raises(NotImplementedError):
        host.due_reminders()
    with pytest.raises(NotImplementedError):
        host.deliver_reminder(PulseTarget(payload="x"), {})


async def test_adapter_host_works_with_pulse_engine() -> None:
    received: list[PulseAction] = []

    def dispatcher(action: PulseAction) -> ActionOutcome:
        received.append(action)
        return ActionOutcome(delivered=True)

    host = HostAdapterFixture(dispatcher=dispatcher)
    engine = PulseEngine(
        host,
        config=PulseConfig(
            min_quiet_seconds=0.0,
            distress_v_drop=15.0,
            distress_w_drop=15.0,
            distance_trigger=20.0,
        ),
    )
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "distress"
    assert host.drift_ticks == 1
    assert len(received) == 1
    assert received[0].kind == "direct_message"
    assert received[0].target == host.target
