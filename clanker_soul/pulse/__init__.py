"""``clanker_soul.pulse`` — host-agnostic mood-driven motivation engine.

Re-exports the public surface of the submodules:
  - :py:mod:`.config` — :py:class:`PulseConfig`
  - :py:mod:`.triggers` — :py:class:`Trigger`, :py:class:`PulseTarget`,
    :py:class:`PulseAction`, :py:class:`ActionOutcome`,
    :py:data:`ACTION_KINDS`
  - :py:mod:`.host` — :py:class:`PulseHost` Protocol
  - :py:mod:`.prompt` — :py:func:`compose_self_prompt`
  - :py:mod:`.engine` — :py:class:`PulseEngine`
  - :py:mod:`.corpus` — :py:class:`PromptCorpus`, :py:class:`PromptFace`,
    :py:class:`VadugwiPredicate`, :py:class:`RecencyLog`,
    :py:func:`default_tags_from_metrics` (M3.1; engine-wired in M3.2)

``from clanker_soul.pulse import X`` keeps working unchanged."""
from clanker_soul.pulse.config import PulseConfig
from clanker_soul.pulse.corpus import (
    PromptCorpus,
    PromptFace,
    RecencyLog,
    VadugwiPredicate,
    default_tags_from_metrics,
)
from clanker_soul.pulse.engine import PulseEngine
from clanker_soul.pulse.host import PulseHost
from clanker_soul.pulse.prompt import compose_self_prompt
from clanker_soul.pulse.triggers import (
    ACTION_KINDS,
    ActionOutcome,
    PulseAction,
    PulseTarget,
    Trigger,
)

__all__ = [
    "PulseEngine",
    "PulseHost",
    "PulseConfig",
    "PulseTarget",
    "Trigger",
    "PulseAction",
    "ActionOutcome",
    "ACTION_KINDS",
    "compose_self_prompt",
    # Corpus / sampler (M3.1)
    "PromptCorpus",
    "PromptFace",
    "VadugwiPredicate",
    "RecencyLog",
    "default_tags_from_metrics",
]
