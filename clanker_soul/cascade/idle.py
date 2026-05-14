"""``IdleLoop`` — heartbeat tick + Roll 0 (gate) + Roll 1 (contemplation).

Ships #81: the M4 cascade entry point. Each heartbeat the loop rolls
``Roll 0`` (a gate) to decide *whether to think at all*. Most rolls say
no — that's the whole point of the design. When the gate passes, the
loop samples a contemplation face from the corpus and runs
:py:meth:`EmotionalPhysics.contemplate`. The mood shifts. Higher cascade
layers (#82, ``cascade/action.py``) consume the resulting delta and
decide whether to act on the thought.

Three cooldowns gate the loop independently:

* ``cooldown_after_action_s`` — quiet for N seconds after any outbound
  action. Hosts call :py:meth:`IdleLoop.note_action` from their action
  dispatch path. Default 300s.
* ``cooldown_after_contemplation_s`` — quiet for N seconds after the
  most recent actual contemplation. Loop tracks this internally. Default 60s.
* ``min_quiet_s`` — quiet for N seconds after any external event (a
  Score arrives, a message lands). Hosts call :py:meth:`IdleLoop.note_event`
  from their ingest path. Default 30s.

When a cooldown signal was never set (host never called the corresponding
``note_*``), the cooldown is treated as already-elapsed — the gate never
falsely suppresses on missing data. That makes IdleLoop safe to drop in
without re-plumbing the host's event path.

Custom gate behaviour: pass ``gate_fn=`` to replace the whole gate
function. The default :py:func:`default_gate` is the operator-overridable
reference — replace it wholesale rather than forking the cooldown checks.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from clanker_soul.cascade.action import (
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    RegisteredAction,
    should_act,
    tags_from_delta,
)
from clanker_soul.physics import EmotionalPhysics
from clanker_soul.physics.contemplation import ContemplationResult
from clanker_soul.pulse.corpus import PromptCorpus, PromptFace, RecencyLog
from clanker_soul.pulse.triggers import ActionOutcome, Trigger
from clanker_soul.soul import SoulState

logger = logging.getLogger(__name__)


IDLE_CONTEMPLATION_KIND: str = "idle_introspection"
"""The synthetic trigger kind IdleLoop uses to sample the contemplation
corpus. Faces in the contemplation corpus declare this kind to opt in.
Overridable per-loop via :py:attr:`GateConfig.contemplation_kind` if a
host wants their faces tagged differently."""


@dataclass(frozen=True)
class GateConfig:
    """Knobs for :py:func:`default_gate` and the cooldown bookkeeping.

    Every field is a default; operators replace fields rather than fork
    the gate function. See ``memory/feedback_everything_is_a_toggle.md``.
    """

    base_probability: float = 0.05
    """P(think) per tick before mood biasing. 5% = ~3 thoughts/min at 1Hz."""

    mood_arousal_bias: float = 0.5
    """How strongly arousal pushes P up/down. 0 = no bias; 1 = full
    doubling at A=255, full halving at A=0. Capped to keep P in [0, 1]."""

    cooldown_after_action_s: float = 300.0
    """Quiet seconds after any outbound action. Host calls
    :py:meth:`IdleLoop.note_action`."""

    cooldown_after_contemplation_s: float = 60.0
    """Quiet seconds between successive contemplations."""

    min_quiet_s: float = 30.0
    """Quiet seconds after any external event. Host calls
    :py:meth:`IdleLoop.note_event`."""

    contemplation_kind: str = IDLE_CONTEMPLATION_KIND
    """Trigger kind used to sample the contemplation corpus."""

    contemplation_weight_scale: float = 1.0
    """Forwarded to :py:meth:`EmotionalPhysics.contemplate`. Attenuates
    how strongly the affinity blends into mood. <1.0 for fleeting
    thoughts, >1.0 for charged ones."""


@dataclass(frozen=True)
class GateRollContext:
    """The bundle passed to the gate function. Frozen so custom gate
    functions can't mutate IdleLoop state — they decide on/off only."""

    config: GateConfig
    mood: tuple[int, int, int, int, int, int, int] | None
    seconds_since_last_action: float
    seconds_since_last_contemplation: float
    seconds_since_any_event: float
    rng: random.Random


@dataclass(frozen=True)
class TickResult:
    """Full record of one heartbeat tick. Hosts inspect / log this.

    ``gate_passed=False`` paths:
      * ``gate_skip_reason`` names the cooldown ("cooldown_action",
        "cooldown_contemplation", "min_quiet") or "random_roll" when the
        default gate rolled below P, or ``None`` when a custom gate
        returned False without a reason. ``face`` and ``contemplation``
        are both None.

    ``gate_passed=True`` paths:
      * ``face`` is the sampled :py:class:`PromptFace`, or None when
        the corpus had nothing eligible.
      * ``contemplation`` is the :py:class:`ContemplationResult` from
        :py:meth:`EmotionalPhysics.contemplate`, or None when ``face`` is.

    ``gate_probability`` is the computed P (post arousal-bias) for the
    default gate. Custom gates report 0.0 — the loop can't introspect
    what they computed.

    ``elapsed_seconds`` is wall time spent inside ``tick()``, useful for
    budgeting and dashboarding.
    """

    gate_passed: bool
    gate_probability: float
    gate_skip_reason: str | None
    face: PromptFace | None
    contemplation: ContemplationResult | None
    elapsed_seconds: float
    action_tags: frozenset[str] = field(default_factory=frozenset)
    chosen_action: RegisteredAction | None = None
    action_outcome: ActionOutcome | None = None


def default_gate(ctx: GateRollContext) -> bool:
    """Reference Roll 0 implementation.

    Order:

    1. Cooldown-after-action: never fire too soon after outbound work.
    2. Cooldown-after-contemplation: don't ruminate continuously.
    3. min_quiet: the agent shouldn't think while events are still
       arriving — wait for the inbound stream to quiet down.
    4. Mood arousal-biased probability roll.

    Returns ``True`` iff the agent should think this tick.
    """
    cfg = ctx.config
    if ctx.seconds_since_last_action < cfg.cooldown_after_action_s:
        return False
    if ctx.seconds_since_last_contemplation < cfg.cooldown_after_contemplation_s:
        return False
    if ctx.seconds_since_any_event < cfg.min_quiet_s:
        return False

    p = _biased_probability(cfg, ctx.mood)
    return ctx.rng.random() < p


def _biased_probability(
    cfg: GateConfig,
    mood: tuple[int, int, int, int, int, int, int] | None,
) -> float:
    """Compute the mood-arousal-biased gate probability. Pure function so
    :py:class:`TickResult.gate_probability` can report the same number
    the gate rolled against without re-implementing the math."""
    p = cfg.base_probability
    if mood is not None and cfg.mood_arousal_bias != 0.0:
        arousal_norm = (mood[1] - 128) / 128.0  # -1.0 (A=0) … +1.0 (A=255)
        p *= 1.0 + arousal_norm * cfg.mood_arousal_bias
    return max(0.0, min(1.0, p))


class IdleLoop:
    """Heartbeat tick + Roll 0 (gate) + Roll 1 (contemplation).

    Constructor:
      ``physics`` — the live :py:class:`EmotionalPhysics`. Usually
        ``plugin.physics``. The loop calls ``physics.contemplate`` on
        gate-pass and reads ``physics.mood`` for the arousal bias.
      ``contemplation_corpus`` — :py:class:`PromptCorpus` whose faces
        declare ``trigger_kinds`` containing
        :py:data:`IDLE_CONTEMPLATION_KIND` (or whatever
        ``gate_config.contemplation_kind`` says) and have
        ``vadugwi_affinity`` set. Faces lacking affinity are filtered
        out by ``contemplate`` itself but it's cheaper to author the
        contemplation corpus with affinity-required.
      ``gate_config`` — defaults to :py:class:`GateConfig`.
      ``gate_fn`` — operator override. Signature
        ``Callable[[GateRollContext], bool]`` (sync) or
        ``Callable[[GateRollContext], Awaitable[bool]]`` (async). When
        provided, ``TickResult.gate_probability`` is 0.0 — the loop
        can't introspect what the custom function computed.
      ``now_fn`` — clock injection for tests.
      ``rng`` — :py:class:`random.Random` for the default gate's
        coin-flip and the corpus's weighted sampler.
      ``recency`` — optional shared :py:class:`RecencyLog`. The loop
        uses one internally even when ``recency=None`` so face-level
        cooldowns work; pass a host-owned log if you want one
        :py:class:`RecencyLog` shared across pulse + cascade.
      ``registry`` — optional :py:class:`ActionRegistry`. When absent,
        the loop stops after contemplation. When present, Roll 2/3 run:
        delta → tags → threshold → registered action handler.
    """

    def __init__(
        self,
        *,
        physics: EmotionalPhysics,
        contemplation_corpus: PromptCorpus,
        gate_config: GateConfig | None = None,
        gate_fn: (
            Callable[[GateRollContext], bool] | Callable[[GateRollContext], Awaitable[bool]] | None
        ) = None,
        now_fn: Callable[[], float] | None = None,
        rng: random.Random | None = None,
        recency: RecencyLog | None = None,
        registry: ActionRegistry | None = None,
        action_threshold_config: ActionThresholdConfig | None = None,
        tags_fn: (
            Callable[
                [
                    tuple[int, int, int, int, int, int, int],
                    tuple[int, int, int, int, int, int, int],
                    SoulState,
                ],
                frozenset[str],
            ]
            | None
        ) = None,
        should_act_fn: (
            Callable[
                [
                    tuple[int, int, int, int, int, int, int],
                    RegisteredAction,
                    ActionThresholdConfig,
                ],
                bool,
            ]
            | None
        ) = None,
        action_recency: RecencyLog | None = None,
    ) -> None:
        self._physics = physics
        self._corpus = contemplation_corpus
        self._config = gate_config or GateConfig()
        self._gate_fn = gate_fn
        self._now_fn = now_fn or time.time
        self._rng = rng or random.Random()
        self._recency = recency or RecencyLog()
        self._registry = registry
        self._action_threshold_config = action_threshold_config or ActionThresholdConfig()
        self._tags_fn = tags_fn or tags_from_delta
        self._should_act_fn = should_act_fn or should_act
        self._action_recency = action_recency or RecencyLog()

        self._last_action_ts: float | None = None
        self._last_contemplation_ts: float | None = None
        self._last_event_ts: float | None = None

    # ── public bookkeeping ──────────────────────────────────────────

    @property
    def config(self) -> GateConfig:
        return self._config

    def note_action(self) -> None:
        """Record that an outbound action just dispatched. Resets the
        action-cooldown bookkeeping. Hosts call this from their action
        dispatch path (or from ``PulseHost.dispatch_action`` wrapping)."""
        self._last_action_ts = self._now_fn()

    def note_event(self) -> None:
        """Record that an external event just arrived. Resets the
        min-quiet bookkeeping. Hosts call this from their ingest path
        (or from a Score callback)."""
        self._last_event_ts = self._now_fn()

    def seconds_since_last_action(self) -> float:
        return self._seconds_since(self._last_action_ts)

    def seconds_since_last_contemplation(self) -> float:
        return self._seconds_since(self._last_contemplation_ts)

    def seconds_since_last_event(self) -> float:
        return self._seconds_since(self._last_event_ts)

    def _seconds_since(self, ts: float | None) -> float:
        if ts is None:
            return float("inf")
        return max(0.0, self._now_fn() - ts)

    # ── gate context construction ───────────────────────────────────

    def build_gate_context(self) -> GateRollContext:
        """Snapshot the inputs the gate function consumes. Public so
        operators can probe / log what the next gate roll *would* see."""
        mood_obj = self._physics.mood
        mood_tuple: tuple[int, int, int, int, int, int, int] | None
        if mood_obj is None:
            mood_tuple = None
        else:
            # Score.as_list() returns [V, A, D, U, G, W, I].
            ml = mood_obj.as_list()
            mood_tuple = (ml[0], ml[1], ml[2], ml[3], ml[4], ml[5], ml[6])
        return GateRollContext(
            config=self._config,
            mood=mood_tuple,
            seconds_since_last_action=self.seconds_since_last_action(),
            seconds_since_last_contemplation=self.seconds_since_last_contemplation(),
            seconds_since_any_event=self.seconds_since_last_event(),
            rng=self._rng,
        )

    # ── tick ────────────────────────────────────────────────────────

    async def tick(self) -> TickResult:
        """Run one heartbeat. Cheap on the gated-off path."""
        start = time.perf_counter()
        ctx = self.build_gate_context()

        # Cheap pre-roll skip-reason: we want a precise reason for
        # observability without forking the gate function. The default
        # gate happens to check the same cooldowns; custom gate_fn paths
        # leave skip_reason=None (the host's gate is opaque).
        skip_reason = self._default_skip_reason(ctx) if self._gate_fn is None else None

        if self._gate_fn is None:
            passed = default_gate(ctx)
            probability = _biased_probability(self._config, ctx.mood)
            if not passed and skip_reason is None:
                skip_reason = "random_roll"
        else:
            result = self._gate_fn(ctx)
            if inspect.isawaitable(result):
                passed = bool(await result)
            else:
                passed = bool(result)
            probability = 0.0

        if not passed:
            return TickResult(
                gate_passed=False,
                gate_probability=probability,
                gate_skip_reason=skip_reason,
                face=None,
                contemplation=None,
                elapsed_seconds=time.perf_counter() - start,
            )

        # Gate-on: sample a contemplation face and contemplate it.
        face, contemplation = await self._sample_and_contemplate(ctx)
        action_tags, chosen_action, action_outcome = await self._maybe_run_action(
            ctx,
            face,
            contemplation,
        )
        return TickResult(
            gate_passed=True,
            gate_probability=probability,
            gate_skip_reason=None,
            face=face,
            contemplation=contemplation,
            elapsed_seconds=time.perf_counter() - start,
            action_tags=action_tags,
            chosen_action=chosen_action,
            action_outcome=action_outcome,
        )

    async def _sample_and_contemplate(
        self,
        ctx: GateRollContext,
    ) -> tuple[PromptFace | None, ContemplationResult | None]:
        """Sample → contemplate. Returns (None, None) when the corpus has
        nothing eligible. We update internal recency *before* contemplate
        so a contemplate exception still leaves the loop cooldown-honest."""
        synthetic_trigger = self._synthesize_trigger(ctx)
        now = self._now_fn()
        face = self._corpus.sample(
            synthetic_trigger,
            recency=self._recency,
            now=now,
        )
        if face is None:
            return None, None

        self._recency.note_fired(face.id, now)
        self._last_contemplation_ts = self._now_fn()

        try:
            result = self._physics.contemplate(
                face,
                weight_scale=self._config.contemplation_weight_scale,
            )
        except Exception:
            logger.exception(
                "physics.contemplate(face=%r) raised — face dropped, mood unchanged",
                face.id,
            )
            return face, None

        # Yield once so async callers see expected scheduling semantics
        # (e.g. tick under asyncio.gather doesn't block the loop on the
        # synchronous contemplate path).
        await asyncio.sleep(0)
        return face, result

    async def _maybe_run_action(
        self,
        ctx: GateRollContext,
        face: PromptFace | None,
        contemplation: ContemplationResult | None,
    ) -> tuple[frozenset[str], RegisteredAction | None, ActionOutcome | None]:
        """Run Roll 2/3 when an action registry is configured."""
        if self._registry is None or face is None or contemplation is None:
            return frozenset(), None, None

        tags = self._tags_fn(
            contemplation.pre_mood,
            contemplation.post_mood,
            self._physics.soul,
        )
        if not tags:
            return tags, None, None

        candidates = [
            action
            for action in self._registry.filter(tags)
            if self._should_act_fn(
                contemplation.delta,
                action,
                self._action_threshold_config,
            )
        ]
        if not candidates:
            return tags, None, None

        now = self._now_fn()
        chosen = self._registry.sample(
            tags,
            soul=self._physics.soul,
            recency=self._action_recency,
            rng=self._rng,
            now=now,
            actions=candidates,
        )
        if chosen is None:
            return tags, None, None

        self._action_recency.note_fired(chosen.name, now)
        self._last_action_ts = now
        context = CascadeActionContext(
            trigger=self._synthesize_trigger(ctx),
            face=face,
            contemplation=contemplation,
            tags=tags,
            action=chosen,
        )
        try:
            result = chosen.handler(context)
            outcome = await result if inspect.isawaitable(result) else result
        except Exception:
            logger.exception("cascade action handler %r raised", chosen.name)
            return tags, chosen, ActionOutcome(delivered=False, note="cascade_action_exception")

        if not isinstance(outcome, ActionOutcome):
            logger.error(
                "cascade action handler %r returned %r, expected ActionOutcome",
                chosen.name,
                outcome,
            )
            return tags, chosen, ActionOutcome(delivered=False, note="invalid_action_outcome")

        self._absorb_action_consequences(outcome)
        return tags, chosen, outcome

    def _absorb_action_consequences(self, outcome: ActionOutcome) -> None:
        for score in outcome.consequences:
            try:
                self._physics.ingest(score)
            except Exception:
                logger.exception(
                    "Failed to ingest cascade action consequence Score (patterns=%s)",
                    score.patterns,
                )

    def _synthesize_trigger(self, ctx: GateRollContext) -> Trigger:
        """Build a Trigger purely for corpus eligibility lookup. Carries
        the current mood + soul so VADUGWI predicates filter correctly."""
        soul_dict = self._physics.soul.to_dict()
        mood_list: list[int] | None
        if ctx.mood is None:
            mood_list = None
        else:
            mood_list = list(ctx.mood)
        return Trigger(
            kind=self._config.contemplation_kind,
            soul=soul_dict,
            mood=mood_list,
            metrics={},
        )

    def _default_skip_reason(self, ctx: GateRollContext) -> str | None:
        """Mirror :py:func:`default_gate`'s cooldown checks for
        observability. Returns the *specific* cooldown that would block,
        or None when none would. Only meaningful for the default gate;
        custom gate functions leave skip_reason=None."""
        cfg = ctx.config
        if ctx.seconds_since_last_action < cfg.cooldown_after_action_s:
            return "cooldown_action"
        if ctx.seconds_since_last_contemplation < cfg.cooldown_after_contemplation_s:
            return "cooldown_contemplation"
        if ctx.seconds_since_any_event < cfg.min_quiet_s:
            return "min_quiet"
        return None


__all__ = [
    "GateConfig",
    "GateRollContext",
    "IDLE_CONTEMPLATION_KIND",
    "IdleLoop",
    "TickResult",
    "default_gate",
]
