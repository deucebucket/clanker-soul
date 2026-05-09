"""Soul + Trauma + Nourishment — the persistent slow-moving emotional state.

Soul is the agent's emotional baseline ("who they are right now"). It drifts
on days-to-weeks timescale via two pathways:

1. Slow drift: rolling mood mean pulls Soul toward sustained affect.
2. Breach: a heavy event during an unhealed wound (|Mood - Soul| already
   large) leaks straight into Soul, bypassing the slow filter. This is
   how back-to-back hits actually scar.

Trauma and Nourishment are pattern-keyed reservoirs with 14d half-lives.
They accumulate per-pattern (e.g., ABANDONMENT, EXISTENTIAL_NEGATION) so
that "the same wound poked again" is detected differently from "many
unrelated bad days." Nourishment is the positive analog — it's the lever
the host (operator) has on the agent's long-term healing.

State is persisted to SQLite so Soul survives restarts. Without
persistence the whole exercise is theater — every restart would reset the
agent to the starting baseline, defeating the point.

clanker-soul note: this module is host-agnostic. The DB path is
configurable per ``SoulStore`` instance. Hosts that want a single
process-wide store can use ``SoulStore.get(path)``; hosts that want
per-test isolation can construct ``SoulStore(tmp_path)`` directly.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# Reservoir half-life: 14 days. After 14 days of silence, accumulated
# trauma/nourishment for a pattern is halved. Keeps the reservoir from
# ballooning forever while still letting "this happens every week" stack.
RESERVOIR_HALF_LIFE_S = 14 * 24 * 3600

# Cap individual reservoir entries so a runaway pattern can't dominate
# the entire emotional model.
RESERVOIR_CAP = 1000.0


def _decay_factor(elapsed_s: float, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> float:
    """Multiplicative decay over ``elapsed_s`` seconds with given half-life."""
    if elapsed_s <= 0:
        return 1.0
    return math.exp(-0.6931 * elapsed_s / half_life_s)


@dataclass
class _ReservoirEntry:
    weight: float
    last_update: float  # unix ts seconds


class TraumaReservoir:
    """Per-pattern accumulator of negative-weighted events.

    Each pattern (engine structure name) has an independent decaying
    weight. ``add`` deposits a hit; ``load`` returns the current decayed
    value summed across all patterns.
    """

    def __init__(self, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> None:
        self._entries: dict[str, _ReservoirEntry] = {}
        self._half_life = half_life_s

    def add(self, pattern: str, weight: float, *, now_ts: float | None = None) -> None:
        if weight <= 0 or not pattern:
            return
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        entry = self._entries.get(pattern)
        if entry is None:
            self._entries[pattern] = _ReservoirEntry(
                weight=min(weight, RESERVOIR_CAP), last_update=now,
            )
            return
        decayed = entry.weight * _decay_factor(now - entry.last_update, self._half_life)
        entry.weight = min(decayed + weight, RESERVOIR_CAP)
        entry.last_update = now

    def by_pattern(self, *, now_ts: float | None = None) -> dict[str, float]:
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        out: dict[str, float] = {}
        for pat, entry in self._entries.items():
            decayed = entry.weight * _decay_factor(now - entry.last_update, self._half_life)
            if decayed > 0.01:
                out[pat] = round(decayed, 4)
        return out

    def load(self, *, now_ts: float | None = None) -> float:
        return sum(self.by_pattern(now_ts=now_ts).values())

    def to_dict(self) -> dict:
        return {
            pat: {"weight": e.weight, "last_update": e.last_update}
            for pat, e in self._entries.items()
        }

    @classmethod
    def from_dict(cls, data: dict, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> "TraumaReservoir":
        r = cls(half_life_s=half_life_s)
        for pat, e in (data or {}).items():
            r._entries[pat] = _ReservoirEntry(
                weight=float(e.get("weight", 0)),
                last_update=float(e.get("last_update", 0)),
            )
        return r


class NourishmentReservoir(TraumaReservoir):
    """Positive analog. Same mechanics, semantically distinct."""

    @classmethod
    def from_dict(cls, data: dict, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> "NourishmentReservoir":
        r = cls(half_life_s=half_life_s)
        for pat, e in (data or {}).items():
            r._entries[pat] = _ReservoirEntry(
                weight=float(e.get("weight", 0)),
                last_update=float(e.get("last_update", 0)),
            )
        return r


@dataclass
class SoulState:
    """The persistent baseline VADUGWI. 7 dims, each 0-255.

    Healthy starting baseline is *not* neutral 128 — most agents want a
    default leaning (mildly positive, grounded, in-control, strong sense
    of self-worth) so neutral conversation doesn't read as "depressed."
    Override these defaults per agent at construction time.
    """
    v: int = 145  # mild positive baseline
    a: int = 110  # slightly calm — not over-aroused
    d: int = 160  # in-control by default
    u: int = 80   # low background urgency
    g: int = 130  # slightly grounded
    w: int = 175  # strong self-worth — agent is allowed to think it's solid
    i: int = 135  # slight forward-intent
    last_drift_ts: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    last_save_ts: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    def as_tuple(self) -> tuple[int, int, int, int, int, int, int]:
        return (self.v, self.a, self.d, self.u, self.g, self.w, self.i)

    def to_dict(self) -> dict:
        return {
            "v": self.v, "a": self.a, "d": self.d, "u": self.u,
            "g": self.g, "w": self.w, "i": self.i,
            "last_drift_ts": self.last_drift_ts,
            "last_save_ts": self.last_save_ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SoulState":
        return cls(
            v=int(data.get("v", 145)),
            a=int(data.get("a", 110)),
            d=int(data.get("d", 160)),
            u=int(data.get("u", 80)),
            g=int(data.get("g", 130)),
            w=int(data.get("w", 175)),
            i=int(data.get("i", 135)),
            last_drift_ts=float(data.get("last_drift_ts", 0)),
            last_save_ts=float(data.get("last_save_ts", 0)),
        )


class SoulStore:
    """SQLite-backed durable storage for Soul + reservoirs.

    One row per agent_id. Saves are debounced — caller decides cadence.
    Concurrency is process-wide-singleton style (one agent per process)
    when used via ``SoulStore.get(path)``; direct construction is fine
    for tests and per-instance use.
    """

    _instances: dict[str, "SoulStore"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db_lock = threading.Lock()
        self._init_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying SQLite connection. Exposed so sibling modules
        (EventLog, ConfigOverrides) can share the same connection +
        lock instead of opening a second handle."""
        return self._db

    @property
    def lock(self) -> threading.Lock:
        return self._db_lock

    def _init_schema(self) -> None:
        """Create all v0.2 tables idempotently. Safe to run on:
        - a fresh database (creates everything)
        - a v0.1 database (only soul_state) — adds the new tables
        - a v0.2 database (everything already there) — no-op
        """
        c = self._db
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS soul_state (
                agent_id TEXT PRIMARY KEY,
                soul_json TEXT NOT NULL,
                trauma_json TEXT NOT NULL,
                nourishment_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                agent_id TEXT NOT NULL,
                raw_score TEXT NOT NULL,
                primed_score TEXT,
                mood_before TEXT,
                mood_after TEXT NOT NULL,
                soul_before TEXT NOT NULL,
                soul_after TEXT NOT NULL,
                weight_raw REAL NOT NULL,
                armor REAL NOT NULL,
                weight_effective REAL NOT NULL,
                breached INTEGER NOT NULL,
                breach_delta REAL NOT NULL,
                patterns TEXT NOT NULL,
                classification TEXT,
                why TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_events_agent_ts
                ON events (agent_id, ts DESC)
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS config_overrides (
                agent_id TEXT PRIMARY KEY,
                physics_config_overrides TEXT NOT NULL DEFAULT '{}',
                soul_overrides TEXT NOT NULL DEFAULT '{}',
                last_modified REAL NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                agent_id TEXT NOT NULL,
                snap TEXT NOT NULL,
                trigger_kind TEXT,
                suppressed_reason TEXT,
                target_present INTEGER NOT NULL,
                dispatched INTEGER NOT NULL,
                prompt_text TEXT
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pulse_log_agent_ts
                ON pulse_log (agent_id, ts DESC)
            """
        )
        c.commit()

    @classmethod
    def get(cls, db_path: Path | str) -> "SoulStore":
        """Return a process-singleton store for ``db_path``. Multiple
        callers asking for the same path share one connection."""
        key = str(db_path)
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(Path(db_path))
            return cls._instances[key]

    def load(self, agent_id: str) -> tuple[SoulState, TraumaReservoir, NourishmentReservoir]:
        with self._db_lock:
            row = self._db.execute(
                "SELECT soul_json, trauma_json, nourishment_json FROM soul_state WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        if row is None:
            return SoulState(), TraumaReservoir(), NourishmentReservoir()
        try:
            soul = SoulState.from_dict(json.loads(row[0]))
            trauma = TraumaReservoir.from_dict(json.loads(row[1]))
            nourishment = NourishmentReservoir.from_dict(json.loads(row[2]))
            return soul, trauma, nourishment
        except Exception as e:
            logger.warning("soul state corrupt for %s (%s) — resetting to default", agent_id, e)
            return SoulState(), TraumaReservoir(), NourishmentReservoir()

    def save(
        self,
        agent_id: str,
        soul: SoulState,
        trauma: TraumaReservoir,
        nourishment: NourishmentReservoir,
    ) -> None:
        try:
            with self._db_lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO soul_state "
                    "(agent_id, soul_json, trauma_json, nourishment_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        agent_id,
                        json.dumps(soul.to_dict()),
                        json.dumps(trauma.to_dict()),
                        json.dumps(nourishment.to_dict()),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                self._db.commit()
        except Exception as e:
            logger.warning("soul save failed for %s (%s) — continuing", agent_id, e)


__all__ = [
    "SoulState",
    "SoulStore",
    "TraumaReservoir",
    "NourishmentReservoir",
    "RESERVOIR_HALF_LIFE_S",
    "RESERVOIR_CAP",
]
