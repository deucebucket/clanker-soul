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

from clanker_soul.eventlog import (
    EventLog,
    IngestRecord,
    NullEventLog,
    PulseRecord,
    SqliteEventLog,
)
from clanker_soul.governor import (
    DEFAULT_CAPABILITY_PROFILES,
    STRICT_CAPABILITY_PROFILES,
    CapabilityGate,
    CapabilityLevel,
    CapabilityProfile,
    CrisisDiagnosis,
    GateDecision,
    GovernorConfig,
)
from clanker_soul.overrides import (
    ConfigOverrides,
    OverrideBundle,
    apply_overrides,
)
from clanker_soul.pending import (
    ClassifyOutcome,
    InMemoryPendingActionStore,
    KeywordOutcomeClassifier,
    LLMOutcomeClassifier,
    OutcomeClassifier,
    PendingAction,
    PendingActionStore,
    PendingCoordinator,
    PendingDeltaConfig,
    PendingStatus,
    ResolutionResult,
    SqlitePendingActionStore,
)
from clanker_soul.plugin import SoulPlugin
from clanker_soul.presets import (
    ADULT,
    ALL as PRESETS,
    BRITTLE,
    CHILD,
    Preset,
    STOIC,
)
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
    ACTION_KINDS,
    ActionHandler,
    ActionOutcome,
    DEFAULT_FACES,
    CorpusStore,
    PersistentRecencyLog,
    PromptCorpus,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseDispatcher,
    PulseEngine,
    PulseHost,
    PulseTarget,
    RecencyLog,
    Trigger,
    VadugwiPredicate,
    build_default_corpus,
    default_tags_from_metrics,
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

__version__ = "0.16.0"

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
    # Pulse / motivation
    "PulseEngine",
    "PulseHost",
    "PulseConfig",
    "PulseTarget",
    "Trigger",
    "PulseAction",
    "ActionOutcome",
    "ACTION_KINDS",
    # Prompt corpus / sampler (M3.1; engine-wired in M3.2)
    "PromptCorpus",
    "PromptFace",
    "VadugwiPredicate",
    "RecencyLog",
    "default_tags_from_metrics",
    # Default baseline corpus (M3.2)
    "DEFAULT_FACES",
    "build_default_corpus",
    # Corpus persistence (M3.3)
    "CorpusStore",
    "PersistentRecencyLog",
    # PulseDispatcher (#53)
    "PulseDispatcher",
    "ActionHandler",
    # Event log
    "EventLog",
    "IngestRecord",
    "PulseRecord",
    "NullEventLog",
    "SqliteEventLog",
    # Live-tunable overrides
    "ConfigOverrides",
    "OverrideBundle",
    "apply_overrides",
    # PendingAction tracking (#57)
    "PendingAction",
    "PendingStatus",
    "ClassifyOutcome",
    "PendingDeltaConfig",
    "PendingActionStore",
    "InMemoryPendingActionStore",
    "SqlitePendingActionStore",
    "OutcomeClassifier",
    "KeywordOutcomeClassifier",
    "LLMOutcomeClassifier",
    "PendingCoordinator",
    "ResolutionResult",
    # Presets
    "Preset",
    "CHILD",
    "ADULT",
    "BRITTLE",
    "STOIC",
    "PRESETS",
    # One-call plugin
    "SoulPlugin",
    # Safety governor
    "CapabilityLevel",
    "CapabilityProfile",
    "CapabilityGate",
    "GateDecision",
    "DEFAULT_CAPABILITY_PROFILES",
    "STRICT_CAPABILITY_PROFILES",
    "CrisisDiagnosis",
    "GovernorConfig",
    # Meta
    "__version__",
]
