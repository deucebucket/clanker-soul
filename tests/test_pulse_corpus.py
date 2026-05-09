"""Tests for ``clanker_soul.pulse.corpus`` — M3.1 PromptCorpus + sampler.

Pure-data tests; no engine wiring, no persistence. M3.2 / M3.3 / M3.4
add their own integration tests on top of these.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from clanker_soul.pulse.corpus import (
    PromptCorpus,
    PromptFace,
    RecencyLog,
    VadugwiPredicate,
    default_tags_from_metrics,
    motif_bias,
    novelty,
    vadugwi_affinity,
)
from clanker_soul.pulse.triggers import Trigger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default soul vector (matches SoulState defaults)
SOUL_DEFAULT = {
    "v": 145,
    "a": 110,
    "d": 160,
    "u": 80,
    "g": 130,
    "w": 175,
    "i": 135,
}

# A neutral mood — copy of soul, no event bias
MOOD_NEUTRAL: list[int] = [145, 110, 160, 80, 130, 175, 135]

# Distress mood — V dropped, W dropped
MOOD_DISTRESS: list[int] = [80, 130, 110, 70, 100, 110, 100]


def _trigger(kind: str, mood: list[int] | None = None, metrics: dict | None = None) -> Trigger:
    return Trigger(
        kind=kind,
        soul=dict(SOUL_DEFAULT),
        mood=mood,
        metrics=metrics or {},
    )


def _face(id: str, **kwargs) -> PromptFace:
    """Build a PromptFace with sensible defaults for tests."""
    defaults = dict(
        id=id,
        trigger_kinds=frozenset({"distress"}),
        template="test prompt",
    )
    defaults.update(kwargs)
    return PromptFace(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VadugwiPredicate
# ---------------------------------------------------------------------------


class TestVadugwiPredicate:
    def test_validates_dim(self):
        with pytest.raises(ValueError, match="dim"):
            VadugwiPredicate(dim="X", op=">=", value=100)

    def test_validates_op(self):
        with pytest.raises(ValueError, match="op"):
            VadugwiPredicate(dim="V", op="!=", value=100)

    def test_validates_layer(self):
        with pytest.raises(ValueError, match="layer"):
            VadugwiPredicate(dim="V", op=">=", value=100, layer="dream")

    def test_validates_value_range(self):
        with pytest.raises(ValueError, match="value"):
            VadugwiPredicate(dim="V", op=">=", value=300)
        with pytest.raises(ValueError, match="value"):
            VadugwiPredicate(dim="V", op=">=", value=-1)

    def test_evaluate_mood_layer_basic_ops(self):
        # V is 80 in MOOD_DISTRESS
        p_lt = VadugwiPredicate(dim="V", op="<=", value=100)
        p_gt = VadugwiPredicate(dim="V", op=">=", value=100)
        assert p_lt.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is True
        assert p_gt.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is False

    def test_evaluate_strict_inequality(self):
        # V = 80 → V<80 false, V<81 true
        p_strict = VadugwiPredicate(dim="V", op="<", value=80)
        p_strict_yes = VadugwiPredicate(dim="V", op="<", value=81)
        assert p_strict.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is False
        assert p_strict_yes.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is True

    def test_evaluate_equality(self):
        p_eq = VadugwiPredicate(dim="V", op="==", value=80)
        assert p_eq.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is True
        assert p_eq.evaluate(MOOD_NEUTRAL, SOUL_DEFAULT) is False

    def test_evaluate_soul_layer(self):
        # Soul W = 175 in defaults
        p = VadugwiPredicate(dim="W", op=">=", value=170, layer="soul")
        assert p.evaluate(None, SOUL_DEFAULT) is True
        # Soul that doesn't carry the dim → False (returns None internally)
        p_missing = VadugwiPredicate(dim="W", op=">=", value=100, layer="soul")
        assert p_missing.evaluate(None, {}) is False

    def test_evaluate_mood_none_returns_false(self):
        # Mood-layer predicate with no mood vector — face is ineligible.
        p = VadugwiPredicate(dim="V", op=">=", value=100, layer="mood")
        assert p.evaluate(None, SOUL_DEFAULT) is False

    def test_evaluate_primed_falls_back_to_mood(self):
        """``primed`` layer with no primed vector reuses ``mood``."""
        p = VadugwiPredicate(dim="V", op="<=", value=100, layer="primed")
        assert p.evaluate(MOOD_DISTRESS, SOUL_DEFAULT) is True

    def test_evaluate_primed_uses_primed_when_provided(self):
        primed = list(MOOD_NEUTRAL)
        primed[0] = 200  # very high V
        p = VadugwiPredicate(dim="V", op=">=", value=180, layer="primed")
        assert p.evaluate(MOOD_DISTRESS, SOUL_DEFAULT, primed) is True
        # And mood layer still reads MOOD_DISTRESS V=80
        p_mood = VadugwiPredicate(dim="V", op=">=", value=180, layer="mood")
        assert p_mood.evaluate(MOOD_DISTRESS, SOUL_DEFAULT, primed) is False

    def test_margin_grows_with_distance(self):
        p = VadugwiPredicate(dim="W", op=">=", value=150)
        # W = 175 → margin 25
        # W = 200 → margin 50
        assert p.margin([0, 0, 0, 0, 0, 175, 0], SOUL_DEFAULT) == 25
        assert p.margin([0, 0, 0, 0, 0, 200, 0], SOUL_DEFAULT) == 50

    def test_margin_zero_when_unsatisfied(self):
        p = VadugwiPredicate(dim="W", op=">=", value=150)
        assert p.margin([0, 0, 0, 0, 0, 100, 0], SOUL_DEFAULT) == 0

    def test_margin_for_lt_op(self):
        p = VadugwiPredicate(dim="V", op="<=", value=100)
        # V = 80 → margin 20 (we're 20 below the threshold)
        assert p.margin(MOOD_DISTRESS, SOUL_DEFAULT) == 20

    def test_margin_for_eq_op_is_zero(self):
        p = VadugwiPredicate(dim="V", op="==", value=80)
        assert p.margin(MOOD_DISTRESS, SOUL_DEFAULT) == 0


# ---------------------------------------------------------------------------
# PromptFace
# ---------------------------------------------------------------------------


class TestPromptFace:
    def test_validates_id(self):
        with pytest.raises(ValueError, match="id"):
            PromptFace(id="", trigger_kinds=frozenset({"distress"}), template="x")

    def test_validates_trigger_kinds(self):
        with pytest.raises(ValueError, match="trigger kind"):
            PromptFace(id="x", trigger_kinds=frozenset(), template="x")

    def test_validates_motif(self):
        with pytest.raises(ValueError, match="motif"):
            PromptFace(
                id="x",
                trigger_kinds=frozenset({"distress"}),
                template="x",
                motif="weird",
            )

    def test_validates_situation_match(self):
        with pytest.raises(ValueError, match="situation_match"):
            PromptFace(
                id="x",
                trigger_kinds=frozenset({"distress"}),
                template="x",
                situation_match="some",
            )

    def test_validates_template_nonempty(self):
        with pytest.raises(ValueError, match="template"):
            PromptFace(id="x", trigger_kinds=frozenset({"distress"}), template="")

    def test_validates_cooldown_nonnegative(self):
        with pytest.raises(ValueError, match="cooldown"):
            PromptFace(
                id="x",
                trigger_kinds=frozenset({"distress"}),
                template="x",
                cooldown_seconds=-1,
            )

    def test_validates_base_weight_nonnegative(self):
        with pytest.raises(ValueError, match="base_weight"):
            PromptFace(
                id="x",
                trigger_kinds=frozenset({"distress"}),
                template="x",
                base_weight=-0.1,
            )

    def test_situation_eligible_empty_is_universal(self):
        f = _face("x", situation_tags=frozenset())
        assert f.situation_eligible(frozenset()) is True
        assert f.situation_eligible(frozenset({"random_tag"})) is True

    def test_situation_eligible_any_match(self):
        f = _face("x", situation_tags=frozenset({"a", "b"}), situation_match="any")
        assert f.situation_eligible(frozenset({"a"})) is True
        assert f.situation_eligible(frozenset({"c"})) is False
        assert f.situation_eligible(frozenset({"b", "c"})) is True

    def test_situation_eligible_all_match(self):
        f = _face("x", situation_tags=frozenset({"a", "b"}), situation_match="all")
        assert f.situation_eligible(frozenset({"a"})) is False
        assert f.situation_eligible(frozenset({"a", "b"})) is True
        assert f.situation_eligible(frozenset({"a", "b", "c"})) is True

    def test_state_eligible_no_predicates(self):
        f = _face("x")  # no predicates
        assert f.state_eligible(MOOD_NEUTRAL, SOUL_DEFAULT) is True
        assert f.state_eligible(None, SOUL_DEFAULT) is True

    def test_state_eligible_and_combines_predicates(self):
        # Both must be satisfied
        f = _face(
            "x",
            vadugwi_predicates=(
                VadugwiPredicate("V", "<=", 100),
                VadugwiPredicate("W", "<=", 120),
            ),
        )
        assert f.state_eligible(MOOD_DISTRESS, SOUL_DEFAULT) is True
        # V=145 in MOOD_NEUTRAL — V predicate fails
        assert f.state_eligible(MOOD_NEUTRAL, SOUL_DEFAULT) is False


# ---------------------------------------------------------------------------
# RecencyLog
# ---------------------------------------------------------------------------


class TestRecencyLog:
    def test_seconds_since_returns_none_for_unfired(self):
        log = RecencyLog()
        assert log.seconds_since("nope", now=100.0) is None

    def test_note_fired_records_time_and_count(self):
        log = RecencyLog()
        log.note_fired("a", now=100.0)
        log.note_fired("a", now=200.0)
        log.note_fired("b", now=150.0)
        assert log.last_fired["a"] == 200.0
        assert log.last_fired["b"] == 150.0
        assert log.fire_counts == {"a": 2, "b": 1}
        assert log.seconds_since("a", now=300.0) == 100.0


# ---------------------------------------------------------------------------
# vadugwi_affinity
# ---------------------------------------------------------------------------


class TestVadugwiAffinity:
    def test_no_predicates_returns_one(self):
        assert vadugwi_affinity((), MOOD_NEUTRAL, SOUL_DEFAULT) == 1.0

    def test_at_boundary_returns_one(self):
        # W>=175, mood W=175 → margin 0 → 1.0
        p = VadugwiPredicate("W", ">=", 175)
        assert vadugwi_affinity((p,), MOOD_NEUTRAL, SOUL_DEFAULT) == 1.0

    def test_at_extreme_approaches_two(self):
        # W>=130, mood W=255 → margin 125 / max 125 = 1.0 → contribution 2.0
        p = VadugwiPredicate("W", ">=", 130)
        mood = list(MOOD_NEUTRAL)
        mood[5] = 255
        assert vadugwi_affinity((p,), mood, SOUL_DEFAULT) == pytest.approx(2.0)

    def test_averages_across_multiple_predicates(self):
        # Two preds, one at boundary (1.0) one at extreme (2.0) → avg 1.5
        p_boundary = VadugwiPredicate("W", ">=", 175)  # mood W=175, margin 0
        p_extreme = VadugwiPredicate("V", "<=", 145)  # mood V=145, margin 0
        # Both at boundary → 1.0
        result = vadugwi_affinity(
            (p_boundary, p_extreme),
            MOOD_NEUTRAL,
            SOUL_DEFAULT,
        )
        assert result == pytest.approx(1.0)

        # Now bump V down to 0 — V predicate margin 145, max margin 145 → 2.0
        mood = list(MOOD_NEUTRAL)
        mood[0] = 0
        result = vadugwi_affinity(
            (p_boundary, p_extreme),
            mood,
            SOUL_DEFAULT,
        )
        assert result == pytest.approx(1.5)

    def test_eq_op_is_neutral(self):
        # == predicate has no ramp; margin always 0; contributes 1.0
        p = VadugwiPredicate("V", "==", 145)
        assert vadugwi_affinity((p,), MOOD_NEUTRAL, SOUL_DEFAULT) == 1.0


# ---------------------------------------------------------------------------
# novelty
# ---------------------------------------------------------------------------


class TestNovelty:
    def test_unfired_face_is_one(self):
        log = RecencyLog()
        assert novelty("x", cooldown_seconds=60, recency=log, now=100.0) == 1.0

    def test_zero_cooldown_is_always_one(self):
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        assert novelty("x", cooldown_seconds=0, recency=log, now=101.0) == 1.0

    def test_in_cooldown_is_zero(self):
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        # 30s after fire, cooldown 60s → still in cooldown
        assert novelty("x", cooldown_seconds=60, recency=log, now=130.0) == 0.0

    def test_just_out_of_cooldown_starts_at_zero(self):
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        # exactly cooldown_seconds elapsed → ramp starts at 0
        result = novelty("x", cooldown_seconds=60, recency=log, now=160.0)
        assert result == pytest.approx(0.0)

    def test_after_double_cooldown_is_one(self):
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        # 2 * cooldown elapsed → ramp == 1.0
        result = novelty("x", cooldown_seconds=60, recency=log, now=220.0)
        assert result == pytest.approx(1.0)

    def test_ramp_is_monotonic(self):
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        a = novelty("x", cooldown_seconds=60, recency=log, now=170.0)
        b = novelty("x", cooldown_seconds=60, recency=log, now=190.0)
        assert b > a


# ---------------------------------------------------------------------------
# motif_bias
# ---------------------------------------------------------------------------


class TestMotifBias:
    def test_informational_is_neutral(self):
        assert motif_bias("informational", MOOD_NEUTRAL, SOUL_DEFAULT) == 1.0
        assert motif_bias("informational", MOOD_DISTRESS, SOUL_DEFAULT) == 1.0

    def test_relational_lifts_when_v_gap_and_w_dipped(self):
        # MOOD_DISTRESS: V=80 vs soul V=145 (gap 65), W=110 vs soul W=175 (gap 65)
        bias = motif_bias("relational", MOOD_DISTRESS, SOUL_DEFAULT)
        assert bias > 1.5

    def test_relational_neutral_when_at_baseline(self):
        assert motif_bias("relational", MOOD_NEUTRAL, SOUL_DEFAULT) == 1.0

    def test_exploratory_lifts_when_curious(self):
        mood = [150, 150, 160, 80, 130, 175, 140]  # high V, high A
        bias = motif_bias("exploratory", mood, SOUL_DEFAULT)
        assert bias >= 1.5

    def test_regulatory_lifts_when_overheated(self):
        mood = [120, 200, 100, 200, 100, 100, 150]  # extreme A + U
        bias = motif_bias("regulatory", mood, SOUL_DEFAULT)
        assert bias >= 1.5

    def test_no_mood_returns_one(self):
        # All non-informational motifs return 1.0 when mood is None
        for m in ("relational", "exploratory", "regulatory"):
            assert motif_bias(m, None, SOUL_DEFAULT) == 1.0


# ---------------------------------------------------------------------------
# PromptCorpus
# ---------------------------------------------------------------------------


class TestPromptCorpus:
    def test_empty_corpus_yields_no_eligible(self):
        c = PromptCorpus(())
        assert c.faces_for(_trigger("distress", MOOD_DISTRESS)) == []
        assert c.sample(_trigger("distress", MOOD_DISTRESS)) is None

    def test_duplicate_id_raises(self):
        with pytest.raises(ValueError, match="duplicate"):
            PromptCorpus((_face("a"), _face("a")))

    def test_filters_by_trigger_kind(self):
        f1 = _face("d1", trigger_kinds=frozenset({"distress"}))
        f2 = _face("e1", trigger_kinds=frozenset({"elation"}))
        c = PromptCorpus((f1, f2))
        result = c.faces_for(_trigger("distress", MOOD_DISTRESS))
        assert [f.id for f, _ in result] == ["d1"]

    def test_filters_by_predicate(self):
        # Eligible only when V <= 100
        f = _face(
            "x",
            vadugwi_predicates=(VadugwiPredicate("V", "<=", 100),),
        )
        c = PromptCorpus((f,))
        # MOOD_DISTRESS V=80 → eligible
        assert len(c.faces_for(_trigger("distress", MOOD_DISTRESS))) == 1
        # MOOD_NEUTRAL V=145 → ineligible
        assert c.faces_for(_trigger("distress", MOOD_NEUTRAL)) == []

    def test_filters_by_situation_tag(self):
        f = _face("x", situation_tags=frozenset({"incoming_public_stimulus"}))
        c = PromptCorpus((f,))
        trig = _trigger("distress", MOOD_DISTRESS)
        assert c.faces_for(trig, frozenset()) == []
        assert (
            len(
                c.faces_for(
                    trig,
                    frozenset({"incoming_public_stimulus"}),
                )
            )
            == 1
        )

    def test_filters_by_cooldown(self):
        f = _face("x", cooldown_seconds=60)
        c = PromptCorpus((f,))
        log = RecencyLog()
        log.note_fired("x", now=100.0)
        trig = _trigger("distress", MOOD_DISTRESS)
        # In cooldown
        assert c.faces_for(trig, recency=log, now=130.0) == []
        # Past cooldown
        assert len(c.faces_for(trig, recency=log, now=300.0)) == 1

    def test_memory_anchor_without_callback_filters_out(self):
        f = _face("x", memory_anchor="phone_curiosity")
        c = PromptCorpus((f,))
        # No callback → anchored faces are ineligible
        assert c.faces_for(_trigger("distress", MOOD_DISTRESS)) == []

    def test_memory_anchor_with_callback_yes(self):
        f = _face("x", memory_anchor="phone_curiosity")
        c = PromptCorpus((f,))
        result = c.faces_for(
            _trigger("distress", MOOD_DISTRESS),
            memory_topics_present=lambda topic: topic == "phone_curiosity",
        )
        assert len(result) == 1

    def test_memory_anchor_callback_exception_is_safe(self):
        f = _face("x", memory_anchor="bad_topic")
        c = PromptCorpus((f,))

        def boom(_):
            raise RuntimeError("memory store down")

        # Must not raise; face just becomes ineligible
        result = c.faces_for(
            _trigger("distress", MOOD_DISTRESS),
            memory_topics_present=boom,
        )
        assert result == []

    def test_sample_returns_none_when_no_eligible(self):
        f = _face("x", trigger_kinds=frozenset({"elation"}))
        c = PromptCorpus((f,))
        assert c.sample(_trigger("distress", MOOD_DISTRESS)) is None

    def test_sample_picks_among_eligible(self):
        # Two faces, both eligible — over many samples both should appear.
        f1 = _face("a", base_weight=1.0)
        f2 = _face("b", base_weight=1.0)
        c = PromptCorpus((f1, f2), rng=random.Random(42))
        seen = Counter()
        for _ in range(200):
            picked = c.sample(_trigger("distress", MOOD_DISTRESS))
            assert picked is not None
            seen[picked.id] += 1
        assert seen["a"] > 0
        assert seen["b"] > 0

    def test_sample_respects_base_weights(self):
        # 9:1 weights — over 1000 samples the heavier face should dominate.
        f_heavy = _face("heavy", base_weight=9.0)
        f_light = _face("light", base_weight=1.0)
        c = PromptCorpus((f_heavy, f_light), rng=random.Random(0))
        seen = Counter()
        for _ in range(1000):
            picked = c.sample(_trigger("distress", MOOD_DISTRESS))
            assert picked is not None
            seen[picked.id] += 1
        # Heavy should land roughly 9x as often. Loose bound to avoid
        # flakiness — just confirm direction is correct.
        assert seen["heavy"] > seen["light"] * 4

    def test_motif_bias_swings_selection(self):
        """A relational face should beat an informational face when the
        agent is in a relational shape — even at equal base weight."""
        f_info = _face("info", motif="informational", base_weight=1.0)
        f_rel = _face("rel", motif="relational", base_weight=1.0)
        c = PromptCorpus((f_info, f_rel), rng=random.Random(7))
        seen = Counter()
        for _ in range(500):
            picked = c.sample(_trigger("distress", MOOD_DISTRESS))
            assert picked is not None
            seen[picked.id] += 1
        assert seen["rel"] > seen["info"]


# ---------------------------------------------------------------------------
# default_tags_from_metrics
# ---------------------------------------------------------------------------


class TestDefaultTagsFromMetrics:
    def test_idle_long_marks_operator_silent(self):
        trig = _trigger("long_silence", metrics={"idle_seconds": 3600})
        tags = default_tags_from_metrics(trig)
        assert "operator_silent_long" in tags
        assert "autonomy_idle" in tags

    def test_short_idle_marks_post_conversation(self):
        trig = _trigger("connect_impulse", metrics={"idle_seconds": 120})
        tags = default_tags_from_metrics(trig)
        assert "post_conversation" in tags
        assert "autonomy_idle" in tags
        assert "operator_silent_long" not in tags

    def test_trauma_load_signal(self):
        trig = _trigger("trauma_pressure", metrics={"trauma_load": 250})
        tags = default_tags_from_metrics(trig)
        assert "trauma_pressure" in tags

    def test_nourishment_load_signal(self):
        trig = _trigger("gratitude", metrics={"nourishment_load": 300})
        tags = default_tags_from_metrics(trig)
        assert "sustained_care" in tags

    def test_no_metrics_yields_empty(self):
        trig = _trigger("distress")
        assert default_tags_from_metrics(trig) == frozenset()
