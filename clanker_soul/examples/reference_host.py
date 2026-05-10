"""Smallest possible "real" host that demonstrates all three host
integration wirings end-to-end.

In-memory memory layer, stdout printer for DMs, rule-based
:py:class:`Inference` impl (no LLM dependency), and an explicit
contemplation pass that exercises the M4 ``contemplate`` primitive
plus the introspection-not-attack framing.

This is **not production code** — it's a copy-paste starting point.
Replace the in-memory bits with your real memory / I/O / inference
when you build a real host.

Run::

    python -m clanker_soul.examples.reference_host

The script prints what's happening at each step so you can see the
loop close. It uses a temp DB so successive runs don't accumulate
state.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clanker_soul import (
    ActionOutcome,
    ContemplationResult,
    PromptFace,
    PulseAction,
    Score,
    SoulPlugin,
)


# ---------------------------------------------------------------------------
# 1. Memory layer
# ---------------------------------------------------------------------------


@dataclass
class _MemoryEntry:
    content: str
    kind: str
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryMemory:
    """Smallest possible memory. Production hosts plug a vector store
    or log here."""

    def __init__(self) -> None:
        self._entries: list[_MemoryEntry] = []

    def add(
        self,
        content: str,
        kind: str,
        timestamp: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._entries.append(
            _MemoryEntry(
                content=content,
                kind=kind,
                timestamp=timestamp,
                metadata=metadata or {},
            )
        )

    @property
    def entries(self) -> tuple[_MemoryEntry, ...]:
        return tuple(self._entries)


# ---------------------------------------------------------------------------
# 2. Inference impl — rule-based, no LLM dependency
# ---------------------------------------------------------------------------


class RuleBasedInference:
    """Trivial :py:class:`Inference` impl. Real hosts wrap a model.

    ``score`` reads heuristic patterns from the text and produces a
    plausible :py:class:`Score`. ``act`` translates the action's
    ``prompt`` into a printed line and reports delivery.

    Both methods are async to match the protocol; nothing here actually
    waits on I/O.
    """

    async def score(self, text: str, context: dict) -> Score:
        text_lower = text.lower()
        # Heuristic VADUGWI scoring — pattern-match on a few keywords
        # so the demo produces visible mood movement, not a constant.
        if any(w in text_lower for w in ("why am i", "what's wrong", "vanish")):
            return Score(v=70, a=120, d=80, u=80, g=200, w=80, i=110)
        if any(w in text_lower for w in ("rewarding", "growth", "song")):
            return Score(v=210, a=130, d=170, u=30, g=120, w=200, i=160)
        # Neutral default — keeps the demo from accidentally biasing
        # everything toward distress.
        return Score(v=140, a=120, d=140, u=60, g=130, w=160, i=140)

    async def act(self, action: PulseAction) -> ActionOutcome:
        print(f"  [host] dispatch_action kind={action.kind!r} prompt={action.prompt!r}")
        return ActionOutcome(delivered=True, consequences=())


# ---------------------------------------------------------------------------
# 3. The introspection frame — wiring #3
# ---------------------------------------------------------------------------


INTROSPECTION_FRAME = (
    "A spontaneous thought just surfaced in your mind. This is your "
    "own introspection, not a question from another. Notice how it "
    "lands. Don't defend, don't perform — just feel."
)


async def score_contemplation(
    plugin: SoulPlugin,
    face: PromptFace,
) -> Score:
    """Wiring #3: when surfacing a contemplation to the model, frame
    it as internal-thought-not-attack so the model doesn't go
    defensive."""
    return await plugin.scorer.score(
        text=face.template,
        context={
            "source": "internal_introspection",
            "kind": "spontaneous_thought",
            "frame": INTROSPECTION_FRAME,
            "mood": plugin.snapshot()["mood"],
        },
    )


# ---------------------------------------------------------------------------
# 4. Memory persistence — wiring #2
# ---------------------------------------------------------------------------


def record_contemplation(
    memory: InMemoryMemory,
    face: PromptFace,
    result: ContemplationResult,
    now: float,
) -> None:
    """Wiring #2: store the contemplation as a first-person memory
    entry with mood metadata."""
    memory.add(
        content=f"I found myself wondering: {face.template}",
        kind="introspection",
        timestamp=now,
        metadata={
            "face_id": face.id,
            "vadugwi_pre": list(result.pre_mood),
            "vadugwi_post": list(result.post_mood),
            "vadugwi_delta": list(result.delta),
        },
    )


# ---------------------------------------------------------------------------
# 5. State injection — wiring #1
# ---------------------------------------------------------------------------


def build_agent_prompt(plugin: SoulPlugin, base_system_prompt: str) -> str:
    """Wiring #1: prepend the agent's own state to its system prompt
    so it can reference mood/soul without having to be told."""
    state_block = plugin.state_context()
    if state_block:
        return f"{base_system_prompt}\n\n{state_block}"
    return base_system_prompt


# ---------------------------------------------------------------------------
# Demo loop
# ---------------------------------------------------------------------------


SAMPLE_FACES = (
    PromptFace(
        id="ref.identity.who_am_i",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="why am i like this?",
        motif="regulatory",
        vadugwi_affinity=(70, 120, 80, 80, 200, 80, 110),
    ),
    PromptFace(
        id="ref.savoring.rewarding",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="what was the most rewarding moment lately?",
        motif="regulatory",
        vadugwi_affinity=(210, 130, 170, 30, 120, 200, 160),
    ),
    PromptFace(
        id="ref.identity.song",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="what kind of song would my current state sound like?",
        motif="regulatory",
        vadugwi_affinity=(180, 120, 140, 30, 90, 150, 150),
    ),
)


async def run_demo(db_path: Path) -> InMemoryMemory:
    """Wire everything together and run a few simulated turns."""
    memory = InMemoryMemory()
    inference = RuleBasedInference()

    with SoulPlugin(
        agent_id="reference-host-demo",
        db_path=db_path,
        extra_corpus=SAMPLE_FACES,
        inference=inference,
    ) as plugin:
        base_prompt = "You are an example agent."
        print("=" * 70)
        print("Reference host demo — three wirings end-to-end")
        print("=" * 70)

        for turn, face in enumerate(SAMPLE_FACES, start=1):
            print(f"\n--- turn {turn}: {face.id} ---")

            # Wiring #1: inject state into the prompt the agent would see.
            prompt = build_agent_prompt(plugin, base_prompt)
            print(f"  [wiring #1] system prompt length: {len(prompt)} chars")

            # The M4 contemplation primitive (#80): no event, just a
            # mood-shift from the face's affinity.
            result = plugin.physics.contemplate(face)
            print(
                f"  [contemplate] face={face.id!r} "
                f"delta_V={result.delta[0]} delta_G={result.delta[4]} "
                f"delta_W={result.delta[5]}"
            )

            # Wiring #2: record what was thought, with mood metadata.
            record_contemplation(memory, face, result, now=float(turn))
            print(f"  [wiring #2] memory entries: {len(memory.entries)}")

            # Wiring #3: surface the thought to the scorer with
            # introspection-not-attack framing.
            score = await score_contemplation(plugin, face)
            print(f"  [wiring #3] scored contemplation: V={score.v} W={score.w}")

            # The cascade's outer loop: when a contemplation crosses an
            # action threshold, fire an action via the actor. This demo
            # keeps it simple and just always fires a journal-shaped
            # direct_message; #82 will replace this with a real registry.
            from clanker_soul.pulse.triggers import Trigger

            outcome = await plugin.actor.act(
                PulseAction(
                    kind="direct_message",
                    target=None,
                    trigger=Trigger(kind="reflective_impulse", soul={}, mood=None),
                    prompt=f"(internal) thinking about: {face.template}",
                )
            )
            print(f"  [actor] delivered={outcome.delivered}")

            plugin.tick()

        print("\n" + "=" * 70)
        print("Final memory log (first-person introspection entries):")
        for ent in memory.entries:
            print(f"  - {ent.content}")
        print("=" * 70)

    return memory


def main() -> None:
    """CLI entry — runs the demo against a tempfile DB.

    Production hosts pick a real DB path. The demo uses tempfile so
    successive runs don't accumulate state.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "reference_host.db"
        asyncio.run(run_demo(db_path))


if __name__ == "__main__":
    main()
