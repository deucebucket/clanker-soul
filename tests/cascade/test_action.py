"""Roll 2 + Roll 3 action registry tests."""

from __future__ import annotations

import random

import pytest

from clanker_soul import ActionOutcome, PulseConfig, RecencyLog, Score, SoulState
from clanker_soul.cascade import (
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    RegisteredAction,
    confide_proxy_score,
    mistake_aware_tags,
    should_act,
    tags_from_delta,
)


def _handler(ctx: CascadeActionContext) -> ActionOutcome:
    return ActionOutcome(delivered=True)


def _action(
    name: str,
    *,
    tags: frozenset[str] = frozenset({"reflect"}),
    affinity: tuple[int, int, int, int, int, int, int] | None = None,
    cost: int = 1,
    cooldown_seconds: int = 300,
) -> RegisteredAction:
    return RegisteredAction(
        name=name,
        tags=tags,
        handler=_handler,
        vadugwi_affinity=affinity,
        cost=cost,
        cooldown_seconds=cooldown_seconds,
    )


def test_registry_register_and_filter_by_tags() -> None:
    registry = ActionRegistry()
    registry.register(_action("journal", tags=frozenset({"reflect", "private"})))
    registry.register(_action("post", tags=frozenset({"share"})))

    assert [a.name for a in registry.filter({"reflect"})] == ["journal"]
    assert [a.name for a in registry.filter({"share", "private"})] == ["journal", "post"]
    assert registry.filter({"missing"}) == []


def test_registry_rejects_duplicate_names() -> None:
    registry = ActionRegistry([_action("journal")])

    with pytest.raises(ValueError, match="duplicate action name"):
        registry.register(_action("journal"))


def test_sample_is_deterministic_with_seeded_rng() -> None:
    registry = ActionRegistry(
        [
            _action("a", tags=frozenset({"reflect"}), cooldown_seconds=0),
            _action("b", tags=frozenset({"reflect"}), cooldown_seconds=0),
        ]
    )

    first = registry.sample(
        {"reflect"},
        soul=SoulState(),
        recency=RecencyLog(),
        rng=random.Random(4),
    )
    second = registry.sample(
        {"reflect"},
        soul=SoulState(),
        recency=RecencyLog(),
        rng=random.Random(4),
    )

    assert first is not None
    assert second is not None
    assert first.name == second.name


def test_sample_weights_toward_soul_affinity() -> None:
    registry = ActionRegistry(
        [
            _action(
                "fits",
                affinity=(145, 110, 160, 80, 130, 175, 135),
                cooldown_seconds=0,
            ),
            _action(
                "far",
                affinity=(0, 255, 0, 255, 255, 0, 255),
                cooldown_seconds=0,
            ),
        ]
    )
    rng = random.Random(2)

    picks = [
        registry.sample({"reflect"}, soul=SoulState(), recency=RecencyLog(), rng=rng).name
        for _ in range(200)
    ]

    assert picks.count("fits") > picks.count("far")


def test_recency_suppresses_recently_fired_action() -> None:
    registry = ActionRegistry(
        [
            _action("recent", cooldown_seconds=100),
            _action("fresh", cooldown_seconds=100),
        ]
    )
    recency = RecencyLog()
    recency.note_fired("recent", 1000.0)

    chosen = registry.sample(
        {"reflect"},
        soul=SoulState(),
        recency=recency,
        rng=random.Random(1),
        now=1010.0,
    )

    assert chosen is not None
    assert chosen.name == "fresh"


def test_should_act_uses_dim_or_total_delta_and_cost() -> None:
    cfg = ActionThresholdConfig(min_abs_delta_per_dim=12, min_total_delta=30, cost_scaling=1.0)

    assert should_act((12, 0, 0, 0, 0, 0, 0), _action("cheap"), cfg)
    assert should_act((5, 5, 5, 5, 5, 5, 0), _action("cheap"), cfg)
    assert not should_act((4, 4, 4, 4, 4, 4, 0), _action("cheap"), cfg)
    assert not should_act((12, 0, 0, 0, 0, 0, 0), _action("costly", cost=2), cfg)


def test_tags_from_delta_quiet_delta_returns_empty() -> None:
    assert (
        tags_from_delta(
            (128, 128, 128, 128, 128, 128, 128),
            (132, 130, 128, 128, 130, 128, 128),
            SoulState(),
        )
        == frozenset()
    )


def test_tags_from_delta_sadness_high_worth_high_agency_reaches_out() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (70, 70, 120, 40, 180, 150, 80),
        SoulState(d=170, w=180),
    )

    assert tags == frozenset({"reach_out", "soothe", "problem_solve", "plan", "create", "journal"})


def test_tags_from_delta_sadness_low_worth_low_agency_withdraws() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (60, 60, 40, 30, 180, 60, 40),
        SoulState(d=70, w=80),
    )

    assert tags == frozenset({"withdraw", "isolate", "reflect", "consume", "distract"})


def test_tags_from_delta_anxiety_secure_researches_and_plans() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (90, 150, 120, 140, 160, 180, 160),
        SoulState(d=160, w=180),
    )

    assert tags == frozenset({"research", "problem_solve", "reflect", "journal", "plan"})


def test_tags_from_delta_shame_paradox_hides_before_sadness_default() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (30, 100, 20, 80, 240, 30, 20),
        SoulState(d=170, w=180),
    )

    assert tags == frozenset({"withdraw", "isolate", "distract"})


def test_tags_from_delta_grief_reflects_and_shares_memory() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (40, 50, 60, 20, 255, 150, 30),
        SoulState(w=150),
    )

    assert tags == frozenset({"reflect", "journal", "ritual", "reach_out", "share"})


def test_tags_from_delta_fear_freeze_withdraws() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (80, 180, 40, 220, 170, 120, 40),
        SoulState(),
    )

    assert tags == frozenset({"withdraw", "isolate"})


def test_tags_from_delta_loneliness_reaches_out_when_secure() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (85, 105, 120, 70, 150, 105, 100),
        SoulState(d=150, w=160),
    )

    assert tags == frozenset({"reach_out", "share", "soothe", "journal"})


def test_tags_from_delta_loneliness_withdraws_when_brittle() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (85, 105, 90, 70, 150, 105, 100),
        SoulState(d=80, w=90),
    )

    assert tags == frozenset({"withdraw", "isolate", "reflect", "journal"})


def test_tags_from_delta_anger_sets_boundaries() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (90, 180, 170, 150, 150, 145, 150),
        SoulState(),
    )

    assert tags == frozenset({"set_boundary", "problem_solve", "plan", "journal", "reflect"})


def test_tags_from_delta_disgust_distances_and_cleans_up() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (80, 130, 150, 100, 190, 140, 120),
        SoulState(),
    )

    assert tags == frozenset({"withdraw", "set_boundary", "clean_up", "reflect"})


def test_tags_from_delta_restlessness_channels_energy() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (125, 185, 145, 100, 90, 150, 125),
        SoulState(),
    )

    assert tags == frozenset({"research", "create", "distract", "plan"})


def test_tags_from_delta_excitement_shares_and_builds() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (210, 180, 170, 140, 130, 190, 180),
        SoulState(),
    )

    assert tags == frozenset({"share", "create", "plan", "research"})


def test_tags_from_delta_curiosity_explores() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (150, 120, 150, 70, 120, 170, 180),
        SoulState(),
    )

    assert tags == frozenset({"research", "explore", "create", "problem_solve"})


def test_tags_from_delta_joy_reaches_out_and_creates() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (190, 130, 170, 80, 120, 200, 135),
        SoulState(),
    )

    assert tags == frozenset({"share", "create", "reach_out", "journal"})


def test_tags_from_delta_contentment_savors() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (190, 90, 170, 50, 115, 200, 120),
        SoulState(),
    )

    assert tags == frozenset({"savor", "reflect", "journal", "share"})


def test_tags_from_delta_boredom_explores() -> None:
    tags = tags_from_delta(
        (145, 110, 160, 80, 130, 175, 135),
        (115, 60, 130, 50, 100, 150, 60),
        SoulState(),
    )

    assert tags == frozenset({"explore", "research", "create", "consume"})


def test_confide_proxy_uses_mood_when_available() -> None:
    soul = SoulState(v=220, a=20, w=220)

    assert confide_proxy_score(soul, None) > 0.6
    assert confide_proxy_score(soul, Score(v=40, a=220)) < 0.1


def test_mistake_aware_tags_high_d_high_w_troubleshoots() -> None:
    cfg = PulseConfig(mistake_pressure_floor=60.0)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (128, 128, 128, 128, 128, 128, 128),
        SoulState(d=170, w=170),
        mistake_pressure=61.0,
        obstruction_count=0,
        pulse_config=cfg,
    )
    assert tags == frozenset({"troubleshoot"})


def test_mistake_aware_tags_high_d_low_w_files_issue() -> None:
    cfg = PulseConfig(mistake_pressure_floor=60.0)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (128, 128, 128, 128, 128, 128, 128),
        SoulState(d=170, w=90),
        mistake_pressure=61.0,
        obstruction_count=0,
        pulse_config=cfg,
    )
    assert tags == frozenset({"file_issue"})


def test_mistake_aware_tags_low_d_high_w_reflects() -> None:
    cfg = PulseConfig(mistake_pressure_floor=60.0)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (128, 128, 128, 128, 128, 128, 128),
        SoulState(d=100, w=170),
        mistake_pressure=61.0,
        obstruction_count=0,
        pulse_config=cfg,
    )
    assert tags == frozenset({"reflect"})


def test_mistake_aware_tags_low_d_low_w_journals_and_withdraws() -> None:
    cfg = PulseConfig(mistake_pressure_floor=60.0)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (128, 128, 128, 128, 128, 128, 128),
        SoulState(d=100, w=90),
        mistake_pressure=61.0,
        obstruction_count=0,
        pulse_config=cfg,
    )
    assert tags == frozenset({"journal_distress", "withdraw_silent"})


def test_mistake_aware_tags_obstruction_with_comfort_confides() -> None:
    cfg = PulseConfig(obstruction_count_floor=5)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (220, 20, 128, 128, 128, 128, 128),
        SoulState(d=170, w=220),
        mistake_pressure=0.0,
        obstruction_count=6,
        pulse_config=cfg,
    )
    assert tags == frozenset({"file_issue", "confide"})


def test_mistake_aware_tags_obstruction_low_w_protects() -> None:
    cfg = PulseConfig(obstruction_count_floor=5)
    tags = mistake_aware_tags(
        (128, 128, 128, 128, 128, 128, 128),
        (220, 20, 128, 128, 128, 128, 128),
        SoulState(d=170, w=80),
        mistake_pressure=0.0,
        obstruction_count=6,
        pulse_config=cfg,
    )
    assert tags == frozenset({"journal_distress", "withdraw_silent"})
