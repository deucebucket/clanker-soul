"""Personality presets — bundled (SoulState, PhysicsConfig) tuples that
shape an agent's emotional baseline and dynamics.

Why presets exist
-----------------
"Soul can start anywhere" is the design realization that motivates
this module. The package's ADULT defaults are mildly positive,
in-control, strong-worth — appropriate for a competent assistant. But
agents are not all assistants. A child-shaped agent — easily moved,
high arousal, low control, fragile worth — is a perfectly valid
configuration; you just can't get there with the default soul. CHILD
preset gives you that with one click.

Each preset is a complete (SoulState, PhysicsConfig) bundle.
Calling :py:meth:`Preset.apply` writes ALL physics fields and the
personality soul fields (V/A/D/U/G/W/I) into the overrides table —
not just the customized ones — so switching presets is a clean
replacement, not a confusing merge.

The four built-ins
------------------
``CHILD``    — easily influenced, eager, ungrounded
``ADULT``    — the package defaults; competent, settled
``BRITTLE``  — feels every event; armor turned WAY down
``STOIC``    — slow to move; high baseline gravity

Custom presets are encouraged. The tuple shape is intentionally small
so anyone can add their own.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from clanker_soul.overrides import ConfigOverrides
from clanker_soul.physics import PhysicsConfig
from clanker_soul.soul import SoulState


@dataclass(frozen=True)
class Preset:
    """A named (SoulState, PhysicsConfig) bundle.

    ``apply(overrides, agent_id)`` writes every PhysicsConfig field and
    every personality SoulState field (V/A/D/U/G/W/I) into the agent's
    override row. Bookkeeping soul fields (``last_drift_ts``,
    ``last_save_ts``) are intentionally NOT overridden — they're
    runtime state, not personality."""

    name: str
    description: str
    soul: SoulState
    config: PhysicsConfig

    def apply(self, overrides: ConfigOverrides, agent_id: str) -> None:
        """Write the full preset bundle into the agent's override row.

        Replaces any existing overrides outright (uses
        :py:meth:`ConfigOverrides.set`) — switching presets must not
        merge stale knobs from the previous one."""
        physics_dict = asdict(self.config)
        bookkeeping = {"last_drift_ts", "last_save_ts"}
        soul_dict = {
            f.name: getattr(self.soul, f.name)
            for f in fields(self.soul)
            if f.name not in bookkeeping
        }
        overrides.set(agent_id, physics=physics_dict, soul=soul_dict)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------


CHILD = Preset(
    name="child",
    description=(
        "Easily influenced, eager, ungrounded. Low W (fragile worth), "
        "low D (looks for direction), high A (excitable), high I "
        "(forward-leaning intent), ungrounded G. Naturally low soul-"
        "armor — events land hard, soul drift is fast. Use this for "
        "agents that should be shaped by their environment rather than "
        "imposing a stable baseline."
    ),
    soul=SoulState(v=145, a=170, d=80, u=100, g=110, w=90, i=180),
    config=PhysicsConfig(
        # Child agents accumulate trauma/nourishment faster — give Soul
        # drift more pull so the baseline reshapes quickly with sustained
        # input. Other physics knobs stay at adult defaults.
        soul_drift_per_hour=0.0024,
    ),
)


ADULT = Preset(
    name="adult",
    description=(
        "The package's reference baseline. Mildly positive valence, "
        "in-control dominance, strong self-worth, slightly grounded. "
        "Neutral input does not read as depression. Use this for "
        "competent-assistant-style agents where stability is a feature."
    ),
    soul=SoulState(),
    config=PhysicsConfig(),
)


BRITTLE = Preset(
    name="brittle",
    description=(
        "Feels every event. Medium W with armor cap turned WAY down "
        "and dim-resilience nearly disabled. Lower breach threshold "
        "means smaller mood-vs-soul gaps trigger soul-leak on heavy "
        "events. Useful for vulnerability narratives or testing how "
        "an agent reacts when its defenses are stripped."
    ),
    soul=SoulState(v=128, a=140, d=120, u=100, g=120, w=130, i=130),
    config=PhysicsConfig(
        armor_max=0.25,
        dim_resilience_max=0.2,
        breach_threshold=20.0,
        wounding_rate=0.0015,
    ),
)


STOIC = Preset(
    name="stoic",
    description=(
        "Slow to move, strong baseline gravity. High W and high D "
        "produce maximum soul-armor; low blend_alpha + high "
        "dim-resilience pull mood back toward soul aggressively. "
        "Heavy events still register but recovery is fast. Use this "
        "for steady, hard-to-rattle agents."
    ),
    soul=SoulState(v=145, a=100, d=210, u=60, g=140, w=210, i=140),
    config=PhysicsConfig(
        armor_max=0.85,
        blend_alpha=0.3,
        dim_resilience_max=0.65,
        mood_decay_half_life_base=300.0,  # faster recovery
    ),
)


ALL: dict[str, Preset] = {p.name: p for p in (CHILD, ADULT, BRITTLE, STOIC)}


__all__ = [
    "Preset",
    "CHILD",
    "ADULT",
    "BRITTLE",
    "STOIC",
    "ALL",
]
