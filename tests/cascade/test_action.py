"""Roll 2 + Roll 3 action registry tests."""

from __future__ import annotations

import random

import pytest

from clanker_soul import ActionOutcome, RecencyLog, SoulState
from clanker_soul.cascade import (
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    RegisteredAction,
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
