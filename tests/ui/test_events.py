"""Events log — query layer + /events route + filters + pagination."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from clanker_soul import Score, SoulPlugin, SoulStore
from clanker_soul.ui.app import create_app
from clanker_soul.ui.events import (
    parse_iso_date,
    query_events,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _populated_db(tmp_path) -> str:
    """Mix of positive, negative, and breach events for filtering tests."""
    db = tmp_path / "events.db"
    with SoulPlugin(agent_id="alice", db_path=db) as p:
        # 5 negative ABANDONMENT (one likely breaches by 8th hit, but we
        # only do 5 here so probably no breach yet)
        for _ in range(5):
            p.ingest(
                Score(
                    v=40,
                    w=40,
                    u=180,
                    patterns=("ABANDONMENT",),
                    direction="SELF_DIRECTED",
                    source="hostile_user",
                )
            )
        # 3 positive AFFIRMATION
        for _ in range(3):
            p.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
        # 1 strong heavy from external source
        p.ingest(
            Score(
                v=20,
                w=15,
                u=240,
                patterns=("EXISTENTIAL_NEGATION",),
                direction="EXTERNAL_REPORT",
                source="x.com/post/news",
            )
        )
    return str(db)


# ---------------------------------------------------------------------------
# query_events — pure
# ---------------------------------------------------------------------------


def test_query_returns_all_events_unfiltered(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice")
    assert res.total == 9  # 5 + 3 + 1
    assert len(res.rows) == 9


def test_query_default_sort_is_ts_desc(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice")
    timestamps = [r.ts for r in res.rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_query_sort_weight_desc(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice", sort="weight_desc")
    weights = [r.weight_raw for r in res.rows]
    assert weights == sorted(weights, reverse=True)


def test_query_filter_by_classification_negative(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice", classification="negative")
    assert all(r.classification == "negative" for r in res.rows)
    assert res.total == 6  # 5 ABANDONMENT + 1 EXISTENTIAL_NEGATION


def test_query_filter_by_classification_positive(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice", classification="positive")
    assert all(r.classification == "positive" for r in res.rows)
    assert res.total == 3


def test_query_filter_by_pattern_substring(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice", pattern_q="ABANDONMENT")
    assert res.total == 5
    assert all("ABANDONMENT" in r.patterns for r in res.rows)


def test_query_pagination(tmp_path) -> None:
    db = _populated_db(tmp_path)
    page1 = query_events(SoulStore.get(db), "alice", page=1, page_size=4)
    assert len(page1.rows) == 4
    assert page1.has_next is True
    assert page1.has_prev is False

    page2 = query_events(SoulStore.get(db), "alice", page=2, page_size=4)
    assert len(page2.rows) == 4
    assert page2.has_next is True
    assert page2.has_prev is True

    page3 = query_events(SoulStore.get(db), "alice", page=3, page_size=4)
    assert len(page3.rows) == 1  # remainder
    assert page3.has_next is False


def test_query_filter_by_ts_range(tmp_path) -> None:
    db = _populated_db(tmp_path)
    # All events were just ingested → ts ≈ now. A future timestamp
    # should match nothing.
    future_ts = time.time() + 10000
    res = query_events(SoulStore.get(db), "alice", ts_after=future_ts)
    assert res.total == 0


def test_parse_iso_date_handles_yyyy_mm_dd() -> None:
    ts = parse_iso_date("2026-01-01")
    assert ts is not None
    assert ts > 1735689000  # roughly Jan 1 2025 onward


def test_parse_iso_date_rejects_garbage() -> None:
    assert parse_iso_date("not a date") is None
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None


def test_query_unknown_sort_falls_back_to_default(tmp_path) -> None:
    db = _populated_db(tmp_path)
    res = query_events(SoulStore.get(db), "alice", sort="garbage")
    timestamps = [r.ts for r in res.rows]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# /events route
# ---------------------------------------------------------------------------


def test_events_page_renders_table(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events?agent_id=alice")
    assert res.status_code == 200
    assert "<table" in res.text
    assert "ABANDONMENT" in res.text
    assert "AFFIRMATION" in res.text


def test_events_partial_returns_only_table_fragment(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events?agent_id=alice&partial=1")
    assert res.status_code == 200
    # Fragment-only: no <html> chrome
    assert "<html" not in res.text.lower()
    assert "<table" in res.text


def test_events_filter_by_classification_via_route(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events?agent_id=alice&classification=positive&partial=1")
    assert res.status_code == 200
    assert "AFFIRMATION" in res.text
    assert "ABANDONMENT" not in res.text


def test_events_drill_down_includes_full_record(tmp_path) -> None:
    """The <details> drill-down has source, direction, mood-before/after,
    weight breakdown — all in the initial HTML (no extra round trip)."""
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events?agent_id=alice&partial=1")
    assert "x.com/post/news" in res.text  # source
    assert "EXTERNAL_REPORT" in res.text  # direction
    assert "raw score" in res.text.lower()
    assert "mood before" in res.text.lower()


def test_events_pagination_via_route(tmp_path) -> None:
    db = _populated_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events?agent_id=alice&partial=1&page_size=4&page=1")
    assert "page 1" in res.text
    assert "next" in res.text.lower()


def test_events_no_agent_no_table(tmp_path) -> None:
    """No agent selected → no events table; just the empty-state."""
    db = tmp_path / "empty.db"
    SoulStore(db)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/events")
    assert res.status_code == 200
    assert "<table" not in res.text
