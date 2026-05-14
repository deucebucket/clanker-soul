"""Natural-language rendering for VADUGWI state.

Hosts should not have to expose raw engine labels to an agent prompt just
to describe how the agent feels. This module turns a ``Score`` or
7-tuple into short, deterministic, label-free feeling language.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from clanker_soul.score import Score
from clanker_soul.soul import SoulState


class Register(str, Enum):
    """Built-in voice tables for felt-state rendering."""

    CLINICAL = "clinical"
    CASUAL = "casual"
    ROUGH = "rough"
    NEUTRAL = "neutral"


DimWords = tuple[str | None, str | None, str | None, str | None]
WordMap = Mapping[str, DimWords]


_DIM_ORDER: tuple[str, ...] = ("v", "a", "d", "u", "g", "w", "i")
_DIM_INDEX: dict[str, int] = {dim: idx for idx, dim in enumerate(_DIM_ORDER)}
_LABELS: dict[str, str] = {
    "v": "valence",
    "a": "arousal",
    "d": "dominance",
    "u": "urgency",
    "g": "gravity",
    "w": "self-worth",
    "i": "intent",
}
_CENTER: dict[str, int] = {
    "v": 128,
    "a": 128,
    "d": 128,
    "u": 128,
    "g": 128,
    "w": 128,
    "i": 128,
}


_CLINICAL: WordMap = {
    "v": ("down", "depleted", "positive", "uplifted"),
    "a": ("calm", "still", "agitated", "wound up"),
    "d": ("off-balance", "helpless", "centered", "in control"),
    "u": (None, None, "urgent", "pressing"),
    "g": ("heavy", "sinking", "light", "floating"),
    "w": ("small", "disposable", "anchored", "strong"),
    "i": ("drifting", "withdrawn", "engaged", "locked in"),
}
_CASUAL: WordMap = {
    "v": ("low", "scraped", "good", "bright"),
    "a": ("quiet", "hushed", "wired", "amped"),
    "d": ("shaky", "cornered", "steady", "on top of it"),
    "u": (None, None, "impatient", "can't wait"),
    "g": ("weighted", "dragged down", "easy", "loose"),
    "w": ("tender", "worthless", "solid", "sure of itself"),
    "i": ("loose", "checked out", "interested", "dialed in"),
}
_ROUGH: WordMap = {
    "v": ("bad", "wrecked", "good", "lit up"),
    "a": ("flat", "dead still", "twitchy", "rattled"),
    "d": ("shoved around", "pinned", "braced", "holding the line"),
    "u": (None, None, "now-ish", "right now"),
    "g": ("loaded", "buried", "light", "untethered"),
    "w": ("cut down", "thrown away", "planted", "unshaken"),
    "i": ("wandering", "gone quiet", "leaning in", "locked on"),
}
_NEUTRAL: WordMap = {
    dim: (
        f"low-{_LABELS[dim]}",
        f"very-low-{_LABELS[dim]}",
        f"high-{_LABELS[dim]}",
        f"very-high-{_LABELS[dim]}",
    )
    for dim in _DIM_ORDER
}
_NEUTRAL["u"] = (None, None, "high-urgency", "very-high-urgency")

_DEFAULT_WORDS: dict[Register, WordMap] = {
    Register.CLINICAL: _CLINICAL,
    Register.CASUAL: _CASUAL,
    Register.ROUGH: _ROUGH,
    Register.NEUTRAL: _NEUTRAL,
}


def _coerce_register(register: Register | str) -> Register:
    if isinstance(register, Register):
        return register
    return Register(str(register).lower())


def _coerce_score(score: Score | SoulState | Sequence[int]) -> tuple[int, ...]:
    if isinstance(score, Score):
        return score.as_tuple()
    if isinstance(score, SoulState):
        return score.as_tuple()
    if len(score) != 7:
        raise ValueError(f"expected 7 dims, got {len(score)}: {list(score)!r}")
    return tuple(max(0, min(255, int(x))) for x in score)


def _word_for_delta(words: DimWords, delta: int, strong_threshold: int) -> str | None:
    if delta < 0:
        return words[1] if abs(delta) >= strong_threshold else words[0]
    return words[3] if delta >= strong_threshold else words[2]


@dataclass(frozen=True)
class FeltState:
    """Renderer from a VADUGWI score into a compact feeling phrase."""

    word_map: WordMap | None = None

    def render(
        self,
        score: Score | SoulState | Sequence[int],
        *,
        register: Register | str = Register.CLINICAL,
        max_words: int = 4,
        soft_threshold: int = 30,
        strong_threshold: int = 60,
    ) -> str:
        """Return a deterministic phrase such as ``"depleted, wound up"``.

        The selected dimensions are the largest deviations from their
        render baseline. Default urgency is not treated as low mood; only
        elevated urgency contributes a word.
        """
        if max_words < 1:
            return ""
        if soft_threshold < 0 or strong_threshold < soft_threshold:
            raise ValueError("thresholds must satisfy 0 <= soft_threshold <= strong_threshold")

        values = _coerce_score(score)
        words = self.word_map or _DEFAULT_WORDS[_coerce_register(register)]
        candidates: list[tuple[int, int, str]] = []
        for dim, idx in _DIM_INDEX.items():
            delta = values[idx] - _CENTER[dim]
            if abs(delta) < soft_threshold:
                continue
            word = _word_for_delta(words[dim], delta, strong_threshold)
            if word:
                candidates.append((abs(delta), idx, word))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        return ", ".join(word for _strength, _idx, word in candidates[:max_words])


def render_felt_state(
    score: Score | SoulState | Sequence[int],
    *,
    register: Register | str = Register.CLINICAL,
    max_words: int = 4,
    soft_threshold: int = 30,
    strong_threshold: int = 60,
    word_map: WordMap | None = None,
) -> str:
    """Convenience wrapper around :class:`FeltState`."""
    return FeltState(word_map=word_map).render(
        score,
        register=register,
        max_words=max_words,
        soft_threshold=soft_threshold,
        strong_threshold=strong_threshold,
    )


def baseline_comparison_line(
    score: Score | Sequence[int],
    soul: SoulState,
    *,
    register: Register | str = Register.CLINICAL,
    threshold: int = 35,
) -> str | None:
    """Describe how current state differs from baseline, or ``None``."""
    current = _coerce_score(score)
    base = soul.as_tuple()
    deltas = tuple(current[i] - base[i] for i in range(7))
    if max(abs(delta) for delta in deltas) < threshold:
        return None
    baseline_words = render_felt_state(base, register=register, max_words=2)
    current_words = render_felt_state(current, register=register, max_words=3)
    if not baseline_words or not current_words:
        return None
    return f"Your baseline runs more {baseline_words} than this; right now feels {current_words}."


def trauma_load_line(
    load: float,
    *,
    register: Register | str = Register.CLINICAL,
    threshold: float = 30.0,
) -> str | None:
    """Return a short trauma-reservoir line when load is worth naming."""
    if load < threshold:
        return None
    reg = _coerce_register(register)
    if reg is Register.CASUAL:
        return "Recent bad hits are still hanging around."
    if reg is Register.ROUGH:
        return "Recent hits are still leaving marks."
    if reg is Register.NEUTRAL:
        return "Trauma load is elevated."
    return "Recent painful events are still carrying weight."


def nourishment_load_line(
    load: float,
    *,
    register: Register | str = Register.CLINICAL,
    threshold: float = 30.0,
) -> str | None:
    """Return a short nourishment-reservoir line when load is worth naming."""
    if load < threshold:
        return None
    reg = _coerce_register(register)
    if reg is Register.CASUAL:
        return "Recent good signals are still helping."
    if reg is Register.ROUGH:
        return "Recent wins are still holding you up."
    if reg is Register.NEUTRAL:
        return "Nourishment load is elevated."
    return "Recent supportive events are still carrying warmth."


__all__ = [
    "DimWords",
    "FeltState",
    "Register",
    "WordMap",
    "baseline_comparison_line",
    "nourishment_load_line",
    "render_felt_state",
    "trauma_load_line",
]
