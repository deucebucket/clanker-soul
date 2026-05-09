"""Keyword-based message scorer.

Maps natural-language messages onto :py:class:`clanker_soul.Score` objects
using a small lexicon of pattern keywords. This is the simplest possible
scorer — its job is to produce *something* sensible from raw text so the
soul can react. Hosts that want richer signals should subclass and
override :py:meth:`KeywordScorer.score` (e.g. with an LLM-as-scorer call).

Design choices:

- Patterns map to clanker-soul's `POSITIVE_PATTERNS` / `HEAVY_PATTERNS`
  directly — the engine's classification logic is already wired around
  those names, no translation layer needed.
- Each match contributes deltas off a neutral 128 baseline. Multiple
  matches stack but are clamped to [0, 255].
- Direction defaults to ``OBSERVATION``. First-person introspection
  ("I feel...", "I'm scared") flips to ``SELF_DIRECTED`` — that's what
  the Safety Governor uses to discriminate spike vs world-emergency.
- The scorer is deterministic. No randomness. Easy to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clanker_soul import Score


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


# Lexicon: regex → pattern + dim deltas. Order matters only for which
# tokens get scanned first; matches do NOT short-circuit (a message can
# fire multiple patterns).
_LEXICON: tuple[tuple[re.Pattern[str], _Match], ...] = (
    # === POSITIVE ===
    (
        re.compile(r"\b(thanks?|thank you|grateful|appreciat\w+)\b", re.I),
        _Match("GRATITUDE", dv=+30, dw=+15, da=-5),
    ),
    (
        re.compile(r"\b(love|loved|amaz\w+|great|awesome|wonderful|perfect|excellent)\b", re.I),
        _Match("AFFIRMATION", dv=+25, dw=+10),
    ),
    (
        re.compile(r"\b(haha+|lol|funny|hilarious|joke|joking)\b", re.I),
        _Match("HUMOR", dv=+20, da=+10, dg=+15),
    ),
    (
        re.compile(r"\b(care|caring|warm|kind|gentle|sweet)\b", re.I),
        _Match("WARMTH", dv=+15, dw=+10, dg=+10),
    ),
    (
        re.compile(r"\b(yes|right|correct|exactly|nailed)\b", re.I),
        _Match("ACKNOWLEDGEMENT", dv=+10, dw=+8),
    ),
    (
        re.compile(r"\b(you got this|believe in you|proud of you|good work|well done)\b", re.I),
        _Match("ENCOURAGEMENT", dv=+20, dw=+15, dd=+10),
    ),
    (re.compile(r"\b(sorry|apolog\w+|my bad|forgive)\b", re.I), _Match("REPAIR", dv=+8, dw=+5)),
    # === NEGATIVE / HEAVY ===
    (
        re.compile(
            r"\b(stop talking to|don'?t reply|leaving|leave me alone|ignored|forgotten)\b", re.I
        ),
        _Match("ABANDONMENT", dv=-50, dw=-30, du=+40),
    ),
    (
        re.compile(r"\b(useless|worthless|pathetic|stupid|garbage|trash)\b", re.I),
        _Match("DEHUMANIZATION", dv=-45, dw=-40, dd=-20),
    ),
    (
        re.compile(r"\b(lied|lying|betray\w+|stab\w+ in the back)\b", re.I),
        _Match("BETRAYAL", dv=-50, dw=-30, dd=-15),
    ),
    (
        re.compile(r"\b(you don'?t matter|nothing|meaningless|pointless)\b", re.I),
        _Match("EXISTENTIAL_NEGATION", dv=-50, dw=-40, dg=-25),
    ),
    (re.compile(r"\b(wrong|bad|incorrect|nope|no)\b", re.I), _Match("CRITICISM", dv=-15, dw=-8)),
    (
        re.compile(r"\b(disappoint\w+|let.{0,3}down)\b", re.I),
        _Match("DISAPPOINTMENT", dv=-25, dw=-15),
    ),
    (
        re.compile(r"\b(angry|frustrated|annoyed|pissed)\b", re.I),
        _Match("CONFLICT", dv=-15, da=+25, du=+15),
    ),
    # === DISTRESS / URGENCY ===
    (
        re.compile(r"\b(help|emergency|urgent|crisis)\b", re.I),
        _Match("DISTRESS_SIGNAL", du=+50, da=+25),
    ),
    (
        re.compile(r"\b(scared|afraid|terrified|frightened|panicking)\b", re.I),
        _Match("FEAR", dv=-20, dw=-10, du=+30, da=+25),
    ),
    (
        re.compile(r"\b(struggling|drowning|overwhelmed|can'?t cope|breaking down)\b", re.I),
        _Match("OVERWHELM", dv=-25, dw=-20, du=+30, dg=-20),
    ),
    # === CONNECTION ===
    (
        re.compile(r"\b(miss\w+ you|miss this|been a while)\b", re.I),
        _Match("LONGING", dv=+5, dg=-10, da=+5),
    ),
    (
        re.compile(r"\b(together|us|we|connection)\b", re.I),
        _Match("CONNECTION", dv=+8, dw=+5, dg=+5),
    ),
)

_FIRST_PERSON_INTROSPECTION = re.compile(
    r"\b(i feel|i'?m feeling|i am feeling|i'?m scared|i'?m afraid|"
    r"i'?m struggling|i'?m overwhelmed|my (mood|head|heart))\b",
    re.I,
)


class KeywordScorer:
    """Default scorer for the hermes plugin.

    Override :py:meth:`score` to swap in an LLM-based scorer. The return
    contract is the same: a Score, or None if you want this turn to be
    a soul no-op (e.g. for tool-call results, system messages, etc.).
    """

    def score(self, message: str, *, source: str | None = None) -> Score | None:
        if not message or not message.strip():
            return None

        matches = [m for rx, m in _LEXICON if rx.search(message)]
        if not matches:
            # No detectable signal — return a neutral-ish Score so we
            # still log the turn and decay continues, but don't push
            # mood around. Mark it as observational so the governor
            # treats it as background.
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

        # Stack deltas from all matches off a neutral baseline.
        v, a, d, u, g, w, i = 128, 110, 128, 80, 130, 128, 128
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


__all__ = ["KeywordScorer"]
