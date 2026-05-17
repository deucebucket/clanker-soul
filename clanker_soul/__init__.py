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
from clanker_soul.cascade import (
    ActionRegistry,
    ActionThresholdConfig,
    CascadeActionContext,
    GateConfig,
    GateRollContext,
    IDLE_CONTEMPLATION_KIND,
    IdleLoop,
    RegisteredAction,
    TickResult,
    confide_proxy_score,
    default_gate,
    mistake_aware_tags,
    should_act,
    tags_from_delta,
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
from clanker_soul.felt_state import (
    FeltState,
    Register,
    baseline_comparison_line,
    nourishment_load_line,
    render_felt_state,
    trauma_load_line,
)
from clanker_soul.inference import Inference
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
from clanker_soul.plugins import (
    STANDARD_PLUGIN_KINDS,
    LoadedPlugin,
    MasterEntry,
    PluginManifest,
    PluginLoader,
    manifest_from_dict,
    overlay_settings,
    parse_manifest_json,
    parse_plugins_toml,
)
from clanker_soul.presets import (
    ADULT,
    ALL as PRESETS,
    BRITTLE,
    CHILD,
    Preset,
    STOIC,
)
from clanker_soul.physics import (
    CORRECTION_PATTERNS,
    ContemplationResult,
    EmotionalPhysics,
    HEAVY_PATTERNS,
    MISTAKE_PATTERNS,
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
    DEFAULT_CONTEMPLATION_FACES,
    DEFAULT_FACES,
    CorpusStore,
    PersistentRecencyLog,
    PromptCorpus,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseDispatcher,
    PulseDispatchCallable,
    PulseEngine,
    PulseHost,
    PulseHostAdapter,
    PulseTarget,
    RecencyLog,
    Trigger,
    VadugwiPredicate,
    build_default_contemplation_corpus,
    build_default_corpus,
    default_tags_from_metrics,
)
from clanker_soul.score import Score
from clanker_soul.soul import (
    MistakeReservoir,
    NourishmentReservoir,
    RESERVOIR_CAP,
    RESERVOIR_HALF_LIFE_S,
    SoulState,
    SoulStore,
    TraumaReservoir,
)
from clanker_soul.tool_health import (
    score_from_action_failure,
    score_from_correction,
)

__version__ = "0.17.0"

__all__ = [
    # Conversational layer
    "Score",
    "FeltState",
    "Register",
    "render_felt_state",
    "baseline_comparison_line",
    "trauma_load_line",
    "nourishment_load_line",
    # Soul layer
    "SoulState",
    "SoulStore",
    "TraumaReservoir",
    "NourishmentReservoir",
    "MistakeReservoir",
    "RESERVOIR_HALF_LIFE_S",
    "RESERVOIR_CAP",
    # Mood layer (physics)
    "EmotionalPhysics",
    "PhysicsConfig",
    "PhysicsTick",
    "ContemplationResult",
    "event_weight",
    "soul_armor",
    "soul_distance",
    "mood_prime_score",
    "dim_resilience",
    "POSITIVE_PATTERNS",
    "HEAVY_PATTERNS",
    "MISTAKE_PATTERNS",
    "CORRECTION_PATTERNS",
    # Pulse / motivation
    "PulseEngine",
    "PulseHost",
    "PulseHostAdapter",
    "PulseDispatchCallable",
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
    # Default M4 contemplation corpus (#84)
    "DEFAULT_CONTEMPLATION_FACES",
    "build_default_contemplation_corpus",
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
    # Inference protocol (M4 #79)
    "Inference",
    # Plugin manifest spec (#54)
    "STANDARD_PLUGIN_KINDS",
    "LoadedPlugin",
    "PluginManifest",
    "PluginLoader",
    "MasterEntry",
    "manifest_from_dict",
    "overlay_settings",
    "parse_manifest_json",
    "parse_plugins_toml",
    # Tool-failure attribution (M4 #97)
    "score_from_action_failure",
    "score_from_correction",
    # M4 cascade
    "ActionRegistry",
    "ActionThresholdConfig",
    "CascadeActionContext",
    "confide_proxy_score",
    "GateConfig",
    "GateRollContext",
    "IDLE_CONTEMPLATION_KIND",
    "IdleLoop",
    "RegisteredAction",
    "TickResult",
    "default_gate",
    "mistake_aware_tags",
    "should_act",
    "tags_from_delta",
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
