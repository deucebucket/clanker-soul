"""Frozen record dataclasses for the event log.

These are the rows. The :py:class:`EventLog` protocol decides where
they go; :py:class:`SqliteEventLog` is the production sink.
"""

from __future__ import annotations

from dataclasses import dataclass

from clanker_soul.score import Score
from clanker_soul.soul import SoulState


@dataclass(frozen=True)
class IngestRecord:
    """Everything needed to reconstruct one ``EmotionalPhysics.ingest`` call.

    All seven fields the UI needs to answer "why did the agent end up
    here": the raw event, the optionally mood-primed event, the mood
    before/after, the soul before/after, the physics math (weight/armor/
    effective), the breach result, the patterns and classification, and
    a pre-baked human-readable ``why`` string."""

    ts: float
    agent_id: str
    raw: Score
    primed: Score | None
    mood_before: Score | None
    mood_after: Score
    soul_before: SoulState
    soul_after: SoulState
    weight_raw: float
    armor: float
    weight_effective: float
    breached: bool
    breach_delta: float
    patterns: tuple[str, ...]
    classification: str | None
    why: str


@dataclass(frozen=True)
class PulseRecord:
    """One ``PulseEngine`` evaluation, whether or not it fired.

    ``trigger_kind`` is None when no trigger matched (steady state) or
    when one matched but was suppressed. ``suppressed_reason`` is one
    of ``"cooldown"``, ``"no_target"``, ``"dispatch_failed"``,
    ``"no_trigger"``, or None when a pulse actually dispatched.
    ``prompt`` is the synthetic self-prompt text — None when no pulse
    was attempted.

    ``face_id`` is the corpus face that authored the prompt — None when
    the engine had no corpus configured or the legacy fallback path
    fired. Recorded in M3.3 so log analysis can answer "which faces
    actually fire" without reparsing the prompt text.
    """

    ts: float
    agent_id: str
    snap: dict
    trigger_kind: str | None
    suppressed_reason: str | None
    target_present: bool
    dispatched: bool
    prompt: str | None
    face_id: str | None = None


__all__ = ["IngestRecord", "PulseRecord"]
