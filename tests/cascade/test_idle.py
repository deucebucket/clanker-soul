"""IdleLoop + Roll 0 gate — the M4 cascade entry point (#81).

The cascade is *gated*. Most heartbeats do nothing. When the roll passes,
the loop samples a face from the contemplation corpus and runs
``physics.contemplate``. Higher cascade layers (#82) consume the result.

These tests pin the contract:

* default gate honors action / contemplation / quiet cooldowns
* mood arousal biases the dice up or down
* custom ``gate_fn`` cleanly overrides the default
* ``TickResult`` is fully populated either way
* the gated-off path doesn't sample or contemplate (cheap)
* the gated-on path samples → contemplates → updates internal recency
"""

from __future__ import annotations

import random

import pytest

from clanker_soul import (
    EmotionalPhysics,
    PhysicsConfig,
    PromptCorpus,
    PromptFace,
    SoulState,
)
from clanker_soul.cascade import (
    GateConfig,
    GateRollContext,
    IdleLoop,
    TickResult,
    default_gate,
)
from clanker_soul.cascade.idle import IDLE_CONTEMPLATION_KIND


def _affinity_face(
    fid: str = "idle.face",
    *,
    affinity: tuple[int, int, int, int, int, int, int] = (60, 100, 90, 80, 200, 80, 110),
    weight: float = 1.0,
) -> PromptFace:
    return PromptFace(
        id=fid,
        trigger_kinds=frozenset({IDLE_CONTEMPLATION_KIND}),
        template="why am i like this?",
        vadugwi_affinity=affinity,
        base_weight=weight,
    )


def _physics() -> EmotionalPhysics:
    return EmotionalPhysics(soul=SoulState(), config=PhysicsConfig())


def _corpus(*faces: PromptFace) -> PromptCorpus:
    faces = faces or (_affinity_face(),)
    return PromptCorpus(faces, rng=random.Random(1234))


def _loop(
    *,
    physics: EmotionalPhysics | None = None,
    corpus: PromptCorpus | None = None,
    config: GateConfig | None = None,
    gate_fn=None,
    now_fn=None,
    rng: random.Random | None = None,
) -> IdleLoop:
    return IdleLoop(
        physics=physics or _physics(),
        contemplation_corpus=corpus or _corpus(),
        gate_config=config,
        gate_fn=gate_fn,
        now_fn=now_fn,
        rng=rng or random.Random(7),
    )


# ── default gate cooldowns ──────────────────────────────────────────────


def test_default_gate_blocks_when_action_cooldown_active() -> None:
    cfg = GateConfig(cooldown_after_action_s=300.0)
    ctx = GateRollContext(
        config=cfg,
        mood=None,
        seconds_since_last_action=120.0,
        seconds_since_last_contemplation=float("inf"),
        seconds_since_any_event=float("inf"),
        rng=random.Random(0),
    )
    assert default_gate(ctx) is False


def test_default_gate_blocks_when_contemplation_cooldown_active() -> None:
    cfg = GateConfig(cooldown_after_contemplation_s=60.0)
    ctx = GateRollContext(
        config=cfg,
        mood=None,
        seconds_since_last_action=float("inf"),
        seconds_since_last_contemplation=30.0,
        seconds_since_any_event=float("inf"),
        rng=random.Random(0),
    )
    assert default_gate(ctx) is False


def test_default_gate_blocks_when_min_quiet_not_satisfied() -> None:
    cfg = GateConfig(min_quiet_s=30.0)
    ctx = GateRollContext(
        config=cfg,
        mood=None,
        seconds_since_last_action=float("inf"),
        seconds_since_last_contemplation=float("inf"),
        seconds_since_any_event=10.0,
        rng=random.Random(0),
    )
    assert default_gate(ctx) is False


def test_default_gate_arousal_bias_lifts_probability() -> None:
    # base_probability=0.05, mood_arousal_bias=0.5. With A=255 → p≈0.075.
    # Roll an RNG that returns 0.06 — would block at base but pass when biased.
    cfg = GateConfig(base_probability=0.05, mood_arousal_bias=0.5)
    high_arousal = (128, 255, 128, 128, 128, 128, 128)
    ctx_high = GateRollContext(
        config=cfg,
        mood=high_arousal,
        seconds_since_last_action=float("inf"),
        seconds_since_last_contemplation=float("inf"),
        seconds_since_any_event=float("inf"),
        rng=_rng_returning(0.06),
    )
    assert default_gate(ctx_high) is True

    low_arousal = (128, 0, 128, 128, 128, 128, 128)
    ctx_low = GateRollContext(
        config=cfg,
        mood=low_arousal,
        seconds_since_last_action=float("inf"),
        seconds_since_last_contemplation=float("inf"),
        seconds_since_any_event=float("inf"),
        rng=_rng_returning(0.04),
    )
    # A=0 → p ≈ 0.025 — 0.04 misses.
    assert default_gate(ctx_low) is False


def test_default_gate_uses_base_probability_when_mood_unset() -> None:
    cfg = GateConfig(base_probability=0.5, mood_arousal_bias=0.5)
    ctx = GateRollContext(
        config=cfg,
        mood=None,
        seconds_since_last_action=float("inf"),
        seconds_since_last_contemplation=float("inf"),
        seconds_since_any_event=float("inf"),
        rng=_rng_returning(0.4),  # < base, would pass at base
    )
    assert default_gate(ctx) is True


# ── IdleLoop wiring ─────────────────────────────────────────────────────


async def test_tick_returns_tickresult_gated_off_without_sampling() -> None:
    """Gated-off path: no sample, no contemplate, just clean return."""
    physics = _physics()
    sampled: list = []

    class SpyCorpus(PromptCorpus):
        def sample(self, *args, **kwargs):
            sampled.append(1)
            return super().sample(*args, **kwargs)

    corpus = SpyCorpus([_affinity_face()], rng=random.Random(1))
    loop = _loop(
        physics=physics,
        corpus=corpus,
        # Force gate-off
        gate_fn=lambda ctx: False,
    )
    result = await loop.tick()
    assert isinstance(result, TickResult)
    assert result.gate_passed is False
    assert result.face is None
    assert result.contemplation is None
    assert sampled == [], "gated-off tick must not call corpus.sample"


async def test_tick_gated_on_samples_and_contemplates() -> None:
    physics = _physics()
    loop = _loop(
        physics=physics,
        corpus=_corpus(_affinity_face("idle.heavy", affinity=(40, 100, 90, 80, 230, 80, 110))),
        gate_fn=lambda ctx: True,
    )
    result = await loop.tick()
    assert result.gate_passed is True
    assert result.face is not None
    assert result.face.id == "idle.heavy"
    assert result.contemplation is not None
    # contemplate actually moved mood (V should drop toward 40 from soul anchor 145)
    assert result.contemplation.delta[0] < 0
    # Internal recency now records the contemplation timestamp
    assert loop.seconds_since_last_contemplation() < 1.0


async def test_tick_gated_on_with_empty_corpus_returns_face_none() -> None:
    """Gate passed but corpus had nothing eligible — clean signal back."""
    physics = _physics()
    loop = _loop(
        physics=physics,
        corpus=PromptCorpus((), rng=random.Random(1)),
        gate_fn=lambda ctx: True,
    )
    result = await loop.tick()
    assert result.gate_passed is True
    assert result.face is None
    assert result.contemplation is None


def test_note_action_updates_action_cooldown() -> None:
    clock = _FakeClock(start=1000.0)
    loop = _loop(
        config=GateConfig(cooldown_after_action_s=300.0, min_quiet_s=0.0),
        now_fn=clock.now,
    )
    # Before any note: action cooldown is treated as elapsed.
    ctx = loop.build_gate_context()
    assert ctx.seconds_since_last_action == float("inf")

    loop.note_action()
    clock.advance(60.0)
    ctx = loop.build_gate_context()
    assert ctx.seconds_since_last_action == pytest.approx(60.0, abs=0.01)


async def test_note_event_updates_min_quiet_cooldown() -> None:
    clock = _FakeClock(start=1000.0)
    loop = _loop(
        config=GateConfig(min_quiet_s=30.0),
        now_fn=clock.now,
    )
    loop.note_event()
    clock.advance(10.0)
    # default gate: should block because min_quiet not met
    result = await loop.tick()
    assert result.gate_passed is False
    assert result.gate_skip_reason == "min_quiet"


async def test_custom_gate_fn_receives_context_and_overrides() -> None:
    seen: list[GateRollContext] = []

    def gate(ctx: GateRollContext) -> bool:
        seen.append(ctx)
        return True

    loop = _loop(gate_fn=gate)
    result = await loop.tick()
    assert len(seen) == 1
    assert result.gate_passed is True
    # custom gate is opaque — skip_reason is None on pass
    assert result.gate_skip_reason is None


async def test_tick_result_carries_gate_probability_under_default_gate() -> None:
    cfg = GateConfig(base_probability=0.99, mood_arousal_bias=0.0, min_quiet_s=0.0)
    loop = _loop(config=cfg, rng=_rng_returning(0.5))
    result = await loop.tick()
    assert result.gate_probability == pytest.approx(0.99, abs=0.01)


async def test_contemplation_cooldown_blocks_back_to_back_thoughts() -> None:
    clock = _FakeClock(start=1000.0)
    cfg = GateConfig(
        base_probability=1.0,
        mood_arousal_bias=0.0,
        cooldown_after_action_s=0.0,
        cooldown_after_contemplation_s=60.0,
        min_quiet_s=0.0,
    )
    loop = _loop(config=cfg, now_fn=clock.now)
    first = await loop.tick()
    assert first.gate_passed is True
    # Immediate second tick should be blocked by contemplation cooldown.
    second = await loop.tick()
    assert second.gate_passed is False
    assert second.gate_skip_reason == "cooldown_contemplation"
    # After the cooldown expires, the gate passes again.
    clock.advance(61.0)
    third = await loop.tick()
    assert third.gate_passed is True


async def test_action_cooldown_blocks_after_note_action() -> None:
    clock = _FakeClock(start=1000.0)
    cfg = GateConfig(
        base_probability=1.0,
        mood_arousal_bias=0.0,
        cooldown_after_action_s=300.0,
        cooldown_after_contemplation_s=0.0,
        min_quiet_s=0.0,
    )
    loop = _loop(config=cfg, now_fn=clock.now)
    loop.note_action()
    result = await loop.tick()
    assert result.gate_passed is False
    assert result.gate_skip_reason == "cooldown_action"


async def test_tick_elapsed_seconds_is_non_negative() -> None:
    loop = _loop(gate_fn=lambda ctx: True)
    result = await loop.tick()
    assert result.elapsed_seconds >= 0.0


# ── helpers ─────────────────────────────────────────────────────────────


class _FakeClock:
    def __init__(self, start: float) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class _ScriptedRandom(random.Random):
    """A Random whose ``random()`` returns scripted values in order, then
    falls back to the parent for everything else (e.g. ``choices``)."""

    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self._values = list(values)

    def random(self) -> float:  # type: ignore[override]
        if self._values:
            return self._values.pop(0)
        return super().random()


def _rng_returning(value: float) -> random.Random:
    return _ScriptedRandom([value])
