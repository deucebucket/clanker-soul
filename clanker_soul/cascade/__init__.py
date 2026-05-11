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
  (contemplation). Lands #81. Roll 2 + Roll 3 (action registry + tag
  selection) ship in #82 as :py:mod:`clanker_soul.cascade.action`.
"""

from clanker_soul.cascade.idle import (
    GateConfig,
    GateRollContext,
    IDLE_CONTEMPLATION_KIND,
    IdleLoop,
    TickResult,
    default_gate,
)

__all__ = [
    "GateConfig",
    "GateRollContext",
    "IDLE_CONTEMPLATION_KIND",
    "IdleLoop",
    "TickResult",
    "default_gate",
]
