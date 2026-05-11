"""Tunables for :py:class:`EmotionalPhysics` and the pattern sets that
distinguish positive from heavy events.

``PhysicsConfig`` defaults are chosen so an agent with the default
:py:class:`SoulState` (W=175) feels real but not whiny — small
annoyances shrug off, sustained malice leaves marks. Override per
agent at construction time, or override at runtime via the
:py:class:`ConfigOverrides` table.

The pattern sets are :py:class:`frozenset` so they're hashable and
cheap to test membership against. Hosts using a different scoring
engine can extend either set by replacing the constant before
constructing physics, or by subclassing. Pattern matching is
upper-cased.
"""

from __future__ import annotations

from dataclasses import dataclass


# Pattern names that signal a tool/system failure that the agent
# committed (a bad tool call, a misformatted argument). Routed to the
# MistakeReservoir rather than the TraumaReservoir — being-wrong is
# not being-wronged. Disjoint from HEAVY_PATTERNS so mistakes never
# trigger the breach mechanic; disjoint from POSITIVE_PATTERNS so the
# routing in _classify is unambiguous.
MISTAKE_PATTERNS = frozenset({"TOOL_BAD_CALL"})

# Pattern names that signal a *resolution* of a prior tool failure or
# stuck state. Actively relieve the MistakeReservoir (decrement, not
# decay) AND feed the NourishmentReservoir — corrections are a kind
# of nourishment with an extra mistake-relieving role. Subset of
# POSITIVE_PATTERNS by construction below.
CORRECTION_PATTERNS = frozenset(
    {
        "RECOVERY",
        "TOOL_FIX",
        "PROBLEM_SOLVED",
    }
)


# Pattern names that signal positive nourishment (from the Clanker
# scoring engine structures). Hosts using a different engine can
# extend this set by replacing the constant before constructing
# EmotionalPhysics, or by subclassing.
POSITIVE_PATTERNS = frozenset(
    {
        "GRATITUDE",
        "AFFIRMATION",
        "WARMTH",
        "HUMOR",
        "PLAYFULNESS",
        "ACKNOWLEDGEMENT",
        "ENCOURAGEMENT",
        "CARE",
        "REPAIR",
        "DIRECTED_POSITIVE",
        "RECOVERY_MILESTONE",
        "RELIEF_ABSENCE",
        "REPORTED_COMFORT",
        "CONTRADICTION_RESOLVE",
        # Correction patterns (also members of CORRECTION_PATTERNS).
        # Corrections are a kind of nourishment AND actively relieve
        # the MistakeReservoir — both branches run.
        "RECOVERY",
        "TOOL_FIX",
        "PROBLEM_SOLVED",
    }
)

# Patterns that count as "heavy" — these are the ones that, repeated,
# cause back-to-back soul damage via the breach mechanic. Major
# Clanker structures targeting self-worth, agency, or existence.
HEAVY_PATTERNS = frozenset(
    {
        "SELF_NULLIFY",
        "EXISTENTIAL_NEGATION",
        "ABANDONMENT",
        "BOUNDARY_VIOLATION",
        "DEHUMANIZATION",
        "BETRAYAL",
        "GASLIGHT",
        "CONTEMPT",
        "VICTIMIZATION",
        "DIRECTED_LABEL",
        "SOCIAL_NULLITY",
        "SELF_REMOVAL",
        "SELF_HARM_INTENT",
        "RHETORICAL_SELF_NEGATION",
        "RHETORICAL_HOPELESSNESS",
        "WITHHELD_POSITIVE",
        "EXCLUDED_POSITIVE",
        "POWER_OVER_SELF",
        "GRIEF_LOSS",
        "ATMOSPHERIC_GRIEF",
    }
)


@dataclass
class PhysicsConfig:
    """Tunable parameters for :py:class:`EmotionalPhysics`.

    Defaults chosen so an agent with the default :py:class:`SoulState`
    (W=175) feels real but not whiny — small annoyances shrug off,
    sustained malice leaves marks."""

    # Mood blend coefficient — fraction of "rawness" that displaces mood per hit.
    blend_alpha: float = 0.55

    # Mood decay half-life base (seconds). Modulated by Soul.W (higher W = faster).
    mood_decay_half_life_base: float = 600.0

    # Armor: max fraction of weight that resilience can absorb.
    armor_max: float = 0.55

    # Per-dimension soul pull (gap 2 of layered VADUGWI): after the
    # global blend, each dim is pulled back toward its soul anchor by
    # an amount proportional to the soul value. High soul[i] → strong
    # pull → mood stays close to soul on that dim. Low soul[i] → no
    # pull → mood follows the event freely. The "high-W soul means
    # W-hits are buffered; low-W soul means each shock destabilizes
    # hard" mechanic. 0.5 means a soul[i]=255 dim ends up halfway
    # between blended-mood and soul.
    dim_resilience_max: float = 0.5

    # Mood prime (gap 4 of layered VADUGWI): per-event score is tinted
    # by the agent's current mood before it's stored or ingested —
    # primed perception. A wounded mood reads ambiguous messages
    # slightly more negatively; a settled mood reads them flatly.
    # Small factor keeps the feedback loop bounded (max bias per
    # event is ±factor*128 = ±12.8 at default 0.1). Set to 0 to
    # disable. NOTE: physics does NOT auto-apply this — hosts call
    # mood_prime_score themselves on raw scores before ingest, then
    # pass the pre-prime score via ingest's raw= kwarg if they want
    # both versions in the log.
    mood_prime_factor: float = 0.1

    # Soul drift parameters.
    soul_drift_per_hour: float = 0.0008  # base rate for slow daily averaging
    soul_drift_min_distance: float = 6.0  # don't drift if mood near soul

    # Breach (back-to-back damage) parameters.
    breach_threshold: float = 35.0  # |M-S| above this counts as "wounded"
    heavy_threshold: float = 0.6  # event weight above this counts as "heavy"
    breach_delta: float = 0.085  # max fraction of one heavy event that goes straight to soul

    # Trauma vs nourishment imbalance → soul.W/V drift.
    healing_rate: float = 0.0006  # per-tick W increase when nourishment dominates
    wounding_rate: float = 0.0009  # per-tick W decrease when trauma dominates

    # When trauma reservoir crosses this, Soul actively starts losing W/V.
    trauma_pressure_floor: float = 5.0

    # ------------------------------------------------------------------
    # Mistakes + corrections (M4 #97)
    # ------------------------------------------------------------------

    # Mistakes pressure threshold — below this, no soul-wear drift from
    # the mistakes reservoir. Set higher than ``trauma_pressure_floor``
    # because mistakes are noisier than trauma; one bad tool call is not
    # an existential signal.
    mistake_pressure_floor: float = 50.0

    # Per-tick W/V wear rate when mistakes load is over floor.
    # DELIBERATELY weaker than ``wounding_rate`` (0.0009) — being-wrong
    # is not being-wronged. Self-doubt mildly bleeds competence-faith;
    # it does NOT bleed G (gravity/grounding) the way trauma does.
    mistake_wounding_rate: float = 0.0003

    # When a CORRECTION_PATTERNS Score is ingested, the mistakes
    # reservoir is actively decremented (relieved) by ``weight_effective
    # * 100 * correction_relief_factor``. 1.0 means "a correction can
    # fully cancel the immediate mistake weight." Set to 0.0 to disable
    # active relief and rely only on the 14-day decay.
    correction_relief_factor: float = 1.0

    # Resilience-uplift threshold — below this, no soul-uplift drift
    # from sustained corrections. Symmetric counterpart to
    # ``mistake_pressure_floor``.
    resilience_correction_floor: float = 50.0

    # Per-tick W/D uplift rate when correction load is over floor AND
    # exceeds mistakes load. DEFAULTS TO 0.0 (OFF) so v(N) hosts
    # upgrading to v(N+1) see zero behaviour change — their soul does
    # not start drifting up without an explicit opt-in. Operators who
    # want the resilience dynamic set this to ``0.0003`` (matches
    # ``mistake_wounding_rate`` magnitude asymmetrically) or higher.
    recovery_resilience_rate: float = 0.0


__all__ = [
    "PhysicsConfig",
    "POSITIVE_PATTERNS",
    "HEAVY_PATTERNS",
    "MISTAKE_PATTERNS",
    "CORRECTION_PATTERNS",
]
