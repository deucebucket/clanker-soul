"""``score_from_action_failure`` + ``score_from_correction`` — agent
emotional response to tool/system failures and resolutions. M4 #97."""

from __future__ import annotations

import pytest

from clanker_soul import (
    CORRECTION_PATTERNS,
    HEAVY_PATTERNS,
    MISTAKE_PATTERNS,
    POSITIVE_PATTERNS,
    Score,
    score_from_action_failure,
    score_from_correction,
)


# ── score_from_action_failure ──────────────────────────────────────────


def test_each_default_category_produces_score() -> None:
    for reason in (
        "timeout",
        "unreachable",
        "rate_limit",
        "resource_exhausted",
        "denied",
        "cancelled",
        "validation_error",
        "unknown",
    ):
        s = score_from_action_failure(reason)
        assert isinstance(s, Score), f"{reason} returned {s!r}"


def test_tool_populates_source() -> None:
    s = score_from_action_failure("timeout", tool="git")
    assert s is not None
    assert s.source == "tool:git"


def test_empty_tool_falls_back_to_plain_source() -> None:
    s = score_from_action_failure("timeout", tool="")
    assert s is not None
    assert s.source == "tool"


def test_all_default_categories_use_observation_direction() -> None:
    for reason in (
        "timeout",
        "unreachable",
        "rate_limit",
        "resource_exhausted",
        "denied",
        "cancelled",
        "validation_error",
        "unknown",
    ):
        s = score_from_action_failure(reason)
        assert s is not None
        assert s.direction == "OBSERVATION"


def test_enum_with_value_attr_accepted() -> None:
    class _FakeEnum:
        value = "rate_limit"

    s = score_from_action_failure(_FakeEnum())
    assert s is not None
    assert s.patterns == ("TOOL_RATE_LIMIT",)


def test_empty_or_none_reason_returns_none() -> None:
    assert score_from_action_failure(None) is None
    assert score_from_action_failure("") is None


def test_unknown_reason_returns_none() -> None:
    assert score_from_action_failure("this_is_not_a_real_reason") is None


def test_configuration_shaped_reasons_return_none() -> None:
    for reason in ("not_implemented", "tool_disabled", "config_error"):
        assert score_from_action_failure(reason) is None, reason


def test_override_can_disable_category() -> None:
    assert score_from_action_failure("timeout", override={"timeout": None}) is None


def test_override_can_replace_category() -> None:
    s = score_from_action_failure(
        "timeout",
        override={"timeout": {"v": 100, "w": 90, "patterns": ("MY_CUSTOM_TAG",)}},
        tool="git",
    )
    assert s is not None
    assert s.v == 100
    assert s.w == 90
    assert s.patterns == ("MY_CUSTOM_TAG",)


def test_no_default_pattern_in_HEAVY_PATTERNS() -> None:
    """Tool failures never trigger the breach mechanic."""
    for reason in (
        "timeout",
        "unreachable",
        "rate_limit",
        "resource_exhausted",
        "denied",
        "cancelled",
        "validation_error",
        "unknown",
    ):
        s = score_from_action_failure(reason)
        assert s is not None
        for p in s.patterns:
            assert p not in HEAVY_PATTERNS, f"{reason} pattern {p} must not be in HEAVY_PATTERNS"


def test_validation_error_is_only_W_denting_category() -> None:
    """All non-validation reasons leave W=128 (Worth untouched). Only a
    bad call dents Worth."""
    for reason in (
        "timeout",
        "unreachable",
        "rate_limit",
        "resource_exhausted",
        "denied",
        "cancelled",
        "unknown",
    ):
        s = score_from_action_failure(reason)
        assert s is not None
        assert s.w == 128, f"{reason} should leave W=128 but got W={s.w}"
    bad = score_from_action_failure("validation_error")
    assert bad is not None
    assert bad.w < 128
    assert bad.patterns == ("TOOL_BAD_CALL",)
    assert "TOOL_BAD_CALL" in MISTAKE_PATTERNS


# ── score_from_correction ──────────────────────────────────────────────


def test_pride_baseline_at_zero_after_mistakes() -> None:
    s = score_from_correction(tool="git", after_mistakes=0.0, kind="tool_fix")
    assert s.v == 155
    assert s.w == 145
    assert s.a == 100
    assert s.d == 170
    assert s.direction == "OBSERVATION"
    assert s.source == "tool:git"
    assert s.patterns == ("TOOL_FIX",)


def test_pride_scales_with_after_mistakes() -> None:
    """Bigger struggle → bigger lift, capped at +40 each on V and W."""
    s = score_from_correction(tool="git", after_mistakes=200.0, kind="tool_fix")
    # 200 / 4 = 50 → capped at 40
    assert s.v == 155 + 40
    assert s.w == 145 + 40
    # A 1000-load struggle should NOT exceed the cap.
    s2 = score_from_correction(tool="git", after_mistakes=1000.0, kind="tool_fix")
    assert s2.v == 155 + 40
    assert s2.w == 145 + 40


def test_recovery_kind_pattern() -> None:
    s = score_from_correction(tool="g", kind="recovery")
    assert s.patterns == ("RECOVERY",)


def test_problem_solved_kind_pattern() -> None:
    s = score_from_correction(tool="g", kind="problem_solved")
    assert s.patterns == ("PROBLEM_SOLVED",)


def test_relief_exhaustion_flat_shape_regardless_of_after_mistakes() -> None:
    """Relief is FLAT — no scaling with preceding burden."""
    for burden in (0.0, 100.0, 500.0, 1000.0):
        s = score_from_correction(
            tool="git",
            after_mistakes=burden,
            kind="relief_exhaustion",
        )
        assert s.v == 130, f"burden={burden}"
        assert s.a == 40
        assert s.d == 100
        assert s.u == 10
        assert s.g == 100
        assert s.w == 80
        assert s.i == 50
        # Still relieves the reservoir — pattern in CORRECTION_PATTERNS.
        assert s.patterns == ("RECOVERY",)
        assert "RECOVERY" in CORRECTION_PATTERNS


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        score_from_correction(kind="not_a_valid_kind")


def test_correction_patterns_subset_of_positive() -> None:
    """Belt-and-braces: corrections also count as nourishment."""
    assert CORRECTION_PATTERNS <= POSITIVE_PATTERNS
