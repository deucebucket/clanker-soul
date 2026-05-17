"""Roll 2 + Roll 3 action selection for the M4 cascade.

``IdleLoop`` produces a contemplation delta. This module decides whether
that delta is strong enough to consider action, then samples from
host-registered actions by tag match, novelty, and soul fit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from clanker_soul.physics.contemplation import ContemplationResult
from clanker_soul.pulse.config import PulseConfig
from clanker_soul.pulse.corpus import PromptFace, RecencyLog, novelty
from clanker_soul.pulse.triggers import ActionOutcome, Trigger
from clanker_soul.score import Score
from clanker_soul.soul import SoulState


ActionHandler = Callable[["CascadeActionContext"], ActionOutcome | Awaitable[ActionOutcome]]


@dataclass(frozen=True)
class RegisteredAction:
    """A host-owned action that can participate in cascade selection."""

    name: str
    tags: frozenset[str]
    handler: ActionHandler
    vadugwi_affinity: tuple[int, int, int, int, int, int, int] | None = None
    cost: int = 1
    cooldown_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("RegisteredAction.name must be non-empty")
        if not self.tags:
            raise ValueError("RegisteredAction.tags must be non-empty")
        if self.cost < 1:
            raise ValueError("RegisteredAction.cost must be >= 1")
        if self.cooldown_seconds < 0:
            raise ValueError("RegisteredAction.cooldown_seconds must be >= 0")
        if self.vadugwi_affinity is None:
            return
        if len(self.vadugwi_affinity) != 7:
            raise ValueError("RegisteredAction.vadugwi_affinity must be a 7-tuple")
        for idx, value in enumerate(self.vadugwi_affinity):
            if not 0 <= value <= 255:
                raise ValueError(
                    f"RegisteredAction.vadugwi_affinity[{idx}]={value} must be in 0..255"
                )


@dataclass(frozen=True)
class CascadeActionContext:
    """Context passed to a selected :class:`RegisteredAction` handler."""

    trigger: Trigger
    face: PromptFace | None
    contemplation: ContemplationResult | None
    tags: frozenset[str]
    action: RegisteredAction


@dataclass(frozen=True)
class ActionThresholdConfig:
    """Roll 2 thresholds for whether a contemplation delta should act."""

    min_abs_delta_per_dim: int = 12
    min_total_delta: int = 30
    cost_scaling: float = 1.0


class ActionRegistry:
    """Registry and weighted sampler for host-provided cascade actions."""

    def __init__(self, actions: Iterable[RegisteredAction] = ()) -> None:
        self._actions: dict[str, RegisteredAction] = {}
        for action in actions:
            self.register(action)

    @property
    def actions(self) -> tuple[RegisteredAction, ...]:
        return tuple(self._actions.values())

    def register(self, action: RegisteredAction) -> None:
        if action.name in self._actions:
            raise ValueError(f"ActionRegistry: duplicate action name {action.name!r}")
        self._actions[action.name] = action

    def filter(self, tags: Iterable[str]) -> list[RegisteredAction]:
        wanted = frozenset(tags)
        if not wanted:
            return []
        return [action for action in self._actions.values() if action.tags & wanted]

    def sample(
        self,
        tags: Iterable[str],
        *,
        soul: SoulState,
        recency: RecencyLog,
        rng: random.Random,
        now: float = 0.0,
        actions: Iterable[RegisteredAction] | None = None,
    ) -> RegisteredAction | None:
        """Sample by tag match × novelty × soul affinity.

        ``actions`` lets callers pass a pre-filtered subset, such as the
        actions that passed Roll 2 thresholding. When omitted, the registry
        filters by tag itself.
        """
        wanted = frozenset(tags)
        candidates = list(actions) if actions is not None else self.filter(wanted)
        weighted: list[tuple[RegisteredAction, float]] = []
        for action in candidates:
            tag_weight = _tag_match_weight(action, wanted)
            if tag_weight <= 0.0:
                continue
            nov = novelty(action.name, action.cooldown_seconds, recency, now)
            if nov <= 0.0:
                continue
            weight = tag_weight * nov * _soul_affinity_weight(action, soul)
            if weight > 0.0:
                weighted.append((action, weight))
        if not weighted:
            return None
        actions_out, weights = zip(*weighted)
        return rng.choices(list(actions_out), weights=list(weights), k=1)[0]


def tags_from_delta(
    pre: tuple[int, int, int, int, int, int, int],
    post: tuple[int, int, int, int, int, int, int],
    soul: SoulState,
) -> frozenset[str]:
    """Default reaction-shape mapper.

    Maps the research-backed M4 action-tendency matrix (#83) into
    host-action tags. This deliberately stays conservative: only the
    documented sadness/anxiety/shame/grief shapes emit tags first; broader
    mood families below stay intentionally generic so hosts can opt in with
    their own mapper without fighting over-specific defaults.
    """
    if _delta_is_quiet(pre, post):
        return frozenset()

    mood = Score.from_sequence(post)

    # Shame paradox: low V + low W + high G hides even though the agent
    # needs support. This must win before generic sadness.
    if mood.v <= 70 and mood.w <= 80 and mood.g >= 190:
        return frozenset({"withdraw", "isolate", "distract"})

    # Fear/freeze shape: urgent, low-agency threat. No instrumental action.
    if mood.v <= 100 and mood.u >= 180 and mood.d <= 70:
        return frozenset({"withdraw", "isolate"})

    # Grief / reflective loss: low-energy, high-gravity, not worth-collapsed.
    if mood.v <= 80 and mood.a <= 90 and mood.g >= 220 and soul.w >= 120:
        return frozenset({"reflect", "journal", "ritual", "reach_out", "share"})

    # Anger/frustration: activated, agentic negative valence. Prefer
    # boundary-setting and problem work over impulsive social blast.
    if mood.v <= 105 and mood.a >= 150 and mood.d >= 130:
        return frozenset({"set_boundary", "problem_solve", "plan", "journal", "reflect"})

    # Anxiety with enough agency/worth: gather info and plan.
    if mood.v <= 120 and mood.a >= 130 and mood.u >= 120 and soul.d >= 120 and soul.w >= 140:
        return frozenset({"research", "problem_solve", "reflect", "journal", "plan"})

    # Sadness: personality moderates active coping vs withdrawal.
    if mood.v <= 100 and mood.a <= 100:
        if soul.d >= 120 and soul.w >= 140:
            return frozenset({"reach_out", "soothe", "problem_solve", "plan", "create", "journal"})
        if soul.d <= 90 and soul.w <= 100:
            return frozenset({"withdraw", "isolate", "reflect", "consume", "distract"})
        return frozenset({"reflect", "journal"})

    # Disgust/repulsion: negative valence plus high agency and gravity
    # tends toward distancing, cleanup, and boundary repair.
    if mood.v <= 95 and mood.d >= 120 and mood.g >= 170:
        return frozenset({"withdraw", "set_boundary", "clean_up", "reflect"})

    # Pride trap / defensive self-sufficiency: high D/W with negative
    # movement favors self-directed planning over confiding.
    if mood.d >= 200 and mood.w >= 190 and _valence_dropped(pre, post):
        return frozenset({"plan", "problem_solve", "reflect"})

    # Loneliness: low valence with relational worth dip, but not the
    # shame/grief traps above. Secure souls reach out; brittle ones
    # retreat and reflect.
    if mood.v <= 100 and mood.w <= 120 and mood.g >= 130:
        if soul.d >= 120 and soul.w >= 130:
            return frozenset({"reach_out", "share", "soothe", "journal"})
        return frozenset({"withdraw", "isolate", "reflect", "journal"})

    # Restlessness: activated with little gravity. Channel into novelty
    # or movement before it becomes noisy action.
    if mood.a >= 165 and mood.g <= 130 and mood.u <= 120:
        return frozenset({"research", "create", "distract", "plan"})

    # Excitement: high-valence, high-arousal, directed energy.
    if mood.v >= 170 and mood.a >= 150 and (mood.u >= 110 or mood.i >= 140):
        return frozenset({"share", "create", "plan", "research"})

    # Curiosity: directed, non-crisis exploration.
    if mood.v >= 120 and mood.a >= 100 and mood.i >= 150 and mood.g <= 170:
        return frozenset({"research", "explore", "create", "problem_solve"})

    # Joy/contentment: positive states reinforce sharing, savoring, and
    # gentle reflection. Keep excitement above this branch.
    if mood.v >= 170:
        if mood.a <= 115 and mood.u <= 90:
            return frozenset({"savor", "reflect", "journal", "share"})
        return frozenset({"share", "create", "reach_out", "journal"})

    # Boredom / under-stimulation: low arousal, low urgency, low intent.
    if mood.a <= 80 and mood.u <= 80 and mood.i <= 90:
        return frozenset({"explore", "research", "create", "consume"})

    return frozenset()


def mistake_aware_tags(
    pre: tuple[int, int, int, int, int, int, int],
    post: tuple[int, int, int, int, int, int, int],
    soul: SoulState,
    *,
    mistake_pressure: float,
    obstruction_count: int,
    pulse_config: PulseConfig,
) -> frozenset[str]:
    """Tags for VADUGWI-conditioned responses to mistakes/obstruction."""
    mood = Score.from_sequence(post)
    if mistake_pressure > pulse_config.mistake_pressure_floor:
        if soul.d >= 140 and soul.w >= 140:
            return frozenset({"troubleshoot"})
        if soul.d >= 140 and soul.w < 140:
            return frozenset({"file_issue"})
        if soul.d < 140 and soul.w >= 140:
            return frozenset({"reflect"})
        return frozenset({"journal_distress", "withdraw_silent"})

    if obstruction_count > pulse_config.obstruction_count_floor:
        if soul.w < 100:
            return frozenset({"journal_distress", "withdraw_silent"})
        if soul.d >= 140 and confide_proxy_score(soul, mood) >= 0.55:
            return frozenset({"file_issue", "confide"})

    if mistake_pressure > pulse_config.mistake_pressure_floor or (
        obstruction_count > pulse_config.obstruction_count_floor
    ):
        return frozenset({"reflect"})
    _ = pre
    return frozenset()


def confide_proxy_score(soul: SoulState, mood: Score | None = None) -> float:
    """Approximate comfort/trust for confiding until first-class trust exists."""
    if mood is None:
        v = soul.v
        a = soul.a
    else:
        v = mood.v
        a = mood.a
    return _scale(v) * _scale(soul.w) * (1.0 - _scale(a))


def should_act(
    delta: tuple[int, int, int, int, int, int, int],
    action: RegisteredAction,
    config: ActionThresholdConfig,
) -> bool:
    """Return True when ``delta`` is large enough for ``action.cost``."""
    scale = 1.0 + (max(1, action.cost) - 1) * max(0.0, config.cost_scaling)
    dim_floor = config.min_abs_delta_per_dim * scale
    total_floor = config.min_total_delta * scale
    abs_delta = [abs(value) for value in delta]
    return max(abs_delta) >= dim_floor or sum(abs_delta) >= total_floor


def _tag_match_weight(action: RegisteredAction, wanted: frozenset[str]) -> float:
    if not wanted:
        return 0.0
    overlap = len(action.tags & wanted)
    if overlap == 0:
        return 0.0
    return overlap / len(wanted)


def _soul_affinity_weight(action: RegisteredAction, soul: SoulState) -> float:
    if action.vadugwi_affinity is None:
        return 1.0
    soul_tuple = (soul.v, soul.a, soul.d, soul.u, soul.g, soul.w, soul.i)
    closeness = 0.0
    for actual, desired in zip(soul_tuple, action.vadugwi_affinity):
        closeness += 1.0 - (abs(actual - desired) / 255.0)
    return 1.0 + (closeness / 7)


def _scale(value: int) -> float:
    return max(0.0, min(1.0, value / 255.0))


def _delta_is_quiet(
    pre: tuple[int, int, int, int, int, int, int],
    post: tuple[int, int, int, int, int, int, int],
) -> bool:
    delta = tuple(after - before for before, after in zip(pre, post))
    return max(abs(value) for value in delta) < 8 and sum(abs(value) for value in delta) < 20


def _valence_dropped(
    pre: tuple[int, int, int, int, int, int, int],
    post: tuple[int, int, int, int, int, int, int],
) -> bool:
    return post[0] < pre[0]


__all__ = [
    "ActionHandler",
    "ActionRegistry",
    "ActionThresholdConfig",
    "CascadeActionContext",
    "RegisteredAction",
    "confide_proxy_score",
    "mistake_aware_tags",
    "tags_from_delta",
    "should_act",
]
