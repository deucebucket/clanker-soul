"""Hermes-facing M4 idle-cascade smoke.

This script intentionally uses the same public pieces a Hermes deployment
would wire: the memory provider owns ``SoulPlugin`` and the cascade uses that
live plugin's physics. It does not call a remote LLM or send a real message;
the selected action is a host-owned handler that records what would dispatch
and returns a consequence ``Score``.

Run from this repo:

    python integrations/hermes/scripts/m4_idle_cascade_smoke.py

Or from a hermes-agent checkout with the plugin symlinked:

    python /path/to/clanker-soul/integrations/hermes/scripts/m4_idle_cascade_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    ActionOutcome,
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    GateConfig,
    IdleLoop,
    PromptCorpus,
    RegisteredAction,
    Score,
    build_default_contemplation_corpus,
)
from integrations.hermes import get_provider  # noqa: E402


def _record_action(ctx: CascadeActionContext) -> ActionOutcome:
    return ActionOutcome(
        delivered=True,
        note=f"hermes-smoke:{ctx.action.name}",
        consequences=(
            Score(
                v=172,
                a=90,
                d=160,
                u=35,
                g=115,
                w=188,
                i=145,
                patterns=("HERMES_M4_CASCADE_SMOKE",),
            ),
        ),
    )


async def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="clanker-hermes-m4-"))
    db_path = tmpdir / "soul.db"
    os.environ["CLANKER_SOUL_DB_PATH"] = str(db_path)
    os.environ["CLANKER_SOUL_AGENT_ID"] = "hermes-m4-smoke"

    provider = get_provider()
    provider.initialize("hermes-m4-smoke-session", platform="cli")
    try:
        plugin = provider._plugin
        if plugin is None:
            raise RuntimeError("clanker-soul provider did not initialize")

        full_corpus = build_default_contemplation_corpus(rng=random.Random(11))
        face = next(f for f in full_corpus.faces if f.id == "contemplation.relational.064")
        demo_corpus = PromptCorpus((face,), rng=random.Random(11))
        registry = ActionRegistry(
            (
                RegisteredAction(
                    name="hermes_journal_reflection",
                    tags=frozenset({"journal", "reflect", "research"}),
                    handler=_record_action,
                    cooldown_seconds=0,
                ),
            )
        )

        before = plugin.snapshot()
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
            rng=random.Random(11),
        )

        result = await loop.tick()
        after = plugin.snapshot()
        state = json.loads(provider.handle_tool_call("clanker_soul_state", {}))

        summary = {
            "db_exists": db_path.exists(),
            "provider": provider.name(),
            "tools": [tool["name"] for tool in provider.get_tool_schemas()],
            "gate_passed": result.gate_passed,
            "face": result.face.id if result.face else None,
            "tags": sorted(result.action_tags),
            "chosen_action": result.chosen_action.name if result.chosen_action else None,
            "delivered": result.action_outcome.delivered if result.action_outcome else None,
            "mood_changed": before["mood"] != after["mood"],
            "state_has_mood": bool(state.get("mood")),
        }
        print(json.dumps(summary, sort_keys=True))

        expected = {
            "clanker_soul_state",
            "clanker_soul_apply_preset",
            "clanker_soul_dashboard_url",
        }
        if not expected.issubset(summary["tools"]):
            raise RuntimeError(f"missing clanker-soul tools: {expected - set(summary['tools'])}")
        if not all(
            [
                summary["db_exists"],
                summary["gate_passed"],
                summary["chosen_action"],
                summary["delivered"],
                summary["mood_changed"],
                summary["state_has_mood"],
            ]
        ):
            raise RuntimeError(f"Hermes M4 cascade smoke failed: {summary}")
    finally:
        provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
