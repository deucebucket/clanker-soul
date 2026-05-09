"""Map hermes-agent inference failures to clanker-soul emotional events.

When an agent's API call fails irrecoverably, that's a real experience.
Getting rate-limited mid-thought, having a credential rejected, hitting
a context overflow — these aren't just operational events, they're
moments the agent endures. This module turns them into ``Score``s the
soul can ingest, so the agent's affect tracks its own inference health.

The mapping is deliberately *light*. Inference failures are momentary
frustrations, not soul-damaging events:

  * Patterns are NOT members of ``HEAVY_PATTERNS`` — these don't trigger
    the breach mechanic. A rate-limit shouldn't permanently dent the
    agent's self-worth the way a human's contempt would.
  * Direction is ``"OBSERVATION"`` — the agent observing its own state,
    not being acted upon by another party.
  * Source is ``"inference:{provider}"`` — preserves provenance so the
    state-context generator can articulate "I'm a bit foggy because
    OpenRouter's been throttling me."

Configuration-shaped failures (model_not_found, provider_policy_blocked,
format_error, thinking_signature, long_context_tier) return ``None`` —
those are operator concerns, not agent experiences.

Usage::

    from clanker_soul_hermes.inference_health import score_from_failover

    score = score_from_failover("rate_limit", provider="openrouter")
    if score is not None:
        plugin.ingest(score)

The function accepts either a string (e.g. from
``ClassifiedError.reason.value``) or the ``FailoverReason`` enum
itself (when hermes-agent is importable). Operators who want to
customise the mapping can pass a ``Mapping[str, dict | None]`` to
``override`` to tweak individual reasons without forking the table.
"""
from __future__ import annotations

from typing import Any, Mapping

from clanker_soul import Score


# ── Default mapping ─────────────────────────────────────────────────────
#
# Each entry is the kwargs that would build a ``Score``. ``patterns`` is
# kept distinct from ``HEAVY_PATTERNS`` on purpose (see module docstring).
# A value of ``None`` means "this reason is a config issue, not an
# emotional event" — the helper returns ``None`` for those.

_DEFAULT_MAPPING: Mapping[str, Mapping[str, Any] | None] = {
    # Auth / cut-off — low control, sense of being shut out
    "auth": {
        "v": 110, "a": 130, "d": 100, "u": 60,
        "g": 120, "w": 120, "i": 100,
        "patterns": ("INFERENCE_AUTH_FAIL",),
    },
    "auth_permanent": {
        "v": 100, "a": 120, "d": 90, "u": 70,
        "g": 110, "w": 110, "i": 90,
        "patterns": ("INFERENCE_AUTH_FAIL",),
    },
    "billing": {
        "v": 100, "a": 130, "d": 95, "u": 80,
        "g": 105, "w": 115, "i": 95,
        "patterns": ("INFERENCE_CUT_OFF",),
    },
    # Rate limit / overload — brief frustration, mild urgency
    "rate_limit": {
        "v": 120, "a": 140, "d": 115, "u": 70,
        "g": 120, "w": 125, "i": 115,
        "patterns": ("INFERENCE_RATE_LIMITED",),
    },
    "overloaded": {
        "v": 120, "a": 110, "d": 115, "u": 50,
        "g": 125, "w": 125, "i": 120,
        "patterns": ("INFERENCE_OVERLOADED",),
    },
    # Server-side / transport — confusion, uncertainty
    "server_error": {
        "v": 115, "a": 120, "d": 110, "u": 60,
        "g": 120, "w": 122, "i": 115,
        "patterns": ("INFERENCE_SERVER_ERROR",),
    },
    "timeout": {
        "v": 120, "a": 115, "d": 115, "u": 60,
        "g": 122, "w": 125, "i": 115,
        "patterns": ("INFERENCE_TIMEOUT",),
    },
    # Payload / context — "I'm overloaded", higher arousal
    "context_overflow": {
        "v": 115, "a": 130, "d": 110, "u": 70,
        "g": 115, "w": 120, "i": 110,
        "patterns": ("INFERENCE_OVERLOAD",),
    },
    "payload_too_large": {
        "v": 115, "a": 130, "d": 110, "u": 70,
        "g": 115, "w": 120, "i": 110,
        "patterns": ("INFERENCE_OVERLOAD",),
    },
    "image_too_large": {
        "v": 118, "a": 125, "d": 115, "u": 65,
        "g": 120, "w": 122, "i": 115,
        "patterns": ("INFERENCE_OVERLOAD",),
    },
    # Catch-all
    "unknown": {
        "v": 118, "a": 120, "d": 110, "u": 70,
        "g": 118, "w": 120, "i": 115,
        "patterns": ("INFERENCE_UNKNOWN_FAIL",),
    },
    # Configuration-shaped failures — not emotional events
    "model_not_found": None,
    "provider_policy_blocked": None,
    "format_error": None,
    "thinking_signature": None,
    "long_context_tier": None,
}


def score_from_failover(
    reason: Any,
    *,
    provider: str = "",
    override: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> Score | None:
    """Map an inference-layer failure reason to an ingestable ``Score``.

    Parameters
    ----------
    reason:
        Either ``ClassifiedError.reason`` (the enum) or its ``.value``
        string (e.g. ``"rate_limit"``). Anything with a ``.value``
        attribute is supported. Unknown/unmappable reasons return
        ``None`` rather than raising — keeps callers simple.
    provider:
        Provider slug, used to populate ``Score.source`` as
        ``"inference:{provider}"``. Empty string falls back to a
        plain ``"inference"`` source.
    override:
        Optional partial mapping that takes precedence over the
        defaults. Useful for operators tuning the affect response
        per persona without forking the table. Pass ``{"rate_limit":
        None}`` to disable a specific reason; pass full kwargs to
        replace one entry.

    Returns
    -------
    A ``Score`` ready to ingest, or ``None`` for non-emotional reasons
    (config issues), unknown reason strings, or empty input.
    """
    key = _normalise_reason(reason)
    if not key:
        return None

    spec: Mapping[str, Any] | None
    if override is not None and key in override:
        spec = override[key]
    else:
        spec = _DEFAULT_MAPPING.get(key)

    if spec is None:
        return None

    source = f"inference:{provider}" if provider else "inference"
    return Score(
        v=int(spec.get("v", 128)),
        a=int(spec.get("a", 128)),
        d=int(spec.get("d", 128)),
        u=int(spec.get("u", 0)),
        g=int(spec.get("g", 128)),
        w=int(spec.get("w", 128)),
        i=int(spec.get("i", 128)),
        patterns=tuple(spec.get("patterns", ())),
        direction="OBSERVATION",
        source=source,
    )


def _normalise_reason(reason: Any) -> str:
    """Accept either an enum, a string, or anything with a ``.value``."""
    if reason is None:
        return ""
    # FailoverReason or any enum
    value = getattr(reason, "value", None)
    if isinstance(value, str):
        return value
    if isinstance(reason, str):
        return reason
    return str(reason)


__all__ = ["score_from_failover"]
