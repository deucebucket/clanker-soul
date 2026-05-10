"""``Inference`` Protocol — the model-agnostic seam for the M4 cascade.

The autonomous-motivation cascade calls out to inference twice:

* **score** — read a piece of text in context and return its VADUGWI
  scoring (a :py:class:`Score`). Used by the contemplation/idle loop
  to evaluate the effect of a synthesized thought.
* **act** — carry out a :py:class:`PulseAction` (generate text,
  dispatch to wherever it goes, return :py:class:`ActionOutcome`).
  Used when the cascade decides an action should fire.

clanker-soul itself never imports any model SDK. Concrete
implementations live in host code or optional companion packages
(an Ollama wrapper, an OpenAI wrapper, etc.). The protocol is
intentionally minimal: any callable bundle that satisfies the two
methods works.

One model can wear both hats — pass a single instance via
``inference=`` and both roles are filled. Operators with different
budget profiles for the two paths can split via
``scorer=``/``actor=``.

Both methods are async to match how most LLM SDKs expose calls.
Sync impls can wrap themselves trivially (``async def score(...)
return self._sync_score(...)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from clanker_soul.pulse.triggers import ActionOutcome, PulseAction
    from clanker_soul.score import Score


@runtime_checkable
class Inference(Protocol):
    """Model-agnostic seam for scoring + acting.

    Implementations may be sync-wrapped-as-async (no real I/O happens
    in score/act for rule-based scorers, for example); the engine awaits
    either way.
    """

    async def score(self, text: str, context: dict) -> "Score":
        """Read ``text`` in the given ``context`` and return its 7-dim
        VADUGWI scoring as a :py:class:`Score`.

        ``context`` is host-defined — typically includes current mood,
        recent events, agent identity. The protocol only requires that
        an implementation accept a dict; what keys it reads is up to
        the impl.
        """
        ...

    async def act(self, action: "PulseAction") -> "ActionOutcome":
        """Carry out ``action`` and return the :py:class:`ActionOutcome`.

        Action kinds are documented in :py:data:`ACTION_KINDS`. The
        impl is responsible for translating the action's intent into
        host-specific I/O (sending a message, writing a journal entry,
        invoking a tool) and reporting whether it landed plus any
        consequence Scores the cascade should ingest.
        """
        ...


class _MissingInference:
    """Sentinel raised on attribute access when a host asks for
    inference but never wired it. Distinguishes "I haven't passed an
    inference yet" from "I passed None on purpose." Hosts that wire
    only ``scorer`` or only ``actor`` are fine; those roles work, the
    other raises only if accessed.
    """

    __slots__ = ("_role",)

    def __init__(self, role: str) -> None:
        self._role = role

    def __getattr__(self, _name: str) -> None:
        raise RuntimeError(
            f"SoulPlugin was constructed without an Inference for role "
            f"{self._role!r}. Pass either ``inference=`` (single model "
            f"covers both score+act) or the role-specific kwarg "
            f"(``scorer=`` / ``actor=``) at construction time."
        )

    def __bool__(self) -> bool:
        return False


__all__ = ["Inference"]
