"""Schema migration: events, config_overrides, pulse_log tables.

Verifies that ``SoulStore`` creates the v0.2 tables idempotently and that
opening a v0.1-shaped DB (containing only ``soul_state``) upgrades cleanly
without losing existing rows.
"""

from __future__ import annotations

import json
import sqlite3
import time

from clanker_soul import (
    NourishmentReservoir,
    SoulState,
    SoulStore,
    TraumaReservoir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_names(db_path) -> set[str]:
    """User-defined tables only — filters sqlite-internal bookkeeping
    tables like ``sqlite_sequence`` (auto-created when AUTOINCREMENT is
    used)."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _columns(db_path, table: str) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    # PRAGMA returns (cid, name, type, notnull, dflt_value, pk)
    return {r[1]: r[2].upper() for r in rows}


def _indexes(db_path, table: str) -> list[tuple[str, list[str]]]:
    """Return [(index_name, [col1, col2, ...]), ...] for the given table."""
    conn = sqlite3.connect(str(db_path))
    try:
        idx_rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
        out = []
        for idx in idx_rows:
            idx_name = idx[1]
            cols = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
            # PRAGMA index_info returns (seqno, cid, name)
            out.append((idx_name, [c[2] for c in cols]))
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Fresh-DB creation
# ---------------------------------------------------------------------------


def test_fresh_db_has_all_v02_tables(tmp_path) -> None:
    db = tmp_path / "fresh.db"
    SoulStore(db)
    assert _table_names(db) == {
        "soul_state",
        "events",
        "config_overrides",
        "pulse_log",
        "prompt_corpus",
        "face_recency",
    }


def test_events_table_has_documented_columns(tmp_path) -> None:
    db = tmp_path / "events.db"
    SoulStore(db)
    cols = _columns(db, "events")
    expected = {
        "id",
        "ts",
        "agent_id",
        "raw_score",
        "primed_score",
        "mood_before",
        "mood_after",
        "soul_before",
        "soul_after",
        "weight_raw",
        "armor",
        "weight_effective",
        "breached",
        "breach_delta",
        "patterns",
        "classification",
        "why",
    }
    assert expected <= set(cols), f"missing columns: {expected - set(cols)}"


def test_config_overrides_table_has_documented_columns(tmp_path) -> None:
    db = tmp_path / "co.db"
    SoulStore(db)
    cols = _columns(db, "config_overrides")
    expected = {
        "agent_id",
        "physics_config_overrides",
        "soul_overrides",
        "last_modified",
    }
    assert expected <= set(cols)


def test_pulse_log_table_has_documented_columns(tmp_path) -> None:
    db = tmp_path / "pl.db"
    SoulStore(db)
    cols = _columns(db, "pulse_log")
    expected = {
        "id",
        "ts",
        "agent_id",
        "snap",
        "trigger_kind",
        "suppressed_reason",
        "target_present",
        "dispatched",
        "prompt_text",
        # M3.3 — face attribution.
        "face_id",
    }
    assert expected <= set(cols)


def test_prompt_corpus_table_has_documented_columns(tmp_path) -> None:
    db = tmp_path / "pc.db"
    SoulStore(db)
    cols = _columns(db, "prompt_corpus")
    expected = {
        "id",
        "trigger_kinds",
        "vadugwi_predicates",
        "situation_tags",
        "situation_match",
        "memory_anchor",
        "cooldown_seconds",
        "base_weight",
        "motif",
        "template",
        "branch_keys",
        "source",
        "created_at",
        "retired_at",
    }
    assert expected <= set(cols)


def test_face_recency_table_has_documented_columns(tmp_path) -> None:
    db = tmp_path / "fr.db"
    SoulStore(db)
    cols = _columns(db, "face_recency")
    expected = {"agent_id", "face_id", "last_fired_at", "fire_count"}
    assert expected <= set(cols)


def test_v02_db_gains_face_id_column_on_open(tmp_path) -> None:
    """A v0.2 pulse_log without face_id must gain the column when opened
    by the M3.3 SoulStore — agents that ran before M3.3 keep working."""
    db = tmp_path / "legacy_v02.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE pulse_log (
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
        conn.execute(
            "INSERT INTO pulse_log "
            "(ts, agent_id, snap, target_present, dispatched) "
            "VALUES (1.0, 'pre-m33', '{}', 0, 0)",
        )
        conn.commit()
    finally:
        conn.close()

    SoulStore(db)
    cols = _columns(db, "pulse_log")
    assert "face_id" in cols, f"face_id missing after upgrade: {cols}"
    # Pre-existing row survives upgrade.
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM pulse_log").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_events_has_agent_id_ts_index(tmp_path) -> None:
    db = tmp_path / "idx.db"
    SoulStore(db)
    idxs = _indexes(db, "events")
    composite = [cols for _, cols in idxs if cols[:2] == ["agent_id", "ts"]]
    assert composite, f"no (agent_id, ts) composite index on events; got {idxs}"


def test_pulse_log_has_agent_id_ts_index(tmp_path) -> None:
    db = tmp_path / "idx2.db"
    SoulStore(db)
    idxs = _indexes(db, "pulse_log")
    composite = [cols for _, cols in idxs if cols[:2] == ["agent_id", "ts"]]
    assert composite, f"no (agent_id, ts) composite index on pulse_log; got {idxs}"


# ---------------------------------------------------------------------------
# Idempotent re-open
# ---------------------------------------------------------------------------


def test_reopening_db_is_idempotent(tmp_path) -> None:
    db = tmp_path / "reopen.db"
    SoulStore(db)
    SoulStore(db)  # second open must not raise
    SoulStore(db)
    assert _table_names(db) == {
        "soul_state",
        "events",
        "config_overrides",
        "pulse_log",
        "prompt_corpus",
        "face_recency",
    }


# ---------------------------------------------------------------------------
# v0.1 -> v0.2 upgrade preserves data
# ---------------------------------------------------------------------------


def test_v01_db_upgrades_without_losing_soul_state(tmp_path) -> None:
    """Simulate a v0.1 database (only soul_state table) and verify that
    opening it with the new SoulStore creates the missing tables AND
    leaves the original soul_state row intact."""
    db = tmp_path / "legacy.db"
    # Hand-build the v0.1 schema (only soul_state) and insert one row.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE soul_state (
                agent_id TEXT PRIMARY KEY,
                soul_json TEXT NOT NULL,
                trauma_json TEXT NOT NULL,
                nourishment_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        legacy_soul = SoulState(v=160, w=180)
        conn.execute(
            "INSERT INTO soul_state VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-agent",
                json.dumps(legacy_soul.to_dict()),
                json.dumps({}),
                json.dumps({}),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Now open with the new SoulStore — must upgrade in place.
    store = SoulStore(db)
    soul, trauma, nourishment = store.load("legacy-agent")
    assert soul.v == 160 and soul.w == 180
    assert isinstance(trauma, TraumaReservoir)
    assert isinstance(nourishment, NourishmentReservoir)

    # And all v0.2 tables now exist.
    assert _table_names(db) == {
        "soul_state",
        "events",
        "config_overrides",
        "pulse_log",
        "prompt_corpus",
        "face_recency",
    }


# ---------------------------------------------------------------------------
# Sanity: existing v0.1 save/load behavior is unaffected
# ---------------------------------------------------------------------------


def test_save_load_round_trip_still_works(tmp_path) -> None:
    db = tmp_path / "rt.db"
    store = SoulStore(db)
    soul = SoulState(v=160, w=180)
    trauma = TraumaReservoir()
    trauma.add("ABANDONMENT", 25.0, now_ts=time.time())
    nourishment = NourishmentReservoir()
    nourishment.add("WARMTH", 10.0, now_ts=time.time())
    store.save("agent-1", soul, trauma, nourishment)

    # Reopen via a brand new SoulStore instance.
    store2 = SoulStore(db)
    s, t, n = store2.load("agent-1")
    assert s.v == 160 and s.w == 180
    assert t.load() > 0
    assert n.load() > 0
