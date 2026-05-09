"""EventLog sink: IngestRecord/PulseRecord round-trip + soft-fail behavior.

The event log is what the UI reads to answer "why is mood here." This
suite covers:
  - record dataclasses are frozen
  - NullEventLog is a noop and is the default for hosts that don't care
  - SqliteEventLog writes records that round-trip through the DB
  - logging failures DO NOT raise into the caller — physics must keep running
"""

from __future__ import annotations

import time

import pytest

from clanker_soul import (
    Score,
    SoulState,
    SoulStore,
)
from clanker_soul.eventlog import (
    EventLog,
    IngestRecord,
    NullEventLog,
    PulseRecord,
    SqliteEventLog,
)


# ---------------------------------------------------------------------------
# Shapes / immutability
# ---------------------------------------------------------------------------


def test_ingest_record_is_frozen() -> None:
    rec = IngestRecord(
        ts=0.0,
        agent_id="x",
        raw=Score(),
        primed=None,
        mood_before=None,
        mood_after=Score(),
        soul_before=SoulState(),
        soul_after=SoulState(),
        weight_raw=0.1,
        armor=0.5,
        weight_effective=0.05,
        breached=False,
        breach_delta=0.0,
        patterns=(),
        classification=None,
        why="trivial event",
    )
    with pytest.raises(Exception):
        rec.ts = 1.0  # type: ignore[misc]


def test_pulse_record_is_frozen() -> None:
    rec = PulseRecord(
        ts=0.0,
        agent_id="x",
        snap={},
        trigger_kind=None,
        suppressed_reason="cooldown",
        target_present=False,
        dispatched=False,
        prompt=None,
    )
    with pytest.raises(Exception):
        rec.trigger_kind = "distress"  # type: ignore[misc]


def test_eventlog_protocol_is_runtime_checkable() -> None:
    """SqliteEventLog and NullEventLog satisfy the EventLog protocol."""
    store = NullEventLog()  # cheap, no DB needed
    assert isinstance(store, EventLog)


# ---------------------------------------------------------------------------
# NullEventLog
# ---------------------------------------------------------------------------


def test_null_event_log_does_nothing() -> None:
    """Default sink for hosts that don't want logging — must accept any
    record without side effects."""
    log = NullEventLog()
    log.log_ingest(
        IngestRecord(
            ts=0.0,
            agent_id="x",
            raw=Score(),
            primed=None,
            mood_before=None,
            mood_after=Score(),
            soul_before=SoulState(),
            soul_after=SoulState(),
            weight_raw=0.1,
            armor=0.5,
            weight_effective=0.05,
            breached=False,
            breach_delta=0.0,
            patterns=(),
            classification=None,
            why="ok",
        )
    )
    log.log_pulse(
        PulseRecord(
            ts=0.0,
            agent_id="x",
            snap={},
            trigger_kind=None,
            suppressed_reason="no_trigger",
            target_present=False,
            dispatched=False,
            prompt=None,
        )
    )


# ---------------------------------------------------------------------------
# SqliteEventLog round-trip
# ---------------------------------------------------------------------------


def _ingest_rec(ts: float, agent_id: str = "agent-1", **overrides) -> IngestRecord:
    base = dict(
        ts=ts,
        agent_id=agent_id,
        raw=Score(v=80, a=160, d=70, u=180, g=80, w=50, i=110, patterns=("ABANDONMENT",)),
        primed=Score(v=82, a=160, d=70, u=180, g=80, w=52, i=110, patterns=("ABANDONMENT",)),
        mood_before=Score(v=145, w=175),
        mood_after=Score(v=120, w=140),
        soul_before=SoulState(v=145, w=175),
        soul_after=SoulState(v=145, w=175),
        weight_raw=0.78,
        armor=0.55,
        weight_effective=0.42,
        breached=True,
        breach_delta=0.071,
        patterns=("ABANDONMENT",),
        classification="negative",
        why="Heavy ABANDONMENT (weight=0.78) hit through armor=0.55",
    )
    base.update(overrides)
    return IngestRecord(**base)


def test_sqlite_event_log_round_trips_ingest_records(tmp_path) -> None:
    store = SoulStore(tmp_path / "evt.db")
    log = SqliteEventLog(store)

    base = time.time()
    for i in range(5):
        log.log_ingest(_ingest_rec(ts=base + i))

    # Read back via the public read helper.
    records = log.read_ingest(agent_id="agent-1")
    assert len(records) == 5
    # Most recent first.
    assert records[0].ts >= records[-1].ts
    # Round-trip integrity on a representative field set.
    r = records[0]
    assert r.agent_id == "agent-1"
    assert r.weight_raw == pytest.approx(0.78)
    assert r.breached is True
    assert r.breach_delta == pytest.approx(0.071)
    assert r.patterns == ("ABANDONMENT",)
    assert r.classification == "negative"
    assert "ABANDONMENT" in r.why


def test_sqlite_event_log_handles_null_optional_fields(tmp_path) -> None:
    """primed=None, mood_before=None, classification=None must round-trip."""
    store = SoulStore(tmp_path / "null.db")
    log = SqliteEventLog(store)
    log.log_ingest(
        _ingest_rec(
            ts=time.time(),
            primed=None,
            mood_before=None,
            classification=None,
        )
    )
    records = log.read_ingest(agent_id="agent-1")
    assert len(records) == 1
    r = records[0]
    assert r.primed is None
    assert r.mood_before is None
    assert r.classification is None


def test_sqlite_event_log_round_trips_pulse_records(tmp_path) -> None:
    store = SoulStore(tmp_path / "pl.db")
    log = SqliteEventLog(store)

    base = time.time()
    log.log_pulse(
        PulseRecord(
            ts=base,
            agent_id="agent-1",
            snap={"soul": {"v": 145}, "mood": [80, 110, 160, 80, 130, 100, 135]},
            trigger_kind="distress",
            suppressed_reason=None,
            target_present=True,
            dispatched=True,
            prompt="[INTERNAL PULSE — distress]\nYou feel notably worse...",
        )
    )
    log.log_pulse(
        PulseRecord(
            ts=base + 1,
            agent_id="agent-1",
            snap={"soul": {"v": 145}, "mood": [145, 110, 160, 80, 130, 175, 135]},
            trigger_kind=None,
            suppressed_reason="no_trigger",
            target_present=True,
            dispatched=False,
            prompt=None,
        )
    )

    records = log.read_pulse(agent_id="agent-1")
    assert len(records) == 2
    fired = next(r for r in records if r.trigger_kind == "distress")
    assert fired.dispatched is True
    assert fired.prompt and "distress" in fired.prompt.lower()
    suppressed = next(r for r in records if r.trigger_kind is None)
    assert suppressed.suppressed_reason == "no_trigger"
    assert suppressed.dispatched is False


def test_sqlite_event_log_filters_by_agent(tmp_path) -> None:
    store = SoulStore(tmp_path / "multi.db")
    log = SqliteEventLog(store)
    log.log_ingest(_ingest_rec(ts=1.0, agent_id="alice"))
    log.log_ingest(_ingest_rec(ts=2.0, agent_id="bob"))
    log.log_ingest(_ingest_rec(ts=3.0, agent_id="alice"))

    alice = log.read_ingest(agent_id="alice")
    bob = log.read_ingest(agent_id="bob")
    assert len(alice) == 2
    assert len(bob) == 1
    assert all(r.agent_id == "alice" for r in alice)
    assert bob[0].agent_id == "bob"


def test_sqlite_event_log_limit(tmp_path) -> None:
    store = SoulStore(tmp_path / "lim.db")
    log = SqliteEventLog(store)
    base = time.time()
    for i in range(10):
        log.log_ingest(_ingest_rec(ts=base + i))
    assert len(log.read_ingest(agent_id="agent-1", limit=3)) == 3


def test_sqlite_event_log_count(tmp_path) -> None:
    store = SoulStore(tmp_path / "count.db")
    log = SqliteEventLog(store)
    base = time.time()
    for i in range(7):
        log.log_ingest(_ingest_rec(ts=base + i))
    assert log.count_ingest(agent_id="agent-1") == 7
    assert log.count_ingest(agent_id="nobody") == 0


# ---------------------------------------------------------------------------
# Soft-fail behavior — the load-bearing invariant
# ---------------------------------------------------------------------------


def test_log_failure_does_not_raise(tmp_path, caplog) -> None:
    """If the DB write fails, the log call must warn and return rather
    than raising — physics must keep running even when storage hiccups."""
    store = SoulStore(tmp_path / "broken.db")
    log = SqliteEventLog(store)

    # Force a write failure by closing the underlying connection.
    store.connection.close()

    # Should not raise.
    log.log_ingest(_ingest_rec(ts=time.time()))
    log.log_pulse(
        PulseRecord(
            ts=time.time(),
            agent_id="agent-1",
            snap={},
            trigger_kind=None,
            suppressed_reason="no_trigger",
            target_present=False,
            dispatched=False,
            prompt=None,
        )
    )

    # And the failure must have been logged at WARNING level.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("event log" in r.message.lower() for r in warnings)
