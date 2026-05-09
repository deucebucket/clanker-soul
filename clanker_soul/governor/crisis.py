"""Crisis vs spike discrimination.

Two situations look identical on mood/reservoir signals alone:
  - Agent feels horrible because someone is verbally attacking it
  - Agent feels horrible because something is genuinely broken in
    the world

The governor needs to tell these apart so the host can route
appropriately: an emotional spike calls for self-regulation
(restrictions, encouragement to express, ride it out); a real
emergency calls for immediate user notification.

The discrimination key is :py:attr:`Score.direction` plus
:py:attr:`Score.source` on recent ingest events. SELF_DIRECTED
inputs from a single source = personal interaction, spike.
EXTERNAL_REPORT inputs from diverse sources = world is broken,
emergency.

Hosts that don't populate ``direction`` get a low-confidence
"spike or unclear" signal — better than a false emergency, but lossy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from clanker_soul.eventlog.records import IngestRecord
from clanker_soul.governor.levels import GovernorConfig


@dataclass(frozen=True)
class CrisisDiagnosis:
    """Result of :py:func:`crisis_signal`.

    ``is_emergency`` — True if recent input pattern suggests something
    in the external world warrants immediate user attention.
    ``summary`` — human-readable one-liner the host can show the user.
    ``confidence`` — 0..1; how sure we are. Hosts can threshold this.
    ``reasons`` — bullet-list of evidence the discriminator used.
    ``directed_count`` / ``external_count`` / ``atmospheric_count`` /
    ``unspecified_count`` — raw event-direction tally over the
    inspection window.
    ``distinct_sources`` — how many different ``source`` values
    appeared in the window. Diverse sources = broad pattern.
    """

    is_emergency: bool
    summary: str
    confidence: float
    reasons: tuple[str, ...]
    directed_count: int
    external_count: int
    atmospheric_count: int
    unspecified_count: int
    distinct_sources: int


def crisis_signal(
    recent_events: list[IngestRecord],
    config: GovernorConfig,
) -> CrisisDiagnosis:
    """Inspect the last N ingest records and decide whether the
    distress is worth escalating to the user.

    ``recent_events`` should be the most-recent-first slice of
    significant events (host decides what 'significant' means;
    typically classification == 'negative' OR breached == True).
    Empty list = no signal."""
    if not recent_events:
        return CrisisDiagnosis(
            is_emergency=False,
            summary="no recent significant events",
            confidence=1.0,
            reasons=("no events in inspection window",),
            directed_count=0,
            external_count=0,
            atmospheric_count=0,
            unspecified_count=0,
            distinct_sources=0,
        )

    window = recent_events[: config.crisis_window_events]

    direction_counts: Counter[str | None] = Counter(e.raw.direction for e in window)
    directed = direction_counts.get("SELF_DIRECTED", 0)
    external = direction_counts.get("EXTERNAL_REPORT", 0)
    atmospheric = direction_counts.get("ATMOSPHERIC", 0)
    unspecified = direction_counts.get(None, 0)
    total_directional = directed + external + atmospheric

    distinct_sources = len({e.raw.source for e in window if e.raw.source})

    # No direction info on any event → fall back to "spike, unclear"
    if total_directional == 0:
        return CrisisDiagnosis(
            is_emergency=False,
            summary="emotional spike (direction not specified)",
            confidence=0.3,
            reasons=(
                f"no Score.direction populated on {len(window)} recent events",
                "host is not providing crisis-vs-spike attribution",
            ),
            directed_count=directed,
            external_count=external,
            atmospheric_count=atmospheric,
            unspecified_count=unspecified,
            distinct_sources=distinct_sources,
        )

    external_pct = external / total_directional
    self_pct = directed / total_directional

    # Emergency: majority of directional events are EXTERNAL_REPORT
    if external_pct >= config.crisis_external_majority_threshold:
        return CrisisDiagnosis(
            is_emergency=True,
            summary=(
                f"{external}/{total_directional} recent significant events "
                f"describe external state"
                + (f" across {distinct_sources} distinct sources" if distinct_sources > 1 else "")
            ),
            confidence=external_pct,
            reasons=(
                f"{external} EXTERNAL_REPORT events vs {directed} SELF_DIRECTED",
                f"{distinct_sources} distinct sources in window",
                "majority external → likely real-world event, not personal",
            ),
            directed_count=directed,
            external_count=external,
            atmospheric_count=atmospheric,
            unspecified_count=unspecified,
            distinct_sources=distinct_sources,
        )

    # Spike: majority SELF_DIRECTED
    if self_pct > 0.5:
        return CrisisDiagnosis(
            is_emergency=False,
            summary=(
                f"emotional spike from {directed} directed event" + ("s" if directed != 1 else "")
            ),
            confidence=self_pct,
            reasons=(
                f"{directed}/{total_directional} recent events directed at agent",
                "personal interaction pattern, not external emergency",
            ),
            directed_count=directed,
            external_count=external,
            atmospheric_count=atmospheric,
            unspecified_count=unspecified,
            distinct_sources=distinct_sources,
        )

    # Mixed signal — atmospheric pressure or unclear
    return CrisisDiagnosis(
        is_emergency=False,
        summary="ambient pressure (mixed-direction signal)",
        confidence=0.5,
        reasons=(
            f"directions split: {directed} self / {external} external / {atmospheric} atmospheric",
        ),
        directed_count=directed,
        external_count=external,
        atmospheric_count=atmospheric,
        unspecified_count=unspecified,
        distinct_sources=distinct_sources,
    )


__all__ = ["CrisisDiagnosis", "crisis_signal"]
