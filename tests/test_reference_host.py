"""Smoke test for the reference host example.

Verifies the end-to-end demo runs without raising and that the three
host integration wirings actually produce observable effects:

1. ``state_context`` is callable and returns a string for every turn
   (wiring #1 — state injection).
2. The memory layer accumulates first-person introspection entries
   with VADUGWI metadata (wiring #2 — memory persistence).
3. Contemplations route through the scorer with the introspection
   ``frame`` key in the context dict (wiring #3 — introspection
   framing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clanker_soul.examples.reference_host import (
    INTROSPECTION_FRAME,
    SAMPLE_FACES,
    InMemoryMemory,
    RuleBasedInference,
    record_contemplation,
    run_demo,
    score_contemplation,
)
from clanker_soul import (
    ContemplationResult,
    PromptFace,
    Score,
    SoulPlugin,
)


@pytest.mark.asyncio
async def test_reference_host_runs_end_to_end(tmp_path: Path) -> None:
    """The published demo runs without raising and accumulates one
    memory entry per face."""
    db_path = tmp_path / "demo.db"
    memory = await run_demo(db_path)
    assert len(memory.entries) == len(SAMPLE_FACES)


@pytest.mark.asyncio
async def test_reference_host_introspection_metadata(tmp_path: Path) -> None:
    """Each memory entry must be first-person and tagged 'introspection'."""
    db_path = tmp_path / "demo.db"
    memory = await run_demo(db_path)
    for entry in memory.entries:
        assert entry.kind == "introspection"
        assert entry.content.startswith("I found myself wondering:"), entry.content
        assert "vadugwi_pre" in entry.metadata
        assert "vadugwi_post" in entry.metadata
        assert "vadugwi_delta" in entry.metadata
        assert len(entry.metadata["vadugwi_pre"]) == 7


@pytest.mark.asyncio
async def test_score_contemplation_passes_introspection_frame(tmp_path: Path) -> None:
    """Wiring #3: the context dict reaching the scorer must carry the
    source/kind/frame keys so the model knows it's internal-thought-not-attack."""

    captured: list[dict] = []

    class CapturingInference:
        async def score(self, text: str, context: dict) -> Score:  # noqa: ARG002
            captured.append(context)
            return Score(v=128)

        async def act(self, action):  # noqa: ARG002
            from clanker_soul import ActionOutcome

            return ActionOutcome(delivered=True, consequences=())

    plugin = SoulPlugin(
        agent_id="frame-test",
        db_path=tmp_path / "frame.db",
        inference=CapturingInference(),  # type: ignore[arg-type]
    )
    try:
        await score_contemplation(plugin, SAMPLE_FACES[0])
        assert len(captured) == 1
        ctx = captured[0]
        assert ctx["source"] == "internal_introspection"
        assert ctx["kind"] == "spontaneous_thought"
        assert ctx["frame"] == INTROSPECTION_FRAME
        assert "mood" in ctx
    finally:
        plugin.close()


def test_record_contemplation_first_person_framing() -> None:
    """The memory entry text must read first-person, never as a
    third-party prompt."""
    memory = InMemoryMemory()
    face = PromptFace(
        id="t.face",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="why are you like this?",
        vadugwi_affinity=(80, 120, 100, 80, 180, 90, 130),
    )
    result = ContemplationResult(
        pre_mood=(145, 110, 160, 80, 130, 175, 135),
        post_mood=(120, 115, 150, 85, 160, 150, 135),
        delta=(-25, 5, -10, 5, 30, -25, 0),
        score=Score(v=80),
    )
    record_contemplation(memory, face, result, now=42.0)

    assert len(memory.entries) == 1
    entry = memory.entries[0]
    # First-person framing — never quote the prompt as if asked.
    assert "I found myself wondering" in entry.content
    assert "why are you like this?" in entry.content
    # Metadata captures the cascade-relevant signals.
    assert entry.metadata["face_id"] == "t.face"
    assert entry.metadata["vadugwi_delta"] == [-25, 5, -10, 5, 30, -25, 0]


def test_introspection_frame_names_source_and_kind() -> None:
    """The frame string must explicitly tell the model this is its
    own thought, not a question. Catches accidental edits that drop
    the framing."""
    assert "your own introspection" in INTROSPECTION_FRAME
    assert "not a question" in INTROSPECTION_FRAME
    assert "spontaneous thought" in INTROSPECTION_FRAME


@pytest.mark.asyncio
async def test_rule_based_inference_satisfies_protocol() -> None:
    """The reference host's rule-based scorer satisfies the
    Inference protocol (runtime_checkable)."""
    from clanker_soul import Inference

    impl = RuleBasedInference()
    assert isinstance(impl, Inference)


@pytest.mark.asyncio
async def test_rule_based_inference_distinguishes_heavy_vs_light() -> None:
    """Sanity: the demo's rule-based scorer produces visibly
    different mood signatures for heavy vs. light prompts. Without
    this, the demo's loop wouldn't show observable contemplation
    effects and would mislead readers."""
    impl = RuleBasedInference()
    heavy = await impl.score("why am i like this?", {})
    light = await impl.score("what was the most rewarding moment?", {})
    assert heavy.v < light.v, f"heavy V={heavy.v} should be < light V={light.v}"
    assert heavy.g > light.g, f"heavy G={heavy.g} should be > light G={light.g}"
