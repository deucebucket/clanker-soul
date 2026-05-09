"""Tests for ``PulseDispatcher`` — uses real clanker-soul types so the
wire shape is verified against the actual upstream API.

22 hermetic tests, originally drafted in carl PR #248 against an
in-carl `PulseDispatcher`. Moved here verbatim per issue #53; the only
difference is the import line. They run in <0.1s.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from clanker_soul import ActionOutcome, PulseAction, PulseTarget, Score
from clanker_soul.pulse.dispatcher import PulseDispatcher
from clanker_soul.pulse.triggers import Trigger


def _trigger(kind: str = "restless_curiosity") -> Trigger:
    return Trigger(
        kind=kind,
        soul={"v": 128, "a": 128, "d": 128, "u": 128, "g": 128, "w": 128, "i": 128},
        mood=[128, 128, 128, 128, 128, 128, 128],
        metrics={},
    )


def _action(
    kind: str, *, target=None, prompt: str = "test", extra: dict | None = None,
) -> PulseAction:
    return PulseAction(
        kind=kind,
        trigger=_trigger(),
        target=target,
        prompt=prompt,
        extra=extra or {},
    )


# ---- routing ---------------------------------------------------------------


async def test_dispatch_unknown_kind_returns_undelivered():
    d = PulseDispatcher()
    action = _action("withdraw")
    object.__setattr__(action, "kind", "wat_no_such_kind")
    out = await d.dispatch(action)
    assert out.delivered is False
    assert out.note is not None
    assert "unknown_action_kind" in out.note


async def test_dispatch_handler_exception_does_not_propagate():
    d = PulseDispatcher()
    d._dispatch_direct_message = AsyncMock(side_effect=RuntimeError("boom"))
    out = await d.dispatch(_action("direct_message"))
    assert out.delivered is False
    assert out.note == "dispatch_exception:RuntimeError"


# ---- direct_message --------------------------------------------------------


async def test_direct_message_no_signal_sender_returns_not_wired():
    d = PulseDispatcher()
    out = await d.dispatch(
        _action("direct_message", target=PulseTarget("op"), prompt="hi"),
    )
    assert out.delivered is False
    assert out.note == "direct_message_not_wired"


async def test_direct_message_with_async_sender():
    sender = AsyncMock(return_value=True)
    d = PulseDispatcher(signal_sender=sender)
    out = await d.dispatch(
        _action("direct_message", target=PulseTarget("op"), prompt="hi"),
    )
    assert out.delivered is True
    sender.assert_awaited_once()


async def test_direct_message_with_sync_sender():
    sender = MagicMock(return_value=True)
    d = PulseDispatcher(signal_sender=sender)
    out = await d.dispatch(
        _action("direct_message", target=PulseTarget("op"), prompt="hi"),
    )
    assert out.delivered is True
    sender.assert_called_once()


async def test_direct_message_send_failure():
    sender = AsyncMock(return_value=False)
    d = PulseDispatcher(signal_sender=sender)
    out = await d.dispatch(
        _action("direct_message", target=PulseTarget("op"), prompt="hi"),
    )
    assert out.delivered is False
    assert out.note == "direct_message_send_failed"


# ---- tool_invocation -------------------------------------------------------


async def test_tool_invocation_no_executor_returns_not_wired():
    d = PulseDispatcher()
    out = await d.dispatch(
        _action("tool_invocation", extra={"tool_name": "phone", "args": {}}),
    )
    assert out.delivered is False
    assert out.note == "tool_invocation_not_wired"


async def test_tool_invocation_missing_tool_name():
    executor = MagicMock()
    d = PulseDispatcher(tool_executor=executor)
    out = await d.dispatch(_action("tool_invocation", extra={"args": {}}))
    assert out.delivered is False
    assert out.note == "tool_invocation_missing_tool_name"
    executor.execute_tool.assert_not_called()


async def test_tool_invocation_success():
    executor = MagicMock()
    executor.execute_tool = AsyncMock(return_value={"status": "ok", "data": "x"})
    d = PulseDispatcher(tool_executor=executor)
    out = await d.dispatch(_action(
        "tool_invocation",
        extra={"tool_name": "phone", "args": {"action": "open_app"}},
    ))
    assert out.delivered is True
    executor.execute_tool.assert_awaited_once_with("phone", {"action": "open_app"})


async def test_tool_invocation_tool_failure():
    executor = MagicMock()
    executor.execute_tool = AsyncMock(
        return_value={"status": "error", "message": "no service"},
    )
    d = PulseDispatcher(tool_executor=executor)
    out = await d.dispatch(
        _action("tool_invocation", extra={"tool_name": "phone", "args": {}}),
    )
    assert out.delivered is False
    assert out.note is not None
    assert "no service" in out.note


# ---- browse_topic ----------------------------------------------------------


async def test_browse_topic_no_handler_returns_not_wired():
    d = PulseDispatcher()
    out = await d.dispatch(
        _action("browse_topic", extra={"topic": "octopus cognition"}),
    )
    assert out.delivered is False
    assert out.note == "browse_topic_not_wired"


async def test_browse_topic_missing_topic():
    handler = AsyncMock(return_value={"results": []})
    d = PulseDispatcher(browse_handler=handler)
    out = await d.dispatch(_action("browse_topic", extra={}))
    assert out.delivered is False
    assert out.note == "browse_topic_missing_topic"
    handler.assert_not_called()


async def test_browse_topic_success():
    handler = AsyncMock(return_value={"results": ["a", "b"]})
    d = PulseDispatcher(browse_handler=handler)
    out = await d.dispatch(
        _action("browse_topic", extra={"topic": "weird animals"}),
    )
    assert out.delivered is True
    handler.assert_awaited_once_with("weird animals")


# ---- post_public -----------------------------------------------------------


async def test_post_public_no_platform():
    d = PulseDispatcher(post_handlers={"x": AsyncMock()})
    out = await d.dispatch(_action("post_public", prompt="hello", extra={}))
    assert out.delivered is False
    assert out.note is not None
    assert "no_platform" in out.note


async def test_post_public_unknown_platform():
    d = PulseDispatcher(post_handlers={"x": AsyncMock()})
    out = await d.dispatch(
        _action("post_public", prompt="hi", extra={"platform": "mastodon"}),
    )
    assert out.delivered is False
    assert out.note is not None
    assert "mastodon" in out.note


async def test_post_public_routes_to_platform_handler():
    x_handler = AsyncMock(return_value=True)
    reddit_handler = AsyncMock(return_value=True)
    d = PulseDispatcher(post_handlers={"x": x_handler, "reddit": reddit_handler})
    out = await d.dispatch(
        _action("post_public", prompt="tweet", extra={"platform": "x"}),
    )
    assert out.delivered is True
    x_handler.assert_awaited_once()
    reddit_handler.assert_not_called()


# ---- comment_reply ---------------------------------------------------------


async def test_comment_reply_routes_to_platform_handler():
    h = AsyncMock(return_value=True)
    d = PulseDispatcher(reply_handlers={"x": h})
    out = await d.dispatch(_action(
        "comment_reply", target=PulseTarget("thread-123"),
        prompt="reply", extra={"platform": "x"},
    ))
    assert out.delivered is True
    h.assert_awaited_once()


async def test_comment_reply_no_handler():
    d = PulseDispatcher()
    out = await d.dispatch(_action(
        "comment_reply", target=PulseTarget("thread"),
        prompt="r", extra={"platform": "x"},
    ))
    assert out.delivered is False


# ---- withdraw --------------------------------------------------------------


async def test_withdraw_returns_delivered_true_even_without_handler():
    d = PulseDispatcher()
    out = await d.dispatch(_action("withdraw", extra={"seconds": 900}))
    assert out.delivered is True
    assert out.note is not None
    assert "900s" in out.note


async def test_withdraw_calls_handler():
    handler = AsyncMock()
    d = PulseDispatcher(withdraw_handler=handler)
    out = await d.dispatch(_action("withdraw", extra={"seconds": 600}))
    assert out.delivered is True
    handler.assert_awaited_once_with(600)


async def test_withdraw_default_seconds():
    handler = AsyncMock()
    d = PulseDispatcher(withdraw_handler=handler)
    await d.dispatch(_action("withdraw", extra={}))
    handler.assert_awaited_once_with(600)


# ---- ActionOutcome shape ---------------------------------------------------


def test_action_outcome_is_clanker_soul_type():
    out = ActionOutcome(delivered=True, consequences=(), note="x")
    assert hasattr(out, "delivered") and hasattr(out, "consequences")
    s = Score(v=100, w=80, patterns=("X",))
    assert s.v == 100
