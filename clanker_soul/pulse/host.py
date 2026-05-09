"""``PulseHost`` Protocol — what hosts must implement so
:py:class:`PulseEngine` can drive them.

clanker-soul does not know about your message dataclass, your channel
abstraction, your reminders system, or your agent runtime. Hosts
implement these methods; the engine calls them.

All methods may be sync or async. Async hooks are awaited; sync hooks
are called directly. (The engine uses :func:`asyncio.iscoroutine`
discipline rather than wrapping in ``asyncio.run``.)

**Action vs message dispatch:** there are two dispatch hooks, and
hosts implement at most one of them:

- :py:meth:`dispatch_action` — the **modern** path. Receives a full
  :py:class:`PulseAction` (kind / trigger / target / prompt / extra)
  and returns an :py:class:`ActionOutcome` carrying the consequences
  for the soul to learn from. This is how you get the impulse →
  action → consequence → soul-update learning loop.
- :py:meth:`dispatch_pulse` — the **backwards-compatible** path
  preserved from clanker-soul v0.1. Equivalent to a host that only
  handles ``direct_message`` actions and never reports consequences.
  Existing implementations work unchanged via an internal shim.

If a host implements both, the engine prefers ``dispatch_action`` for
non-DM actions and the explicit ``dispatch_pulse`` for legacy DM
flow.
"""
from __future__ import annotations

from typing import Awaitable, Protocol, runtime_checkable

from clanker_soul.pulse.triggers import (
    ActionOutcome,
    PulseAction,
    PulseTarget,
    Trigger,
)


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
        """**Backwards-compatible.** Deliver a direct-message pulse:
        run the synthetic prompt through the agent pipeline and send
        the response. Return True on successful delivery, False if
        dispatch was aborted. Raised exceptions are caught and logged.

        Hosts that want to receive non-DM action kinds (``post_public``,
        ``comment_reply``, ``browse_topic``, etc.) or want to feed
        consequences back into the soul should implement
        :py:meth:`dispatch_action` instead.
        """
        ...

    def dispatch_action(
        self, action: PulseAction,
    ) -> "Awaitable[ActionOutcome] | ActionOutcome":
        """Enact a :py:class:`PulseAction` against the real world and
        return what happened.

        The host inspects ``action.kind`` and routes to the appropriate
        real-world effect — Twitter for ``post_public``, the chat
        channel for ``direct_message``, a search tool for
        ``browse_topic``, no-op for ``withdraw``, etc.

        The returned :py:class:`ActionOutcome.consequences` tuple is
        the **learning signal**. Score events generated from the
        real-world result (a post that got praise, a comment that got
        ratio'd, a DM that got ignored) are auto-ingested by the
        engine so the soul learns from its own actions.

        Hosts that only need legacy DM behavior can leave this method
        unimplemented and use :py:meth:`dispatch_pulse` instead.
        """
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

    # ------------------------------------------------------------------
    # Optional hooks (runtime-detected via getattr, not part of the
    # formal Protocol so existing PulseHost implementations are
    # unaffected). Hosts that want the corresponding behavior add the
    # method; the engine picks it up automatically.
    # ------------------------------------------------------------------
    #
    # ``situation_tags(trigger: Trigger) -> Iterable[str]``
    #     Return the set of host-defined situation tags relevant to the
    #     trigger (e.g. ``"incoming_public_stimulus"``,
    #     ``"autonomy_idle"``, ``"post_conversation"``). The corpus uses
    #     these to filter which faces are eligible. When absent, the
    #     engine falls back to ``default_tags_from_metrics`` for hosts
    #     using the M3.2+ corpus path.
    #
    # ``memory_topics_present(topic: str) -> bool``
    #     Answer "do I have memories tagged with this topic?" The
    #     corpus consults this for faces that declared a
    #     :py:attr:`PromptFace.memory_anchor`. Without this hook,
    #     anchored faces are filtered out entirely. M3.4 wires the call
    #     site; hosts that already implement it (memory-aware CARL,
    #     hermes builds) get anchor-driven faces working without further
    #     changes.
    #
    # ``peer_distress_signals() -> list[dict]``
    #     Return any visible distress signals from peer agents. Enables
    #     the ``caretake_impulse`` trigger; absent → trigger never
    #     fires.


__all__ = ["PulseHost"]
