"""``crisis_signal`` — distinguishes emotional spike from real emergency."""
from __future__ import annotations

from clanker_soul.eventlog import IngestRecord
from clanker_soul.governor import GovernorConfig, crisis_signal
from clanker_soul.score import Score
from clanker_soul.soul import SoulState


def _ev(direction: str | None, source: str | None = None,
        patterns: tuple[str, ...] = ("BETRAYAL",)) -> IngestRecord:
    """Test factory for a heavy negative event with the given direction."""
    raw = Score(v=40, w=40, u=200, patterns=patterns,
                direction=direction, source=source)
    return IngestRecord(
        ts=0.0, agent_id="x", raw=raw, primed=None,
        mood_before=None, mood_after=Score(),
        soul_before=SoulState(), soul_after=SoulState(),
        weight_raw=0.78, armor=0.55, weight_effective=0.42,
        breached=False, breach_delta=0.0,
        patterns=patterns, classification="negative",
        why="test event",
    )


def test_no_recent_events_returns_no_signal() -> None:
    diag = crisis_signal([], GovernorConfig())
    assert diag.is_emergency is False
    assert diag.confidence == 1.0


def test_majority_self_directed_is_spike() -> None:
    """5 SELF_DIRECTED events — agent is being attacked, not the world."""
    events = [_ev("SELF_DIRECTED", f"telegram:user_{i}") for i in range(5)]
    diag = crisis_signal(events, GovernorConfig())
    assert diag.is_emergency is False
    assert "spike" in diag.summary.lower()
    assert diag.directed_count == 5


def test_majority_external_is_emergency() -> None:
    """5 EXTERNAL_REPORT events — something is broken in the world."""
    events = [
        _ev("EXTERNAL_REPORT", "x.com/post/1"),
        _ev("EXTERNAL_REPORT", "x.com/post/2"),
        _ev("EXTERNAL_REPORT", "news.example/article/1"),
        _ev("EXTERNAL_REPORT", "rss/feed/2"),
        _ev("EXTERNAL_REPORT", "x.com/post/3"),
    ]
    diag = crisis_signal(events, GovernorConfig())
    assert diag.is_emergency is True
    assert diag.external_count == 5
    assert diag.distinct_sources >= 3


def test_no_direction_metadata_returns_low_confidence_spike() -> None:
    """Events without direction → can't tell. Default to non-emergency
    with low confidence so host doesn't false-positive."""
    events = [_ev(None) for _ in range(5)]
    diag = crisis_signal(events, GovernorConfig())
    assert diag.is_emergency is False
    assert diag.confidence < 0.5
    assert diag.unspecified_count == 5


def test_mixed_direction_returns_ambient_pressure() -> None:
    events = [
        _ev("SELF_DIRECTED", "telegram:user_a"),
        _ev("SELF_DIRECTED", "telegram:user_b"),
        _ev("ATMOSPHERIC", None),
        _ev("ATMOSPHERIC", None),
        _ev("EXTERNAL_REPORT", "x.com"),
    ]
    diag = crisis_signal(events, GovernorConfig())
    assert diag.is_emergency is False
    assert "ambient" in diag.summary.lower() or "spike" in diag.summary.lower()


def test_emergency_summary_mentions_distinct_sources() -> None:
    events = [
        _ev("EXTERNAL_REPORT", "x.com/post/1"),
        _ev("EXTERNAL_REPORT", "rss/feed/2"),
        _ev("EXTERNAL_REPORT", "news.example/3"),
    ]
    diag = crisis_signal(events, GovernorConfig())
    assert diag.is_emergency is True
    assert "3" in diag.summary or "distinct" in diag.summary


def test_window_caps_at_config_size() -> None:
    """If 100 events are passed but window is 10, only the first 10
    are inspected (most-recent-first contract)."""
    events = [_ev("SELF_DIRECTED")] * 100
    cfg = GovernorConfig(crisis_window_events=10)
    diag = crisis_signal(events, cfg)
    assert diag.directed_count == 10
