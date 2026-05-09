"""Score is the conversational-layer atom — small, frozen, and clamped."""

from __future__ import annotations

import pytest

from clanker_soul import Score


def test_defaults_are_neutral_except_urgency() -> None:
    s = Score()
    assert s.v == 128 and s.a == 128 and s.d == 128
    assert s.g == 128 and s.w == 128 and s.i == 128
    # Urgency is intensity, not polarity — its zero is meaningful.
    assert s.u == 0


def test_clamps_out_of_range_values() -> None:
    s = Score(v=300, a=-50, d=255, u=999, g=0, w=128, i=-1)
    assert s.v == 255
    assert s.a == 0
    assert s.d == 255
    assert s.u == 255
    assert s.g == 0
    assert s.w == 128
    assert s.i == 0


def test_is_frozen() -> None:
    s = Score(v=128)
    with pytest.raises(Exception):
        s.v = 200  # type: ignore[misc]


def test_patterns_normalize_to_tuple() -> None:
    s = Score(patterns=["ABANDONMENT", "SELF_NULLIFY"])  # type: ignore[arg-type]
    assert isinstance(s.patterns, tuple)
    assert s.patterns == ("ABANDONMENT", "SELF_NULLIFY")


def test_from_sequence_round_trips() -> None:
    s = Score.from_sequence([10, 20, 30, 40, 50, 60, 70], patterns=["X"])
    assert s.as_list() == [10, 20, 30, 40, 50, 60, 70]
    assert s.patterns == ("X",)
    assert s.as_tuple() == (10, 20, 30, 40, 50, 60, 70)


def test_from_sequence_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        Score.from_sequence([1, 2, 3])
