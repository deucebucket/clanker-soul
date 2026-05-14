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


def test_default_tags_from_delta_is_empty_until_matrix_lands() -> None:
    assert (
        tags_from_delta(
            (128, 128, 128, 128, 128, 128, 128),
            (80, 160, 128, 128, 200, 100, 128),
            SoulState(),
        )
        == frozenset()
    )


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
