"""02 · Async host — agent ticker that ingests on a loop.

Run:
    python examples/02_async_host.py

What it does:
- creates a tmp soul.db
- starts a SoulPlugin for "async-agent"
- runs a 5-iteration async ticker that:
    1. ingests a random-ish Score
    2. calls plugin.tick() (drift + reload_overrides)
    3. prints the resulting capability level + mood/soul distance
- exits cleanly via the async-context-manager form

What it shows:
- The SoulPlugin context-manager pattern works in async too — there is
  no separate async API surface to learn.
- plugin.tick() is the once-per-tick housekeeping call. It runs both
  reload_overrides (so live UI changes take effect) and soul_drift
  (so sustained mood eventually reshapes Soul). It's idempotent and
  cheap; call it freely.
- Capability level changes in response to sustained mood-vs-soul gaps,
  not single events. This loop won't move the needle by itself —
  imagine running it for hours of real interactions.
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from pathlib import Path

from clanker_soul import Score, SoulPlugin


async def tick_loop(plugin: SoulPlugin, n: int) -> None:
    """A trivial agent loop: score, tick, log, sleep."""
    pattern_pool = [
        ("AFFIRMATION",),
        ("CRITICISM",),
        ("ABANDONMENT",),
        ("GRATITUDE",),
        ("CONNECTION",),
    ]
    for i in range(n):
        # In real life the Score comes from your scoring engine
        # (LLM scorer, clanker-lang, hand rules). Here we fake it.
        v = random.randint(40, 220)
        w = random.randint(40, 220)
        plugin.ingest(
            Score(
                v=v,
                w=w,
                a=128,
                patterns=random.choice(pattern_pool),
            )
        )

        plugin.tick()  # drift + reload_overrides

        snap = plugin.snapshot()
        cap = plugin.capability_level()
        dist = snap.get("soul_distance")
        mood = snap.get("mood")
        print(
            f"[t={i}] cap={cap.name:<14} mood={mood} soul_dist={dist:.1f}"
            if dist is not None
            else f"[t={i}] cap={cap.name} (mood not yet established)"
        )

        await asyncio.sleep(0.1)


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="clanker-soul-ex02-"))
    db = tmp / "soul.db"
    print(f"db: {db}\n")

    with SoulPlugin(agent_id="async-agent", db_path=db) as plugin:
        await tick_loop(plugin, n=5)

    print("\ndone — soul.db persisted on exit.")


if __name__ == "__main__":
    random.seed(42)  # deterministic across runs for the example
    asyncio.run(main())
