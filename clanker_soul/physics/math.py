"""Pure math helpers for the emotional physics.

Stateless functions: ``event_weight``, ``soul_armor``,
``mood_prime_score``, ``dim_resilience``, ``soul_distance``, plus the
internal ``_clamp`` and ``_decay_half_life`` and ``_why`` (the
human-readable reason generator that consumes a :py:class:`PhysicsTick`).

Exported as the package's public math API so hosts can reuse the
helpers — e.g. compute ``event_weight`` outside ingest for a
prediction model.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from clanker_soul.score import Score
from clanker_soul.soul import SoulState

if TYPE_CHECKING:
    from clanker_soul.physics.tick import PhysicsTick


def _clamp(x: float, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, int(round(x))))


def event_weight(score: Score) -> float:
    """How much this event should move things, on [0, 1].

    Combines distance-from-neutral on V/W (the *what* dimensions) with
    Urgency and Gravity (the *intensity* dimensions). A neutral
    chat-message weight is ~0.05; a screaming attack on self-worth
    weighs ~0.9."""
    valence_dist = abs(score.v - 128) / 128.0
    worth_dist = abs(score.w - 128) / 128.0
    urgency = score.u / 255.0
    gravity_intensity = abs(score.g - 128) / 128.0

    base = 0.45 * worth_dist + 0.25 * valence_dist
    intensifier = 0.20 * urgency + 0.10 * gravity_intensity
    return min(1.0, base + intensifier)


def soul_armor(soul: SoulState) -> float:
    """Resilience derived from Soul. On [0, 1].

    High W (strong self) → primary armor. High D (in-control) →
    secondary. Grounded G (close to 128 from either side) → tertiary."""
    w_term = soul.w / 255.0 * 0.55
    d_term = soul.d / 255.0 * 0.30
    g_term = (1.0 - abs(128 - soul.g) / 128.0) * 0.15
    return min(1.0, w_term + d_term + g_term)


def mood_prime_score(
    raw: Score,
    current_mood: Score | None,
    factor: float = 0.1,
) -> Score:
    """Tint a freshly-computed event score with the agent's current
    mood — primed perception. A wounded mood reads ambiguous messages
    slightly more negatively. The shift on each dim is
    ``(mood[i] - 128) * factor``.

    Returns the raw score unchanged when ``current_mood`` is None
    (first event of a session) or factor is 0 (opt-out)."""
    if current_mood is None or factor <= 0:
        return raw

    def shift(r_val: int, m_val: int) -> int:
        return _clamp(r_val + (m_val - 128) * factor)

    primed = (
        shift(raw.v, current_mood.v),
        shift(raw.a, current_mood.a),
        shift(raw.d, current_mood.d),
        shift(raw.u, current_mood.u),
        shift(raw.g, current_mood.g),
        shift(raw.w, current_mood.w),
        shift(raw.i, current_mood.i),
    )
    if primed == (raw.v, raw.a, raw.d, raw.u, raw.g, raw.w, raw.i):
        return raw  # mood was effectively neutral on every dim
    return Score(
        v=primed[0],
        a=primed[1],
        d=primed[2],
        u=primed[3],
        g=primed[4],
        w=primed[5],
        i=primed[6],
        patterns=raw.patterns,
    )


def dim_resilience(
    soul: SoulState,
    dim_resilience_max: float = 0.5,
) -> tuple[float, ...]:
    """Per-dimension pull-toward-soul factors derived from the Soul vector.

    Returns a 7-tuple of pulls in ``[0.0, dim_resilience_max]``: the
    fraction of the post-blend mood-vs-soul gap that should be closed
    back toward soul on each dimension.

    For a default soul ``[145, 110, 160, 80, 130, 175, 135]`` and
    ``dim_resilience_max=0.5``, the resulting pulls are approximately
    ``(0.28, 0.22, 0.31, 0.16, 0.25, 0.34, 0.26)`` — strongest on W
    (the agent's "I know who I am"), weakest on U (a calm baseline is
    too gentle to fully cushion an urgent hit)."""
    return tuple(
        max(0.0, min(dim_resilience_max, dim_resilience_max * (v / 255.0))) for v in soul.as_tuple()
    )


def soul_distance(mood: Score, soul: SoulState) -> float:
    """L2-ish distance between current Mood and Soul. Returns 0..255-ish."""
    diffs = [
        mood.v - soul.v,
        mood.w - soul.w,
        mood.g - soul.g,
        mood.d - soul.d,
    ]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def _decay_half_life(soul: SoulState, base: float) -> float:
    """Soul.W modulates how fast mood returns to baseline. Strong W =
    fast recovery; wounded W = slow ruminating recovery."""
    factor = 0.5 + (soul.w / 255.0)  # 0.5x .. 1.5x
    return base / factor


def _why(tick: "PhysicsTick", event: Score) -> str:
    """One-line human-readable reason for what just happened.

    Pre-baked so ``sqlite3 soul.db "SELECT ts, why FROM events"`` is
    immediately readable in the terminal. The UI can also synthesize
    its own narrative from the structured fields if it wants — this
    is for fast forensic scanning."""
    pat_str = ",".join(event.patterns) if event.patterns else "no-pattern"
    parts = [f"{pat_str} (weight={tick.weight_raw:.2f})"]
    if tick.armor > 0.05:
        parts.append(f"armor={tick.armor:.2f} → w_eff={tick.weight_effective:.2f}")
    else:
        parts.append(f"w_eff={tick.weight_effective:.2f}")
    if tick.breached:
        parts.append(
            f"mood was {tick.soul_distance_before:.0f}pt from soul → "
            f"BREACH (Δ={tick.breach_delta_applied:.3f} to soul.v/w/g)"
        )
    elif tick.soul_distance_before > 30:
        parts.append(f"mood was {tick.soul_distance_before:.0f}pt from soul")
    return "; ".join(parts)


__all__ = [
    "event_weight",
    "soul_armor",
    "mood_prime_score",
    "dim_resilience",
    "soul_distance",
]
