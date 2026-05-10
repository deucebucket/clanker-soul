"""``ContemplationResult`` — diagnostic record from
:py:meth:`EmotionalPhysics.contemplate`.

Contemplation is the M4 primitive: ingest a :py:class:`PromptFace`'s
``vadugwi_affinity`` as a synthetic mood-shift, *without* updating
soul reservoirs or triggering breach. Thinking about a thought is
not a real event — only mood shifts.

The result captures pre/post mood snapshots and the per-dim delta
so downstream cascade layers (gate, action selection in #81/#82)
can route off the *direction* of the shift, not just absolute state.
"""

from __future__ import annotations

from dataclasses import dataclass

from clanker_soul.score import Score


@dataclass(frozen=True)
class ContemplationResult:
    """Outcome of one contemplation pass."""

    pre_mood: tuple[int, int, int, int, int, int, int]
    post_mood: tuple[int, int, int, int, int, int, int]
    delta: tuple[int, int, int, int, int, int, int]
    score: Score


__all__ = ["ContemplationResult"]
