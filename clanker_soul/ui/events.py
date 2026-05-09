"""Events log query layer for the dashboard.

The forensic view: every ``IngestRecord`` from the ``events`` table,
sortable / filterable / paginated. Pure read-only — runs the same
queries the host's :py:class:`SqliteEventLog` would, but with the
filters and sorts the UI cares about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from clanker_soul.eventlog import IngestRecord
from clanker_soul.eventlog.sqlite import _score_from_json, _soul_from_json
from clanker_soul.soul import SoulStore


SORT_OPTIONS = ("ts_desc", "ts_asc", "weight_desc", "weight_asc", "breach_first")
DEFAULT_SORT = "ts_desc"
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True)
class EventQueryResult:
    rows: list[IngestRecord]
    total: int  # total matching the filter, ignoring pagination
    page: int  # 1-indexed
    page_size: int
    has_prev: bool
    has_next: bool


def _build_where(
    agent_id: str,
    *,
    classification: str | None,
    breach: str | None,
    pattern_q: str | None,
    ts_after: float | None,
    ts_before: float | None,
) -> tuple[str, list]:
    where = ["agent_id = ?"]
    params: list = [agent_id]
    if classification == "positive" or classification == "negative":
        where.append("classification = ?")
        params.append(classification)
    elif classification == "null":
        where.append("classification IS NULL")
    if breach == "yes":
        where.append("breached = 1")
    elif breach == "no":
        where.append("breached = 0")
    if pattern_q:
        where.append("patterns LIKE ?")
        params.append(f"%{pattern_q}%")
    if ts_after is not None:
        where.append("ts >= ?")
        params.append(ts_after)
    if ts_before is not None:
        where.append("ts <= ?")
        params.append(ts_before)
    return " AND ".join(where), params


def _build_order(sort: str) -> str:
    if sort == "ts_asc":
        return "ts ASC, id ASC"
    if sort == "weight_desc":
        return "weight_raw DESC, ts DESC, id DESC"
    if sort == "weight_asc":
        return "weight_raw ASC, ts ASC, id ASC"
    if sort == "breach_first":
        return "breached DESC, ts DESC, id DESC"
    return "ts DESC, id DESC"  # default


def _row_to_record(row) -> IngestRecord:
    """Decode a SELECT row into an IngestRecord. Column order must
    match :py:func:`query_events`'s SELECT clause."""
    import json as _json

    (
        ts,
        agent_id,
        raw_score,
        primed_score,
        mood_before,
        mood_after,
        soul_before,
        soul_after,
        weight_raw,
        armor,
        weight_effective,
        breached,
        breach_delta,
        patterns,
        classification,
        why,
    ) = row
    return IngestRecord(
        ts=ts,
        agent_id=agent_id,
        raw=_score_from_json(raw_score),  # type: ignore[arg-type]
        primed=_score_from_json(primed_score),
        mood_before=_score_from_json(mood_before),
        mood_after=_score_from_json(mood_after),  # type: ignore[arg-type]
        soul_before=_soul_from_json(soul_before),
        soul_after=_soul_from_json(soul_after),
        weight_raw=weight_raw,
        armor=armor,
        weight_effective=weight_effective,
        breached=bool(breached),
        breach_delta=breach_delta,
        patterns=tuple(_json.loads(patterns)),
        classification=classification,
        why=why,
    )


def query_events(
    store: SoulStore,
    agent_id: str,
    *,
    sort: str = DEFAULT_SORT,
    classification: str | None = None,
    breach: str | None = None,
    pattern_q: str | None = None,
    ts_after: float | None = None,
    ts_before: float | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> EventQueryResult:
    """Run a paginated, filtered, sorted query against the events table.

    All filter args are optional. Returns rows + total count + pagination
    metadata so the UI can render Prev/Next links."""
    if sort not in SORT_OPTIONS:
        sort = DEFAULT_SORT
    page = max(1, page)
    page_size = max(1, min(500, page_size))

    where_sql, where_params = _build_where(
        agent_id,
        classification=classification,
        breach=breach,
        pattern_q=pattern_q,
        ts_after=ts_after,
        ts_before=ts_before,
    )
    order_sql = _build_order(sort)
    offset = (page - 1) * page_size

    select_cols = (
        "ts, agent_id, raw_score, primed_score, "
        "mood_before, mood_after, soul_before, soul_after, "
        "weight_raw, armor, weight_effective, "
        "breached, breach_delta, patterns, classification, why"
    )

    with store.lock:
        total_row = store.connection.execute(
            f"SELECT COUNT(*) FROM events WHERE {where_sql}",
            where_params,
        ).fetchone()
        total = int(total_row[0])
        rows = store.connection.execute(
            f"SELECT {select_cols} FROM events "
            f"WHERE {where_sql} ORDER BY {order_sql} "
            f"LIMIT ? OFFSET ?",
            (*where_params, page_size, offset),
        ).fetchall()

    records = [_row_to_record(r) for r in rows]
    return EventQueryResult(
        rows=records,
        total=total,
        page=page,
        page_size=page_size,
        has_prev=page > 1,
        has_next=(page * page_size) < total,
    )


def parse_iso_date(value: str | None) -> float | None:
    """Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS; return unix ts or None."""
    if not value:
        return None
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value)
        else:
            dt = datetime.strptime(value, "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


__all__ = [
    "EventQueryResult",
    "query_events",
    "parse_iso_date",
    "SORT_OPTIONS",
    "DEFAULT_SORT",
    "DEFAULT_PAGE_SIZE",
]
