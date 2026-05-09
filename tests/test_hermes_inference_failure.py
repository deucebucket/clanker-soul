"""Tests for ``integrations/hermes/inference_health.py`` and
the ``ClankerSoulMemoryProvider.on_inference_failure`` hook.

The hook is what closes the loop on the agent's *own* inference health:
when hermes-agent's retry logic gives up, the unrecoverable failure
fires through the ``MemoryProvider.on_inference_failure`` plugin hook,
and clanker-soul ingests it as an emotional event so the agent's affect
tracks its own connection state.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from clanker_soul import Score


# Load both the helper module and the provider module by file path so
# the suite runs without hermes-agent installed (same pattern as
# tests/test_hermes_integration.py).
_PLUGIN_DIR = Path(__file__).parent.parent / "integrations" / "hermes"
sys.path.insert(0, str(_PLUGIN_DIR))

for _k in list(sys.modules):
    if _k in ("inference_health", "scorer", "pulse_runner", "clanker_soul_hermes_plugin"):
        sys.modules.pop(_k, None)


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(
        name,
        str(_PLUGIN_DIR / fname),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inference_health = _load("inference_health", "inference_health.py")
_load("scorer", "scorer.py")
_load("pulse_runner", "pulse_runner.py")
plugin_spec = importlib.util.spec_from_file_location(
    "clanker_soul_hermes_plugin",
    str(_PLUGIN_DIR / "__init__.py"),
    submodule_search_locations=[str(_PLUGIN_DIR)],
)
plugin_mod = importlib.util.module_from_spec(plugin_spec)
sys.modules["clanker_soul_hermes_plugin"] = plugin_mod
plugin_spec.loader.exec_module(plugin_mod)

score_from_failover = inference_health.score_from_failover
ClankerSoulMemoryProvider = plugin_mod.ClankerSoulMemoryProvider


# ---------------------------------------------------------------------------
# score_from_failover — pure mapping
# ---------------------------------------------------------------------------


def test_known_reason_returns_score():
    s = score_from_failover("rate_limit", provider="openrouter")
    assert s is not None
    assert isinstance(s, Score)
    assert "INFERENCE_RATE_LIMITED" in s.patterns
    assert s.direction == "OBSERVATION"
    assert s.source == "inference:openrouter"


def test_no_provider_yields_bare_source():
    s = score_from_failover("rate_limit")
    assert s is not None
    assert s.source == "inference"


def test_config_reasons_return_none():
    """Operator-shaped failures aren't emotional events for the agent."""
    for reason in (
        "model_not_found",
        "provider_policy_blocked",
        "format_error",
        "thinking_signature",
        "long_context_tier",
    ):
        assert score_from_failover(reason) is None, (
            f"{reason} should not produce an emotional Score"
        )


def test_unknown_string_returns_none():
    assert score_from_failover("never-heard-of-it") is None


def test_empty_inputs_return_none():
    assert score_from_failover("") is None
    assert score_from_failover(None) is None


def test_accepts_enum_like_value():
    """Also accept anything with a string ``.value`` (e.g. FailoverReason)."""

    class FakeReason:
        value = "billing"

    s = score_from_failover(FakeReason())
    assert s is not None
    assert "INFERENCE_CUT_OFF" in s.patterns


def test_billing_is_heavier_than_rate_limit():
    """Sanity-check the affect gradient: getting cut off feels worse
    than getting throttled."""
    rl = score_from_failover("rate_limit", provider="x")
    bill = score_from_failover("billing", provider="x")
    assert rl is not None and bill is not None
    assert bill.v < rl.v  # billing depresses valence further
    assert bill.w < rl.w  # and dents self-worth more


def test_patterns_are_not_in_heavy_set():
    """Inference failures must NOT trigger the breach mechanic.

    Getting rate-limited shouldn't damage the agent's soul the way a
    human's contempt would.
    """
    from clanker_soul import HEAVY_PATTERNS

    seen: set[str] = set()
    for reason in (
        "auth",
        "auth_permanent",
        "billing",
        "rate_limit",
        "overloaded",
        "server_error",
        "timeout",
        "context_overflow",
        "payload_too_large",
        "image_too_large",
        "unknown",
    ):
        s = score_from_failover(reason)
        assert s is not None
        seen.update(s.patterns)
    assert seen.isdisjoint(HEAVY_PATTERNS), (
        f"Inference patterns must not be heavy; overlap: {seen & HEAVY_PATTERNS}"
    )


def test_override_disables_specific_reason():
    """An operator can suppress a single reason without touching others."""
    override = {"rate_limit": None}
    assert score_from_failover("rate_limit", override=override) is None
    # Other reasons still fire normally
    assert score_from_failover("billing", override=override) is not None


def test_override_replaces_specific_reason():
    """Operator-tuned mapping for one persona without forking the table."""
    override = {
        "rate_limit": {
            "v": 60,
            "a": 200,
            "d": 50,
            "u": 200,
            "g": 60,
            "w": 60,
            "i": 50,
            "patterns": ("MY_CUSTOM_PATTERN",),
        }
    }
    s = score_from_failover("rate_limit", override=override)
    assert s is not None
    assert s.v == 60
    assert s.patterns == ("MY_CUSTOM_PATTERN",)


# ---------------------------------------------------------------------------
# Provider hook — closes the learning loop
# ---------------------------------------------------------------------------


def test_provider_on_inference_failure_ingests(tmp_path):
    provider = ClankerSoulMemoryProvider()
    provider._db_path = tmp_path / "soul.db"
    provider.initialize(session_id="test-session")
    assert provider._plugin is not None

    # Fresh plugin has soul but no mood — first ingest establishes mood.
    assert provider._plugin.snapshot()["mood"] is None

    provider.on_inference_failure(
        "rate_limit",
        provider="openrouter",
        model="x",
        retryable=True,
    )

    snap_after = provider._plugin.snapshot()
    mood = snap_after["mood"]
    assert mood is not None, "rate_limit must establish mood from baseline"
    # mood is a 7-list in V/A/D/U/G/W/I order. rate_limit's V is 120 —
    # blending into the V=145 soul baseline must move mood V downward.
    soul_v = snap_after["soul"]["v"]
    mood_v = mood[0]
    assert mood_v < soul_v, (
        f"expected rate_limit to pull mood V below soul V={soul_v}, got mood V={mood_v}"
    )

    provider.shutdown()


def test_provider_on_inference_failure_no_op_for_config_reasons(tmp_path):
    """``model_not_found`` is a config issue — no soul event should fire."""
    provider = ClankerSoulMemoryProvider()
    provider._db_path = tmp_path / "soul.db"
    provider.initialize(session_id="cfg-session")
    assert provider._plugin is not None

    # Mood is None on a fresh plugin with no events ingested.
    assert provider._plugin.snapshot()["mood"] is None
    provider.on_inference_failure("model_not_found")
    # Still None — config failures don't establish mood.
    assert provider._plugin.snapshot()["mood"] is None

    provider.shutdown()


def test_provider_on_inference_failure_soft_fails(tmp_path):
    """Even if the helper or physics raises, the hook must not propagate."""
    provider = ClankerSoulMemoryProvider()
    provider._db_path = tmp_path / "soul.db"
    provider.initialize(session_id="soft-fail")
    assert provider._plugin is not None

    # Replace ingest with one that explodes
    def boom(*_a, **_kw):
        raise RuntimeError("simulated physics fault")

    provider._plugin.ingest = boom  # type: ignore[method-assign]

    # Must not raise
    provider.on_inference_failure("rate_limit", provider="openrouter")
    provider.shutdown()


def test_provider_skips_when_disabled():
    """Disabled provider doesn't crash, doesn't ingest."""
    provider = ClankerSoulMemoryProvider()
    provider._enabled = False
    # Plugin never initialized — must be a clean no-op.
    provider.on_inference_failure("rate_limit")
