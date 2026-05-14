"""Felt-state language renders VADUGWI without engine vocabulary."""

from __future__ import annotations

import pytest

from clanker_soul import (
    FeltState,
    Register,
    Score,
    SoulState,
    baseline_comparison_line,
    nourishment_load_line,
    render_felt_state,
    trauma_load_line,
)


def test_neutral_score_renders_empty() -> None:
    assert render_felt_state(Score()) == ""


def test_render_picks_largest_deviations_in_score_order_tiebreak() -> None:
    text = render_felt_state(
        Score(v=40, a=210, d=120, u=90, g=128, w=60, i=128),
        max_words=3,
    )
    assert text == "depleted, wound up, disposable"


def test_soft_and_strong_thresholds_select_ladder_words() -> None:
    soft = render_felt_state(Score(v=90), soft_threshold=30, strong_threshold=60)
    strong = render_felt_state(Score(v=60), soft_threshold=30, strong_threshold=60)
    below = render_felt_state(Score(v=110), soft_threshold=30, strong_threshold=60)
    assert soft == "down"
    assert strong == "depleted"
    assert below == ""


def test_urgency_is_intensity_not_low_mood() -> None:
    assert render_felt_state(Score(u=0)) == ""
    assert render_felt_state(Score(u=200), max_words=1) == "pressing"


def test_register_switching_changes_voice() -> None:
    score = Score(v=40, a=210, w=60)
    assert render_felt_state(score, register=Register.CLINICAL, max_words=2) == (
        "depleted, wound up"
    )
    assert render_felt_state(score, register=Register.CASUAL, max_words=2) == ("scraped, amped")
    assert render_felt_state(score, register=Register.ROUGH, max_words=2) == ("wrecked, rattled")
    assert render_felt_state(score, register=Register.NEUTRAL, max_words=2) == (
        "very-low-valence, very-high-arousal"
    )


def test_custom_mapping_override_path() -> None:
    custom = {
        "v": ("v-low", "v-crash", "v-hi", "v-glow"),
        "a": ("a-low", "a-flat", "a-hi", "a-buzz"),
        "d": ("d-low", "d-crash", "d-hi", "d-glow"),
        "u": (None, None, "u-hi", "u-now"),
        "g": ("g-low", "g-crash", "g-hi", "g-glow"),
        "w": ("w-low", "w-crash", "w-hi", "w-glow"),
        "i": ("i-low", "i-crash", "i-hi", "i-glow"),
    }
    renderer = FeltState(word_map=custom)
    assert renderer.render(Score(v=40, a=210), max_words=2) == "v-crash, a-buzz"


def test_sequence_input_validates_length() -> None:
    with pytest.raises(ValueError):
        render_felt_state((1, 2, 3))


def test_baseline_comparison_line_omits_small_delta() -> None:
    soul = SoulState(v=145, a=110, d=160, u=80, g=130, w=175, i=135)
    assert baseline_comparison_line((142, 112, 158, 84, 132, 170, 134), soul) is None


def test_baseline_comparison_line_describes_large_delta_without_labels() -> None:
    soul = SoulState(v=145, a=110, d=160, u=80, g=130, w=175, i=135)
    line = baseline_comparison_line(
        Score(v=50, a=210, d=70, u=150, g=70, w=45, i=80),
        soul,
    )
    assert line is not None
    assert "baseline" in line
    assert "depleted" in line
    assert "VADUGWI" not in line
    assert "Soul" not in line


def test_reservoir_lines_threshold_and_registers() -> None:
    assert trauma_load_line(10) is None
    assert nourishment_load_line(10) is None
    assert trauma_load_line(40) == "Recent painful events are still carrying weight."
    assert nourishment_load_line(40, register=Register.CASUAL) == (
        "Recent good signals are still helping."
    )
    assert trauma_load_line(40, register=Register.NEUTRAL) == "Trauma load is elevated."
