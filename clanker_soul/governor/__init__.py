"""``clanker_soul.governor`` — VADUGWI Safety Governor.

Translates the agent's current emotional state into operational
restrictions. Three concerns:

  - :py:func:`assess_capability` — what tools should the agent be
    allowed to use right now? Returns a :py:class:`CapabilityLevel`.
  - :py:func:`crisis_signal` — is this distress an emotional spike or
    a real-world emergency? Returns a :py:class:`CrisisDiagnosis`.
  - :py:func:`compose_state_context` — produces the human-readable
    string the host injects into the agent's prompt so the agent
    *knows* what state it's in and why.

The user-facing API lives on :py:class:`SoulPlugin`:

    plugin.capability_level()    → CapabilityLevel
    plugin.crisis_signal()       → CrisisDiagnosis
    plugin.state_context()       → str (for system-prompt injection)

Hosts that need custom thresholds can construct their own
:py:class:`GovernorConfig` and pass it to the plugin (or to the
underlying functions directly for advanced use)."""
from clanker_soul.governor.assessment import assess_capability
from clanker_soul.governor.context import compose_state_context
from clanker_soul.governor.crisis import CrisisDiagnosis, crisis_signal
from clanker_soul.governor.gate import CapabilityGate, GateDecision
from clanker_soul.governor.levels import (
    DEFAULT_CAPABILITY_PROFILES,
    STRICT_CAPABILITY_PROFILES,
    CapabilityLevel,
    CapabilityProfile,
    GovernorConfig,
)

__all__ = [
    "CapabilityLevel",
    "CapabilityProfile",
    "DEFAULT_CAPABILITY_PROFILES",
    "STRICT_CAPABILITY_PROFILES",
    "GovernorConfig",
    "CapabilityGate",
    "GateDecision",
    "CrisisDiagnosis",
    "assess_capability",
    "crisis_signal",
    "compose_state_context",
]
