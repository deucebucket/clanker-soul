"""Replay-with-hypothetical-config simulator.

The "what if I had tuned this differently?" tool. Reads the last N
``IngestRecord`` rows for an agent, replays the *raw* scores through a
fresh in-memory :py:class:`EmotionalPhysics` built from the operator's
hypothetical ``SoulState`` + ``PhysicsConfig``, and returns a
side-by-side trajectory: real history vs simulated history.

Determinism + safety:

- The replay engine is constructed *without* an ``event_log`` and
  *without* an ``overrides`` provider. It cannot write to SQLite. It
  cannot read overrides. It is a pure function of (records, soul, config).
- Decay timing: the engine couples mood-decay to ``time.perf_counter``,
  which would collapse to ~0 elapsed across a fast back-to-back replay
  and erase decay entirely. We fix this by backdating ``_mood_time``
  between steps using the real recorded timestamp gap, so decay sees
  the wall-clock delta the agent actually experienced.
- Soul drift is replayed deterministically via ``soul_drift(now_ts=)``,
  which already accepts an injected clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from clanker_soul.eventlog.records import IngestRecord
from clanker_soul.physics import EmotionalPhysics, PhysicsConfig
from clanker_soul.score import Score
from clanker_soul.soul import SoulState


@dataclass(frozen=True)
class SimStep:
    """One paired row in the side-by-side trajectory.

    ``mood_real`` / ``soul_real`` come from the recorded history.
    ``mood_sim`` / ``soul_sim`` come from the hypothetical replay."""

    ts: float
    patterns: tuple[str, ...]
    mood_real: Score
    soul_real: SoulState
    mood_sim: Score
    soul_sim: SoulState


@dataclass(frozen=True)
class DimDeviation:
    """How far a single dim ended up vs the real run, end-of-replay."""

    name: str
    real_end: int
    sim_end: int
    delta: int  # sim - real


@dataclass(frozen=True)
class SimResult:
    """Output of :func:`replay_events`."""

    agent_id: str
    n_events: int
    soul_start: SoulState
    config: PhysicsConfig
    steps: tuple[SimStep, ...]
    soul_real_end: SoulState
    soul_sim_end: SoulState
    mood_deviations: tuple[DimDeviation, ...]
    soul_deviations: tuple[DimDeviation, ...]
    elapsed_ms: float


_DIMS: tuple[str, ...] = ("v", "a", "d", "u", "g", "w", "i")


def replay_events(
    records: list[IngestRecord],
    soul: SoulState,
    config: PhysicsConfig,
    *,
    agent_id: str = "_simulator",
) -> SimResult:
    """Replay ``records`` (oldest-first) through a fresh engine and pair
    each step with the recorded reality.

    The engine is sandboxed — no event_log, no overrides. Mutations stay
    in-memory and are discarded when this function returns."""
    t0 = time.perf_counter()

    if not records:
        return SimResult(
            agent_id=agent_id,
            n_events=0,
            soul_start=replace(soul),
            config=replace(config),
            steps=(),
            soul_real_end=replace(soul),
            soul_sim_end=replace(soul),
            mood_deviations=tuple(
                DimDeviation(d, getattr(soul, d), getattr(soul, d), 0) for d in _DIMS
            ),
            soul_deviations=tuple(
                DimDeviation(d, getattr(soul, d), getattr(soul, d), 0) for d in _DIMS
            ),
            elapsed_ms=0.0,
        )

    # Normalize soul.last_drift_ts to the first record's ts so two replays
    # of the same input produce byte-identical output regardless of when
    # they run. SoulState() default-factories last_drift_ts=now(), which
    # would otherwise leak the wall clock into our output.
    sim_soul = replace(soul, last_drift_ts=records[0].ts, last_save_ts=records[0].ts)
    physics = EmotionalPhysics(soul=sim_soul, config=replace(config))
    steps: list[SimStep] = []

    for i, rec in enumerate(records):
        physics.ingest(rec.raw)
        sim_mood = physics.mood
        # mood is None on the first call only if ingest wasn't called yet —
        # we just called it, so it's set. Keep static checkers happy:
        assert sim_mood is not None
        sim_soul = replace(physics.soul)

        steps.append(
            SimStep(
                ts=rec.ts,
                patterns=rec.patterns,
                mood_real=rec.mood_after,
                soul_real=rec.soul_after,
                mood_sim=sim_mood,
                soul_sim=sim_soul,
            )
        )

        if i + 1 < len(records):
            gap = records[i + 1].ts - rec.ts
            if gap > 0:
                # Backdate mood-time so the next decay sees the real gap.
                physics._mood_time = time.perf_counter() - gap
                # Drift Soul forward to next ts using injected clock.
                physics.soul_drift(now_ts=records[i + 1].ts)

    soul_real_end = records[-1].soul_after
    soul_sim_end = replace(physics.soul)

    last_real_mood = records[-1].mood_after
    last_sim_mood = steps[-1].mood_sim

    mood_devs = tuple(
        DimDeviation(
            name=d,
            real_end=getattr(last_real_mood, d),
            sim_end=getattr(last_sim_mood, d),
            delta=getattr(last_sim_mood, d) - getattr(last_real_mood, d),
        )
        for d in _DIMS
    )
    soul_devs = tuple(
        DimDeviation(
            name=d,
            real_end=getattr(soul_real_end, d),
            sim_end=getattr(soul_sim_end, d),
            delta=getattr(soul_sim_end, d) - getattr(soul_real_end, d),
        )
        for d in _DIMS
    )

    return SimResult(
        agent_id=agent_id,
        n_events=len(records),
        soul_start=replace(soul),
        config=replace(config),
        steps=tuple(steps),
        soul_real_end=soul_real_end,
        soul_sim_end=soul_sim_end,
        mood_deviations=mood_devs,
        soul_deviations=soul_devs,
        elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
    )


# ---------------------------------------------------------------------------
# Form parsing — keep the route handlers simple
# ---------------------------------------------------------------------------


def parse_soul(form: dict[str, str]) -> SoulState:
    """Read the simulator's soul form fields. Missing fields default to
    a neutral SoulState. Raises ValueError on bad numerics."""
    base = SoulState()
    kwargs: dict[str, int] = {}
    for d in _DIMS:
        raw = form.get(f"soul_{d}")
        if raw is None or raw == "":
            kwargs[d] = getattr(base, d)
            continue
        v = int(float(raw))
        if not (0 <= v <= 255):
            raise ValueError(f"soul.{d}={v} is out of range [0, 255]")
        kwargs[d] = v
    return SoulState(**kwargs)


def parse_config(form: dict[str, str]) -> PhysicsConfig:
    """Read the simulator's physics form fields. Missing fields default
    to the constructor defaults. Raises ValueError on bad numerics or
    out-of-range values handled at the form layer."""
    from clanker_soul.ui.config import PHYSICS_FIELDS

    base = PhysicsConfig()
    kwargs: dict[str, float] = {}
    for meta in PHYSICS_FIELDS:
        raw = form.get(f"physics_{meta.name}")
        if raw is None or raw == "":
            kwargs[meta.name] = float(getattr(base, meta.name))
            continue
        v = float(raw)
        if not (meta.min <= v <= meta.max):
            raise ValueError(f"physics.{meta.name}={v} is out of range [{meta.min}, {meta.max}]")
        kwargs[meta.name] = v
    return PhysicsConfig(**kwargs)


__all__ = [
    "SimStep",
    "SimResult",
    "DimDeviation",
    "replay_events",
    "parse_soul",
    "parse_config",
]
