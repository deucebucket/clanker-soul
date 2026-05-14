"""``clanker_soul.cascade`` — the M4 Autonomous Motivation Cascade.

Heartbeat tick → Roll 0 (gate) → Roll 1 (contemplation) → Roll 2 (delta
threshold) → Roll 3 (action selection). Each roll is operator-overridable
via config + a callable hook; defaults are sane but never load-bearing.

This package is **opt-in**. ``SoulPlugin`` does NOT construct an
``IdleLoop`` automatically — hosts wire one explicitly when they want
endogenous motivation. The per-feature drop-in invariant
(``memory/feedback_drop_in_no_refactor.md``) requires it.

Current contents:

* :py:mod:`clanker_soul.cascade.idle` — Roll 0 (gate) + Roll 1
  (contemplation).
* :py:mod:`clanker_soul.cascade.action` — Roll 2 (delta threshold) +
  Roll 3 (tagged action selection).
"""

from clanker_soul.cascade.action import (
    ActionHandler,
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    RegisteredAction,
    should_act,
    tags_from_delta,
)
from clanker_soul.cascade.idle import (
    GateConfig,
    GateRollContext,
    IDLE_CONTEMPLATION_KIND,
    IdleLoop,
    TickResult,
    default_gate,
)

__all__ = [
    "ActionHandler",
    "ActionRegistry",
    "ActionThresholdConfig",
    "CascadeActionContext",
    "GateConfig",
    "GateRollContext",
    "IDLE_CONTEMPLATION_KIND",
    "IdleLoop",
    "RegisteredAction",
    "TickResult",
    "default_gate",
    "should_act",
    "tags_from_delta",
]
