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
    rc = main(
        [
            "prune",
            "--db",
            str(db),
            "--before",
            "2099-01-01",
            "--agent-id",
            "alice",
            "-y",
        ]
    )
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


def test_ui_emits_install_hint_when_extra_not_installed(tmp_path, capsys) -> None:
    """If the [ui] extra isn't installed, ``clanker-soul ui`` prints
    the install hint and exits non-zero. When the extra IS installed,
    this test skips — the post-install behavior is covered by
    ``tests/ui/test_scaffold.py::test_cli_ui_subcommand_no_longer_emits_install_hint``."""
    try:
        import fastapi  # noqa: F401

        pytest.skip("[ui] extra is installed; behavior tested in tests/ui/")
    except ImportError:
        pass

    db = tmp_path / "ui.db"
    SoulStore(db)
    rc = main(["ui", "--db", str(db)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "install" in err.lower()
    assert "[ui]" in err


# ---------------------------------------------------------------------------
# top-level help
# ---------------------------------------------------------------------------


def test_help_lists_subcommands(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "info" in out
    assert "prune" in out
    assert "faces" in out
    assert "ui" in out


# ---------------------------------------------------------------------------
# faces
# ---------------------------------------------------------------------------


def _populate_pulse_log(db_path) -> None:
    """Seed pulse_log with a handful of fires across two agents and
    two motifs so faces-CLI tests have something to filter on."""
    plugin = SoulPlugin(agent_id="alice", db_path=db_path)
    plugin.close()

    store = SoulStore.get(db_path)
    base = time.time() - 3 * 86400  # 3 days ago
    rows = [
        # (offset_seconds, agent_id, trigger, face_id, dispatched, suppressed)
        (0, "alice", "distress", "baseline.distress.directness", 1, None),
        (60, "alice", "distress", "baseline.distress.weariness", 1, None),
        (120, "alice", "distress", "baseline.distress.directness", 0, "cooldown"),
        (86400, "bob", "gratitude", "baseline.gratitude.specific", 1, None),
        (86400 + 60, "bob", "share_impulse", "baseline.share.casual", 1, None),
        (2 * 86400, "alice", "elation", "baseline.elation.bright", 1, None),
    ]
    # Ensure two of the faces exist in prompt_corpus so the LEFT JOIN
    # surfaces a motif for them; the `share_impulse` row deliberately
    # has no matching corpus row to verify the LEFT-JOIN/no-motif path.
    with store.lock:
        c = store.connection
        c.execute(
            "INSERT OR IGNORE INTO prompt_corpus "
            "(id, trigger_kinds, vadugwi_predicates, situation_tags, situation_match, "
            " memory_anchor, cooldown_seconds, base_weight, motif, template, "
            " branch_keys, source, created_at, retired_at) "
            "VALUES (?, '[]', '[]', '[]', 'any', NULL, 0, 1.0, ?, '', '[]', 'test', ?, NULL)",
            ("baseline.distress.directness", "distress", base),
        )
        c.execute(
            "INSERT OR IGNORE INTO prompt_corpus "
            "(id, trigger_kinds, vadugwi_predicates, situation_tags, situation_match, "
            " memory_anchor, cooldown_seconds, base_weight, motif, template, "
            " branch_keys, source, created_at, retired_at) "
            "VALUES (?, '[]', '[]', '[]', 'any', NULL, 0, 1.0, ?, '', '[]', 'test', ?, NULL)",
            ("baseline.gratitude.specific", "gratitude", base),
        )
        for offset, agent, trig, face, disp, supp in rows:
            c.execute(
                "INSERT INTO pulse_log "
                "(ts, agent_id, snap, trigger_kind, suppressed_reason, "
                " target_present, dispatched, prompt_text, face_id) "
                "VALUES (?, ?, '{}', ?, ?, 1, ?, '', ?)",
                (base + offset, agent, trig, supp, disp, face),
            )
        c.commit()


def test_faces_reports_recent_fires(tmp_path, capsys) -> None:
    db = tmp_path / "faces.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    # Most-recent (alice elation) appears first; oldest (alice distress
    # directness) appears last in the visible window.
    assert "elation" in out
    assert "alice" in out and "bob" in out
    assert "baseline.distress.directness" in out
    assert "6 row(s)" in out


def test_faces_filters_by_agent(tmp_path, capsys) -> None:
    db = tmp_path / "faces2.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--agent", "bob"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bob" in out
    assert "alice" not in out
    assert "2 row(s)" in out


def test_faces_filters_by_face(tmp_path, capsys) -> None:
    db = tmp_path / "faces3.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--by-face", "baseline.distress.directness"])
    assert rc == 0
    out = capsys.readouterr().out
    # Two rows for that face id (one dispatched, one cooldown-suppressed).
    assert "2 row(s)" in out
    assert "baseline.distress.weariness" not in out


def test_faces_filters_by_motif(tmp_path, capsys) -> None:
    db = tmp_path / "faces4.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--motif", "gratitude"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 row(s)" in out
    assert "gratitude" in out


def test_faces_dispatched_only_hides_suppressed(tmp_path, capsys) -> None:
    db = tmp_path / "faces5.db"
    _populate_pulse_log(db)

    rc = main(
        [
            "faces",
            "--db",
            str(db),
            "--by-face",
            "baseline.distress.directness",
            "--dispatched-only",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    # The cooldown-suppressed row is hidden; only the dispatched one remains.
    assert "1 row(s)" in out
    assert "cooldown" not in out


def test_faces_limit_caps_rows(tmp_path, capsys) -> None:
    db = tmp_path / "faces6.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--limit", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 row(s)" in out


def test_faces_empty_result_is_not_an_error(tmp_path, capsys) -> None:
    db = tmp_path / "faces7.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--agent", "nobody"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 row(s)" in out


def test_faces_handles_missing_db(tmp_path, capsys) -> None:
    rc = main(["faces", "--db", str(tmp_path / "nope.db")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_faces_invalid_since_date_errors(tmp_path, capsys) -> None:
    db = tmp_path / "faces8.db"
    _populate_pulse_log(db)

    rc = main(["faces", "--db", str(db), "--since", "yesterday"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "since" in err.lower() or "yyyy-mm-dd" in err.lower()
