"""``SoulPlugin`` end-to-end wiring for mistakes + corrections. M4 #97.

Covers:
- ``plugin.mistake_pressure()`` reflects ingested TOOL_BAD_CALL Scores
- mistakes persist across construct/save/reconstruct
- the v0.x → v0.x+1 schema migration adds ``mistakes_json`` in place
- end-to-end recovery loops (pride and relief shapes) close correctly
- ``plugin.tick()`` exposes ``mistakes_load`` / ``correction_load``
  in the drift report
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from clanker_soul import (
    Score,
    SoulPlugin,
    score_from_action_failure,
    score_from_correction,
)


def _bad_call(intensity: float = 1.0) -> Score:
    return Score(
        v=int(120 - 20 * intensity),
        a=int(125 + 10 * intensity),
        d=int(110 - 10 * intensity),
        u=55,
        g=120,
        w=int(120 - 15 * intensity),
        i=110,
        patterns=("TOOL_BAD_CALL",),
    )


def test_mistake_pressure_starts_at_zero(tmp_path: Path) -> None:
    with SoulPlugin("agent-a", tmp_path / "soul.db") as plugin:
        assert plugin.mistake_pressure() == 0.0


def test_mistake_pressure_grows_after_ingesting_bad_calls(tmp_path: Path) -> None:
    with SoulPlugin("agent-a", tmp_path / "soul.db") as plugin:
        plugin.ingest(_bad_call())
        plugin.ingest(_bad_call(intensity=1.5))
        assert plugin.mistake_pressure() > 0.0


def test_mistake_pressure_persists_across_reconstruct(tmp_path: Path) -> None:
    db = tmp_path / "soul.db"
    with SoulPlugin("agent-a", db) as p1:
        for _ in range(3):
            p1.ingest(_bad_call(intensity=1.5))
        pressure_first = p1.mistake_pressure()
        assert pressure_first > 0.0
    # Process-singleton SoulStore is keyed by path — clear it so the
    # second construction reads a fresh handle (mimics a real restart).
    from clanker_soul.soul.store import SoulStore as _SS

    with _SS._instances_lock:
        _SS._instances.clear()
    with SoulPlugin("agent-a", db) as p2:
        # Decay over the test's wall-clock is negligible for a 14-day
        # half-life; should round-trip almost exactly.
        assert p2.mistake_pressure() == pytest.approx(pressure_first, rel=0.01)


def test_v0x_db_without_mistakes_column_migrates_in_place(tmp_path: Path) -> None:
    """Idempotent column migration: open a hand-built v0.x DB without
    the mistakes_json column. SoulPlugin construction must add it and
    return mistake_pressure() == 0.0."""
    db = tmp_path / "legacy.db"
    # Hand-build a minimal v0.x soul_state row.
    conn = sqlite3.connect(str(db))
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
    conn.execute("INSERT INTO soul_state VALUES ('legacy-agent', '{}', '{}', '{}', '2026-01-01')")
    conn.commit()
    conn.close()

    # Clear singleton so SoulPlugin construction sees a fresh handle.
    from clanker_soul.soul.store import SoulStore as _SS

    with _SS._instances_lock:
        _SS._instances.clear()

    with SoulPlugin("legacy-agent", db) as plugin:
        cols = {
            row[1]
            for row in plugin.store.connection.execute("PRAGMA table_info(soul_state)").fetchall()
        }
        assert "mistakes_json" in cols
        assert plugin.mistake_pressure() == 0.0


def test_recovery_loop_pride_shape_lifts_mood_and_w(tmp_path: Path) -> None:
    """Failure → many bad calls → mistake_pressure rises. Then a
    pride-shaped correction relieves the reservoir AND lifts mood W/V."""
    with SoulPlugin("agent-pride", tmp_path / "soul.db") as plugin:
        for _ in range(5):
            plugin.ingest(_bad_call(intensity=1.5))
        load_before = plugin.mistake_pressure()
        assert load_before > 0.0
        mood_before = plugin.physics.mood
        assert mood_before is not None
        v_before, w_before = mood_before.v, mood_before.w

        plugin.ingest(
            score_from_correction(
                tool="git",
                after_mistakes=load_before,
                kind="tool_fix",
            )
        )
        assert plugin.mistake_pressure() < load_before
        mood_after = plugin.physics.mood
        assert mood_after is not None
        assert mood_after.v >= v_before
        assert mood_after.w >= w_before


def test_recovery_loop_relief_shape_relieves_without_pride(tmp_path: Path) -> None:
    """Relief still lifts the reservoir but does NOT integrate as
    competence — mood W stays low rather than rising."""
    with SoulPlugin("agent-relief", tmp_path / "soul.db") as plugin:
        for _ in range(5):
            plugin.ingest(_bad_call(intensity=1.5))
        load_before = plugin.mistake_pressure()
        assert load_before > 0.0

        plugin.ingest(
            score_from_correction(
                tool="git",
                after_mistakes=load_before,
                kind="relief_exhaustion",
            )
        )
        # Reservoir relieved (correction pattern in CORRECTION_PATTERNS).
        assert plugin.mistake_pressure() < load_before
        # Mood W stayed low — the relief Score itself has W=80, which
        # pulls mood W down from where bad calls had left it (~100s).
        # The exact value depends on blend math; we just assert it's
        # not now in the high "I won" range.
        mood_after = plugin.physics.mood
        assert mood_after is not None
        assert mood_after.w < 150


def test_tick_report_carries_mistakes_and_correction_loads(tmp_path: Path) -> None:
    with SoulPlugin("agent-tick", tmp_path / "soul.db") as plugin:
        # Pile up well above mistake_pressure_floor.
        for _ in range(8):
            plugin.ingest(_bad_call(intensity=1.5))
        # Roll the soul_drift clock backwards so tick() does real work
        # (rather than skipping for "too soon").
        plugin.physics.soul.last_drift_ts = time.time() - 24 * 3600
        report = plugin.tick()
        assert "mistakes_load" in report
        assert "correction_load" in report
        assert "resilience_uplift" in report
        assert report["mistakes_load"] > 0.0


def test_snapshot_carries_mistake_pressure(tmp_path: Path) -> None:
    with SoulPlugin("agent-snap", tmp_path / "soul.db") as plugin:
        snap = plugin.snapshot()
        assert "mistake_pressure" in snap
        assert snap["mistake_pressure"] == 0.0
        plugin.ingest(_bad_call(intensity=1.5))
        snap2 = plugin.snapshot()
        assert snap2["mistake_pressure"] > 0.0


def test_score_from_action_failure_integrates_with_plugin(tmp_path: Path) -> None:
    """The end-to-end story: helper builds the Score, plugin ingests
    it, mistake_pressure reflects validation errors only."""
    with SoulPlugin("agent-helper", tmp_path / "soul.db") as plugin:
        # Non-validation failures don't move mistake_pressure.
        plugin.ingest(score_from_action_failure("timeout", tool="git"))
        plugin.ingest(score_from_action_failure("rate_limit", tool="git"))
        assert plugin.mistake_pressure() == 0.0
        # Validation errors DO.
        plugin.ingest(score_from_action_failure("validation_error", tool="git"))
        assert plugin.mistake_pressure() > 0.0
