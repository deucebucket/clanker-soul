"""``clanker-soul`` command-line entry.

Three subcommands cover the local-ops surface for v0.2:

  - ``clanker-soul info  --db PATH``                       inspect a soul.db
  - ``clanker-soul prune --db PATH --before YYYY-MM-DD``   trim old log rows
  - ``clanker-soul ui    --db PATH``                       launch dashboard
                                                            (stub until Phase 2)

The CLI is intentionally minimal — anything fancier (filter by classification,
export to CSV, replay simulator) belongs in the Phase-2 dashboard, not here.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from clanker_soul.soul import SoulStore


def _format_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _info(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"error: db not found: {db}", file=sys.stderr)
        return 2
    try:
        store = SoulStore(db)
    except sqlite3.DatabaseError as e:
        print(f"error: not a valid sqlite database: {e}", file=sys.stderr)
        return 2

    size_bytes = db.stat().st_size

    with store.lock:
        c = store.connection
        agent_ids = sorted({
            r[0] for r in c.execute(
                "SELECT agent_id FROM soul_state "
                "UNION SELECT agent_id FROM events "
                "UNION SELECT agent_id FROM pulse_log "
                "UNION SELECT agent_id FROM config_overrides"
            ).fetchall()
        })
        soul_count = c.execute("SELECT COUNT(*) FROM soul_state").fetchone()[0]
        events_count = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        pulse_count = c.execute("SELECT COUNT(*) FROM pulse_log").fetchone()[0]
        overrides_count = c.execute(
            "SELECT COUNT(*) FROM config_overrides"
        ).fetchone()[0]
        oldest, newest = c.execute(
            "SELECT MIN(ts), MAX(ts) FROM events"
        ).fetchone()

    print(f"db: {db}")
    print(f"size: {size_bytes:,} bytes")
    print(f"agents: {len(agent_ids)}")
    for aid in agent_ids:
        per_agent = store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE agent_id = ?", (aid,),
        ).fetchone()[0]
        per_pulse = store.connection.execute(
            "SELECT COUNT(*) FROM pulse_log WHERE agent_id = ?", (aid,),
        ).fetchone()[0]
        print(f"  - {aid}: {per_agent} events, {per_pulse} pulses")
    print("tables:")
    print(f"  soul_state:       {soul_count}")
    print(f"  events:           {events_count}")
    print(f"  pulse_log:        {pulse_count}")
    print(f"  config_overrides: {overrides_count}")
    print(f"events span: {_format_ts(oldest)} → {_format_ts(newest)}")
    return 0


def _parse_before(s: str) -> float:
    """Accept YYYY-MM-DD; treat it as midnight UTC."""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(
            f"--before must be YYYY-MM-DD (got {s!r}): {e}"
        ) from e
    return dt.timestamp()


def _prune(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"error: db not found: {db}", file=sys.stderr)
        return 2
    try:
        cutoff = _parse_before(args.before)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    store = SoulStore(db)
    base_where = "ts < ?"
    base_params: tuple = (cutoff,)
    if args.agent_id:
        base_where += " AND agent_id = ?"
        base_params = (cutoff, args.agent_id)

    with store.lock:
        c = store.connection
        events_to_drop = c.execute(
            f"SELECT COUNT(*) FROM events WHERE {base_where}", base_params,
        ).fetchone()[0]
        pulse_to_drop = c.execute(
            f"SELECT COUNT(*) FROM pulse_log WHERE {base_where}", base_params,
        ).fetchone()[0]

    scope = f"agent={args.agent_id}" if args.agent_id else "all agents"
    print(f"would delete {events_to_drop} events + {pulse_to_drop} pulses "
          f"older than {args.before} ({scope})")
    if not args.yes:
        print("error: refusing without -y/--yes; pass -y to confirm",
              file=sys.stderr)
        return 1

    with store.lock:
        c = store.connection
        c.execute(f"DELETE FROM events WHERE {base_where}", base_params)
        c.execute(f"DELETE FROM pulse_log WHERE {base_where}", base_params)
        c.commit()
    print(f"deleted {events_to_drop} events + {pulse_to_drop} pulses")
    return 0


def _ui(args: argparse.Namespace) -> int:
    """Phase-2 dashboard launcher. Falls through to a stub when the
    ``[ui]`` extra isn't installed. Once Phase 2 ships, ``clanker_soul.ui``
    will exist with a ``launch(db_path, ...)`` function and this stub
    becomes a thin dispatcher."""
    try:
        from clanker_soul.ui import launch  # type: ignore[import-not-found]
    except ImportError:
        print(
            "error: clanker-soul[ui] not installed; "
            "pip install 'clanker-soul[ui]' to enable the dashboard",
            file=sys.stderr,
        )
        return 1
    return launch(args.db, agent_id=args.agent_id, port=args.port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clanker-soul",
        description=(
            "Local-ops CLI for clanker-soul soul.db files. "
            "Inspect, prune, and (eventually) launch the dashboard."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    info_p = sub.add_parser("info", help="inspect a soul.db")
    info_p.add_argument("--db", required=True, help="path to soul.db")
    info_p.set_defaults(func=_info)

    prune_p = sub.add_parser("prune", help="delete events + pulses older than a date")
    prune_p.add_argument("--db", required=True)
    prune_p.add_argument("--before", required=True,
                         help="ISO date (YYYY-MM-DD); rows with ts < midnight UTC are deleted")
    prune_p.add_argument("--agent-id", default=None,
                         help="scope to one agent (default: all)")
    prune_p.add_argument("-y", "--yes", action="store_true",
                         help="confirm deletion (required)")
    prune_p.set_defaults(func=_prune)

    ui_p = sub.add_parser("ui", help="launch the dashboard (Phase 2; stub for now)")
    ui_p.add_argument("--db", required=True)
    ui_p.add_argument("--agent-id", default=None)
    ui_p.add_argument("--port", type=int, default=7900)
    ui_p.set_defaults(func=_ui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
