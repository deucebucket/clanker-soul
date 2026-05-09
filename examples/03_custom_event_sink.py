"""03 · Custom EventLog — ship events to a non-SQLite destination.

Run:
    python examples/03_custom_event_sink.py

What it does:
- defines NdjsonEventLog: writes IngestRecord + PulseRecord rows as
  newline-delimited JSON to a file
- wires it into EmotionalPhysics directly (skipping SoulPlugin —
  showing the lower-level path)
- ingests a few events
- prints the ndjson lines that landed on disk

What it shows:
- EventLog is a runtime-checkable Protocol (see
  clanker_soul.eventlog.protocol). Anything with .log_ingest() and
  .log_pulse() satisfies it. No subclassing, no registration.
- This is how you ship events to Kafka, Datadog, an internal queue, or
  a forensic audit log instead of (or in addition to) SQLite. Wrap
  multiple sinks in a fan-out class to log to N destinations at once.
- Soft-fail invariant: a logging failure must NOT raise into ingest().
  If your sink can fail (network, disk full), catch and log a warning
  inside log_ingest itself. The engine catches uncaught exceptions
  too — defense in depth.
"""
from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from clanker_soul import EmotionalPhysics, PhysicsConfig, Score, SoulState
from clanker_soul.eventlog import IngestRecord, PulseRecord

logger = logging.getLogger(__name__)


class NdjsonEventLog:
    """Newline-delimited JSON sink. One record per line. Append-only."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Touch the file so consumers can tail it from line 1.
        self.path.touch()

    def log_ingest(self, record: IngestRecord) -> None:
        try:
            row = {
                "type": "ingest",
                "ts": record.ts,
                "agent_id": record.agent_id,
                "raw": asdict(record.raw),
                "primed": asdict(record.primed) if record.primed else None,
                "mood_before": asdict(record.mood_before) if record.mood_before else None,
                "mood_after": asdict(record.mood_after),
                "soul_before": asdict(record.soul_before),
                "soul_after": asdict(record.soul_after),
                "weight_raw": record.weight_raw,
                "armor": record.armor,
                "weight_effective": record.weight_effective,
                "breached": record.breached,
                "breach_delta": record.breach_delta,
                "patterns": list(record.patterns),
                "classification": record.classification,
                "why": record.why,
            }
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            # Soft-fail invariant: never raise into the engine.
            logger.warning("NdjsonEventLog.log_ingest failed: %s", e)

    def log_pulse(self, record: PulseRecord) -> None:
        try:
            row = {
                "type": "pulse",
                "ts": record.ts,
                "agent_id": record.agent_id,
                "snap": record.snap,
                "trigger_kind": record.trigger_kind,
                "suppressed_reason": record.suppressed_reason,
                "target_present": record.target_present,
                "dispatched": record.dispatched,
                "prompt": record.prompt,
            }
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning("NdjsonEventLog.log_pulse failed: %s", e)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="clanker-soul-ex03-"))
    log_path = tmp / "events.ndjson"
    print(f"ndjson sink: {log_path}\n")

    sink = NdjsonEventLog(log_path)

    # Lower-level wire-up: explicit EmotionalPhysics with a custom sink.
    # No SoulStore, no overrides — pure in-memory engine + ndjson log.
    physics = EmotionalPhysics(
        soul=SoulState(),
        config=PhysicsConfig(),
        event_log=sink,
        agent_id="ndjson-agent",
    )

    physics.ingest(Score(v=200, w=200, patterns=("AFFIRMATION",)))
    physics.ingest(Score(v=40, w=50, u=200, patterns=("ABANDONMENT",),
                         direction="SELF_DIRECTED"))
    physics.ingest(Score(v=60, w=80, patterns=("CRITICISM",)))

    print("contents of events.ndjson:")
    print("-" * 60)
    for line in log_path.read_text().splitlines():
        row = json.loads(line)
        print(f"  [{row['type']}] {row['patterns']!r:<25} "
              f"weight={row['weight_raw']:.3f} "
              f"breached={row['breached']} why={row['why'][:50]}...")
    print("-" * 60)


if __name__ == "__main__":
    main()
