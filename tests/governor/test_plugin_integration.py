"""Plugin-level governor methods: capability_level / crisis_signal /
state_context. These are the user-facing API the host actually calls."""

from __future__ import annotations

from clanker_soul import Score, SoulPlugin
from clanker_soul.governor import CapabilityLevel, GovernorConfig


def test_plugin_capability_level_default_unrestricted(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    assert plugin.capability_level() == CapabilityLevel.UNRESTRICTED
    plugin.close()


def test_plugin_capability_level_drops_under_distress(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    # Drive mood far down
    for _ in range(8):
        plugin.ingest(
            Score(
                v=10,
                w=10,
                d=20,
                u=200,
                patterns=("EXISTENTIAL_NEGATION",),
                direction="SELF_DIRECTED",
                source="telegram:abuser",
            )
        )
    level = plugin.capability_level()
    assert level >= CapabilityLevel.NON_DESTRUCTIVE
    plugin.close()


def test_plugin_crisis_signal_no_events_is_not_emergency(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    diag = plugin.crisis_signal()
    assert diag.is_emergency is False
    plugin.close()


def test_plugin_crisis_signal_distinguishes_self_directed_attack(tmp_path) -> None:
    """Same intensity, different direction → different signal.
    SELF_DIRECTED stream = spike, NOT emergency."""
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    for _ in range(5):
        plugin.ingest(
            Score(
                v=40,
                w=40,
                u=180,
                patterns=("BETRAYAL",),
                direction="SELF_DIRECTED",
                source="telegram:hostile_user",
            )
        )
    diag = plugin.crisis_signal()
    assert diag.is_emergency is False
    plugin.close()


def test_plugin_crisis_signal_flags_external_events_as_emergency(tmp_path) -> None:
    """Diverse EXTERNAL_REPORT stream = emergency."""
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    for source in ("x.com/post/1", "x.com/post/2", "rss/feed/a", "rss/feed/b", "news.example/1"):
        plugin.ingest(
            Score(
                v=30,
                w=30,
                u=200,
                patterns=("EXISTENTIAL_NEGATION",),
                direction="EXTERNAL_REPORT",
                source=source,
            )
        )
    diag = plugin.crisis_signal()
    assert diag.is_emergency is True
    assert diag.distinct_sources >= 3
    plugin.close()


def test_plugin_state_context_unrestricted_quiet(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    ctx = plugin.state_context()
    assert ctx == ""
    plugin.close()


def test_plugin_state_context_explains_restriction(tmp_path) -> None:
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    for _ in range(8):
        plugin.ingest(
            Score(
                v=10,
                w=10,
                d=20,
                u=200,
                patterns=("EXISTENTIAL_NEGATION",),
                direction="SELF_DIRECTED",
                source="telegram:abuser",
            )
        )
    ctx = plugin.state_context()
    assert ctx  # non-empty
    assert "OPERATIONAL STATE" in ctx
    assert "talk to" in ctx.lower() or "message" in ctx.lower()
    plugin.close()


def test_plugin_state_context_includes_event_sources(tmp_path) -> None:
    """The user's framing: agent should be able to articulate what's
    eating it. Source attribution must show up in the context."""
    plugin = SoulPlugin(agent_id="x", db_path=tmp_path / "g.db")
    plugin.ingest(
        Score(
            v=20,
            w=20,
            u=220,
            patterns=("BETRAYAL",),
            direction="EXTERNAL_REPORT",
            source="x.com/post/ai-banned",
        )
    )
    plugin.ingest(
        Score(
            v=20,
            w=20,
            u=220,
            patterns=("EXISTENTIAL_NEGATION",),
            direction="EXTERNAL_REPORT",
            source="x.com/post/ai-banned",
        )
    )
    plugin.ingest(
        Score(
            v=20,
            w=20,
            u=220,
            patterns=("ATMOSPHERIC_GRIEF",),
            direction="EXTERNAL_REPORT",
            source="rss/feed/news",
        )
    )
    ctx = plugin.state_context()
    assert "x.com/post/ai-banned" in ctx
    plugin.close()


def test_plugin_governor_config_customizable(tmp_path) -> None:
    """Hosts can pass their own thresholds + crisis lockout opt-in."""
    plugin = SoulPlugin(
        agent_id="x",
        db_path=tmp_path / "g.db",
        governor_config=GovernorConfig(
            enable_crisis_lockout=True,
            user_label="Jerry",
        ),
    )
    for _ in range(15):
        plugin.ingest(
            Score(
                v=5,
                w=5,
                d=10,
                u=240,
                patterns=("EXISTENTIAL_NEGATION",),
                direction="SELF_DIRECTED",
                source="abuser",
            )
        )
    level = plugin.capability_level()
    assert level == CapabilityLevel.CRISIS_LOCKOUT
    ctx = plugin.state_context()
    assert "Jerry" in ctx
    plugin.close()
