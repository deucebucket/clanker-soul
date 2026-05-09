"""Build the live panel view from a SQLite soul.db.

The dashboard is a separate process from the agent. It can't reach
into the agent's :py:class:`EmotionalPhysics` instance to ask for
current mood — it reads what's in the file. This module assembles
the dashboard's view of the agent's state from:

  - latest ``events`` row → current mood (the agent's
    ``mood_after`` from its most recent ingest)
  - ``soul_state`` row → soul + reservoirs (saved on plugin.save /
    plugin.close)
  - latest ``pulse_log`` row → last pulse decision
  - recent ``events`` rows → "what's been landing"

If the agent process saves infrequently, the dashboard's view of
reservoirs will lag. Mood is always fresh because event-log writes
are synchronous per ingest.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from clanker_soul.eventlog import IngestRecord, PulseRecord, SqliteEventLog
from clanker_soul.governor import (
    CapabilityLevel,
    CrisisDiagnosis,
    GovernorConfig,
    assess_capability,
    compose_state_context,
    crisis_signal,
)
from clanker_soul.physics import soul_distance
from clanker_soul.score import Score
from clanker_soul.soul import SoulStore


# 7-dim VADUGWI axis order (matches Score field order)
_AXIS_LABELS = ("V", "A", "D", "U", "G", "W", "I")
_DIM_COUNT = len(_AXIS_LABELS)


@dataclass(frozen=True)
class RadarPoint:
    label: str
    value: int
    x: float
    y: float


@dataclass(frozen=True)
class RadarPolygon:
    """An SVG polygon for one VADUGWI vector."""
    points: tuple[RadarPoint, ...]
    points_attr: str  # space-separated "x,y x,y..." for <polygon>


@dataclass(frozen=True)
class RadarRing:
    """A concentric reference ring."""
    radius: float
    label: str  # e.g. "128", "192"


@dataclass(frozen=True)
class LiveView:
    """Everything the live panel template needs."""
    agent_id: str
    has_state: bool
    mood: list[int] | None
    soul: dict[str, Any]
    soul_distance: float | None
    trauma_load: float
    nourishment_load: float
    trauma_by_pattern: list[tuple[str, float]]
    nourishment_by_pattern: list[tuple[str, float]]
    recent_events: list[IngestRecord]
    last_pulse: PulseRecord | None
    capability_level: CapabilityLevel
    crisis: CrisisDiagnosis
    state_context: str
    # Radar geometry (precomputed for the template)
    radar_size: int
    radar_center: float
    radar_radius: float
    radar_rings: tuple[RadarRing, ...]
    radar_axes: tuple[RadarPoint, ...]
    radar_mood: RadarPolygon | None
    radar_soul: RadarPolygon


def _polar_xy(value: int, axis_idx: int, center: float, radius: float) -> tuple[float, float]:
    """Convert (dim-value, axis-index) into svg coords."""
    angle = -math.pi / 2 + axis_idx * (2 * math.pi / _DIM_COUNT)
    dist = (value / 255.0) * radius
    return center + dist * math.cos(angle), center + dist * math.sin(angle)


def _make_polygon(values: tuple[int, ...], center: float, radius: float) -> RadarPolygon:
    pts: list[RadarPoint] = []
    for i, (label, val) in enumerate(zip(_AXIS_LABELS, values)):
        x, y = _polar_xy(val, i, center, radius)
        pts.append(RadarPoint(label=label, value=val, x=x, y=y))
    points_attr = " ".join(f"{p.x:.1f},{p.y:.1f}" for p in pts)
    return RadarPolygon(points=tuple(pts), points_attr=points_attr)


def _build_radar_axes(center: float, radius: float) -> tuple[RadarPoint, ...]:
    """Endpoints for the 7 axis lines."""
    out = []
    for i, label in enumerate(_AXIS_LABELS):
        x, y = _polar_xy(255, i, center, radius)
        out.append(RadarPoint(label=label, value=255, x=x, y=y))
    return tuple(out)


def _build_radar_rings(center: float, radius: float) -> tuple[RadarRing, ...]:
    """Reference rings at value=64, 128, 192 (relative)."""
    return tuple(
        RadarRing(radius=(v / 255.0) * radius, label=str(v))
        for v in (64, 128, 192)
    )


def _latest_event_mood(log: SqliteEventLog, agent_id: str) -> list[int] | None:
    """Read the latest event's mood_after to get the agent's current
    mood. Returns None if no events yet."""
    recs = log.read_ingest(agent_id, limit=1)
    if not recs:
        return None
    m = recs[0].mood_after
    return [m.v, m.a, m.d, m.u, m.g, m.w, m.i]


def _latest_pulse(log: SqliteEventLog, agent_id: str) -> PulseRecord | None:
    recs = log.read_pulse(agent_id, limit=1)
    return recs[0] if recs else None


def _significant_recent(
    log: SqliteEventLog, agent_id: str, limit: int = 50,
) -> list[IngestRecord]:
    """Recent events for the governor's crisis-signal window."""
    return [
        ev for ev in log.read_ingest(agent_id, limit=limit)
        if ev.classification == "negative" or ev.breached
    ]


def _top_n_by_pattern(decayed: dict[str, float], n: int = 10) -> list[tuple[str, float]]:
    items = sorted(decayed.items(), key=lambda kv: kv[1], reverse=True)
    return items[:n]


def build_live_view(
    store: SoulStore,
    agent_id: str,
    *,
    governor_config: GovernorConfig | None = None,
    radar_size: int = 320,
) -> LiveView:
    """Assemble the LiveView for ``agent_id`` from on-disk state.

    Pure-ish: only reads the DB. Fast enough to call every ~2s
    polling tick."""
    cfg = governor_config or GovernorConfig()
    log = SqliteEventLog(store)
    soul, trauma, nourishment = store.load(agent_id)

    has_any_event = log.count_ingest(agent_id) > 0
    has_state: bool = has_any_event or _agent_in_soul_state(store, agent_id)

    mood_list = _latest_event_mood(log, agent_id) if has_any_event else None
    mood_score = (
        Score(v=mood_list[0], a=mood_list[1], d=mood_list[2], u=mood_list[3],
              g=mood_list[4], w=mood_list[5], i=mood_list[6])
        if mood_list else None
    )
    distance = soul_distance(mood_score, soul) if mood_score else None
    trauma_load = trauma.load()
    nourishment_load = nourishment.load()

    snap = {
        "mood": mood_list,
        "soul": soul.to_dict(),
        "soul_distance": distance,
        "trauma_load": trauma_load,
        "nourishment_load": nourishment_load,
    }

    # Governor outputs
    significant = _significant_recent(log, agent_id, limit=cfg.crisis_window_events * 5)
    significant = significant[: cfg.crisis_window_events]
    level = assess_capability(snap, cfg)
    crisis = crisis_signal(significant, cfg)
    state_ctx = compose_state_context(
        level, snap, cfg, recent_events=significant, crisis=crisis,
    )

    # Recent events for the "what's landing" panel — broader than crisis window
    recent = log.read_ingest(agent_id, limit=5)

    # Radar geometry
    center = radar_size / 2
    radius = (radar_size / 2) - 30  # leave room for labels
    rings = _build_radar_rings(center, radius)
    axes = _build_radar_axes(center, radius)
    soul_polygon = _make_polygon(soul.as_tuple(), center, radius)
    mood_polygon = (
        _make_polygon(tuple(mood_list), center, radius) if mood_list else None
    )

    return LiveView(
        agent_id=agent_id,
        has_state=has_state,
        mood=mood_list,
        soul=soul.to_dict(),
        soul_distance=distance,
        trauma_load=trauma_load,
        nourishment_load=nourishment_load,
        trauma_by_pattern=_top_n_by_pattern(trauma.by_pattern()),
        nourishment_by_pattern=_top_n_by_pattern(nourishment.by_pattern()),
        recent_events=recent,
        last_pulse=_latest_pulse(log, agent_id),
        capability_level=level,
        crisis=crisis,
        state_context=state_ctx,
        radar_size=radar_size,
        radar_center=center,
        radar_radius=radius,
        radar_rings=rings,
        radar_axes=axes,
        radar_mood=mood_polygon,
        radar_soul=soul_polygon,
    )


def _agent_in_soul_state(store: SoulStore, agent_id: str) -> bool:
    with store.lock:
        row = store.connection.execute(
            "SELECT 1 FROM soul_state WHERE agent_id = ?", (agent_id,),
        ).fetchone()
    return row is not None


__all__ = ["LiveView", "build_live_view", "RadarPoint", "RadarPolygon", "RadarRing"]
