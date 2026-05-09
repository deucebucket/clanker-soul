"""EventLog — durable per-event record so the UI can answer "why is mood here?"

Two record types feed two SQLite tables (created in v0.2 schema):

  - ``IngestRecord``  →  ``events``     (every ``EmotionalPhysics.ingest`` call)
  - ``PulseRecord``   →  ``pulse_log``  (every ``PulseEngine`` evaluation,
                                          fired or suppressed)

The ``EventLog`` Protocol decouples "what to log" from "where it lands."
Production hosts use ``SqliteEventLog`` (writes via the existing
``SoulStore`` connection + lock — no second DB handle). Tests can use
``NullEventLog`` (default) or supply their own list-capturing impl.

**Soft-fail invariant:** a write failure (DB locked, disk full, connection
closed mid-tick) MUST NOT raise into the caller. Physics keeps running;
the failure is logged at WARNING and the next event tries again. Losing
log rows is acceptable. Crashing the agent because of a log hiccup is not.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from clanker_soul.score import Score
from clanker_soul.soul import SoulState, SoulStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Record dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestRecord:
    """Everything needed to reconstruct one ``EmotionalPhysics.ingest`` call.

    All seven fields the UI needs to answer "why did the agent end up
    here": the raw event, the optionally mood-primed event, the mood
    before/after, the soul before/after, the physics math (weight/armor/
    effective), the breach result, the patterns and classification, and a
    pre-baked human-readable ``why`` string."""

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

    ``trigger_kind`` is None when no trigger was matched (steady state)
    or when one was matched but suppressed. ``suppressed_reason`` is one
    of ``"cooldown"``, ``"no_target"``, ``"no_trigger"``, or None when a
    pulse actually dispatched. ``prompt`` is the synthetic self-prompt
    text — None when no pulse was attempted."""

    ts: float
    agent_id: str
    snap: dict
    trigger_kind: str | None
    suppressed_reason: str | None
    target_present: bool
    dispatched: bool
    prompt: str | None


# ---------------------------------------------------------------------------
# Protocol + null impl
# ---------------------------------------------------------------------------


@runtime_checkable
class EventLog(Protocol):
    """Sink interface. Implementations MUST be soft-fail — a write
    failure must not raise into the caller."""

    def log_ingest(self, record: IngestRecord) -> None: ...
    def log_pulse(self, record: PulseRecord) -> None: ...


class NullEventLog:
    """No-op sink. Default for hosts that don't want logging."""

    def log_ingest(self, record: IngestRecord) -> None:  # noqa: ARG002
        return None

    def log_pulse(self, record: PulseRecord) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# Sqlite-backed impl
# ---------------------------------------------------------------------------


def _score_to_json(score: Score) -> str:
    return json.dumps({
        "v": score.v, "a": score.a, "d": score.d, "u": score.u,
        "g": score.g, "w": score.w, "i": score.i,
        "patterns": list(score.patterns),
    })


def _score_from_json(blob: str | None) -> Score | None:
    if blob is None:
        return None
    d = json.loads(blob)
    return Score(
        v=d["v"], a=d["a"], d=d["d"], u=d["u"],
        g=d["g"], w=d["w"], i=d["i"],
        patterns=tuple(d.get("patterns", ())),
    )


def _soul_from_json(blob: str) -> SoulState:
    return SoulState.from_dict(json.loads(blob))


class SqliteEventLog:
    """Durable sink writing to the ``events`` and ``pulse_log`` tables of
    the supplied ``SoulStore``. Reuses the store's connection and write
    lock — no second DB handle, no contention surprises."""

    def __init__(self, store: SoulStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Writes (soft-fail)
    # ------------------------------------------------------------------

    def log_ingest(self, record: IngestRecord) -> None:
        try:
            with self._store.lock:
                self._store.connection.execute(
                    """
                    INSERT INTO events (
                        ts, agent_id, raw_score, primed_score,
                        mood_before, mood_after, soul_before, soul_after,
                        weight_raw, armor, weight_effective,
                        breached, breach_delta,
                        patterns, classification, why
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record.ts, record.agent_id,
                        _score_to_json(record.raw),
                        _score_to_json(record.primed) if record.primed else None,
                        _score_to_json(record.mood_before) if record.mood_before else None,
                        _score_to_json(record.mood_after),
                        json.dumps(record.soul_before.to_dict()),
                        json.dumps(record.soul_after.to_dict()),
                        record.weight_raw, record.armor, record.weight_effective,
                        1 if record.breached else 0, record.breach_delta,
                        json.dumps(list(record.patterns)),
                        record.classification,
                        record.why,
                    ),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("event log ingest write failed (%s) — continuing", e)

    def log_pulse(self, record: PulseRecord) -> None:
        try:
            with self._store.lock:
                self._store.connection.execute(
                    """
                    INSERT INTO pulse_log (
                        ts, agent_id, snap, trigger_kind, suppressed_reason,
                        target_present, dispatched, prompt_text
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        record.ts, record.agent_id,
                        json.dumps(record.snap),
                        record.trigger_kind, record.suppressed_reason,
                        1 if record.target_present else 0,
                        1 if record.dispatched else 0,
                        record.prompt,
                    ),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("event log pulse write failed (%s) — continuing", e)

    # ------------------------------------------------------------------
    # Reads — used by the UI in Phase 2 and by tests now
    # ------------------------------------------------------------------

    def read_ingest(
        self, agent_id: str, *, limit: int | None = None,
    ) -> list[IngestRecord]:
        """Return ingest records for ``agent_id``, most recent first."""
        sql = (
            "SELECT ts, agent_id, raw_score, primed_score, "
            "       mood_before, mood_after, soul_before, soul_after, "
            "       weight_raw, armor, weight_effective, "
            "       breached, breach_delta, patterns, classification, why "
            "FROM events WHERE agent_id = ? "
            "ORDER BY ts DESC, id DESC"
        )
        params: tuple = (agent_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (agent_id, int(limit))
        with self._store.lock:
            rows = self._store.connection.execute(sql, params).fetchall()
        return [
            IngestRecord(
                ts=row[0], agent_id=row[1],
                raw=_score_from_json(row[2]),  # type: ignore[arg-type]
                primed=_score_from_json(row[3]),
                mood_before=_score_from_json(row[4]),
                mood_after=_score_from_json(row[5]),  # type: ignore[arg-type]
                soul_before=_soul_from_json(row[6]),
                soul_after=_soul_from_json(row[7]),
                weight_raw=row[8], armor=row[9], weight_effective=row[10],
                breached=bool(row[11]), breach_delta=row[12],
                patterns=tuple(json.loads(row[13])),
                classification=row[14], why=row[15],
            )
            for row in rows
        ]

    def read_pulse(
        self, agent_id: str, *, limit: int | None = None,
    ) -> list[PulseRecord]:
        """Return pulse records for ``agent_id``, most recent first."""
        sql = (
            "SELECT ts, agent_id, snap, trigger_kind, suppressed_reason, "
            "       target_present, dispatched, prompt_text "
            "FROM pulse_log WHERE agent_id = ? "
            "ORDER BY ts DESC, id DESC"
        )
        params: tuple = (agent_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (agent_id, int(limit))
        with self._store.lock:
            rows = self._store.connection.execute(sql, params).fetchall()
        return [
            PulseRecord(
                ts=row[0], agent_id=row[1],
                snap=json.loads(row[2]),
                trigger_kind=row[3], suppressed_reason=row[4],
                target_present=bool(row[5]), dispatched=bool(row[6]),
                prompt=row[7],
            )
            for row in rows
        ]

    def count_ingest(self, agent_id: str) -> int:
        with self._store.lock:
            row = self._store.connection.execute(
                "SELECT COUNT(*) FROM events WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return int(row[0])

    def count_pulse(self, agent_id: str) -> int:
        with self._store.lock:
            row = self._store.connection.execute(
                "SELECT COUNT(*) FROM pulse_log WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return int(row[0])


__all__ = [
    "IngestRecord",
    "PulseRecord",
    "EventLog",
    "NullEventLog",
    "SqliteEventLog",
]
