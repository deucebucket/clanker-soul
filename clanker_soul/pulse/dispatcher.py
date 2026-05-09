"""``PulseDispatcher`` — turns a ``PulseAction`` into a real-world
effect via host-supplied handlers, returns ``ActionOutcome`` so the
soul can learn from consequences.

Host-agnostic. The constructor takes opaque callables for each
subsystem (direct messaging, tool invocation, browsing, public
posting, etc.). Handler signatures are documented but the dispatcher
itself never imports host types — every dependency is a duck-typed
callable so any agent runtime can plug in its own implementations.

Hosts that already implement
:py:meth:`clanker_soul.pulse.host.PulseHost.dispatch_action` directly
can skip this entirely. The dispatcher exists for hosts that prefer
the constructor-injected handler pattern over a single switch-statement
method — it's the recommended starting point for new integrations
because:

  * **Soft-fail by default** — handler exceptions become
    ``ActionOutcome(delivered=False, note="dispatch_exception:...")``,
    never propagate up. The engine keeps running.
  * **Sync/async bridging** — handlers may return either a value or an
    awaitable; the internal ``_maybe_await`` shim handles both.
  * **Not-wired stubs** — when a handler is None the dispatcher returns
    ``ActionOutcome(delivered=False, note="<kind>_not_wired")`` so a
    fresh integration can run the engine end-to-end on day one with
    observable-but-no-op enactment, then turn each subsystem on
    independently.

Handler signatures (all callables; sync or async return type):

  * ``signal_sender(target, prompt) -> bool``
  * ``tool_executor.execute_tool(name, args) -> dict`` (with ``status`` key,
    ``"ok"`` for success, anything else for failure plus a ``message`` key)
  * ``browse_handler(topic) -> truthy if delivered``
  * ``post_handlers[platform](prompt, extra) -> bool``
  * ``reply_handlers[platform](target, prompt, extra) -> bool``
  * ``withdraw_handler(seconds) -> None``
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Union

from clanker_soul.pulse.triggers import ActionOutcome, PulseAction

logger = logging.getLogger(__name__)


ActionHandler = Callable[[PulseAction], Union[ActionOutcome, Awaitable[ActionOutcome]]]


class PulseDispatcher:
    """Routes a :py:class:`PulseAction` to the right host handler.

    Construct with the subsystem dependencies your environment has
    wired up. Anything left as None falls through to a stub that
    returns ``ActionOutcome(delivered=False, note="<kind>_not_wired")``.

    All handler exceptions are caught and converted to
    ``delivered=False`` outcomes — a dispatcher failure must NOT
    propagate up into the engine.
    """

    def __init__(
        self,
        *,
        signal_sender: Any = None,
        tool_executor: Any = None,
        browse_handler: Any = None,
        post_handlers: dict[str, Any] | None = None,
        reply_handlers: dict[str, Any] | None = None,
        withdraw_handler: Any = None,
    ) -> None:
        self._signal_sender = signal_sender
        self._tool_executor = tool_executor
        self._browse_handler = browse_handler
        self._post_handlers = post_handlers or {}
        self._reply_handlers = reply_handlers or {}
        self._withdraw_handler = withdraw_handler

    async def dispatch(self, action: PulseAction) -> ActionOutcome:
        """Main entry — bind this as the dispatcher callback in your
        host's :py:class:`PulseEngine` setup, e.g.
        ``engine = PulseEngine(host=PulseHostAdapter(dispatcher), ...)``
        once :py:class:`PulseHostAdapter` lands, or pass
        ``dispatch_action=dispatcher.dispatch`` directly via your host's
        ``dispatch_action`` method.
        """
        try:
            handler = {
                "direct_message": self._dispatch_direct_message,
                "tool_invocation": self._dispatch_tool_invocation,
                "browse_topic": self._dispatch_browse_topic,
                "post_public": self._dispatch_post_public,
                "comment_reply": self._dispatch_comment_reply,
                "withdraw": self._dispatch_withdraw,
            }.get(action.kind)
            if handler is None:
                logger.warning(
                    "PulseDispatcher: unknown action kind %r",
                    action.kind,
                )
                return ActionOutcome(
                    delivered=False,
                    note=f"unknown_action_kind:{action.kind}",
                )
            return await handler(action)
        except Exception as e:
            logger.exception(
                "PulseDispatcher: handler raised for %s",
                action.kind,
            )
            return ActionOutcome(
                delivered=False,
                note=f"dispatch_exception:{type(e).__name__}",
            )

    async def _dispatch_direct_message(self, action: PulseAction) -> ActionOutcome:
        if self._signal_sender is None:
            return ActionOutcome(delivered=False, note="direct_message_not_wired")
        ok = await _maybe_await(self._signal_sender(action.target, action.prompt))
        return ActionOutcome(
            delivered=bool(ok),
            note=None if ok else "direct_message_send_failed",
        )

    async def _dispatch_tool_invocation(self, action: PulseAction) -> ActionOutcome:
        if self._tool_executor is None:
            return ActionOutcome(delivered=False, note="tool_invocation_not_wired")
        tool_name = action.extra.get("tool_name")
        args = action.extra.get("args") or {}
        if not tool_name:
            return ActionOutcome(delivered=False, note="tool_invocation_missing_tool_name")
        result = await _maybe_await(self._tool_executor.execute_tool(tool_name, args))
        delivered = bool(result and result.get("status") == "ok")
        if delivered:
            return ActionOutcome(delivered=True)
        message = result.get("message", "?") if result else "no_result"
        return ActionOutcome(delivered=False, note=f"tool_failed:{message}")

    async def _dispatch_browse_topic(self, action: PulseAction) -> ActionOutcome:
        if self._browse_handler is None:
            return ActionOutcome(delivered=False, note="browse_topic_not_wired")
        topic = action.extra.get("topic")
        if not topic:
            return ActionOutcome(delivered=False, note="browse_topic_missing_topic")
        result = await _maybe_await(self._browse_handler(topic))
        return ActionOutcome(
            delivered=bool(result),
            note=None if result else "browse_handler_returned_falsy",
        )

    async def _dispatch_post_public(self, action: PulseAction) -> ActionOutcome:
        platform = action.extra.get("platform")
        handler = self._post_handlers.get(platform) if platform else None
        if handler is None:
            return ActionOutcome(
                delivered=False,
                note=f"post_public_not_wired:{platform or 'no_platform'}",
            )
        ok = await _maybe_await(handler(action.prompt, action.extra))
        return ActionOutcome(
            delivered=bool(ok),
            note=None if ok else f"post_public_failed:{platform}",
        )

    async def _dispatch_comment_reply(self, action: PulseAction) -> ActionOutcome:
        platform = action.extra.get("platform")
        handler = self._reply_handlers.get(platform) if platform else None
        if handler is None:
            return ActionOutcome(
                delivered=False,
                note=f"comment_reply_not_wired:{platform or 'no_platform'}",
            )
        ok = await _maybe_await(handler(action.target, action.prompt, action.extra))
        return ActionOutcome(
            delivered=bool(ok),
            note=None if ok else f"comment_reply_failed:{platform}",
        )

    async def _dispatch_withdraw(self, action: PulseAction) -> ActionOutcome:
        seconds = int(action.extra.get("seconds", 600))
        if self._withdraw_handler is not None:
            await _maybe_await(self._withdraw_handler(seconds))
        else:
            logger.info(
                "PulseDispatcher: withdraw requested for %ds (no handler)",
                seconds,
            )
        return ActionOutcome(delivered=True, note=f"withdrew_for_{seconds}s")


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["PulseDispatcher", "ActionHandler"]
