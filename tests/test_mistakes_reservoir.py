"""``MistakeReservoir`` — accumulating self-doubt with self-relief.

Same persistence shape as ``TraumaReservoir`` but with an active
:py:meth:`relieve` method for correction events. M4 #97."""

from __future__ import annotations

from clanker_soul import MistakeReservoir, TraumaReservoir
from clanker_soul.soul.reservoirs import RESERVOIR_CAP, RESERVOIR_HALF_LIFE_S


def test_fresh_reservoir_is_empty() -> None:
    r = MistakeReservoir()
    assert r.load(now_ts=1000.0) == 0.0


def test_add_increases_load() -> None:
    r = MistakeReservoir()
    r.add("TOOL_BAD_CALL", weight=50.0, now_ts=1000.0)
    assert r.load(now_ts=1000.0) > 0.0


def test_repeated_adds_accumulate_and_cap_at_RESERVOIR_CAP() -> None:
    r = MistakeReservoir()
    # Twenty hits at weight 100 should cap at RESERVOIR_CAP=1000.
    for _ in range(20):
        r.add("TOOL_BAD_CALL", weight=100.0, now_ts=1000.0)
    by_pat = r.by_pattern(now_ts=1000.0)
    assert by_pat["TOOL_BAD_CALL"] <= RESERVOIR_CAP


def test_decays_toward_zero_over_time() -> None:
    r = MistakeReservoir()
    r.add("TOOL_BAD_CALL", weight=100.0, now_ts=1000.0)
    fresh = r.load(now_ts=1000.0)
    half_life_later = r.load(now_ts=1000.0 + RESERVOIR_HALF_LIFE_S)
    # After one half-life the weight should be ~half. 5% slop for rounding.
    assert 0.45 * fresh <= half_life_later <= 0.55 * fresh


def test_to_dict_from_dict_roundtrip() -> None:
    r = MistakeReservoir()
    r.add("TOOL_BAD_CALL", weight=40.0, now_ts=1000.0)
    r.add("CUSTOM_MISTAKE", weight=25.0, now_ts=1000.0)
    blob = r.to_dict()
    r2 = MistakeReservoir.from_dict(blob)
    assert r2.by_pattern(now_ts=1000.0) == r.by_pattern(now_ts=1000.0)


def test_isinstance_relationships() -> None:
    """Mistake IS-A Trauma (inherits the math) but Trauma is NOT-A
    Mistake — code can branch cleanly."""
    m = MistakeReservoir()
    t = TraumaReservoir()
    assert isinstance(m, TraumaReservoir)
    assert isinstance(m, MistakeReservoir)
    assert isinstance(t, TraumaReservoir)
    assert not isinstance(t, MistakeReservoir)


def test_relieve_on_empty_returns_zero_no_error() -> None:
    r = MistakeReservoir()
    assert r.relieve(50.0, now_ts=1000.0) == 0.0


def test_relieve_reduces_load_proportionally_across_patterns() -> None:
    r = MistakeReservoir()
    # Two patterns at different weights — relief should spread proportionally.
    r.add("TOOL_BAD_CALL", weight=60.0, now_ts=1000.0)
    r.add("OTHER_MISTAKE", weight=40.0, now_ts=1000.0)
    load_before = r.load(now_ts=1000.0)
    assert load_before > 0.0

    relieved = r.relieve(50.0, now_ts=1000.0)
    assert relieved > 0.0
    load_after = r.load(now_ts=1000.0)
    # Load dropped roughly by the relief amount.
    assert load_after == load_before - relieved or abs((load_before - relieved) - load_after) < 0.5

    # Both patterns share the burden in proportion to their starting weight:
    # TOOL_BAD_CALL=60/100 takes 60% of the relief; OTHER_MISTAKE=40/100 takes 40%.
    by_pat = r.by_pattern(now_ts=1000.0)
    bad_call_remaining = by_pat.get("TOOL_BAD_CALL", 0.0)
    other_remaining = by_pat.get("OTHER_MISTAKE", 0.0)
    # Expected: bad_call ≈ 60 - 30 = 30, other ≈ 40 - 20 = 20
    assert 25.0 <= bad_call_remaining <= 35.0
    assert 15.0 <= other_remaining <= 25.0


def test_relieve_capped_at_current_load_never_negative() -> None:
    r = MistakeReservoir()
    r.add("TOOL_BAD_CALL", weight=30.0, now_ts=1000.0)
    relieved = r.relieve(1000.0, now_ts=1000.0)
    # Can't relieve more than was there.
    assert relieved <= 30.0
    # Reservoir non-negative.
    assert r.load(now_ts=1000.0) >= 0.0
