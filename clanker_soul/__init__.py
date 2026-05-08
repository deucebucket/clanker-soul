"""clanker-soul — the 3-layer VADUGWI runtime.

Layers
------
1. **Conversational** — per-event ``Score`` (V/A/D/U/G/W/I, 0-255). Whatever
   produced it (an LLM scorer, the clanker-lang engine, a hand-written rule)
   is the host's concern. clanker-soul ingests it.
2. **Mood** — fast-moving working state. Updated each ingest by blending in
   the new event, then pulled gently back toward Soul via dim-resilience,
   then optionally primed forward through ``mood_prime_score`` so the
   *next* perception remembers this one.
3. **Soul** — the slow-moving baseline. Drifts toward sustained mood over
   days. Heavy events during an unhealed wound bypass the slow filter and
   leak straight into Soul (the breach mechanic). Persisted to SQLite.

Trauma and Nourishment are pattern-keyed reservoirs (14d half-life) that
let "the same wound poked again" be detected differently from "many
unrelated bad days."

The ``PulseEngine`` is host-agnostic: hosts implement ``PulseHost`` and
the engine fires self-prompts when mood drifts far enough from soul or
trauma load passes thresholds.
"""
from clanker_soul.physics import (
    EmotionalPhysics,
    HEAVY_PATTERNS,
    POSITIVE_PATTERNS,
    PhysicsConfig,
    PhysicsTick,
    dim_resilience,
    event_weight,
    mood_prime_score,
    soul_armor,
    soul_distance,
)
from clanker_soul.pulse import (
    PulseConfig,
    PulseEngine,
    PulseHost,
    PulseTarget,
    Trigger,
)
from clanker_soul.score import Score
from clanker_soul.soul import (
    NourishmentReservoir,
    RESERVOIR_CAP,
    RESERVOIR_HALF_LIFE_S,
    SoulState,
    SoulStore,
    TraumaReservoir,
)

__version__ = "0.1.0"

__all__ = [
    # Conversational layer
    "Score",
    # Soul layer
    "SoulState",
    "SoulStore",
    "TraumaReservoir",
    "NourishmentReservoir",
    "RESERVOIR_HALF_LIFE_S",
    "RESERVOIR_CAP",
    # Mood layer (physics)
    "EmotionalPhysics",
    "PhysicsConfig",
    "PhysicsTick",
    "event_weight",
    "soul_armor",
    "soul_distance",
    "mood_prime_score",
    "dim_resilience",
    "POSITIVE_PATTERNS",
    "HEAVY_PATTERNS",
    # Pulse
    "PulseEngine",
    "PulseHost",
    "PulseConfig",
    "PulseTarget",
    "Trigger",
    # Meta
    "__version__",
]
