"""SoulStore — SQLite-backed durable storage for Soul + reservoirs +
event log + config overrides.

One file per studio (or per agent — the choice is the host's).
``SoulStore.get(path)`` returns a process-wide singleton per path so
multiple plugins/readers/UIs share one connection. ``SoulStore(path)``
is fine for tests and per-instance use.

Schema is created idempotently — opening a fresh DB creates everything;
opening a v0.1 DB (only the ``soul_state`` table) adds the v0.2 tables
in place without touching existing rows.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from clanker_soul.soul.reservoirs import (
    NourishmentReservoir,
    TraumaReservoir,
)
from clanker_soul.soul.state import SoulState

logger = logging.getLogger(__name__)


class SoulStore:
    """SQLite-backed durable storage. One row per agent_id in
    ``soul_state``; per-event rows in ``events``; per-evaluation rows
    in ``pulse_log``; per-agent overrides in ``config_overrides``.

    Sibling modules (:py:class:`SqliteEventLog`, :py:class:`ConfigOverrides`)
    reuse the connection and lock via the public :py:attr:`connection`
    and :py:attr:`lock` properties — no second handle, no contention
    surprises."""

    _instances: dict[str, "SoulStore"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, db_path: Path | str) -> None:
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
        # M3.3 — pulse_log gets a face_id column. Add it idempotently
        # via PRAGMA table_info so v0.2 databases upgrade in place.
        existing_pulse_cols = {
            row[1] for row in c.execute("PRAGMA table_info(pulse_log)").fetchall()
        }
        if "face_id" not in existing_pulse_cols:
            c.execute("ALTER TABLE pulse_log ADD COLUMN face_id TEXT")
        # M3.3 — corpus persistence + per-agent face recency.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_corpus (
                id TEXT PRIMARY KEY,
                trigger_kinds TEXT NOT NULL,
                vadugwi_predicates TEXT NOT NULL,
                situation_tags TEXT NOT NULL,
                situation_match TEXT NOT NULL,
                memory_anchor TEXT,
                cooldown_seconds INTEGER NOT NULL,
                base_weight REAL NOT NULL,
                motif TEXT NOT NULL,
                template TEXT NOT NULL,
                branch_keys TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at REAL NOT NULL,
                retired_at REAL
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prompt_corpus_active
                ON prompt_corpus (retired_at)
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS face_recency (
                agent_id TEXT NOT NULL,
                face_id TEXT NOT NULL,
                last_fired_at REAL NOT NULL,
                fire_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (agent_id, face_id)
            )
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


__all__ = ["SoulStore"]
