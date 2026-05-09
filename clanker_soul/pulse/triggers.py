"""Trigger + target + action + outcome dataclasses for :py:class:`PulseEngine`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clanker_soul.score import Score


@dataclass(frozen=True)
class Trigger:
    """A reason the engine wants to fire a pulse, with state attached.

    ``kind`` is one of:
      - ``distress``        : mood far below soul on V/W
      - ``elation``         : mood far above soul on V with I-lift
      - ``trauma_pressure`` : sustained negative pattern accumulation
      - ``gratitude``       : sustained nourishment > trauma * 2
      - ``long_silence``    : quiet for > max_quiet_seconds

    Additional motivation kinds land in M1.2 (#45):
    ``share_impulse`` / ``restless_curiosity`` / ``argue_impulse`` /
    ``connect_impulse`` / ``withdraw_impulse`` / ``reflective_impulse``
    / ``caretake_impulse``.
    """

    kind: str
    soul: dict
    mood: list[int] | None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "soul": self.soul, "mood": self.mood, **self.metrics}


@dataclass(frozen=True)
class PulseTarget:
    """An opaque address for "where this pulse should go."

    The engine never inspects this — it's passed back to the host's
    ``dispatch_pulse``. Hosts can put a channel id, a recipient meta
    dict, a user id, anything."""

    payload: Any


# Action kinds the host can be asked to enact. The engine itself never
# enacts — it asks; the host decides how. Hosts are free to map the same
# kind onto different real-world tools (e.g. ``post_public`` could be
# Twitter for one host and Mastodon for another).
ACTION_KINDS: frozenset[str] = frozenset(
    {
        "direct_message",  # DM a target
        "post_public",  # tweet / blog / Reddit / etc
        "comment_reply",  # reply to an existing thread
        "browse_topic",  # kick off research/exploration
        "withdraw",  # explicit do-nothing signal
        "tool_invocation",  # generic — host defines the tool
    }
)


@dataclass(frozen=True)
class PulseAction:
    """An action the engine wants the host to enact based on emotional state.

    This is the unit of motivation. The trigger says *why* the engine
    wants to act; the action says *what* and *to whom*. Hosts implement
    :py:meth:`PulseHost.dispatch_action` to turn one of these into a
    real-world effect (a tweet, a DM, a Reddit comment, a search,
    nothing).

    ``kind`` must be in :py:data:`ACTION_KINDS`. ``target`` may be None
    for actions that don't need a recipient (``browse_topic``,
    ``withdraw``, public posts where the host knows the destination).

    ``extra`` carries action-kind-specific metadata. Examples:
      - ``post_public``: ``{"platform": "twitter", "draft": True}``
      - ``browse_topic``: ``{"topic": "how do squids see"}``
      - ``tool_invocation``: ``{"tool_name": "browser_navigate", "args": {...}}``

    The engine never inspects ``extra`` — it's host-defined.
    """

    kind: str
    trigger: Trigger
    target: PulseTarget | None
    prompt: str
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(
                f"PulseAction.kind={self.kind!r} not in ACTION_KINDS ({sorted(ACTION_KINDS)})"
            )


@dataclass(frozen=True)
class ActionOutcome:
    """What happened when a host enacted a :py:class:`PulseAction`.

    ``delivered``: did the action ship? False for declines, failures,
    rate-limited drops, or capability-gated suppressions.

    ``consequences``: the **learning signal**. Score events the host
    generated from the real-world result of the action — a successful
    post that got praise, a comment that got ratio'd, a DM that got
    ignored. The engine auto-ingests every Score here back into physics
    so the soul learns from the agent's own actions. Without this,
    clanker-soul is just an emotional state monitor; with it, the
    feedback loop closes and the soul actually adapts.

    Hosts that don't populate consequences give up the learning signal.
    Hosts that DO populate it shape the agent's future impulses.

    ``note``: optional human-readable detail for logs. Not used by the
    engine.
    """

    delivered: bool
    consequences: tuple[Score, ...] = ()
    note: str | None = None


__all__ = [
    "Trigger",
    "PulseTarget",
    "PulseAction",
    "ActionOutcome",
    "ACTION_KINDS",
]
