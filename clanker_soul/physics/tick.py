"""``PhysicsTick`` — the diagnostic record returned from
:py:meth:`EmotionalPhysics.ingest`.

Pre-rounded floats so direct print/log output is human-readable. The
deeper, structured-for-forensics record is :py:class:`IngestRecord`
in :py:mod:`clanker_soul.eventlog`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PhysicsTick:
    """Result of one event ingestion — for diagnostics/logging."""

    weight_raw: float
    armor: float
    weight_effective: float
    breached: bool
    breach_delta_applied: float
    soul_distance_before: float
    patterns: list[str] = field(default_factory=list)


__all__ = ["PhysicsTick"]
