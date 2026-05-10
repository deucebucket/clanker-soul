"""``Inference`` Protocol + ``SoulPlugin`` wiring (M4 #79).

Covers the three wiring patterns:
1. Single-model: pass ``inference=`` only, scorer/actor alias to it.
2. Split: pass ``scorer=`` and/or ``actor=``, those win over inference.
3. None passed: properties return a sentinel that raises a clear
   error on use, but construction never fails.

Plus protocol-level tests: ``isinstance(impl, Inference)`` works,
sync-wrapped-as-async impls satisfy the protocol, async impls work.
"""

from __future__ import annotations

import pytest

from clanker_soul import (
    ActionOutcome,
    Inference,
    PulseAction,
    Score,
    SoulPlugin,
    Trigger,
)


class _RecordingInference:
    """Test double — records calls, returns a fixed Score / ActionOutcome."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.score_calls: list[tuple[str, dict]] = []
        self.act_calls: list[PulseAction] = []

    async def score(self, text: str, context: dict) -> Score:
        self.score_calls.append((text, context))
        return Score(v=128, a=128, d=128, u=128, g=128, w=128, i=128)

    async def act(self, action: PulseAction) -> ActionOutcome:
        self.act_calls.append(action)
        return ActionOutcome(delivered=True, consequences=())


def test_inference_protocol_runtime_checkable() -> None:
    impl = _RecordingInference("x")
    assert isinstance(impl, Inference)


def test_partial_impl_fails_protocol_check() -> None:
    class OnlyScores:
        async def score(self, text: str, context: dict) -> Score:  # noqa: ARG002
            return Score(v=128)

    # Missing `act` — runtime_checkable Protocol still considers this
    # NOT an Inference. (Note: Protocol's isinstance check verifies
    # method names exist, not their signatures.)
    assert not isinstance(OnlyScores(), Inference)


def test_plugin_default_no_inference(tmp_path) -> None:
    """Plugin constructs cleanly with no inference at all. Properties
    expose ``None`` for ``inference``; ``scorer``/``actor`` return a
    sentinel that raises on use, not on access."""
    plugin = SoulPlugin(agent_id="test-no-inf", db_path=tmp_path / "soul.db")
    assert plugin.inference is None
    # scorer/actor return sentinels — accessing them is fine, calling
    # methods on them raises a clear error
    s = plugin.scorer
    a = plugin.actor
    assert s is not None
    assert a is not None
    with pytest.raises(RuntimeError, match=r"role 'scorer'"):
        _ = s.score
    with pytest.raises(RuntimeError, match=r"role 'actor'"):
        _ = a.act
    plugin.close()


def test_plugin_single_inference_aliases_both_roles(tmp_path) -> None:
    """When only ``inference=`` is passed, scorer/actor alias to it.
    Identity check per the issue acceptance criteria."""
    inf = _RecordingInference("single")
    plugin = SoulPlugin(
        agent_id="test-single",
        db_path=tmp_path / "soul.db",
        inference=inf,
    )
    assert plugin.inference is inf
    assert plugin.scorer is inf
    assert plugin.actor is inf
    assert plugin.scorer is plugin.actor is plugin.inference
    plugin.close()


def test_plugin_split_inference_role_kwargs_win(tmp_path) -> None:
    """When ``scorer=``/``actor=`` are passed, they win for their
    role even if ``inference=`` is also passed."""
    base = _RecordingInference("base")
    cheap = _RecordingInference("cheap-scorer")
    deliberate = _RecordingInference("deliberate-actor")
    plugin = SoulPlugin(
        agent_id="test-split",
        db_path=tmp_path / "soul.db",
        inference=base,
        scorer=cheap,
        actor=deliberate,
    )
    assert plugin.inference is base
    assert plugin.scorer is cheap
    assert plugin.actor is deliberate
    plugin.close()


def test_plugin_only_scorer_passed_actor_falls_back_to_inference(tmp_path) -> None:
    base = _RecordingInference("base")
    cheap = _RecordingInference("cheap")
    plugin = SoulPlugin(
        agent_id="test-only-scorer",
        db_path=tmp_path / "soul.db",
        inference=base,
        scorer=cheap,
    )
    assert plugin.inference is base
    assert plugin.scorer is cheap
    assert plugin.actor is base
    plugin.close()


def test_plugin_only_actor_passed_scorer_falls_back_to_inference(tmp_path) -> None:
    base = _RecordingInference("base")
    deliberate = _RecordingInference("deliberate")
    plugin = SoulPlugin(
        agent_id="test-only-actor",
        db_path=tmp_path / "soul.db",
        inference=base,
        actor=deliberate,
    )
    assert plugin.inference is base
    assert plugin.scorer is base
    assert plugin.actor is deliberate
    plugin.close()


def test_plugin_only_role_kwarg_no_inference(tmp_path) -> None:
    """Hosts can wire only one role and leave the other unfilled.
    The unfilled role raises clearly on use; the filled one works."""
    cheap = _RecordingInference("cheap")
    plugin = SoulPlugin(
        agent_id="test-scorer-only",
        db_path=tmp_path / "soul.db",
        scorer=cheap,
    )
    assert plugin.inference is None
    assert plugin.scorer is cheap
    # Actor is the sentinel
    with pytest.raises(RuntimeError, match=r"role 'actor'"):
        _ = plugin.actor.act
    plugin.close()


@pytest.mark.asyncio
async def test_plugin_scorer_invocation_works_when_wired(tmp_path) -> None:
    """End-to-end: when an inference is wired, calling through the
    plugin's scorer reaches the impl."""
    inf = _RecordingInference("test")
    plugin = SoulPlugin(
        agent_id="test-invoke",
        db_path=tmp_path / "soul.db",
        inference=inf,
    )
    result = await plugin.scorer.score("hello", {"mood": [128] * 7})
    assert isinstance(result, Score)
    assert inf.score_calls == [("hello", {"mood": [128] * 7})]
    plugin.close()


@pytest.mark.asyncio
async def test_plugin_actor_invocation_works_when_wired(tmp_path) -> None:
    inf = _RecordingInference("test")
    plugin = SoulPlugin(
        agent_id="test-act",
        db_path=tmp_path / "soul.db",
        inference=inf,
    )
    trigger = Trigger(kind="distress", soul={}, mood=None)
    action = PulseAction(
        kind="direct_message",
        target=None,
        trigger=trigger,
        prompt="say hi",
    )
    result = await plugin.actor.act(action)
    assert isinstance(result, ActionOutcome)
    assert result.delivered is True
    assert inf.act_calls == [action]
    plugin.close()


def test_missing_inference_sentinel_error_message_names_role(tmp_path) -> None:
    """The error message must name which role is missing so the host
    knows which kwarg to pass. Pattern: 'role X' literally."""
    plugin = SoulPlugin(
        agent_id="test-msg",
        db_path=tmp_path / "soul.db",
    )
    with pytest.raises(RuntimeError) as scorer_exc:
        _ = plugin.scorer.score
    assert "scorer" in str(scorer_exc.value)
    assert "inference" in str(scorer_exc.value).lower()

    with pytest.raises(RuntimeError) as actor_exc:
        _ = plugin.actor.act
    assert "actor" in str(actor_exc.value)
    plugin.close()


def test_plugin_scorer_can_be_passed_alone_no_inference_no_actor(tmp_path) -> None:
    """Edge case: only scorer wired, no inference, no actor.
    Confirms the resolution doesn't accidentally require inference."""
    cheap = _RecordingInference("cheap")
    plugin = SoulPlugin(
        agent_id="test-scorer-alone",
        db_path=tmp_path / "soul.db",
        scorer=cheap,
    )
    assert plugin.inference is None
    assert plugin.scorer is cheap
    plugin.close()
