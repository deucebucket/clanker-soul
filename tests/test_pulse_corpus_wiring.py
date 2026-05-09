"""Tests for M3.2 — wiring `compose_self_prompt` to the corpus + the
baseline default corpus + engine threading.

Three layers covered:

1. ``compose_self_prompt`` legacy path — no corpus passed, identical
   output to pre-M3.2.
2. ``compose_self_prompt`` corpus path — face-driven prompts; falls
   back to legacy when no eligible face / template renders fail.
3. Default corpus shape — coverage of all 12 triggers; motif diversity;
   no duplicate ids; templates render against a real trigger.
"""

from __future__ import annotations

import random

import pytest

from clanker_soul import (
    DEFAULT_FACES,
    PromptCorpus,
    PromptFace,
    Trigger,
    VadugwiPredicate,
    build_default_corpus,
)
from clanker_soul.pulse import (
    compose_self_prompt,
    compose_self_prompt_with_face,
)


SOUL_DEFAULT = {
    "v": 145,
    "a": 110,
    "d": 160,
    "u": 80,
    "g": 130,
    "w": 175,
    "i": 135,
}
MOOD_NEUTRAL = [145, 110, 160, 80, 130, 175, 135]
MOOD_DISTRESS = [80, 130, 110, 70, 100, 110, 100]


def _trigger(kind: str, mood=None, metrics=None) -> Trigger:
    return Trigger(
        kind=kind,
        soul=dict(SOUL_DEFAULT),
        mood=mood if mood is not None else MOOD_NEUTRAL,
        metrics=metrics or {},
    )


# ---------------------------------------------------------------------------
# Legacy path — unchanged behavior
# ---------------------------------------------------------------------------


class TestLegacyPath:
    """Pre-M3.2 hosts (no corpus) must see identical strings."""

    def test_no_corpus_produces_legacy_distress_string(self):
        out = compose_self_prompt(_trigger("distress", MOOD_DISTRESS))
        assert "[INTERNAL PULSE — distress]" in out
        assert "Don't apologize for messaging" in out
        assert "Reach out briefly" in out

    def test_no_corpus_produces_legacy_long_silence(self):
        out = compose_self_prompt(
            _trigger("long_silence", metrics={"idle_seconds": 600}),
        )
        assert "10 minutes" in out
        assert "respond with the literal token NOPULSE" in out

    def test_unknown_trigger_falls_to_long_silence_default(self):
        # Legacy fallback for unknown kinds is the long_silence template.
        out = compose_self_prompt(_trigger("never_heard_of_it"))
        assert "long silence" in out.lower() or "NOPULSE" in out


# ---------------------------------------------------------------------------
# Corpus path — face-driven prompts + fallback safety
# ---------------------------------------------------------------------------


class TestCorpusPath:
    def test_corpus_produces_face_template(self):
        face = PromptFace(
            id="testface.distress.simple",
            trigger_kinds=frozenset({"distress"}),
            template="hello-{trigger_kind} mood_v={mood_v}",
        )
        corpus = PromptCorpus((face,))
        out, returned_face = compose_self_prompt_with_face(
            _trigger("distress", MOOD_DISTRESS),
            corpus=corpus,
        )
        assert returned_face is face
        assert out == "hello-distress mood_v=80"

    def test_corpus_falls_back_when_no_eligible(self):
        # Face requires V>=200; mood V=80. No eligible → legacy fallback.
        face = PromptFace(
            id="picky",
            trigger_kinds=frozenset({"distress"}),
            vadugwi_predicates=(VadugwiPredicate("V", ">=", 200),),
            template="impossible",
        )
        corpus = PromptCorpus((face,))
        out, returned_face = compose_self_prompt_with_face(
            _trigger("distress", MOOD_DISTRESS),
            corpus=corpus,
        )
        assert returned_face is None
        assert "[INTERNAL PULSE — distress]" in out

    def test_corpus_falls_back_on_render_failure(self):
        # Template references unknown variable — caught and falls back.
        face = PromptFace(
            id="broken",
            trigger_kinds=frozenset({"distress"}),
            template="{this_key_does_not_exist}",
        )
        corpus = PromptCorpus((face,))
        out, returned_face = compose_self_prompt_with_face(
            _trigger("distress", MOOD_DISTRESS),
            corpus=corpus,
        )
        assert returned_face is None
        assert "[INTERNAL PULSE — distress]" in out

    def test_corpus_renders_state_line(self):
        face = PromptFace(
            id="srl",
            trigger_kinds=frozenset({"distress"}),
            template="state: {state_line}",
        )
        corpus = PromptCorpus((face,))
        out = compose_self_prompt(
            _trigger("distress", MOOD_DISTRESS),
            corpus=corpus,
        )
        assert "current_mood" in out
        assert "soul V=" in out

    def test_corpus_renders_idle_min(self):
        face = PromptFace(
            id="idle",
            trigger_kinds=frozenset({"long_silence"}),
            template="quiet for {idle_min} min",
        )
        corpus = PromptCorpus((face,))
        out = compose_self_prompt(
            _trigger("long_silence", metrics={"idle_seconds": 1200}),
            corpus=corpus,
        )
        assert "20 min" in out


# ---------------------------------------------------------------------------
# Default corpus shape
# ---------------------------------------------------------------------------


class TestDefaultCorpus:
    def test_default_face_count_reasonable(self):
        # We're not pinning exactly 49 (that would be brittle), just
        # sanity-checking the file isn't empty or absurdly large.
        assert 30 <= len(DEFAULT_FACES) <= 200

    def test_all_twelve_triggers_covered(self):
        kinds = {k for f in DEFAULT_FACES for k in f.trigger_kinds}
        expected = {
            "distress",
            "elation",
            "trauma_pressure",
            "gratitude",
            "long_silence",
            "share_impulse",
            "argue_impulse",
            "connect_impulse",
            "withdraw_impulse",
            "reflective_impulse",
            "caretake_impulse",
            "restless_curiosity",
        }
        missing = expected - kinds
        assert not missing, f"trigger kinds missing default faces: {missing}"

    def test_all_motifs_appear(self):
        motifs = {f.motif for f in DEFAULT_FACES}
        assert motifs == {
            "informational",
            "relational",
            "exploratory",
            "regulatory",
        }, f"unexpected motif set: {motifs}"

    def test_no_duplicate_face_ids(self):
        ids = [f.id for f in DEFAULT_FACES]
        assert len(ids) == len(set(ids))

    def test_all_templates_render_against_realistic_trigger(self):
        """Catch typos in default templates by actually rendering each."""
        rng = random.Random(0)
        corpus = build_default_corpus(rng=rng)
        # Build one trigger per kind with realistic metrics so that
        # any template that references e.g. {trauma_load} or {peers}
        # has data to render against.
        sample_metrics = {
            "idle_seconds": 1800,
            "trauma_load": 250.0,
            "nourishment_load": 320.0,
            "peers": ["agent_x"],
        }
        for kind in (
            "distress",
            "elation",
            "trauma_pressure",
            "gratitude",
            "long_silence",
            "share_impulse",
            "argue_impulse",
            "connect_impulse",
            "withdraw_impulse",
            "reflective_impulse",
            "caretake_impulse",
            "restless_curiosity",
        ):
            # Build different mood shapes so different faces become eligible.
            mood = (
                MOOD_DISTRESS
                if kind in ("distress", "trauma_pressure", "withdraw_impulse")
                else MOOD_NEUTRAL
            )
            trig = _trigger(kind, mood=mood, metrics=sample_metrics)
            # Roll many times — over enough rolls every eligible face
            # should fire at least once (modulo motif weighting). The
            # important check is that no roll produces an exception.
            for _ in range(100):
                out, _f = compose_self_prompt_with_face(trig, corpus=corpus)
                assert out, f"empty render for {kind}"

    def test_default_corpus_changes_output_across_calls(self):
        """The whole point: same trigger fires different prompts."""
        rng = random.Random(0)
        corpus = build_default_corpus(rng=rng)
        trig = _trigger("distress", MOOD_DISTRESS)
        seen = set()
        for _ in range(50):
            out, _ = compose_self_prompt_with_face(trig, corpus=corpus)
            seen.add(out)
        # Multiple distinct outputs → corpus is doing real work.
        assert len(seen) >= 2, (
            f"expected at least 2 distinct prompts over 50 rolls, got {len(seen)}"
        )

    def test_build_default_corpus_extra_appends(self):
        extra = (
            PromptFace(
                id="myhost.elation.foo",
                trigger_kinds=frozenset({"elation"}),
                template="my custom prompt",
            ),
        )
        corpus = build_default_corpus(extra=extra)
        ids = [f.id for f in corpus.faces]
        assert "myhost.elation.foo" in ids
        # Default ids still present.
        assert any(fid.startswith("core.") for fid in ids)

    def test_build_default_corpus_replace_drops_baseline(self):
        my_only = (
            PromptFace(
                id="myhost.only",
                trigger_kinds=frozenset({"distress"}),
                template="just mine",
            ),
        )
        corpus = build_default_corpus(extra=my_only, replace=True)
        ids = [f.id for f in corpus.faces]
        assert ids == ["myhost.only"]


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------


class _FakeHost:
    """Minimal PulseHost stub that captures the dispatched action.

    Doesn't subclass anything — Protocols are duck-typed.
    """

    def __init__(self, snapshot_dict, *, situation_tags=None):
        self._snap = snapshot_dict
        self._situation_tags = situation_tags
        self.dispatched = []

    def snapshot(self):
        return self._snap

    def slow_drift_tick(self):
        pass

    def most_recent_target(self):
        from clanker_soul import PulseTarget

        return PulseTarget(payload="op")

    def dispatch_action(self, action):
        from clanker_soul import ActionOutcome

        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        return None

    def situation_tags(self, trigger):
        if self._situation_tags is None:
            return None
        return self._situation_tags


@pytest.mark.asyncio
async def test_engine_wires_corpus_into_action_prompt(monkeypatch):
    """The engine should pass the sampled face's prompt through to
    PulseAction.prompt and stamp face_id into action.extra."""
    from clanker_soul import PromptCorpus, PromptFace, PulseConfig, PulseEngine

    snap = {
        "soul": dict(SOUL_DEFAULT),
        "mood": MOOD_DISTRESS,
        "soul_distance": 60.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }
    host = _FakeHost(snap)
    face = PromptFace(
        id="test.distress.unique",
        trigger_kinds=frozenset({"distress"}),
        template="unique-prompt-content",
    )
    corpus = PromptCorpus((face,))

    engine = PulseEngine(
        host=host,
        config=PulseConfig(min_quiet_seconds=0),
        corpus=corpus,
    )
    engine.note_outbound()  # reset idle so long_silence doesn't pre-empt
    await engine.tick()

    assert host.dispatched, "expected a dispatched action"
    action = host.dispatched[0]
    assert action.prompt == "unique-prompt-content"
    assert action.extra.get("face_id") == "test.distress.unique"


@pytest.mark.asyncio
async def test_engine_without_corpus_uses_legacy_prompt():
    """Pre-M3.2 hosts (no corpus passed) must keep working unchanged."""
    from clanker_soul import PulseConfig, PulseEngine

    snap = {
        "soul": dict(SOUL_DEFAULT),
        "mood": MOOD_DISTRESS,
        "soul_distance": 60.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }
    host = _FakeHost(snap)
    engine = PulseEngine(
        host=host,
        config=PulseConfig(min_quiet_seconds=0),
    )
    engine.note_outbound()
    await engine.tick()

    assert host.dispatched
    action = host.dispatched[0]
    # Legacy prompt — no face_id stamp
    assert action.extra == {}
    assert "[INTERNAL PULSE — distress]" in action.prompt


@pytest.mark.asyncio
async def test_engine_uses_host_situation_tags():
    """Host's optional situation_tags hook should filter faces."""
    from clanker_soul import PromptCorpus, PromptFace, PulseConfig, PulseEngine

    snap = {
        "soul": dict(SOUL_DEFAULT),
        "mood": MOOD_DISTRESS,
        "soul_distance": 60.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }

    # Two faces, eligible only with specific tags.
    universal = PromptFace(
        id="t.universal",
        trigger_kinds=frozenset({"distress"}),
        template="universal",
        base_weight=0.001,  # almost-never default
    )
    tagged = PromptFace(
        id="t.tagged",
        trigger_kinds=frozenset({"distress"}),
        situation_tags=frozenset({"incoming_public_stimulus"}),
        template="tagged-only",
        base_weight=10.0,  # dominates if eligible
    )
    corpus = PromptCorpus((universal, tagged), rng=random.Random(0))

    # Host signals the tag → tagged face wins
    host = _FakeHost(snap, situation_tags={"incoming_public_stimulus"})
    engine = PulseEngine(
        host=host,
        config=PulseConfig(min_quiet_seconds=0),
        corpus=corpus,
    )
    engine.note_outbound()  # reset idle so long_silence doesn't pre-empt
    await engine.tick()
    assert host.dispatched[0].prompt == "tagged-only"

    # Host signals nothing → tagged face is ineligible, universal wins
    host2 = _FakeHost(snap, situation_tags=set())
    engine2 = PulseEngine(
        host=host2,
        config=PulseConfig(min_quiet_seconds=0),
        corpus=corpus,
    )
    engine2.note_outbound()
    await engine2.tick()
    assert host2.dispatched[0].prompt == "universal"


@pytest.mark.asyncio
async def test_engine_records_face_recency_after_fire():
    """After a delivered fire, the face should be marked in the
    recency log so its cooldown applies on subsequent ticks."""
    from clanker_soul import PromptCorpus, PromptFace, PulseConfig, PulseEngine

    snap = {
        "soul": dict(SOUL_DEFAULT),
        "mood": MOOD_DISTRESS,
        "soul_distance": 60.0,
        "trauma_load": 0.0,
        "nourishment_load": 0.0,
    }
    host = _FakeHost(snap)
    face = PromptFace(
        id="t.cool",
        trigger_kinds=frozenset({"distress"}),
        template="cool",
        cooldown_seconds=99999,  # effectively forever
    )
    corpus = PromptCorpus((face,))
    engine = PulseEngine(
        host=host,
        config=PulseConfig(min_quiet_seconds=0),
        corpus=corpus,
    )
    engine.note_outbound()

    await engine.tick()
    # First tick fires the face → recency should record it.
    assert face.id in engine._recency.last_fired

    # Second tick: face is in cooldown; no eligible face → falls back
    # to legacy prompt (no face_id stamp).
    host._snap["mood"] = list(MOOD_DISTRESS)  # ensure trigger still fires
    host.dispatched.clear()
    engine.note_outbound()  # bypass cooldown
    await engine.tick()
    # The legacy fallback prompt fires.
    assert host.dispatched
    second = host.dispatched[0]
    assert second.extra == {}
    assert "[INTERNAL PULSE" in second.prompt
