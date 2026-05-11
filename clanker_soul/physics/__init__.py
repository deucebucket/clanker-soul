"""``clanker_soul.physics`` — emotional physics engine.

Re-exports the public surface of the four submodules:
  - :py:mod:`.config` — :py:class:`PhysicsConfig`, ``POSITIVE_PATTERNS``,
    ``HEAVY_PATTERNS``
  - :py:mod:`.math` — pure helpers: ``event_weight``, ``soul_armor``,
    ``mood_prime_score``, ``dim_resilience``, ``soul_distance``
  - :py:mod:`.tick` — :py:class:`PhysicsTick` diagnostic record
  - :py:mod:`.engine` — :py:class:`EmotionalPhysics` stateful engine

``from clanker_soul.physics import X`` keeps working unchanged."""

from clanker_soul.physics.config import (
    CORRECTION_PATTERNS,
    HEAVY_PATTERNS,
    MISTAKE_PATTERNS,
    POSITIVE_PATTERNS,
    PhysicsConfig,
)
from clanker_soul.physics.contemplation import ContemplationResult
from clanker_soul.physics.engine import EmotionalPhysics
from clanker_soul.physics.math import (
    dim_resilience,
    event_weight,
    mood_prime_score,
    soul_armor,
    soul_distance,
)
from clanker_soul.physics.tick import PhysicsTick

__all__ = [
    "EmotionalPhysics",
    "PhysicsConfig",
    "PhysicsTick",
    "ContemplationResult",
    "POSITIVE_PATTERNS",
    "HEAVY_PATTERNS",
    "MISTAKE_PATTERNS",
    "CORRECTION_PATTERNS",
    "event_weight",
    "soul_armor",
    "soul_distance",
    "mood_prime_score",
    "dim_resilience",
]
