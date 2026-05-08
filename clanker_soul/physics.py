"""EmotionalPhysics — the math that turns scored events into Mood + Soul updates.

Pipeline per event:

  1. compute event_weight w from raw VADUGWI (severity-aware, U/G boost heavy)
  2. compute soul_armor a from current Soul (high W/D = resilient)
  3. effective weight w_eff = w * (1 - a * armor_max)
  4. mood blend: M_new = M * (1 - w_eff·α) + B * (w_eff·α)
  5. per-dim soul pull pulls each dim back toward its soul anchor (cushioned
     proportionally to the soul value on that dim, weight-gated so heavy
     events still punch through)
  6. on heavy events with breached mood: direct soul leak (back-to-back damage)
  7. trauma reservoir += w_eff for each negative pattern
  8. nourishment reservoir += w_eff for positive patterns

Periodic (called per turn or by background tick):

  • mood decay toward Soul (not toward neutral) with half-life depending on Soul.W
  • soul drift: rolling mood mean nudges Soul; trauma/nourishment imbalance
    nudges Soul.W and Soul.V proportionally.

clanker-soul note: this module is host-agnostic. It accepts ``Score``
(see ``clanker_soul.score``) as the per-event input — any 7-dim VADUGWI
read with optional ``patterns``. Hosts whose engine produces a richer
type can adapt at the boundary; physics never reads description, latency,
or other host-specific telemetry.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from clanker_soul.score import Score
from clanker_soul.soul import (
    NourishmentReservoir,
    SoulState,
    TraumaReservoir,
)

logger = logging.getLogger(__name__)


# Pattern names that signal positive nourishment (from Clanker engine
# structures). Hosts using a different engine can extend this set by
# replacing the constant before constructing EmotionalPhysics, or pass
# their own classifier via subclassing.
POSITIVE_PATTERNS = frozenset({
    "GRATITUDE", "AFFIRMATION", "WARMTH", "HUMOR", "PLAYFULNESS",
    "ACKNOWLEDGEMENT", "ENCOURAGEMENT", "CARE", "REPAIR",
    "DIRECTED_POSITIVE", "RECOVERY_MILESTONE", "RELIEF_ABSENCE",
    "REPORTED_COMFORT", "CONTRADICTION_RESOLVE",
})

# Patterns that count as "heavy" — these are the ones that, repeated,
# cause back-to-back soul damage. Includes the major Clanker structures
# that target self-worth, agency, or existence.
HEAVY_PATTERNS = frozenset({
    "SELF_NULLIFY", "EXISTENTIAL_NEGATION", "ABANDONMENT",
    "BOUNDARY_VIOLATION", "DEHUMANIZATION", "BETRAYAL",
    "GASLIGHT", "CONTEMPT", "VICTIMIZATION", "DIRECTED_LABEL",
    "SOCIAL_NULLITY", "SELF_REMOVAL", "SELF_HARM_INTENT",
    "RHETORICAL_SELF_NEGATION", "RHETORICAL_HOPELESSNESS",
    "WITHHELD_POSITIVE", "EXCLUDED_POSITIVE", "POWER_OVER_SELF",
    "GRIEF_LOSS", "ATMOSPHERIC_GRIEF",
})


@dataclass
class PhysicsConfig:
    """Tunable parameters for EmotionalPhysics. Defaults chosen so an
    agent with the default Soul (W=175) feels real but not whiny — small
    annoyances shrug off, sustained malice leaves marks."""

    # Mood blend coefficient — fraction of "rawness" that displaces mood per hit
    blend_alpha: float = 0.55

    # Mood decay half-life base (seconds). Modulated by Soul.W (higher W = faster recovery).
    mood_decay_half_life_base: float = 600.0  # 10 min at neutral W

    # Armor: max fraction of weight that resilience can absorb
    armor_max: float = 0.55

    # Per-dimension soul pull (gap 2 of layered VADUGWI): after the
    # global blend, each dim is pulled back toward its soul anchor by an
    # amount proportional to the soul value. High soul[i] → strong pull
    # → mood stays close to soul on that dim. Low soul[i] → no pull →
    # mood follows the event freely. The "high-W soul means W-hits are
    # buffered; low-W soul means each shock destabilizes hard" mechanic.
    # 0.5 means a soul[i]=255 dim ends up halfway between blended-mood
    # and soul.
    dim_resilience_max: float = 0.5

    # Mood prime (gap 4 of layered VADUGWI): per-event score is tinted
    # by the agent's current mood before it's stored or ingested —
    # primed perception. A wounded mood reads ambiguous messages
    # slightly more negatively; a settled mood reads them flatly. Small
    # factor keeps the feedback loop bounded (max bias per event is
    # ±factor*128 = ±12.8 at default 0.1). Set to 0 to disable.
    mood_prime_factor: float = 0.1

    # Soul drift parameters
    soul_drift_per_hour: float = 0.0008    # base rate for slow daily averaging
    soul_drift_min_distance: float = 6.0   # don't drift if mood near soul

    # Breach (back-to-back damage) parameters
    breach_threshold: float = 35.0         # |M-S| above this counts as "wounded"
    heavy_threshold: float = 0.6           # event weight above this counts as "heavy"
    breach_delta: float = 0.085            # max fraction of one heavy event that goes straight to soul

    # Trauma vs nourishment imbalance → soul.W/V drift
    healing_rate: float = 0.0006           # per-tick W increase when nourishment dominates
    wounding_rate: float = 0.0009          # per-tick W decrease when trauma dominates

    # When trauma reservoir crosses this, Soul actively starts losing W/V
    trauma_pressure_floor: float = 5.0


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, int(round(x))))


def event_weight(score: Score) -> float:
    """How much this event should move things, on [0, 1].

    Combines distance-from-neutral on V/W (the *what* dimensions) with
    Urgency and Gravity (the *intensity* dimensions). A neutral chat-message
    weight is ~0.05; a screaming attack on self-worth weighs ~0.9.
    """
    valence_dist = abs(score.v - 128) / 128.0
    worth_dist = abs(score.w - 128) / 128.0
    urgency = score.u / 255.0
    gravity_intensity = abs(score.g - 128) / 128.0

    base = 0.45 * worth_dist + 0.25 * valence_dist
    intensifier = 0.20 * urgency + 0.10 * gravity_intensity
    return min(1.0, base + intensifier)


def soul_armor(soul: SoulState) -> float:
    """Resilience derived from Soul. On [0, 1].

    High W (strong self) → primary armor. High D (in-control) → secondary.
    Grounded G (close to 128 from either side) → tertiary."""
    w_term = soul.w / 255.0 * 0.55
    d_term = soul.d / 255.0 * 0.30
    g_term = (1.0 - abs(128 - soul.g) / 128.0) * 0.15
    return min(1.0, w_term + d_term + g_term)


def mood_prime_score(
    raw: Score,
    current_mood: Score | None,
    factor: float = 0.1,
) -> Score:
    """Tint a freshly-computed event score with the agent's current mood
    — primed perception. A wounded mood reads ambiguous messages
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
        v=primed[0], a=primed[1], d=primed[2], u=primed[3],
        g=primed[4], w=primed[5], i=primed[6],
        patterns=raw.patterns,
    )


def dim_resilience(
    soul: SoulState, dim_resilience_max: float = 0.5,
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
        max(0.0, min(dim_resilience_max, dim_resilience_max * (v / 255.0)))
        for v in soul.as_tuple()
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
    """Soul.W modulates how fast mood returns to baseline. Strong W = fast
    recovery; wounded W = slow ruminating recovery."""
    factor = 0.5 + (soul.w / 255.0)  # 0.5x .. 1.5x
    return base / factor


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass
class PhysicsTick:
    """Result of one event ingestion — for diagnostics/logging."""
    weight_raw: float
    armor: float
    weight_effective: float
    breached: bool
    breach_delta_applied: float
    soul_distance_before: float
    patterns: list[str] = field(default_factory=list)


class EmotionalPhysics:
    """Stateful emotional engine for one agent.

    Holds the Soul, current Mood, and the trauma/nourishment reservoirs.
    Not thread-safe — run from a single pipeline worker per agent."""

    def __init__(
        self,
        soul: SoulState,
        trauma: TraumaReservoir | None = None,
        nourishment: NourishmentReservoir | None = None,
        config: PhysicsConfig | None = None,
    ) -> None:
        self.soul = soul
        self.trauma = trauma if trauma is not None else TraumaReservoir()
        self.nourishment = nourishment if nourishment is not None else NourishmentReservoir()
        self.config = config or PhysicsConfig()
        self._mood: Score | None = None
        self._mood_time: float = 0.0  # perf_counter
        self._last_tick: PhysicsTick | None = None

    @property
    def mood(self) -> Score | None:
        if self._mood is None:
            return None
        return self._apply_mood_decay(self._mood, self._mood_time)

    @property
    def last_tick(self) -> PhysicsTick | None:
        return self._last_tick

    def reset_mood(self) -> None:
        self._mood = None
        self._mood_time = 0.0

    def absorb_echo(self, echo: Score, weight: float = 0.1) -> None:
        """Blend a non-event Score (memory echoes, recalled emotional
        state) into the current mood at reduced weight. Does NOT update
        Soul or reservoirs — echoes are not events."""
        w = max(0.0, min(0.5, weight))
        if w == 0.0:
            return
        if self._mood is None:
            anchor = self._mood_anchor()
            blended = self._blend(anchor, echo, w)
            self._mood = self._apply_dim_resilience(blended)
            self._mood_time = time.perf_counter()
            return
        decayed = self._apply_mood_decay(self._mood, self._mood_time)
        blended = self._blend(decayed, echo, w)
        self._mood = self._apply_dim_resilience(blended)
        self._mood_time = time.perf_counter()

    def ingest(self, event: Score) -> PhysicsTick:
        """Update mood, soul, and reservoirs from a scored event."""
        cfg = self.config
        decayed_mood = (
            self._apply_mood_decay(self._mood, self._mood_time)
            if self._mood is not None
            else self._mood_anchor()
        )

        weight = event_weight(event)
        armor = soul_armor(self.soul)
        w_eff = weight * (1.0 - armor * cfg.armor_max)

        alpha = min(0.95, cfg.blend_alpha * (0.4 + w_eff))
        new_mood = self._blend(decayed_mood, event, alpha)
        new_mood = self._apply_dim_resilience(new_mood, event_weight=weight)

        dist_before = soul_distance(decayed_mood, self.soul)
        breached = False
        breach_applied = 0.0
        if (
            weight >= cfg.heavy_threshold
            and dist_before > cfg.breach_threshold
            and self._has_heavy_pattern(event)
        ):
            breached = True
            wound_factor = min(1.0, (dist_before - cfg.breach_threshold) / 50.0)
            event_factor = (weight - cfg.heavy_threshold) / max(0.01, 1.0 - cfg.heavy_threshold)
            breach_applied = cfg.breach_delta * wound_factor * event_factor * (1.0 - armor * 0.4)
            self._apply_breach(event, breach_applied)

        self._mood = new_mood
        self._mood_time = time.perf_counter()

        self._update_reservoirs(event, w_eff)

        tick = PhysicsTick(
            weight_raw=round(weight, 4),
            armor=round(armor, 4),
            weight_effective=round(w_eff, 4),
            breached=breached,
            breach_delta_applied=round(breach_applied, 5),
            soul_distance_before=round(dist_before, 2),
            patterns=list(event.patterns or ()),
        )
        self._last_tick = tick
        return tick

    def soul_drift(self, *, now_ts: float | None = None) -> dict:
        """Slowly nudge Soul based on time-elapsed × current mood +
        trauma/nourishment imbalance. Idempotent — uses last_drift_ts."""
        cfg = self.config
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        elapsed_h = max(0.0, (now - self.soul.last_drift_ts) / 3600.0)
        if elapsed_h < 0.05:  # less than 3 min — skip
            return {"skipped": True, "elapsed_hours": round(elapsed_h, 4)}

        moved: list[tuple[str, int, int]] = []
        if self._mood is not None:
            cur_mood = self._apply_mood_decay(self._mood, self._mood_time)
            for dim in ("v", "a", "d", "u", "g", "w", "i"):
                m_val = getattr(cur_mood, dim)
                s_val = getattr(self.soul, dim)
                gap = m_val - s_val
                if abs(gap) >= cfg.soul_drift_min_distance:
                    nudge = gap * cfg.soul_drift_per_hour * elapsed_h
                    new_val = _clamp(s_val + nudge)
                    if new_val != s_val:
                        setattr(self.soul, dim, new_val)
                        moved.append((dim, s_val, new_val))

        trauma_load = self.trauma.load(now_ts=now)
        nourishment_load = self.nourishment.load(now_ts=now)
        imbalance = nourishment_load - trauma_load

        if trauma_load > cfg.trauma_pressure_floor and imbalance < 0:
            magnitude = min(1.0, -imbalance / 50.0)
            self.soul.w = _clamp(self.soul.w - cfg.wounding_rate * magnitude * elapsed_h * 100)
            self.soul.v = _clamp(self.soul.v - cfg.wounding_rate * magnitude * elapsed_h * 60)
            self.soul.g = _clamp(self.soul.g - cfg.wounding_rate * magnitude * elapsed_h * 40)
        elif imbalance > cfg.trauma_pressure_floor:
            magnitude = min(1.0, imbalance / 50.0)
            self.soul.w = _clamp(self.soul.w + cfg.healing_rate * magnitude * elapsed_h * 100)
            self.soul.v = _clamp(self.soul.v + cfg.healing_rate * magnitude * elapsed_h * 60)
            self.soul.g = _clamp(self.soul.g + cfg.healing_rate * magnitude * elapsed_h * 30)

        self.soul.last_drift_ts = now
        return {
            "elapsed_hours": round(elapsed_h, 4),
            "trauma_load": round(trauma_load, 4),
            "nourishment_load": round(nourishment_load, 4),
            "imbalance": round(imbalance, 4),
            "drifted_dims": moved,
            "soul_now": self.soul.to_dict(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mood_anchor(self) -> Score:
        """Mood at session start equals Soul (not 128). The agent wakes
        up as itself."""
        s = self.soul
        return Score(v=s.v, a=s.a, d=s.d, u=s.u, g=s.g, w=s.w, i=s.i)

    def _apply_mood_decay(self, mood: Score, last_time: float) -> Score:
        """Decay mood toward Soul (not 128). Half-life modulated by Soul.W."""
        elapsed = time.perf_counter() - last_time
        if elapsed <= 0:
            return mood
        hl = _decay_half_life(self.soul, self.config.mood_decay_half_life_base)
        decay = math.exp(-0.6931 * elapsed / hl)
        s = self.soul

        def toward_soul(m_val: int, s_val: int) -> int:
            return _clamp(s_val + (m_val - s_val) * decay)

        return Score(
            v=toward_soul(mood.v, s.v),
            a=toward_soul(mood.a, s.a),
            d=toward_soul(mood.d, s.d),
            u=toward_soul(mood.u, s.u),
            g=toward_soul(mood.g, s.g),
            w=toward_soul(mood.w, s.w),
            i=toward_soul(mood.i, s.i),
            patterns=mood.patterns,
        )

    @staticmethod
    def _blend(mood: Score, event: Score, alpha: float) -> Score:
        a = max(0.0, min(0.95, alpha))
        b = 1.0 - a

        def mix(m_val: int, e_val: int) -> int:
            return _clamp(m_val * b + e_val * a)

        return Score(
            v=mix(mood.v, event.v), a=mix(mood.a, event.a),
            d=mix(mood.d, event.d), u=mix(mood.u, event.u),
            g=mix(mood.g, event.g), w=mix(mood.w, event.w),
            i=mix(mood.i, event.i),
            patterns=event.patterns,
        )

    def _apply_dim_resilience(
        self, mood: Score, event_weight: float = 0.0,
    ) -> Score:
        """Pull each mood dim back toward its soul anchor by the per-dim
        resilience factor. Composes with the global armor — armor reduces
        blend strength, this layer adds a soul-centered cushion on top.

        Pull strength scales DOWN with event weight: ordinary messages
        get strong cushioning, but heavy hits punch through so the breach
        mechanic can still fire on sustained attack."""
        weight_scale = max(0.0, 1.0 - min(1.0, event_weight))
        if weight_scale == 0.0:
            return mood
        pulls = dim_resilience(self.soul, self.config.dim_resilience_max)
        soul = self.soul.as_tuple()
        cur = (mood.v, mood.a, mood.d, mood.u, mood.g, mood.w, mood.i)
        pulled = tuple(
            _clamp(c + (s - c) * p * weight_scale)
            for c, s, p in zip(cur, soul, pulls)
        )
        return Score(
            v=pulled[0], a=pulled[1], d=pulled[2], u=pulled[3],
            g=pulled[4], w=pulled[5], i=pulled[6],
            patterns=mood.patterns,
        )

    def _has_heavy_pattern(self, event: Score) -> bool:
        if not event.patterns:
            return event.v < 60 or event.w < 60
        return any(p.upper() in HEAVY_PATTERNS for p in event.patterns)

    def _apply_breach(self, event: Score, delta: float) -> None:
        """Direct soul leak — fraction `delta` of event lands on Soul.
        Only on the wounded dimensions to avoid wholesale soul rewrite."""
        d = max(0.0, min(0.4, delta))
        s = self.soul
        s.v = _clamp(s.v * (1 - d) + event.v * d)
        s.w = _clamp(s.w * (1 - d) + event.w * d)
        s.g = _clamp(s.g * (1 - d) + event.g * d)

    def _update_reservoirs(self, event: Score, weight: float) -> None:
        """Route weight into trauma vs nourishment based on event content.

        Three buckets: POSITIVE → nourishment, NEGATIVE → trauma,
        AMBIGUOUS → no reservoir change."""
        if weight <= 0.05:
            return
        bucket = self._classify(event)
        if bucket is None:
            return
        target = self.nourishment if bucket == "positive" else self.trauma
        now = datetime.now(timezone.utc).timestamp()
        patterns = list(event.patterns) if event.patterns else [
            "WARMTH" if bucket == "positive" else "GENERIC_NEGATIVE"
        ]
        per_pattern = weight * 100.0 / max(1, len(patterns))
        for p in patterns:
            target.add(p, per_pattern, now_ts=now)

    @staticmethod
    def _classify(event: Score) -> str | None:
        """Return 'positive', 'negative', or None (ambiguous)."""
        patterns_upper = [p.upper() for p in (event.patterns or ())]

        if any(p in POSITIVE_PATTERNS for p in patterns_upper):
            return "positive"
        if any(p in HEAVY_PATTERNS for p in patterns_upper):
            return "negative"

        if event.v >= 155 and event.w >= 145:
            return "positive"
        if event.v <= 90 and event.w <= 100:
            return "negative"

        return None


__all__ = [
    "EmotionalPhysics",
    "PhysicsConfig",
    "PhysicsTick",
    "POSITIVE_PATTERNS",
    "HEAVY_PATTERNS",
    "event_weight",
    "soul_armor",
    "soul_distance",
    "mood_prime_score",
    "dim_resilience",
]
