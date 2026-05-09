"""Tests for M3.4 — branch trees + memory anchors via PulseHost.

Three layers covered:

1. ``branch_bias`` math + ``faces_for`` plumbing — a face whose
   ``branch_keys`` includes the previous face id gets a moderate weight
   bump; faces with no branch_keys or unmatched parents see no change.
2. Engine wiring — ``PulseEngine`` tracks the last delivered face id
   and threads it into the next sample call. Construction can
   pre-populate it via ``previous_face_id=`` so branches survive process
   restart. ``SoulPlugin.most_recent_face_id()`` reads the freshest
   dispatched ``pulse_log`` row.
3. Memory anchors — faces with ``memory_anchor`` set are filtered out
   when no host callback is supplied OR the callback returns False.
   With the callback returning True, anchored faces enter the dice.
"""
from __future__ import annotations

import asyncio
import random

import pytest

from clanker_soul import (
    ActionOutcome,
    PromptCorpus,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    SoulPlugin,
    SoulStore,
    Trigger,
)
from clanker_soul.pulse.corpus import branch_bias


SOUL_DEFAULT = {
    "v": 145, "a": 110, "d": 160, "u": 80,
    "g": 130, "w": 175, "i": 135,
}
MOOD_DISTRESS = [80, 130, 110, 70, 100, 110, 100]


def _trigger(kind: str = "distress", mood=None) -> Trigger:
    return Trigger(
        kind=kind,
        soul=dict(SOUL_DEFAULT),
        mood=mood if mood is not None else MOOD_DISTRESS,
        metrics={},
    )


def _face(
    face_id: str,
    *,
    trigger: str = "distress",
    template: str = "x",
    base_weight: float = 1.0,
    cooldown: int = 0,
    branch_keys: frozenset[str] | None = None,
    memory_anchor: str | None = None,
) -> PromptFace:
    return PromptFace(
        id=face_id,
        trigger_kinds=frozenset({trigger}),
        cooldown_seconds=cooldown,
        base_weight=base_weight,
        motif="informational",
        template=template,
        branch_keys=branch_keys or frozenset(),
        memory_anchor=memory_anchor,
    )


# ---------------------------------------------------------------------------
# branch_bias math
# ---------------------------------------------------------------------------


class TestBranchBias:
    def test_no_previous_returns_one(self):
        face = _face("a", branch_keys=frozenset({"x"}))
        assert branch_bias(face, None) == 1.0

    def test_no_branch_keys_returns_one(self):
        face = _face("a")  # branch_keys is empty by default
        assert branch_bias(face, "anything") == 1.0

    def test_unmatched_parent_returns_one(self):
        face = _face("a", branch_keys=frozenset({"x", "y"}))
        assert branch_bias(face, "z") == 1.0

    def test_matched_parent_returns_bump(self):
        face = _face("a", branch_keys=frozenset({"x", "y"}))
        assert branch_bias(face, "x") == 1.5
        assert branch_bias(face, "y") == 1.5


# ---------------------------------------------------------------------------
# Sampler wiring — branch_bias swings the dice
# ---------------------------------------------------------------------------


class TestSamplerBranchBias:
    def test_child_face_weight_bumped_by_parent(self):
        parent = _face("p")
        child = _face("c", branch_keys=frozenset({"p"}))
        independent = _face("i")
        corpus = PromptCorpus([parent, child, independent])

        # Without a previous face, all three weigh equally.
        weights_no_parent = {
            f.id: w for f, w in corpus.faces_for(_trigger())
        }
        assert weights_no_parent["c"] == pytest.approx(weights_no_parent["i"])

        # With parent = "p", child gets the 1.5× bump; independent doesn't.
        weights_with_parent = {
            f.id: w for f, w in corpus.faces_for(
                _trigger(), previous_face_id="p",
            )
        }
        assert weights_with_parent["c"] == pytest.approx(
            weights_with_parent["i"] * 1.5
        )
        assert weights_with_parent["i"] == pytest.approx(weights_no_parent["i"])

    def test_branch_bonus_visible_in_distribution(self):
        parent = _face("p")
        child = _face("c", branch_keys=frozenset({"p"}))
        sibling = _face("s")
        # Use a deterministic RNG so the test is stable.
        rng = random.Random(42)
        corpus = PromptCorpus([parent, child, sibling], rng=rng)
        N = 800
        counts = {"c": 0, "s": 0, "p": 0}
        for _ in range(N):
            # Use a fresh RNG seed each call so the dice rolls vary —
            # the corpus's _rng draws fresh values each call.
            face = corpus.sample(_trigger(), previous_face_id="p")
            assert face is not None
            counts[face.id] += 1
        # Child should be picked meaningfully more often than sibling.
        # With 1.5× bump and roughly equal pre-bias weights:
        # P(c) ≈ 1.5/3.5 ≈ 0.428; P(s) ≈ 1.0/3.5 ≈ 0.286
        # Allow ±10% tolerance for sampling variance.
        assert counts["c"] > counts["s"], (
            f"child should win more rolls than sibling: {counts}"
        )
        # Rough proportionality check.
        ratio = counts["c"] / max(counts["s"], 1)
        assert 1.2 < ratio < 1.9, f"expected ~1.5× ratio, got {ratio}"


# ---------------------------------------------------------------------------
# Memory anchors
# ---------------------------------------------------------------------------


class TestMemoryAnchors:
    def test_anchor_without_callback_filters_out(self):
        anchored = _face("a", memory_anchor="topic.x")
        plain = _face("p")
        corpus = PromptCorpus([anchored, plain])
        # No callback → anchored face is ineligible.
        eligible = {f.id for f, _ in corpus.faces_for(_trigger())}
        assert "a" not in eligible
        assert "p" in eligible

    def test_anchor_with_callback_returning_false_filters_out(self):
        anchored = _face("a", memory_anchor="topic.x")
        plain = _face("p")
        corpus = PromptCorpus([anchored, plain])
        eligible = {
            f.id for f, _ in corpus.faces_for(
                _trigger(),
                memory_topics_present=lambda topic: False,
            )
        }
        assert "a" not in eligible
        assert "p" in eligible

    def test_anchor_with_callback_returning_true_eligible(self):
        anchored = _face("a", memory_anchor="topic.x")
        plain = _face("p")
        corpus = PromptCorpus([anchored, plain])
        eligible = {
            f.id for f, _ in corpus.faces_for(
                _trigger(),
                memory_topics_present=lambda topic: True,
            )
        }
        assert "a" in eligible
        assert "p" in eligible

    def test_anchor_callback_raises_face_filtered(self):
        anchored = _face("a", memory_anchor="topic.x")
        corpus = PromptCorpus([anchored])

        def boom(_topic: str) -> bool:
            raise RuntimeError("memory backend down")

        # Callback raises → face is filtered out, sampler doesn't crash.
        eligible = list(corpus.faces_for(
            _trigger(), memory_topics_present=boom,
        ))
        assert eligible == []


# ---------------------------------------------------------------------------
# PulseEngine — _previous_face_id tracking
# ---------------------------------------------------------------------------


class _Host:
    def __init__(self, snap: dict):
        self._snap = snap
        self.dispatched: list[PulseAction] = []

    def snapshot(self) -> dict:
        return dict(self._snap)

    def slow_drift_tick(self) -> None:
        return None

    def most_recent_target(self) -> PulseTarget | None:
        return PulseTarget(payload={"x": 1})

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        return None

    async def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        return True


def _distress_snap() -> dict:
    return {
        "soul": dict(SOUL_DEFAULT),
        "mood": list(MOOD_DISTRESS),
        "soul_distance": 70.0,
        "trauma_load": 5.0,
        "nourishment_load": 0.0,
    }


class TestEnginePreviousFaceId:
    def test_engine_remembers_last_delivered_face(self):
        # Pin the corpus to a single face so the dice has nothing to
        # decide — we just need to verify that whatever fires is what
        # the engine remembers.
        only = _face("only", base_weight=1.0)
        corpus = PromptCorpus([only])
        host = _Host(_distress_snap())
        engine = PulseEngine(
            host, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            corpus=corpus,
        )
        # Pre-tick the previous face id is whatever was passed (None
        # by default).
        assert engine._previous_face_id is None
        engine.note_outbound()
        asyncio.run(engine.tick())
        # After delivered fire, engine should remember the face.
        assert host.dispatched[0].extra.get("face_id") == "only"
        assert engine._previous_face_id == "only"

    def test_constructor_previous_face_id_threads_through(self, tmp_path):
        parent = _face("parent", base_weight=0.0001)  # near-zero so it never fires
        child = _face("child", branch_keys=frozenset({"parent"}))
        sibling = _face("sibling")
        corpus = PromptCorpus([parent, child, sibling], rng=random.Random(0))

        host = _Host(_distress_snap())
        engine = PulseEngine(
            host, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            corpus=corpus,
            previous_face_id="parent",  # restored from disk
        )
        engine.note_outbound()
        # The engine should pass previous_face_id="parent" into the
        # sampler, giving child a 1.5× bump. Run many ticks to observe.
        # But note: each delivered fire updates _previous_face_id, so
        # only the FIRST tick uses our seeded value.
        wins = {"child": 0, "sibling": 0}
        for _ in range(50):
            host_local = _Host(_distress_snap())
            engine_local = PulseEngine(
                host_local, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                corpus=corpus,
                previous_face_id="parent",
            )
            engine_local.note_outbound()
            asyncio.run(engine_local.tick())
            face_id = host_local.dispatched[0].extra.get("face_id")
            if face_id in wins:
                wins[face_id] += 1
        # Child should win meaningfully more often (1.5× bias).
        assert wins["child"] > wins["sibling"], (
            f"child should win more often with branch bonus: {wins}"
        )

    def test_undelivered_attempts_dont_update_previous_face(self):
        # An undelivered fire (target missing or gate denies) must NOT
        # poison the branch chain.
        parent = _face("parent")
        corpus = PromptCorpus([parent])

        class _NoTargetHost(_Host):
            def most_recent_target(self):
                return None

        host = _NoTargetHost(_distress_snap())
        engine = PulseEngine(
            host, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            corpus=corpus, previous_face_id="seeded",
        )
        engine.note_outbound()
        asyncio.run(engine.tick())
        # No target → no dispatch → previous_face_id should still be "seeded".
        assert engine._previous_face_id == "seeded"


# ---------------------------------------------------------------------------
# SoulPlugin.most_recent_face_id
# ---------------------------------------------------------------------------


class TestPluginMostRecentFaceId:
    def test_empty_log_returns_none(self, tmp_path):
        with SoulPlugin(agent_id="m", db_path=tmp_path / "m.db") as plugin:
            assert plugin.most_recent_face_id() is None

    def test_after_dispatch_returns_face_id(self, tmp_path):
        db = tmp_path / "m2.db"
        host = _Host(_distress_snap())
        with SoulPlugin(agent_id="m2", db_path=db) as plugin:
            engine = PulseEngine(
                host, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                event_log=plugin.event_log, agent_id="m2",
                corpus=plugin.corpus, recency=plugin.recency,
                physics=plugin.physics,
            )
            engine.note_outbound()
            asyncio.run(engine.tick())
            fired_id = host.dispatched[0].extra.get("face_id")
            assert fired_id is not None
            # Round-trip via SoulPlugin.most_recent_face_id().
            assert plugin.most_recent_face_id() == fired_id

    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "m3.db"
        host = _Host(_distress_snap())
        with SoulPlugin(agent_id="m3", db_path=db) as plugin:
            engine = PulseEngine(
                host, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                event_log=plugin.event_log, agent_id="m3",
                corpus=plugin.corpus, recency=plugin.recency,
                physics=plugin.physics,
            )
            engine.note_outbound()
            asyncio.run(engine.tick())
            fired_id = host.dispatched[0].extra.get("face_id")

        SoulStore._instances.pop(str(db), None)
        with SoulPlugin(agent_id="m3", db_path=db) as plugin2:
            assert plugin2.most_recent_face_id() == fired_id
            # And hosts can use this to seed engine state.
            engine2 = PulseEngine(
                _Host(_distress_snap()),
                PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                corpus=plugin2.corpus, recency=plugin2.recency,
                previous_face_id=plugin2.most_recent_face_id(),
            )
            assert engine2._previous_face_id == fired_id
