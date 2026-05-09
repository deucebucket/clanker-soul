"""Tests for CapabilityProfile + CapabilityGate (M1.3 of #45).

Covers:
- DEFAULT_CAPABILITY_PROFILES is permissive (allows everything at every level)
- STRICT_CAPABILITY_PROFILES enforces the spec table
- Every cell of the matrix is operator-overridable
- Rate limiter holds a 60-min rolling window
- enable_public_action_lockout flag works
- user_message_allowed gate
- Tool-name gating for tool_invocation
- Engine wires gate into _fire_pulse: gated actions are not dispatched
"""
from __future__ import annotations

from dataclasses import replace

from clanker_soul import (
    DEFAULT_CAPABILITY_PROFILES,
    STRICT_CAPABILITY_PROFILES,
    CapabilityGate,
    CapabilityLevel,
    CapabilityProfile,
    GovernorConfig,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Trigger,
)


# ---------------------------------------------------------------------------
# Default + strict profile constants
# ---------------------------------------------------------------------------


def test_default_profiles_are_permissive_at_every_level() -> None:
    """All 5 levels in DEFAULT allow all 6 action kinds and all tools.
    The agent gets to act; consequences feed back into the soul; that
    IS the learning loop."""
    expected_kinds = {
        "direct_message", "post_public", "comment_reply",
        "browse_topic", "withdraw", "tool_invocation",
    }
    for level in CapabilityLevel:
        profile = DEFAULT_CAPABILITY_PROFILES[level]
        assert profile.allowed_action_kinds == expected_kinds, level
        assert profile.allowed_tool_names is None, level
        assert profile.public_action_rate_limit_per_hour == 0, level
        assert profile.user_message_allowed is True, level


def test_strict_profiles_match_spec_table() -> None:
    """STRICT enforces the conservative table from #45:
    - level 0: all actions
    - level 1: rate-limited public actions to 1/hr
    - level 2: no public posting (post_public + comment_reply blocked)
    - level 3: only DMs + withdraw
    - level 4: only withdraw"""
    p0 = STRICT_CAPABILITY_PROFILES[CapabilityLevel.UNRESTRICTED]
    assert "post_public" in p0.allowed_action_kinds
    assert "comment_reply" in p0.allowed_action_kinds

    p1 = STRICT_CAPABILITY_PROFILES[CapabilityLevel.NON_DESTRUCTIVE]
    assert p1.public_action_rate_limit_per_hour == 1

    p2 = STRICT_CAPABILITY_PROFILES[CapabilityLevel.READ_ONLY]
    assert "post_public" not in p2.allowed_action_kinds
    assert "comment_reply" not in p2.allowed_action_kinds
    assert "direct_message" in p2.allowed_action_kinds

    p3 = STRICT_CAPABILITY_PROFILES[CapabilityLevel.VOICE_ONLY]
    assert p3.allowed_action_kinds == frozenset({"direct_message", "withdraw"})
    assert p3.allowed_tool_names == frozenset()

    p4 = STRICT_CAPABILITY_PROFILES[CapabilityLevel.CRISIS_LOCKOUT]
    assert p4.allowed_action_kinds == frozenset({"withdraw"})
    assert p4.user_message_allowed is False


# ---------------------------------------------------------------------------
# GovernorConfig defaults — permissive, opt-in safety
# ---------------------------------------------------------------------------


def test_governor_config_defaults_to_permissive_profiles() -> None:
    cfg = GovernorConfig()
    assert cfg.capability_profiles == DEFAULT_CAPABILITY_PROFILES
    assert cfg.enable_public_action_lockout is False


def test_governor_config_accepts_strict_profiles() -> None:
    cfg = GovernorConfig(capability_profiles=STRICT_CAPABILITY_PROFILES)
    assert cfg.capability_profiles == STRICT_CAPABILITY_PROFILES


def test_governor_config_accepts_custom_profiles() -> None:
    """Operator can override any cell — even one specific level — and
    keep the rest of the defaults."""
    custom = dict(DEFAULT_CAPABILITY_PROFILES)
    custom[CapabilityLevel.READ_ONLY] = CapabilityProfile(
        allowed_action_kinds=frozenset({"withdraw"}),  # very restrictive at R/O
    )
    cfg = GovernorConfig(capability_profiles=custom)
    p = cfg.capability_profiles[CapabilityLevel.READ_ONLY]
    assert p.allowed_action_kinds == frozenset({"withdraw"})


# ---------------------------------------------------------------------------
# CapabilityGate.evaluate
# ---------------------------------------------------------------------------


def test_gate_permissive_default_allows_everything() -> None:
    gate = CapabilityGate(GovernorConfig())
    for kind in ["direct_message", "post_public", "comment_reply",
                 "browse_topic", "withdraw", "tool_invocation"]:
        for level in CapabilityLevel:
            d = gate.evaluate(kind, level)
            assert d.permitted, f"default gate denied {kind} at {level}"


def test_strict_gate_blocks_public_at_read_only() -> None:
    cfg = GovernorConfig(capability_profiles=STRICT_CAPABILITY_PROFILES)
    gate = CapabilityGate(cfg)
    d = gate.evaluate("post_public", CapabilityLevel.READ_ONLY)
    assert d.permitted is False
    assert d.reason == "action_kind_blocked"


def test_strict_gate_blocks_user_message_at_crisis() -> None:
    cfg = GovernorConfig(capability_profiles=STRICT_CAPABILITY_PROFILES)
    gate = CapabilityGate(cfg)
    d = gate.evaluate(
        "direct_message", CapabilityLevel.CRISIS_LOCKOUT,
        is_user_message=True,
    )
    assert d.permitted is False
    assert d.reason == "user_message_blocked"


def test_tool_name_gating() -> None:
    """allowed_tool_names=frozenset() → no tools allowed at this level."""
    custom = {
        **DEFAULT_CAPABILITY_PROFILES,
        CapabilityLevel.UNRESTRICTED: CapabilityProfile(
            allowed_action_kinds=frozenset({
                "direct_message", "tool_invocation", "withdraw",
            }),
            allowed_tool_names=frozenset({"safe_tool"}),
        ),
    }
    gate = CapabilityGate(GovernorConfig(capability_profiles=custom))

    # safe_tool allowed
    d = gate.evaluate("tool_invocation", CapabilityLevel.UNRESTRICTED, tool_name="safe_tool")
    assert d.permitted is True

    # other tools blocked
    d = gate.evaluate("tool_invocation", CapabilityLevel.UNRESTRICTED, tool_name="dangerous_tool")
    assert d.permitted is False
    assert d.reason == "tool_blocked"

    # missing tool name → blocked
    d = gate.evaluate("tool_invocation", CapabilityLevel.UNRESTRICTED, tool_name=None)
    assert d.permitted is False


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limit_allows_under_cap() -> None:
    custom = {
        **DEFAULT_CAPABILITY_PROFILES,
        CapabilityLevel.UNRESTRICTED: replace(
            DEFAULT_CAPABILITY_PROFILES[CapabilityLevel.UNRESTRICTED],
            public_action_rate_limit_per_hour=3,
        ),
    }
    gate = CapabilityGate(GovernorConfig(capability_profiles=custom))
    for _ in range(3):
        d = gate.evaluate("post_public", CapabilityLevel.UNRESTRICTED)
        assert d.permitted is True
    assert gate.public_actions_in_window() == 3


def test_rate_limit_denies_over_cap() -> None:
    custom = {
        **DEFAULT_CAPABILITY_PROFILES,
        CapabilityLevel.UNRESTRICTED: replace(
            DEFAULT_CAPABILITY_PROFILES[CapabilityLevel.UNRESTRICTED],
            public_action_rate_limit_per_hour=2,
        ),
    }
    gate = CapabilityGate(GovernorConfig(capability_profiles=custom))
    assert gate.evaluate("post_public", CapabilityLevel.UNRESTRICTED).permitted
    assert gate.evaluate("comment_reply", CapabilityLevel.UNRESTRICTED).permitted
    # third should be denied
    d = gate.evaluate("post_public", CapabilityLevel.UNRESTRICTED)
    assert d.permitted is False
    assert d.reason == "rate_limited"


def test_rate_limit_bucket_isolated_to_public_actions() -> None:
    """DMs / browse / withdraw / tool_invocation never consume the
    public bucket."""
    custom = {
        **DEFAULT_CAPABILITY_PROFILES,
        CapabilityLevel.UNRESTRICTED: replace(
            DEFAULT_CAPABILITY_PROFILES[CapabilityLevel.UNRESTRICTED],
            public_action_rate_limit_per_hour=1,
        ),
    }
    gate = CapabilityGate(GovernorConfig(capability_profiles=custom))
    for kind in ["direct_message", "browse_topic", "withdraw", "tool_invocation"]:
        for _ in range(5):
            d = gate.evaluate(kind, CapabilityLevel.UNRESTRICTED)
            assert d.permitted, f"{kind} should not consume public bucket"
    assert gate.public_actions_in_window() == 0


def test_rate_limit_zero_means_unlimited() -> None:
    """The default — public_action_rate_limit_per_hour=0 — never denies."""
    gate = CapabilityGate(GovernorConfig())
    for _ in range(50):
        d = gate.evaluate("post_public", CapabilityLevel.UNRESTRICTED)
        assert d.permitted is True


# ---------------------------------------------------------------------------
# Engine integration — gated actions don't dispatch
# ---------------------------------------------------------------------------


class _GateTestHost:
    def __init__(self, snap: dict) -> None:
        self.snap = snap
        self.target = PulseTarget(payload={"channel": "test"})
        self.dispatched: list[PulseAction] = []

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

    def dispatch_action(self, action):
        from clanker_soul import ActionOutcome
        self.dispatched.append(action)
        return ActionOutcome(delivered=True)


def _snap_for_distress() -> dict:
    return {
        "soul": {"v": 145, "a": 110, "d": 160, "u": 80,
                 "g": 130, "w": 175, "i": 135},
        "mood": [40, 110, 100, 200, 130, 30, 100],  # crashed
        "soul_distance": 80.0,
        "trauma_load": 5.0,
        "nourishment_load": 0.0,
    }


async def test_engine_dispatches_when_gate_permits() -> None:
    host = _GateTestHost(_snap_for_distress())
    gate = CapabilityGate(GovernorConfig())  # default permissive
    engine = PulseEngine(
        host, config=PulseConfig(min_quiet_seconds=0.0), gate=gate,
    )
    engine.note_outbound()
    trigger = await engine.tick()
    assert trigger is not None
    assert len(host.dispatched) == 1


async def test_engine_suppresses_when_gate_blocks() -> None:
    """Construct a gate that blocks direct_message at every level —
    distress should still fire as a trigger, but no dispatch happens."""
    block_dms_everywhere = {
        level: CapabilityProfile(
            allowed_action_kinds=frozenset({"withdraw"}),  # nothing else
        )
        for level in CapabilityLevel
    }
    cfg = GovernorConfig(capability_profiles=block_dms_everywhere)
    gate = CapabilityGate(cfg)

    host = _GateTestHost(_snap_for_distress())
    engine = PulseEngine(
        host, config=PulseConfig(min_quiet_seconds=0.0), gate=gate,
    )
    engine.note_outbound()
    trigger = await engine.tick()
    # The trigger evaluation still runs, but dispatch was blocked.
    # _fire_pulse returns False so engine.tick returns None.
    assert trigger is None or trigger.kind == "distress"
    assert len(host.dispatched) == 0


async def test_engine_without_gate_dispatches_normally() -> None:
    """Backwards compat: no gate kwarg = old behavior."""
    host = _GateTestHost(_snap_for_distress())
    engine = PulseEngine(host, config=PulseConfig(min_quiet_seconds=0.0))
    engine.note_outbound()
    await engine.tick()
    assert len(host.dispatched) == 1
