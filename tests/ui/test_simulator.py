"""Simulator — deterministic replay + sandbox isolation + routes."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from clanker_soul import PhysicsConfig, Score, SoulPlugin, SoulState, SoulStore
from clanker_soul.eventlog import SqliteEventLog
from clanker_soul.ui.app import create_app
from clanker_soul.ui.simulator import (
    parse_config,
    parse_soul,
    replay_events,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _populated_db(tmp_path) -> str:
    db = tmp_path / "sim.db"
    with SoulPlugin(agent_id="alice", db_path=db) as p:
        for _ in range(6):
            p.ingest(Score(v=40, w=40, u=180, patterns=("ABANDONMENT",), direction="SELF_DIRECTED"))
        for _ in range(4):
            p.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
    return str(db)


def _records(db: str, agent_id: str = "alice"):
    """Read records oldest-first, the order replay_events expects."""
    log = SqliteEventLog(SoulStore.get(db))
    return list(reversed(log.read_ingest(agent_id)))


# ---------------------------------------------------------------------------
# replay_events — pure
# ---------------------------------------------------------------------------


def test_replay_empty_records_is_a_noop(tmp_path) -> None:
    soul = SoulState(v=200)
    res = replay_events([], soul, PhysicsConfig())
    assert res.n_events == 0
    assert res.steps == ()
    assert res.soul_sim_end.v == 200


def test_replay_produces_one_step_per_record(tmp_path) -> None:
    db = _populated_db(tmp_path)
    records = _records(db)
    res = replay_events(records, SoulState(), PhysicsConfig())
    assert res.n_events == len(records)
    assert len(res.steps) == len(records)


def test_replay_is_deterministic(tmp_path) -> None:
    """Same (records, soul, config) → same simulated trajectory."""
    db = _populated_db(tmp_path)
    records = _records(db)
    a = replay_events(records, SoulState(v=200), PhysicsConfig())
    b = replay_events(records, SoulState(v=200), PhysicsConfig())
    assert [s.mood_sim for s in a.steps] == [s.mood_sim for s in b.steps]
    assert a.soul_sim_end == b.soul_sim_end


def test_replay_different_configs_produce_different_trajectories(tmp_path) -> None:
    db = _populated_db(tmp_path)
    records = _records(db)
    soft = replay_events(
        records,
        SoulState(),
        PhysicsConfig(blend_alpha=0.05),  # mood barely moves
    )
    hard = replay_events(
        records,
        SoulState(),
        PhysicsConfig(blend_alpha=0.6),  # mood swings hard
    )
    soft_v = [s.mood_sim.v for s in soft.steps]
    hard_v = [s.mood_sim.v for s in hard.steps]
    assert soft_v != hard_v


def test_replay_does_not_write_to_db(tmp_path) -> None:
    """Sandbox guarantee: replay must never log new events to the live DB."""
    db = _populated_db(tmp_path)
    records = _records(db)
    log = SqliteEventLog(SoulStore.get(db))
    before = log.count_ingest("alice")
    replay_events(records, SoulState(), PhysicsConfig(blend_alpha=0.6))
    after = log.count_ingest("alice")
    assert before == after


def test_replay_pairs_real_with_sim(tmp_path) -> None:
    db = _populated_db(tmp_path)
    records = _records(db)
    res = replay_events(records, SoulState(), PhysicsConfig())
    # mood_real should match the recorded mood_after for that step
    for step, rec in zip(res.steps, records):
        assert step.mood_real == rec.mood_after


def test_replay_performance_under_500ms_for_100_events(tmp_path) -> None:
    """Acceptance criterion from #29."""
    db = tmp_path / "perf.db"
    with SoulPlugin(agent_id="bob", db_path=db) as p:
        for i in range(100):
            p.ingest(Score(v=40 + (i % 30), w=80, patterns=("ABANDONMENT",)))
    records = _records(str(db), "bob")
    res = replay_events(records, SoulState(), PhysicsConfig())
    assert res.elapsed_ms < 500.0


# ---------------------------------------------------------------------------
# parse_soul / parse_config
# ---------------------------------------------------------------------------


def test_parse_soul_uses_defaults_for_missing_fields() -> None:
    soul = parse_soul({})
    base = SoulState()
    for d in ("v", "a", "d", "u", "g", "w", "i"):
        assert getattr(soul, d) == getattr(base, d)


def test_parse_soul_reads_provided_fields() -> None:
    soul = parse_soul({"soul_v": "210", "soul_w": "200"})
    assert soul.v == 210
    assert soul.w == 200


def test_parse_soul_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        parse_soul({"soul_v": "999"})


def test_parse_config_reads_provided_fields() -> None:
    cfg = parse_config({"physics_blend_alpha": "0.3", "physics_armor_max": "0.7"})
    assert cfg.blend_alpha == 0.3
    assert cfg.armor_max == 0.7


def test_parse_config_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        parse_config({"physics_blend_alpha": "5.0"})


# ---------------------------------------------------------------------------
# /simulate routes
# ---------------------------------------------------------------------------


def test_simulate_page_renders_form(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/simulate?agent_id=alice")
    assert res.status_code == 200
    assert "hypothetical starting soul" in res.text
    assert "hypothetical physics config" in res.text
    assert "events to replay" in res.text


def test_simulate_run_returns_result_fragment(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/simulate/run",
            data={"agent_id": "alice", "n_events": "10"},
        )
    assert res.status_code == 200
    assert "<html" not in res.text.lower()  # fragment, not full page
    assert "replay result" in res.text


def test_simulate_run_with_no_events_shows_empty_state(tmp_path) -> None:
    db = tmp_path / "empty.db"
    SoulStore(db)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/simulate/run",
            data={"agent_id": "ghost", "n_events": "10"},
        )
    assert res.status_code == 200
    assert "nothing to replay" in res.text


def test_simulate_run_rejects_out_of_range_field(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/simulate/run",
            data={"agent_id": "alice", "n_events": "5", "physics_blend_alpha": "9.9"},
        )
    assert res.status_code == 400


def test_simulate_run_clamps_n_events(tmp_path) -> None:
    """Out-of-range n_events should clamp, not error."""
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/simulate/run",
            data={"agent_id": "alice", "n_events": "99999"},
        )
    assert res.status_code == 200


def test_simulate_apply_writes_only_non_default_fields(tmp_path) -> None:
    """The apply handler is the bridge from simulator → live config."""
    db = _populated_db(tmp_path)
    app = create_app(db)
    # All-defaults form → no overrides written.
    with TestClient(app) as client:
        res = client.post(
            "/simulate/apply",
            data={"agent_id": "alice"},
            follow_redirects=False,
        )
    assert res.status_code == 303
    from clanker_soul.overrides import ConfigOverrides

    bundle = ConfigOverrides(SoulStore.get(db)).get("alice")
    assert bundle.physics == {}
    assert bundle.soul == {}


def test_simulate_apply_writes_provided_overrides(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/simulate/apply",
            data={
                "agent_id": "alice",
                "soul_v": "200",
                "physics_blend_alpha": "0.4",
            },
            follow_redirects=False,
        )
    assert res.status_code == 303
    from clanker_soul.overrides import ConfigOverrides

    bundle = ConfigOverrides(SoulStore.get(db)).get("alice")
    assert bundle.soul.get("v") == 200
    assert bundle.physics.get("blend_alpha") == 0.4
