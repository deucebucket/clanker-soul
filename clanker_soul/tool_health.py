"""Map agent tool/system failures (and resolutions) to clanker-soul Scores.

When the agent's tools or systems break — a file write fails, an MCP
server times out, a git push gets rejected — the resulting emotional
impact today goes through the same path as a human being contemptuous
to the agent. That's wrong. A tool breaking is **not the agent's
fault**. The agent should feel annoyance and a small dent in
Dominance, but **not** lose self-Worth, and **not** trigger the breach
mechanic.

The exception is **validation errors** — the tool rejected the call
shape. That's partly the agent's fault. A small W dent IS appropriate,
and the resulting Score carries the ``TOOL_BAD_CALL`` pattern that
routes to the :py:class:`~clanker_soul.MistakeReservoir` for
accumulating self-doubt. (See ``MISTAKE_PATTERNS`` in
``clanker_soul/physics/config.py``.)

This module is the agent-facing counterpart to
:py:mod:`integrations.hermes.inference_health` — the latter is
inference-layer-specific, the former is the rest of the agent's tool
surface (filesystem, browser, MCP, git, OS commands, custom). Both
emit Scores with ``direction="OBSERVATION"`` and patterns disjoint
from ``HEAVY_PATTERNS``.

The module exports two helpers so the failure-and-recovery loop is
symmetric:

* :py:func:`score_from_action_failure` for the failure event.
* :py:func:`score_from_correction` for the resolution event. Two
  distinct affective shapes per the literature (Sweeny & Vohs 2012,
  Bandura 1997, Ryan & Deci 2000, ``docs/research/m4_failure_response_matrix.md``):
  pride-shaped (mastery, durable W/D uplift, scales with preceding
  burden) vs. relief-shaped (cessation-of-negative, flat, no pride
  integration).

Usage::

    from clanker_soul.tool_health import (
        score_from_action_failure,
        score_from_correction,
    )

    # On failure:
    s = score_from_action_failure("timeout", tool="git")
    if s is not None:
        plugin.ingest(s)

    # On successful resolution after at least one failure:
    mistakes_before = plugin.mistake_pressure()
    if result.ok and mistakes_before > 0:
        plugin.ingest(score_from_correction(
            tool="git",
            after_mistakes=mistakes_before,
            kind="tool_fix",  # or "relief_exhaustion" — see docstring
        ))
"""

from __future__ import annotations

from typing import Any, Mapping

from clanker_soul.score import Score


# ── Failure mapping ─────────────────────────────────────────────────────
#
# All non-``validation_error`` rows have W=128 (Worth untouched) and
# patterns disjoint from ``HEAVY_PATTERNS`` — a tool breaking is not
# being-wronged. ``validation_error`` is the only category that dents
# Worth (W=120) AND emits the ``TOOL_BAD_CALL`` pattern that routes
# into the MistakeReservoir.
#
# Configuration-shaped failures (``not_implemented``, ``tool_disabled``,
# ``config_error``) map to ``None`` — operator concerns, not agent
# experiences. The agent doesn't feel the absence of an unconfigured
# capability.

_DEFAULT_MAPPING: Mapping[str, Mapping[str, Any] | None] = {
    "timeout": {
        "v": 118,
        "a": 138,
        "d": 115,
        "u": 60,
        "g": 122,
        "w": 128,
        "i": 115,
        "patterns": ("TOOL_TIMEOUT",),
    },
    "unreachable": {
        "v": 115,
        "a": 132,
        "d": 105,
        "u": 65,
        "g": 118,
        "w": 128,
        "i": 110,
        "patterns": ("TOOL_UNREACHABLE",),
    },
    "rate_limit": {
        "v": 120,
        "a": 140,
        "d": 115,
        "u": 70,
        "g": 120,
        "w": 128,
        "i": 115,
        "patterns": ("TOOL_RATE_LIMIT",),
    },
    "resource_exhausted": {
        "v": 118,
        "a": 135,
        "d": 110,
        "u": 70,
        "g": 115,
        "w": 128,
        "i": 110,
        "patterns": ("TOOL_RESOURCE_EXHAUSTED",),
    },
    "denied": {
        # Operator/OS permission denial — agent had no agency over this.
        "v": 110,
        "a": 130,
        "d": 95,
        "u": 60,
        "g": 115,
        "w": 128,
        "i": 100,
        "patterns": ("TOOL_DENIED",),
    },
    "cancelled": {
        "v": 122,
        "a": 115,
        "d": 115,
        "u": 40,
        "g": 122,
        "w": 128,
        "i": 115,
        "patterns": ("TOOL_CANCELLED",),
    },
    "validation_error": {
        # "I made a mistake" — the ONLY category with W < 128. Carries
        # the TOOL_BAD_CALL pattern that routes into the MistakeReservoir
        # (see clanker_soul.physics.config.MISTAKE_PATTERNS).
        "v": 120,
        "a": 125,
        "d": 110,
        "u": 55,
        "g": 120,
        "w": 120,
        "i": 110,
        "patterns": ("TOOL_BAD_CALL",),
    },
    "unknown": {
        "v": 118,
        "a": 125,
        "d": 110,
        "u": 60,
        "g": 118,
        "w": 128,
        "i": 110,
        "patterns": ("TOOL_UNKNOWN_FAIL",),
    },
    # Configuration-shaped — not emotional events.
    "not_implemented": None,
    "tool_disabled": None,
    "config_error": None,
}


def score_from_action_failure(
    reason: Any,
    *,
    tool: str = "",
    override: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> Score | None:
    """Map a tool/action failure reason to an ingestable ``Score``.

    Parameters
    ----------
    reason:
        Either a string (e.g. ``"timeout"``), an enum value, or
        anything with a ``.value`` attribute. Unknown/unmappable
        reasons return ``None`` rather than raising — keeps callers
        simple. ``None`` or empty input also returns ``None``.
    tool:
        Tool/action slug, used to populate ``Score.source`` as
        ``"tool:{tool}"``. Empty string falls back to a plain
        ``"tool"`` source.
    override:
        Optional partial mapping that takes precedence over the
        defaults. Pass ``{"timeout": None}`` to disable a specific
        category; pass a full dict of kwargs to replace one entry
        with custom dims/patterns. Useful for per-host tuning without
        forking the table.

    Returns
    -------
    A ``Score`` ready to ingest, or ``None`` for:

    * non-emotional reasons (``not_implemented``, ``tool_disabled``,
      ``config_error``)
    * unknown reason strings
    * empty / None input
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

    source = f"tool:{tool}" if tool else "tool"
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


# ── Correction mapping ──────────────────────────────────────────────────
#
# Two distinct emotional shapes per the literature
# (docs/research/m4_failure_response_matrix.md). The pride-shaped
# kinds (``tool_fix``, ``problem_solved``, ``recovery``) integrate
# the resolution as mastery: V↑↑, W↑, D↑↑, scaling with the
# ``after_mistakes`` load (a bigger answer to a longer struggle).
# The relief-only kind (``relief_exhaustion``) is flat — V back to
# neutral, A and U drop sharply, W stays low. Both still relieve
# the MistakeReservoir (the ``RECOVERY`` / ``TOOL_FIX`` /
# ``PROBLEM_SOLVED`` patterns are all members of
# ``CORRECTION_PATTERNS``).

_PRIDE_KINDS: frozenset[str] = frozenset({"tool_fix", "problem_solved", "recovery"})
_VALID_KINDS: frozenset[str] = _PRIDE_KINDS | {"relief_exhaustion"}

# Map ``kind`` to the pattern that gets emitted on the Score.
_KIND_PATTERN: Mapping[str, str] = {
    "tool_fix": "TOOL_FIX",
    "problem_solved": "PROBLEM_SOLVED",
    "recovery": "RECOVERY",
    # Relief still routes through the CORRECTION_PATTERNS branch so
    # the MistakeReservoir is relieved. Reuses ``RECOVERY`` as the
    # canonical "something resolved" tag; the *shape* of the Score
    # is what distinguishes pride from relief, not the pattern name.
    "relief_exhaustion": "RECOVERY",
}


def score_from_correction(
    *,
    tool: str = "",
    after_mistakes: float = 0.0,
    kind: str = "tool_fix",
) -> Score:
    """Score representing the emotional payoff of resolving a failure.

    Hosts call this on the turn a tool succeeds after at least one
    prior failure on the same logical operation. The resulting Score
    is ingested into physics where:

    1. It feeds the :py:class:`NourishmentReservoir` (correction
       patterns are members of ``POSITIVE_PATTERNS``).
    2. It **actively relieves** the
       :py:class:`~clanker_soul.MistakeReservoir` (correction patterns
       are also members of ``CORRECTION_PATTERNS``).
    3. It lifts mood per the shape below.

    Parameters
    ----------
    tool:
        Tool/action slug. Populates ``Score.source`` as
        ``"tool:{tool}"``.
    after_mistakes:
        ``plugin.mistake_pressure()`` at the moment the correction
        happens — i.e. the load right BEFORE the relief gets applied.
        Used to scale the pride-shaped kinds (a bigger answer to a
        longer struggle). Ignored by ``relief_exhaustion`` (which is
        flat by construction).
    kind:
        One of:

        * ``"tool_fix"`` *(default)*, ``"problem_solved"``, ``"recovery"`` —
          pride-shaped. Durable W and D affirmation, V scales with
          ``after_mistakes`` up to a cap. Hosts use these when the
          agent's current state suggests mastery integration: solid
          self-worth, growth-mindset framing, "I conquered this
          system" cognitive frame.

        * ``"relief_exhaustion"`` — relief-only. V returns to neutral,
          A and U drop sharply, W stays low. Cessation-of-negative,
          not mastery. Hosts use this when the agent's state suggests
          exhausted-but-not-triumphant resolution: low-W,
          fixed-mindset framing, anxiety-driven persistence that
          finally paid off. **Still relieves the MistakeReservoir**
          — the agent gets the load lifted, just without the
          durable competence boost.

    Returns
    -------
    A ``Score`` with ``direction="OBSERVATION"``, ``source=f"tool:{tool}"``
    (or ``"tool"`` when tool is empty), and the appropriate pattern
    for the kind.

    Raises
    ------
    ValueError: when ``kind`` is not one of the four valid values.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"score_from_correction: kind={kind!r} must be one of {sorted(_VALID_KINDS)}"
        )

    source = f"tool:{tool}" if tool else "tool"
    pattern = _KIND_PATTERN[kind]

    if kind in _PRIDE_KINDS:
        # Pride-shape: V and W scale with preceding burden, capped at
        # +40 each so a 1000-load reservoir doesn't blow out the
        # 0-255 channel. Scaling factor ``after_mistakes / 4`` was
        # chosen so a typical reservoir load of 100-200 produces a
        # meaningful but not overwhelming lift.
        scale = max(0.0, after_mistakes)
        v_lift = int(min(40, scale / 4))
        w_lift = int(min(40, scale / 4))
        return Score(
            v=155 + v_lift,
            a=100,  # calm, settled — not the spike of joy, the let-out breath
            d=170,  # competence affirmed
            u=30,  # urgency drops
            g=135,  # grounded
            w=145 + w_lift,  # self-Worth recovers
            i=140,  # forward-leaning
            patterns=(pattern,),
            direction="OBSERVATION",
            source=source,
        )

    # relief_exhaustion — flat, no scaling, no pride integration.
    # The cognitive frame: "thank god it stopped."
    return Score(
        v=130,  # back to neutral, no positive spike
        a=40,  # sharp drop in arousal — the let-out breath
        d=100,  # dominance not affirmed (the situation ended, not me winning)
        u=10,  # urgency collapses
        g=100,  # drops; situation no longer feels heavy
        w=80,  # unchanged-or-low — no integration of this as a win
        i=50,  # passive — agent wants rest, not next task
        patterns=(pattern,),
        direction="OBSERVATION",
        source=source,
    )


# ── helpers ─────────────────────────────────────────────────────────────


def _normalise_reason(reason: Any) -> str:
    """Accept either an enum, a string, or anything with a ``.value``."""
    if reason is None:
        return ""
    value = getattr(reason, "value", None)
    if isinstance(value, str):
        return value
    if isinstance(reason, str):
        return reason
    return str(reason)


__all__ = [
    "score_from_action_failure",
    "score_from_correction",
]
