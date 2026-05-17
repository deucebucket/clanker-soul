"""End-to-end M4 idle cascade example.

This is the smallest runnable host shape that wires:

* ``build_default_contemplation_corpus()`` for the shipped M4 faces
* ``IdleLoop`` for heartbeat / Roll 0 / Roll 1
* ``tags_from_delta()`` through the loop's default tag mapper
* ``ActionRegistry`` for Roll 2 / Roll 3 action dispatch

The example narrows the default corpus to one known face so the subprocess
smoke test is deterministic. A real host would pass the full default corpus.
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from pathlib import Path

from clanker_soul import (
    ActionOutcome,
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    GateConfig,
    IdleLoop,
    PromptCorpus,
    RegisteredAction,
    Score,
    SoulPlugin,
    build_default_contemplation_corpus,
)


def journal_handler(ctx: CascadeActionContext) -> ActionOutcome:
    face = ctx.face
    thought = face.template if face is not None else "(no thought)"
    print(f"[action] {ctx.action.name}: {thought}")
    print(f"[tags] {', '.join(sorted(ctx.tags))}")
    return ActionOutcome(
        delivered=True,
        note="journaled to host memory",
        consequences=(
            Score(
                v=170,
                a=95,
                d=155,
                u=40,
                g=120,
                w=185,
                i=145,
                patterns=("SELF_REFLECTION_COMPLETED",),
            ),
        ),
    )


async def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="clanker-soul-ex05-"))
    db_path = tmpdir / "soul.db"

    with SoulPlugin(agent_id="example-m4", db_path=db_path) as plugin:
        full_corpus = build_default_contemplation_corpus(rng=random.Random(7))
        face = next(f for f in full_corpus.faces if f.id == "contemplation.relational.064")
        demo_corpus = PromptCorpus((face,), rng=random.Random(7))

        registry = ActionRegistry(
            (
                RegisteredAction(
                    name="journal_reflection",
                    tags=frozenset({"journal", "reflect"}),
                    handler=journal_handler,
                    vadugwi_affinity=(120, 120, 150, 70, 150, 170, 140),
                    cooldown_seconds=0,
                ),
            )
        )

        loop = IdleLoop(
            physics=plugin.physics,
            contemplation_corpus=demo_corpus,
            gate_config=GateConfig(
                base_probability=1.0,
                cooldown_after_action_s=0.0,
                cooldown_after_contemplation_s=0.0,
                min_quiet_s=0.0,
                contemplation_weight_scale=4.0,
            ),
            action_threshold_config=ActionThresholdConfig(
                min_abs_delta_per_dim=8,
                min_total_delta=20,
            ),
            registry=registry,
            rng=random.Random(7),
        )

        result = await loop.tick()

        print(f"soul db: {db_path}")
        print(f"gate passed: {result.gate_passed}")
        print(f"face: {result.face.id if result.face else None}")
        print(f"delta: {result.contemplation.delta if result.contemplation else None}")
        print(f"chosen action: {result.chosen_action.name if result.chosen_action else None}")
        print(f"delivered: {result.action_outcome.delivered if result.action_outcome else None}")
        print(f"mood after consequence: {plugin.snapshot()['mood']}")


if __name__ == "__main__":
    asyncio.run(main())
