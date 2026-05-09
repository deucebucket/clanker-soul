"""``clanker_soul.pulse`` — host-agnostic mood-driven proactive
messaging engine.

Re-exports the public surface of the five submodules:
  - :py:mod:`.config` — :py:class:`PulseConfig`
  - :py:mod:`.triggers` — :py:class:`Trigger`, :py:class:`PulseTarget`
  - :py:mod:`.host` — :py:class:`PulseHost` Protocol
  - :py:mod:`.prompt` — :py:func:`compose_self_prompt`
  - :py:mod:`.engine` — :py:class:`PulseEngine`

``from clanker_soul.pulse import X`` keeps working unchanged."""
from clanker_soul.pulse.config import PulseConfig
from clanker_soul.pulse.engine import PulseEngine
from clanker_soul.pulse.host import PulseHost
from clanker_soul.pulse.prompt import compose_self_prompt
from clanker_soul.pulse.triggers import PulseTarget, Trigger

__all__ = [
    "PulseEngine",
    "PulseHost",
    "PulseConfig",
    "PulseTarget",
    "Trigger",
    "compose_self_prompt",
]
