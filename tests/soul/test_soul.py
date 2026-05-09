"""Soul, reservoirs, and durable storage."""

from __future__ import annotations

import time

from clanker_soul import (
    NourishmentReservoir,
    RESERVOIR_HALF_LIFE_S,
    SoulState,
    SoulStore,
    TraumaReservoir,
)


def test_soul_defaults_lean_positive_not_neutral() -> None:
    """A fresh agent shouldn't read as 'depressed' on neutral input —
    Soul defaults bias mildly positive, in-control, with strong worth."""
    s = SoulState()
    assert s.v > 128
    assert s.w > 128
    assert s.d > 128
    assert s.u < 128  # urgency is low at rest


def test_soul_state_round_trips_through_dict() -> None:
    s = SoulState(
        v=140, a=110, d=160, u=80, g=130, w=175, i=135, last_drift_ts=12345.6, last_save_ts=12350.0
    )
    s2 = SoulState.from_dict(s.to_dict())
    assert s2.v == 140 and s2.w == 175 and s2.last_drift_ts == 12345.6


def test_trauma_reservoir_decays_over_time() -> None:
    r = TraumaReservoir()
    r.add("ABANDONMENT", 100.0, now_ts=0.0)
    # One full half-life later it should be ~half.
    later = r.load(now_ts=RESERVOIR_HALF_LIFE_S)
    assert 45.0 < later < 55.0


def test_trauma_reservoir_stacks_same_pattern() -> None:
    r = TraumaReservoir()
    r.add("LOSS", 30.0, now_ts=0.0)
    r.add("LOSS", 30.0, now_ts=0.0)
    # Both events at same instant → roughly additive (clamped by cap).
    assert r.load(now_ts=0.0) >= 55.0


def test_trauma_reservoir_ignores_zero_or_blank() -> None:
    r = TraumaReservoir()
    r.add("", 50.0)
    r.add("PAT", 0.0)
    r.add("PAT", -10.0)
    assert r.load() == 0


def test_nourishment_distinct_from_trauma_class() -> None:
    n = NourishmentReservoir()
    n.add("CARE", 40.0, now_ts=0.0)
    assert isinstance(n, NourishmentReservoir)
    assert n.load(now_ts=0.0) > 0


def test_soul_store_round_trip_with_explicit_path(tmp_path) -> None:
    db = tmp_path / "soul.db"
    store = SoulStore(db)

    soul = SoulState(v=160, w=180)
    trauma = TraumaReservoir()
    trauma.add("EXISTENTIAL_NEGATION", 25.0, now_ts=time.time())
    nourishment = NourishmentReservoir()
    nourishment.add("WARMTH", 10.0, now_ts=time.time())

    store.save("test-agent", soul, trauma, nourishment)

    loaded_soul, loaded_trauma, loaded_nourishment = store.load("test-agent")
    assert loaded_soul.v == 160 and loaded_soul.w == 180
    assert loaded_trauma.load() > 0
    assert loaded_nourishment.load() > 0


def test_soul_store_get_singleton_per_path(tmp_path) -> None:
    a = SoulStore.get(tmp_path / "shared.db")
    b = SoulStore.get(tmp_path / "shared.db")
    assert a is b


def test_soul_store_unknown_agent_returns_defaults(tmp_path) -> None:
    store = SoulStore(tmp_path / "empty.db")
    soul, trauma, nourishment = store.load("never-saved")
    # Should be the default SoulState, not a crash or None.
    assert isinstance(soul, SoulState)
    assert trauma.load() == 0
    assert nourishment.load() == 0
