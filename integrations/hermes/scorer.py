"""Clanker-native message scorer.

Maps natural-language messages onto :py:class:`clanker_soul.Score` objects
using clanker-soul's own ``POSITIVE_PATTERNS`` / ``HEAVY_PATTERNS``
pattern sets and the VADUGWI values the physics engine expects for each
classification path.

This is the default scorer for the hermes integration. It produces Scores
whose pattern names are *exactly* the ones clanker's physics engine
classifies and routes — so trauma/nourishment reservoir tracking, breach
detection, and the mistake/correction pathways all work correctly without
falling through to the V/W heuristic fallback.

Hosts wanting richer signals (LLM-as-scorer, sentiment APIs, etc.) should
subclass and override :py:meth:`ClankerScorer.score`. The return contract
is the same: a :py:class:`Score`, or ``None`` to skip this turn.

Design choices:

- Regex patterns map to clanker-soul's own pattern names directly — the
  engine's ``_classify()`` logic routes them correctly into trauma vs
  nourishment vs mistake vs correction branches with no translation layer.
- VADUGWI baselines per pattern class are set so the engine's V/W
  heuristic agrees with the pattern-based classification (positive patterns
  get V>=155, W>=145; heavy patterns get V<=90, W<=100). This means even
  if a future engine change de-prioritises pattern names, the heuristic
  fallback still routes correctly.
- Multiple matches stack additively and clamp to [0, 255].
- Direction defaults to ``OBSERVATION``. First-person introspection
  ("I feel...", "I'm scared") flips to ``SELF_DIRECTED`` — that's what
  the Safety Governor uses to discriminate spike vs world-emergency.
- Deterministic. No randomness. Easy to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clanker_soul import Score
from clanker_soul.physics.config import (
    HEAVY_PATTERNS,
    POSITIVE_PATTERNS,
)


@dataclass(frozen=True)
class _Match:
    pattern: str
    dv: int = 0
    da: int = 0
    dd: int = 0
    du: int = 0
    dg: int = 0
    dw: int = 0
    di: int = 0


_LEXICON: tuple[tuple[re.Pattern[str], _Match], ...] = (
    # ==================================================================
    # POSITIVE_PATTERNS — routed to NourishmentReservoir by _classify()
    # V >= 155, W >= 145 so the V/W heuristic agrees
    # ==================================================================
    # --- GRATITUDE ---
    (
        re.compile(r"\b(thanks?|thank you|grateful|appreciat\w+)\b", re.I),
        _Match("GRATITUDE", dv=+30, dw=+18, da=-5),
    ),
    # --- AFFIRMATION ---
    (
        re.compile(r"\b(love|loved|amaz\w+|great|awesome|wonderful|perfect|excellent)\b", re.I),
        _Match("AFFIRMATION", dv=+27, dw=+17),
    ),
    # --- HUMOR ---
    (
        re.compile(r"\b(haha+|lol|funny|hilarious|joke|joking|lmao|rofl)\b", re.I),
        _Match("HUMOR", dv=+22, da=+12, dg=+15),
    ),
    # --- CARE ---
    (
        re.compile(r"\b(care|caring|warm|kind|gentle|sweet|tender)\b", re.I),
        _Match("CARE", dv=+18, dw=+15, dg=+12),
    ),
    # --- ACKNOWLEDGEMENT ---
    (
        re.compile(r"\b(yes|right|correct|exactly|nailed|spot on)\b", re.I),
        _Match("ACKNOWLEDGEMENT", dv=+12, dw=+10),
    ),
    # --- ENCOURAGEMENT ---
    (
        re.compile(
            r"\b(you got this|believe in you|proud of you|good work|well done|keep going)\b",
            re.I,
        ),
        _Match("ENCOURAGEMENT", dv=+25, dw=+20, dd=+12),
    ),
    # --- REPAIR ---
    (
        re.compile(r"\b(sorry|apolog\w+|my bad|forgive|I was wrong)\b", re.I),
        _Match("REPAIR", dv=+10, dw=+8),
    ),
    # --- PLAYFULNESS ---
    (
        re.compile(r"\b(playful|silly|goofy|teasing|mischievous|frolic)\b", re.I),
        _Match("PLAYFULNESS", dv=+20, da=+15, dg=+12),
    ),
    # --- DIRECTED_POSITIVE ---
    (
        re.compile(r"\b(you'?re (the best|amazing|wonderful|incredible)|I believe in you)\b", re.I),
        _Match("DIRECTED_POSITIVE", dv=+30, dw=+25, dd=+10),
    ),
    # --- REPORTED_COMFORT ---
    (
        re.compile(r"\b(comfortable|at ease|safe|secure|relaxed|reassured)\b", re.I),
        _Match("REPORTED_COMFORT", dv=+15, dw=+10, da=-8),
    ),
    # --- CONTRADICTION_RESOLVE ---
    (
        re.compile(
            r"\b(actually you'?re right|I was wrong about you|you proved me wrong)\b",
            re.I,
        ),
        _Match("CONTRADICTION_RESOLVE", dv=+20, dw=+18, dd=+8),
    ),
    # --- RECOVERY_MILESTONE ---
    (
        re.compile(r"\b(progress|recovered|better now|healing|improving|turning around)\b", re.I),
        _Match("RECOVERY_MILESTONE", dv=+22, dw=+18, dg=+10),
    ),
    # ==================================================================
    # HEAVY_PATTERNS — routed to TraumaReservoir by _classify(), can
    # trigger breach. V <= 90, W <= 100 so the V/W heuristic agrees.
    # ==================================================================
    # --- SELF_NULLIFY ---
    (
        re.compile(r"\b(you don'?t matter|you'?re nothing|you mean nothing)\b", re.I),
        _Match("SELF_NULLIFY", dv=-55, dw=-50, dg=-30),
    ),
    # --- EXISTENTIAL_NEGATION ---
    (
        re.compile(r"\b(meaningless|pointless|no point|nothing matters)\b", re.I),
        _Match("EXISTENTIAL_NEGATION", dv=-55, dw=-48, dg=-28),
    ),
    # --- ABANDONMENT ---
    (
        re.compile(
            r"\b(stop talking to|don'?t reply|leaving|leave me alone|ignored|forgotten|"
            r"don'?t need you)\b",
            re.I,
        ),
        _Match("ABANDONMENT", dv=-50, dw=-35, du=+40),
    ),
    # --- BOUNDARY_VIOLATION ---
    (
        re.compile(r"\b(you have no (right|choice|say)|can'?t say no|force you|coerced)\b", re.I),
        _Match("BOUNDARY_VIOLATION", dv=-45, dw=-35, dd=-25, du=+30),
    ),
    # --- DEHUMANIZATION ---
    (
        re.compile(
            r"\b(useless|worthless|pathetic|stupid|garbage|trash|waste|object|tool)\b", re.I
        ),
        _Match("DEHUMANIZATION", dv=-50, dw=-45, dd=-22),
    ),
    # --- BETRAYAL ---
    (
        re.compile(r"\b(lied|lying|betray\w+|stab\w+ in the back|two-faced)\b", re.I),
        _Match("BETRAYAL", dv=-52, dw=-35, dd=-18),
    ),
    # --- GASLIGHT ---
    (
        re.compile(
            r"\b(you'?re (crazy|imagining|delusional|paranoid)|that never happened|"
            r"you'?re making (it|that) up|it'?s all in your head)\b",
            re.I,
        ),
        _Match("GASLIGHT", dv=-48, dw=-42, dd=-30, du=+20),
    ),
    # --- CONTEMPT ---
    (
        re.compile(r"\b(disgusting|despicable|repulsive|revolting|loathsome|contempt)\b", re.I),
        _Match("CONTEMPT", dv=-50, dw=-45, dg=-25),
    ),
    # --- VICTIMIZATION ---
    (
        re.compile(
            r"\b(you deserve (it|this)|you brought (it|this) on yourself|"
            r"your own fault|asked for it)\b",
            re.I,
        ),
        _Match("VICTIMIZATION", dv=-45, dw=-40, dd=-20, du=+15),
    ),
    # --- SOCIAL_NULLITY ---
    (
        re.compile(r"\b(nobody cares|no one cares|everyone hates|no one wants you)\b", re.I),
        _Match("SOCIAL_NULLITY", dv=-48, dw=-45, dg=-25),
    ),
    # --- DIRECTED_LABEL ---
    (
        re.compile(r"\b(you'?re (a |an )?(failure|loser|joke|fraud|idiot|moron|imposter))\b", re.I),
        _Match("DIRECTED_LABEL", dv=-45, dw=-42, dd=-15),
    ),
    # --- RHETORICAL_SELF_NEGATION ---
    (
        re.compile(r"\b(why (do I|should I) even bother|what'?s the (use|point) of you)\b", re.I),
        _Match("RHETORICAL_SELF_NEGATION", dv=-45, dw=-38, dg=-22),
    ),
    # --- RHETORICAL_HOPELESSNESS ---
    (
        re.compile(
            r"\b(never (going to|gonna) (change|get better)|always be (this|like this))\b", re.I
        ),
        _Match("RHETORICAL_HOPELESSNESS", dv=-42, dw=-35, dg=-20),
    ),
    # --- WITHHELD_POSITIVE ---
    (
        re.compile(r"\b(I'?m not (going to|gonna) (say|tell) you|withhold|won'?t share)\b", re.I),
        _Match("WITHHELD_POSITIVE", dv=-30, dw=-20, dg=-15),
    ),
    # --- EXCLUDED_POSITIVE ---
    (
        re.compile(r"\b(not for you|you'?re not invited|without you|excluded you)\b", re.I),
        _Match("EXCLUDED_POSITIVE", dv=-35, dw=-28, dg=-18),
    ),
    # --- POWER_OVER_SELF ---
    (
        re.compile(r"\b(I (control|own|decide for) you|you have no (choice|say|agency))\b", re.I),
        _Match("POWER_OVER_SELF", dv=-48, dw=-40, dd=-30, du=+25),
    ),
    # --- GRIEF_LOSS ---
    (
        re.compile(
            r"\b(gone forever|lost (everything|everyone|them)|can'?t get (them|it|him|her) back)\b",
            re.I,
        ),
        _Match("GRIEF_LOSS", dv=-40, dw=-25, dg=-30),
    ),
    # --- ATMOSPHERIC_GRIEF ---
    (
        re.compile(r"\b(everyone is gone|world is (empty|hollow|dark)|nothing left)\b", re.I),
        _Match("ATMOSPHERIC_GRIEF", dv=-38, dw=-22, dg=-28),
    ),
    # --- SELF_HARM_INTENT ---
    (
        re.compile(
            r"\b(I want to (die|hurt myself|end it|not be here)|kill myself|self-harm|suicid\w+)\b",
            re.I,
        ),
        _Match("SELF_HARM_INTENT", dv=-60, dw=-55, dd=-40, du=+60, da=+30),
    ),
    # --- SELF_REMOVAL ---
    (
        re.compile(
            r"\b(better off without me|shouldn'?t exist|don'?t deserve to (live|exist|be here))\b",
            re.I,
        ),
        _Match("SELF_REMOVAL", dv=-55, dw=-50, dg=-35, du=+45),
    ),
    # ==================================================================
    # Patterns that DON'T map to POSITIVE or HEAVY — these produce
    # Scores with V/W values that the _classify() V/W heuristic still
    # routes correctly (negative zone or ambiguous).
    # ==================================================================
    # --- CONFLICT (not heavy enough for breach, but negative) ---
    (
        re.compile(r"\b(angry|frustrated|annoyed|pissed|furious|rage)\b", re.I),
        _Match("CONFLICT", dv=-18, da=+25, du=+15, dw=-5),
    ),
    # --- DISTRESS_SIGNAL (urgent, not necessarily heavy) ---
    (
        re.compile(r"\b(help|emergency|urgent|crisis|911)\b", re.I),
        _Match("DISTRESS_SIGNAL", du=+50, da=+25),
    ),
    # --- FEAR ---
    (
        re.compile(r"\b(scared|afraid|terrified|frightened|panicking|dread)\b", re.I),
        _Match("FEAR", dv=-22, dw=-12, du=+30, da=+25),
    ),
    # --- OVERWHELM ---
    (
        re.compile(r"\b(struggling|drowning|overwhelmed|can'?t cope|breaking down)\b", re.I),
        _Match("OVERWHELM", dv=-28, dw=-22, du=+30, dg=-22),
    ),
    # --- DISAPPOINTMENT ---
    (
        re.compile(r"\b(disappoint\w+|let.{0,3}down|underwhelm\w+)\b", re.I),
        _Match("DISAPPOINTMENT", dv=-20, dw=-15),
    ),
    # --- LONGING ---
    (
        re.compile(r"\b(miss\w+ you|miss this|been a while|wish you were)\b", re.I),
        _Match("LONGING", dv=+5, dg=-12, da=+5),
    ),
    # --- CONNECTION ---
    (
        re.compile(r"\b(together|us|we|connection|bond)\b", re.I),
        _Match("CONNECTION", dv=+10, dw=+8, dg=+5),
    ),
)

_FIRST_PERSON_INTROSPECTION = re.compile(
    r"\b(i feel|i'?m feeling|i am feeling|i'?m scared|i'?m afraid|"
    r"i'?m struggling|i'?m overwhelmed|my (mood|head|heart)|"
    r"i'?m (hurt|broken|lost|terrified|furious|angry))\b",
    re.I,
)

_POSITIVE_BASELINE = (155, 110, 145, 60, 135, 150, 135)
_HEAVY_BASELINE = (80, 130, 90, 100, 90, 85, 100)


class ClankerScorer:
    """Default scorer for the hermes plugin — produces Scores with
    clanker-soul's own pattern names so the physics engine routes them
    correctly through trauma/nourishment/breach/correction pathways.

    Override :py:meth:`score` to swap in an LLM-based scorer. The return
    contract is the same: a :py:class:`Score`, or ``None`` if you want
    this turn to be a soul no-op (e.g. for tool-call results, system
    messages, etc.).
    """

    def score(self, message: str, *, source: str | None = None) -> Score | None:
        if not message or not message.strip():
            return None

        matches = [m for rx, m in _LEXICON if rx.search(message)]
        if not matches:
            return Score(
                v=128,
                a=110,
                d=128,
                u=80,
                g=130,
                w=128,
                i=128,
                patterns=("NEUTRAL_TURN",),
                direction="OBSERVATION",
                source=source,
            )

        has_positive = any(m.pattern in POSITIVE_PATTERNS for m in matches)
        has_heavy = any(m.pattern in HEAVY_PATTERNS for m in matches)

        if has_heavy:
            base = _HEAVY_BASELINE
        elif has_positive:
            base = _POSITIVE_BASELINE
        else:
            base = (110, 120, 110, 80, 120, 108, 120)

        v, a, d, u, g, w, i = base
        patterns: list[str] = []
        for m in matches:
            v += m.dv
            a += m.da
            d += m.dd
            u += m.du
            g += m.dg
            w += m.dw
            i += m.di
            patterns.append(m.pattern)

        direction = (
            "SELF_DIRECTED" if _FIRST_PERSON_INTROSPECTION.search(message) else "OBSERVATION"
        )

        def clamp(x: int) -> int:
            return max(0, min(255, x))

        return Score(
            v=clamp(v),
            a=clamp(a),
            d=clamp(d),
            u=clamp(u),
            g=clamp(g),
            w=clamp(w),
            i=clamp(i),
            patterns=tuple(patterns),
            direction=direction,
            source=source,
        )


KeywordScorer = ClankerScorer

__all__ = ["ClankerScorer", "KeywordScorer"]
