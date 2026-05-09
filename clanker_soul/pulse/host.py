"""``PulseHost`` Protocol — what hosts must implement so
:py:class:`PulseEngine` can drive them.

clanker-soul does not know about your message dataclass, your channel
abstraction, your reminders system, or your agent runtime. Hosts
implement these methods; the engine calls them.

All methods may be sync or async. Async hooks are awaited; sync hooks
are called directly. (The engine uses :func:`asyncio.iscoroutine`
discipline rather than wrapping in ``asyncio.run``.)
"""
from __future__ import annotations

from typing import Awaitable, Protocol, runtime_checkable

from clanker_soul.pulse.triggers import PulseTarget, Trigger


@runtime_checkable
class PulseHost(Protocol):
    """Hooks the engine calls into."""

    def snapshot(self) -> dict:
        """Return a dict shaped like ``EmotionalPhysics`` snapshots:

        ``{"soul": {"v": int, ..., "i": int}, "mood": [v,a,d,u,g,w,i] | None,
           "soul_distance": float | None, "trauma_load": float,
           "nourishment_load": float, ...}``

        Hosts can return additional keys; the engine ignores extras."""
        ...

    def slow_drift_tick(self) -> None:
        """Run soul-drift bookkeeping. Called every interval regardless
        of whether a pulse fires."""
        ...

    def most_recent_target(self) -> PulseTarget | None:
        """Return the freshest external chat target, or None to stay quiet."""
        ...

    def dispatch_pulse(self, target: PulseTarget, trigger: Trigger,
                      prompt: str) -> Awaitable[bool] | bool:
        """Deliver a pulse: run the synthetic prompt through the agent
        pipeline and send the response. Return True on successful
        delivery, False if dispatch was aborted (no recipient, channel
        down, etc.). Raised exceptions are caught and logged by the
        engine."""
        ...

    def due_reminders(self) -> list[dict]:
        """Return reminders that have come due since the last tick.
        Each dict must include a ``message`` key; the rest is
        host-defined."""
        ...

    def deliver_reminder(self, target: PulseTarget,
                        reminder: dict) -> Awaitable[None] | None:
        """Send a reminder message. Sync or async; raised exceptions
        are caught and logged."""
        ...


__all__ = ["PulseHost"]
