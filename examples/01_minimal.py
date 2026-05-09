"""01 · Minimal — the absolute smallest clanker-soul integration.

Run:
    python examples/01_minimal.py

What it does:
- creates a tmp soul.db
- spins up a SoulPlugin for "demo-agent"
- ingests a handful of hand-built Scores
- prints the resulting state-context block (the string the agent reads
  each turn to know how it feels)
- saves on context exit

What it shows:
- SoulPlugin is the one-call drop-in. You don't compose
  EmotionalPhysics + SoulStore + SqliteEventLog by hand unless you have
  a reason to.
- A fresh agent's mood anchors to the personality SoulState defaults
  (mildly positive, in-control, strong-worth) — neutral input does NOT
  read as depression.
- Patterns matter. The string tags on a Score steer classification,
  reservoir accounting, and breach behavior — not just the V/A/D
  numbers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clanker_soul import Score, SoulPlugin


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="clanker-soul-ex01-"))
    db = tmp / "soul.db"
    print(f"db: {db}\n")

    with SoulPlugin(agent_id="demo-agent", db_path=db) as plugin:
        # Three positive events.
        for _ in range(3):
            plugin.ingest(
                Score(
                    v=200,
                    a=140,
                    w=190,
                    patterns=("AFFIRMATION", "GRATITUDE"),
                )
            )

        # One small negative event with a self-directed tag.
        plugin.ingest(
            Score(
                v=60,
                w=70,
                u=180,
                patterns=("CRITICISM",),
                direction="SELF_DIRECTED",
            )
        )

        snap = plugin.snapshot()
        print("snapshot:")
        print(f"  soul:  {snap['soul']}")
        print(f"  mood:  {snap.get('mood')}")
        print(f"  trauma_load:      {snap.get('trauma_load', 0):.2f}")
        print(f"  nourishment_load: {snap.get('nourishment_load', 0):.2f}")
        print()

        print("state-context (this is what the agent reads each turn):")
        print("-" * 60)
        print(plugin.state_context())
        print("-" * 60)
    # plugin auto-saves on __exit__


if __name__ == "__main__":
    main()
