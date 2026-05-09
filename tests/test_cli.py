"""CLI: clanker-soul {info, prune, ui}.

Tests call ``main()`` directly with argv lists — faster than spawning
a subprocess and captures output via capsys.
"""
from __future__ import annotations

import time

import pytest

from clanker_soul import Score, SoulPlugin, SoulStore, SqliteEventLog
from clanker_soul.__main__ import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_db(db_path) -> tuple[float, float]:
    """Make a DB with 5 events for 'alice' (older) and 3 for 'bob' (newer).
    Returns (alice_oldest_ts, bob_newest_ts) for prune timing tests."""
    plugin = SoulPlugin(agent_id="alice", db_path=db_path)
    base = time.time() - 86400  # 1 day ago
    for i in range(5):
        plugin.ingest(Score(v=80, w=50 + i, patterns=("ABANDONMENT",)))
    plugin.close()

    # Re-time alice's events into the past by directly editing the DB.
    store = SoulStore.get(db_path)
    with store.lock:
        store.connection.execute(
            "UPDATE events SET ts = ? WHERE agent_id = 'alice'",
            (base,),
        )
        store.connection.commit()

    plugin2 = SoulPlugin(agent_id="bob", db_path=db_path)
    for _ in range(3):
        plugin2.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
    plugin2.close()

    return base, time.time()


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def test_info_reports_table_counts(tmp_path, capsys) -> None:
    db = tmp_path / "info.db"
    _populate_db(db)

    rc = main(["info", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out and "bob" in out
    assert "events" in out.lower()
    # Should report counts numerically.
    assert "5" in out and "3" in out


def test_info_handles_missing_db(tmp_path, capsys) -> None:
    rc = main(["info", "--db", str(tmp_path / "nope.db")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not found" in err.lower() or "no such" in err.lower()


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_refuses_without_yes_flag(tmp_path, capsys) -> None:
    db = tmp_path / "p.db"
    _populate_db(db)
    rc = main(["prune", "--db", str(db), "--before", "2099-01-01"])
    # Refused → non-zero exit, no rows deleted.
    assert rc != 0
    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("alice") == 5
    assert log.count_ingest("bob") == 3


def test_prune_with_yes_deletes_old_rows(tmp_path, capsys) -> None:
    db = tmp_path / "p.db"
    _populate_db(db)

    # Use a cutoff that's after alice's events but before bob's.
    rc = main(["prune", "--db", str(db), "--before", "2099-01-01", "-y"])
    assert rc == 0

    log = SqliteEventLog(SoulStore.get(db))
    # Both alice (older) and bob (newer-but-still-before-2099) should be gone.
    assert log.count_ingest("alice") == 0
    assert log.count_ingest("bob") == 0


def test_prune_scoped_by_agent_id(tmp_path) -> None:
    db = tmp_path / "p.db"
    _populate_db(db)
    rc = main([
        "prune", "--db", str(db),
        "--before", "2099-01-01",
        "--agent-id", "alice", "-y",
    ])
    assert rc == 0

    log = SqliteEventLog(SoulStore.get(db))
    assert log.count_ingest("alice") == 0
    assert log.count_ingest("bob") == 3  # untouched


def test_prune_rejects_invalid_date(tmp_path) -> None:
    db = tmp_path / "p.db"
    _populate_db(db)
    rc = main(["prune", "--db", str(db), "--before", "not-a-date", "-y"])
    assert rc != 0


# ---------------------------------------------------------------------------
# ui (Phase 2 stub)
# ---------------------------------------------------------------------------


def test_ui_emits_install_hint_until_phase_2(tmp_path, capsys) -> None:
    db = tmp_path / "ui.db"
    SoulStore(db)  # create empty DB
    rc = main(["ui", "--db", str(db)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "install" in err.lower()
    assert "[ui]" in err


# ---------------------------------------------------------------------------
# top-level help
# ---------------------------------------------------------------------------


def test_help_lists_three_subcommands(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "info" in out
    assert "prune" in out
    assert "ui" in out
