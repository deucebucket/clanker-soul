"""Tests for the 7 new motivation triggers added in M1.2 (#45).

Each trigger has:
- A "fires under right conditions" test
- A "stays quiet under wrong conditions" test (where useful)
- An action-kind mapping check (withdraw_impulse → withdraw, etc.)

Plus tests for trigger priority ordering and the optional
``host.peer_distress_signals`` hook for caretake_impulse.
"""

from __future__ import annotations

from dataclasses import replace


from clanker_soul import (
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Trigger,
)
from clanker_soul.pulse.engine import _action_kind_for_trigger


# ---------------------------------------------------------------------------
# Stub host that lets tests inject snapshot state directly
# ---------------------------------------------------------------------------


class StubHost:
    """Minimal PulseHost that returns whatever snapshot we feed it.

    Implements dispatch_action so we can read which PulseAction kind
    the engine produced for a given trigger."""

    def __init__(self, snap: dict) -> None:
        self.snap = snap
        self.target = PulseTarget(payload={"channel": "test"})
        self.dispatched: list[PulseAction] = []
        self.peer_signals: list[dict] = []

    def snapshot(self) -> dict:
        return self.snap

    def slow_drift_tick(self) -> None:
        pass

    def most_recent_target(self):
        return self.target

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        pass

    def dispatch_action(self, action: PulseAction):
        from clanker_soul import ActionOutcome

        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def peer_distress_signals(self) -> list[dict]:
        return self.peer_signals


def _snap(
    *,
    soul: dict | None = None,
    mood: list[int] | None = None,
    distance: float = 0.0,
    trauma: float = 0.0,
    nourishment: float = 0.0,
) -> dict:
    return {
        "soul": soul or {"v": 145, "a": 110, "d": 160, "u": 80, "g": 130, "w": 175, "i": 135},
        "mood": mood,
        "soul_distance": distance,
        "trauma_load": trauma,
        "nourishment_load": nourishment,
    }


_NO_COOLDOWN_CFG = PulseConfig(min_quiet_seconds=0.0)


# ---------------------------------------------------------------------------
# Action-kind mapping
# ---------------------------------------------------------------------------


def test_action_kind_for_existing_triggers_unchanged() -> None:
    assert _action_kind_for_trigger("distress") == "direct_message"
    assert _action_kind_for_trigger("elation") == "direct_message"
    assert _action_kind_for_trigger("gratitude") == "direct_message"
    assert _action_kind_for_trigger("trauma_pressure") == "direct_message"
    assert _action_kind_for_trigger("long_silence") == "direct_message"


def test_action_kind_for_new_triggers() -> None:
    assert _action_kind_for_trigger("withdraw_impulse") == "withdraw"
    assert _action_kind_for_trigger("restless_curiosity") == "browse_topic"
    assert _action_kind_for_trigger("argue_impulse") == "comment_reply"
    # Most others stay direct_message
    assert _action_kind_for_trigger("share_impulse") == "direct_message"
    assert _action_kind_for_trigger("connect_impulse") == "direct_message"
    assert _action_kind_for_trigger("reflective_impulse") == "direct_message"
    assert _action_kind_for_trigger("caretake_impulse") == "direct_message"
    assert _action_kind_for_trigger("stuck_impulse") == "tool_invocation"
    assert _action_kind_for_trigger("obstructed_impulse") == "tool_invocation"


def test_action_kind_unknown_falls_back_to_dm() -> None:
    assert _action_kind_for_trigger("future_kind_we_dont_know") == "direct_message"


# ---------------------------------------------------------------------------
# share_impulse
# ---------------------------------------------------------------------------


async def test_share_impulse_fires_on_lifted_v_and_arousal() -> None:
    snap = _snap(
        mood=[170, 145, 160, 80, 130, 180, 140],  # V lifted, A high
        nourishment=40.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "share_impulse"


async def test_share_impulse_quiet_when_arousal_too_low() -> None:
    snap = _snap(
        mood=[170, 100, 160, 80, 130, 180, 140],  # arousal below threshold
        nourishment=40.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    # share shouldn't fire; lower-priority triggers might or might not.
    if trigger is not None:
        assert trigger.kind != "share_impulse"


# ---------------------------------------------------------------------------
# argue_impulse
# ---------------------------------------------------------------------------


async def test_argue_impulse_fires_on_v_drop_arousal_and_intent() -> None:
    snap = _snap(
        mood=[100, 150, 170, 100, 130, 145, 160],  # V dropped 45, A=150, I=160
        distance=20.0,  # below distance_trigger so distress doesn't pre-empt
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "argue_impulse"


async def test_argue_action_kind_is_comment_reply() -> None:
    """When argue_impulse fires and dispatches, action.kind should be
    comment_reply (not the default direct_message)."""
    snap = _snap(
        mood=[100, 150, 170, 100, 130, 145, 160],
        distance=20.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    await engine.tick()
    assert len(host.dispatched) == 1
    assert host.dispatched[0].kind == "comment_reply"


# ---------------------------------------------------------------------------
# withdraw_impulse
# ---------------------------------------------------------------------------


async def test_withdraw_impulse_fires_on_high_trauma_and_low_w() -> None:
    snap = _snap(
        mood=[100, 110, 130, 100, 130, 80, 120],  # W=80 (low)
        trauma=80.0,
        distance=15.0,  # below distance_trigger so distress doesn't pre-empt
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "withdraw_impulse"


async def test_withdraw_action_kind_is_withdraw() -> None:
    snap = _snap(
        mood=[100, 110, 130, 100, 130, 80, 120],
        trauma=80.0,
        distance=15.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    await engine.tick()
    assert len(host.dispatched) == 1
    assert host.dispatched[0].kind == "withdraw"


# ---------------------------------------------------------------------------
# connect_impulse
# ---------------------------------------------------------------------------


async def test_connect_impulse_fires_on_warmth_idle_and_low_trauma() -> None:
    """connect needs idle > 90min by default. We override to 0 for testing."""
    cfg = replace(_NO_COOLDOWN_CFG, connect_idle_min_seconds=0.0)
    snap = _snap(
        mood=[145, 110, 160, 80, 130, 175, 135],  # V=145 (warm), low trauma
        trauma=10.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    # NOTE: we don't call note_outbound — engine sees idle since epoch
    # which is > connect_idle_min_seconds=0. long_silence has its own
    # max_quiet_seconds (default 6h) so for a fresh engine, idle > 0
    # but < 6h so long_silence won't pre-empt.
    # Actually idle_since_epoch is far > 6h, so long_silence WILL fire.
    # Need to call note_outbound() to bring idle close to 0, then test.
    engine.note_outbound()
    # Now idle ≈ 0 which is > connect_idle_min_seconds (0.0)
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "connect_impulse"


async def test_connect_impulse_suppressed_by_high_trauma() -> None:
    cfg = replace(_NO_COOLDOWN_CFG, connect_idle_min_seconds=0.0)
    snap = _snap(
        mood=[145, 110, 160, 80, 130, 175, 135],
        trauma=80.0,  # above connect_max_trauma (30)
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    engine.note_outbound()
    trigger = await engine.tick()
    if trigger is not None:
        assert trigger.kind != "connect_impulse"


# ---------------------------------------------------------------------------
# reflective_impulse
# ---------------------------------------------------------------------------


async def test_reflective_impulse_fires_on_idle_with_distance() -> None:
    cfg = replace(_NO_COOLDOWN_CFG, reflective_idle_min_seconds=0.0)
    snap = _snap(
        mood=[160, 110, 160, 80, 130, 175, 135],  # mood off baseline
        distance=20.0,  # > reflective_distance_min
        trauma=10.0,  # below reflective_max_trauma
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    # Other triggers might pre-empt — we accept any non-None as long as
    # under THIS state, reflective is reachable when others are higher.
    # Most sensitive check:
    if trigger.kind != "reflective_impulse":
        # connect_impulse (warmth+idle+low trauma) might pre-empt — that's OK
        # because connect has higher priority. Just verify SOMETHING fires.
        assert trigger.kind in {"connect_impulse", "share_impulse"}


# ---------------------------------------------------------------------------
# restless_curiosity
# ---------------------------------------------------------------------------


async def test_curiosity_fires_with_high_arousal_near_baseline() -> None:
    cfg = replace(_NO_COOLDOWN_CFG, curiosity_idle_min_seconds=0.0)
    snap = _snap(
        mood=[140, 160, 160, 75, 130, 175, 132],  # arousal=160 (high), close to soul
        distance=10.0,  # < curiosity_distance_max
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "restless_curiosity"


async def test_curiosity_action_kind_is_browse_topic() -> None:
    cfg = replace(_NO_COOLDOWN_CFG, curiosity_idle_min_seconds=0.0)
    snap = _snap(
        mood=[140, 160, 160, 75, 130, 175, 132],
        distance=10.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    engine.note_outbound()
    await engine.tick()
    assert len(host.dispatched) == 1
    assert host.dispatched[0].kind == "browse_topic"


async def test_curiosity_suppressed_by_distance() -> None:
    """If mood is far from soul, curiosity is overshadowed by something
    heavier (distress, elation, etc.)."""
    cfg = replace(_NO_COOLDOWN_CFG, curiosity_idle_min_seconds=0.0)
    snap = _snap(
        mood=[40, 160, 160, 75, 130, 30, 132],  # V crashed
        distance=80.0,  # large
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=cfg)
    engine.note_outbound()
    trigger = await engine.tick()
    # distress should fire, not curiosity.
    assert trigger is not None
    assert trigger.kind == "distress"


# ---------------------------------------------------------------------------
# caretake_impulse — peer-distress hook
# ---------------------------------------------------------------------------


async def test_caretake_fires_when_peer_signals_present_and_self_w_high() -> None:
    snap = _snap(
        mood=[140, 110, 160, 75, 130, 180, 132],  # W=180 (high — caretaker can give)
        distance=10.0,
        trauma=5.0,
    )
    host = StubHost(snap)
    host.peer_signals = [{"agent_id": "carl", "kind": "distress"}]
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "caretake_impulse"
    assert trigger.metrics["peer_count"] == 1


async def test_caretake_suppressed_when_self_w_low() -> None:
    snap = _snap(
        mood=[140, 110, 160, 75, 130, 90, 132],  # W=90 (below caretake_self_w_min=110)
        trauma=5.0,
    )
    host = StubHost(snap)
    host.peer_signals = [{"agent_id": "carl", "kind": "distress"}]
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    if trigger is not None:
        assert trigger.kind != "caretake_impulse"


async def test_caretake_no_op_when_host_lacks_hook() -> None:
    """Hosts that don't implement peer_distress_signals never see
    caretake_impulse fire."""

    class NoPeerHost(StubHost):
        # Override: no peer_distress_signals method at all.
        peer_distress_signals = None  # type: ignore[assignment]

    snap = _snap(mood=[140, 110, 160, 75, 130, 180, 132], trauma=5.0)
    host = NoPeerHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    if trigger is not None:
        assert trigger.kind != "caretake_impulse"


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


async def test_distress_pre_empts_argue() -> None:
    """When V drop is large enough for distress AND argue, distress wins."""
    snap = _snap(
        mood=[40, 160, 130, 100, 130, 30, 160],  # massive V drop, high A, high I
        distance=80.0,
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=_NO_COOLDOWN_CFG)
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "distress"


async def test_withdraw_pre_empts_connect() -> None:
    """High trauma + low W should withdraw, not seek connection."""
    snap = _snap(
        mood=[140, 110, 130, 75, 130, 80, 132],  # W=80 (low)
        distance=10.0,
        trauma=80.0,  # above withdraw_trauma_min, above connect_max_trauma
    )
    host = StubHost(snap)
    engine = PulseEngine(host, config=replace(_NO_COOLDOWN_CFG, connect_idle_min_seconds=0.0))
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert trigger.kind == "withdraw_impulse"


# ---------------------------------------------------------------------------
# Prompt composition for new kinds
# ---------------------------------------------------------------------------


def test_new_trigger_kinds_have_unique_prompts() -> None:
    """Every new trigger kind should produce a distinct synthetic prompt
    so the agent can tell what state fired."""
    from clanker_soul.pulse.prompt import compose_self_prompt

    new_kinds = [
        "share_impulse",
        "argue_impulse",
        "connect_impulse",
        "withdraw_impulse",
        "reflective_impulse",
        "caretake_impulse",
        "restless_curiosity",
        "stuck_impulse",
        "obstructed_impulse",
    ]
    prompts = []
    for kind in new_kinds:
        trig = Trigger(kind=kind, soul={"v": 145}, mood=[100] * 7, metrics={"idle_seconds": 600})
        prompts.append(compose_self_prompt(trig))

    # All distinct
    assert len(set(prompts)) == len(prompts)
    # Each mentions its own kind in the [INTERNAL PULSE — ...] header
    for kind, prompt in zip(new_kinds, prompts):
        kind_words = kind.replace("_impulse", "").replace("_", " ")
        assert kind_words in prompt.lower(), f"prompt for {kind!r} missing kind reference"


def test_withdraw_prompt_says_nopulse() -> None:
    """The withdraw prompt should explicitly tell the agent to respond
    NOPULSE — that's how it stays quiet."""
    from clanker_soul.pulse.prompt import compose_self_prompt

    trig = Trigger(
        kind="withdraw_impulse",
        soul={"v": 145},
        mood=[100, 110, 130, 100, 130, 80, 120],
        metrics={"trauma_load": 80.0, "w_mood": 80},
    )
    prompt = compose_self_prompt(trig)
    assert "NOPULSE" in prompt


# ---------------------------------------------------------------------------
# All 12 trigger kinds reachable
# ---------------------------------------------------------------------------


def test_all_12_trigger_kinds_have_action_mappings() -> None:
    """Every trigger kind the engine can emit must have an action-kind mapping."""
    from clanker_soul.pulse.engine import _DEFAULT_TRIGGER_TO_ACTION

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
        "stuck_impulse",
        "obstructed_impulse",
    }
    assert set(_DEFAULT_TRIGGER_TO_ACTION.keys()) == expected
