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
"""
from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["CapabilityLevel", "GovernorConfig"]
