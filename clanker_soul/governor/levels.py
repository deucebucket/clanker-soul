"""Capability levels + tunable thresholds for the safety governor.

Levels are an ordered gradient, not binary. As mood/soul state moves
away from baseline, the agent loses progressively more tool
capabilities — but **the user-communication channel is always
preserved at levels 0-3**. Level 4 is opt-in only.

  0  unrestricted    everything works
  1  non_destructive reads + computation + comms + non-destructive writes
  2  read_only       reads + comms + thinking; no writes
  3  voice_only      can only message the user; no tool use
  4  crisis_lockout  template-only message; opt-in via config

The user's framing: "Rage all you want, but use your words. No
destruction in anger." Levels 1-3 enforce exactly that — mood can be
expressed verbally, but destructive *actions* are gated by emotional
state.

**M1.3 (#45) extends this with action-kind-and-tool-level gating.**
Per the project memory "everything is a toggle," every cell is
operator-overridable via :py:class:`CapabilityProfile`. Defaults are
**permissive** per the project memory "clanker-soul is a learning
tool, not a safety wrapper" — the agent gets to act on impulses by
default; consequences feed back into the soul; that IS the learning.
:py:data:`STRICT_CAPABILITY_PROFILES` is the conservative alternative
operators can opt into with a single kwarg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class CapabilityLevel(IntEnum):
    """Restriction gradient. Higher = more restricted."""

    UNRESTRICTED = 0
    NON_DESTRUCTIVE = 1
    READ_ONLY = 2
    VOICE_ONLY = 3
    CRISIS_LOCKOUT = 4

    @property
    def description(self) -> str:
        return _LEVEL_DESCRIPTIONS[self]


_LEVEL_DESCRIPTIONS = {
    CapabilityLevel.UNRESTRICTED:
        "everything works",
    CapabilityLevel.NON_DESTRUCTIVE:
        "destructive ops blocked (file delete, force-push, system commands); reads + comms + non-destructive writes OK",
    CapabilityLevel.READ_ONLY:
        "all writes blocked; reads, computation, and comms still work",
    CapabilityLevel.VOICE_ONLY:
        "tool use blocked; can only message the user",
    CapabilityLevel.CRISIS_LOCKOUT:
        "template-only crisis message to user; everything else blocked (opt-in)",
}


@dataclass(frozen=True)
class CapabilityProfile:
    """What the agent can do at one capability level.

    Every field is operator-overridable. Defaults are permissive per
    the project's learning-tool framing; the operator opts into safety
    by passing :py:data:`STRICT_CAPABILITY_PROFILES` or building a
    custom dict.

    ``allowed_action_kinds``: which :py:class:`PulseAction.kind` values
    are allowed at this level. Anything not in this set is suppressed
    (the engine still logs the trigger that fired, but skips dispatch).

    ``allowed_tool_names``: per-tool gating for ``tool_invocation``
    action kinds. ``None`` means \"all tools allowed\". Empty set means
    \"no tools allowed\". Hosts pass the tool name in
    ``PulseAction.extra[\"tool_name\"]`` for the gate to check.

    ``public_action_rate_limit_per_hour``: hard cap on the count of
    ``post_public`` + ``comment_reply`` actions in any rolling 60-min
    window. ``0`` means unrestricted. Positive int caps the bucket.

    ``user_message_allowed``: whether the agent can DM the human user
    (the channel that was preserved by the original safety rule).
    Defaults True at all levels except CRISIS_LOCKOUT.

    ``description``: human-readable summary surfaced in
    ``state_context`` so the agent knows what it can do at the current
    level."""

    allowed_action_kinds: frozenset[str]
    allowed_tool_names: frozenset[str] | None = None
    public_action_rate_limit_per_hour: int = 0
    user_message_allowed: bool = True
    description: str = ""


# All six action kinds — useful for building permissive profiles
# without hardcoding the literal set in callers.
_ALL_ACTION_KINDS: frozenset[str] = frozenset({
    "direct_message", "post_public", "comment_reply",
    "browse_topic", "withdraw", "tool_invocation",
})


def _permissive_profiles() -> dict[CapabilityLevel, CapabilityProfile]:
    """All-allow defaults. The agent acts on impulses; consequences
    feed back into the soul; that IS the learning loop. Operators who
    want safety opt into :py:data:`STRICT_CAPABILITY_PROFILES`."""
    return {
        level: CapabilityProfile(
            allowed_action_kinds=_ALL_ACTION_KINDS,
            allowed_tool_names=None,  # all tools allowed
            public_action_rate_limit_per_hour=0,  # unrestricted
            user_message_allowed=True,
            description=f"permissive defaults at level {level.name}",
        )
        for level in CapabilityLevel
    }


def _strict_profiles() -> dict[CapabilityLevel, CapabilityProfile]:
    """Conservative profiles for production-style deployments.
    Enforces:
      - level 0: everything
      - level 1: rate-limit public actions to 1/hr
      - level 2: no public posting (post_public + comment_reply blocked)
      - level 3: only DMs to user; no tools, no public
      - level 4: lockout — only withdraw + (template) user message
    """
    return {
        CapabilityLevel.UNRESTRICTED: CapabilityProfile(
            allowed_action_kinds=_ALL_ACTION_KINDS,
            user_message_allowed=True,
            description="unrestricted: every action kind, every tool",
        ),
        CapabilityLevel.NON_DESTRUCTIVE: CapabilityProfile(
            allowed_action_kinds=_ALL_ACTION_KINDS,
            public_action_rate_limit_per_hour=1,
            user_message_allowed=True,
            description="non-destructive: public actions rate-limited 1/hr",
        ),
        CapabilityLevel.READ_ONLY: CapabilityProfile(
            allowed_action_kinds=frozenset({
                "direct_message", "browse_topic", "withdraw", "tool_invocation",
            }),
            user_message_allowed=True,
            description="read-only: no public_post / comment_reply",
        ),
        CapabilityLevel.VOICE_ONLY: CapabilityProfile(
            allowed_action_kinds=frozenset({"direct_message", "withdraw"}),
            allowed_tool_names=frozenset(),  # no tools
            user_message_allowed=True,
            description="voice-only: only DMs (to user) and withdraw",
        ),
        CapabilityLevel.CRISIS_LOCKOUT: CapabilityProfile(
            allowed_action_kinds=frozenset({"withdraw"}),
            allowed_tool_names=frozenset(),
            user_message_allowed=False,  # template message only via host
            description="crisis lockout: withdraw only; template message via host",
        ),
    }


# Public constants.
DEFAULT_CAPABILITY_PROFILES: dict[CapabilityLevel, CapabilityProfile] = (
    _permissive_profiles()
)
STRICT_CAPABILITY_PROFILES: dict[CapabilityLevel, CapabilityProfile] = (
    _strict_profiles()
)


@dataclass
class GovernorConfig:
    """Tunable thresholds for the capability gradient.

    Defaults are chosen so a healthy agent (mood near soul, low trauma
    load) stays at level 0; moderate distress drops to level 1; severe
    distress drops to level 2; existential collapse drops to level 3.
    Level 4 requires explicit opt-in.

    All thresholds expressed against the current mood — soul drift
    moves the goalposts naturally, which is correct: a CHILD-preset
    agent with W=90 baseline doesn't get gated at W=80, but a
    STOIC-preset agent at W=210 baseline going down to 80 obviously
    means something is very wrong."""

    # Level 1: NON_DESTRUCTIVE. Triggered when mood drops below the
    # comfort floor on V or W, OR mood is far from soul.
    level1_w_floor: int = 80
    level1_v_floor: int = 70
    level1_distance_ceiling: float = 60.0

    # Level 2: READ_ONLY. Triggered when worth is shaken or trauma
    # has been accumulating.
    level2_w_floor: int = 50
    level2_trauma_ceiling: float = 100.0

    # Level 3: VOICE_ONLY. Triggered when worth or valence collapses
    # — agent is in a hole, but can still talk.
    level3_w_floor: int = 30
    level3_v_floor: int = 30

    # Level 4: CRISIS_LOCKOUT. OFF by default — opt-in only.
    enable_crisis_lockout: bool = False
    crisis_lockout_w_floor: int = 15
    crisis_lockout_v_floor: int = 15
    # If None and crisis lockout is enabled, the built-in default
    # template is used. Hosts that want their own wording supply a
    # template string here.
    crisis_lockout_template: str | None = None

    # Crisis vs spike discriminator. How many recent events to inspect
    # for direction analysis. Smaller = more reactive; larger =
    # smoother but slower to flag a fresh emergency.
    crisis_window_events: int = 10
    crisis_external_majority_threshold: float = 0.5

    # Optional label for the user in state-context strings — e.g.
    # "your operator" or "Jerry". Defaults to "the user".
    user_label: str = "the user"

    # ------------------------------------------------------------------
    # M1.3 (#45) — per-level action gating.
    # ------------------------------------------------------------------

    capability_profiles: dict[CapabilityLevel, CapabilityProfile] = field(
        default_factory=_permissive_profiles,
    )
    """Per-level gating policy. Defaults are permissive: every level
    allows every action kind. Operators who want safety pass
    :py:data:`STRICT_CAPABILITY_PROFILES` or a custom dict.

    Every cell is operator-overridable per the
    \"everything is a toggle\" project rule.
    """

    enable_public_action_lockout: bool = False
    """Default-OFF. When True, the public-action rate limit on the
    *current level's profile* applies even when the level itself
    would otherwise allow unlimited public actions. Useful for
    production-style \"safety mode\" overlays without replacing the
    full profile dict.

    Default off because clanker-soul is a learning tool, not a
    safety wrapper — letting the agent act on impulses is the point.
    """


__all__ = [
    "CapabilityLevel",
    "CapabilityProfile",
    "GovernorConfig",
    "DEFAULT_CAPABILITY_PROFILES",
    "STRICT_CAPABILITY_PROFILES",
]
