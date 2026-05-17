"""Live panel — radar geometry, snapshot fragment, governor badges."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from tests.ui.asgi import asgi_client

from clanker_soul import (
    BRITTLE,
    GovernorConfig,
    Score,
    SoulPlugin,
    SoulStore,
)
from clanker_soul.ui.app import create_app
from clanker_soul.ui.live import build_live_view


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populated_db(tmp_path, agent_id: str = "alice", events=None) -> str:
    db = tmp_path / "live.db"
    with SoulPlugin(agent_id=agent_id, db_path=db) as p:
        for s in events or [Score(v=200, w=210, patterns=("AFFIRMATION",))]:
            p.ingest(s)
    return str(db)


def _wounded_db(tmp_path) -> str:
    db = tmp_path / "wounded.db"
    with SoulPlugin(
        agent_id="hurt",
        db_path=db,
        default_soul=BRITTLE.soul,
    ) as p:
        BRITTLE.apply(p.overrides, "hurt")
        p.tick()
        for _ in range(8):
            p.ingest(
                Score(
                    v=10,
                    w=10,
                    d=20,
                    u=200,
                    patterns=("EXISTENTIAL_NEGATION",),
                    direction="SELF_DIRECTED",
                    source="abuser",
                )
            )
    return str(db)


# ---------------------------------------------------------------------------
# build_live_view
# ---------------------------------------------------------------------------


async def test_view_has_state_false_for_unknown_agent(tmp_path) -> None:
    db = tmp_path / "v.db"
    SoulStore(db)
    view = build_live_view(SoulStore.get(db), "nobody")
    assert view.has_state is False
    assert view.mood is None


async def test_view_includes_mood_after_ingest(tmp_path) -> None:
    db = _populated_db(tmp_path, "alice")
    view = build_live_view(SoulStore.get(db), "alice")
    assert view.has_state is True
    assert view.mood is not None
    assert len(view.mood) == 7


async def test_view_radar_polygon_has_seven_points(tmp_path) -> None:
    db = _populated_db(tmp_path, "alice")
    view = build_live_view(SoulStore.get(db), "alice")
    assert view.radar_soul is not None
    assert len(view.radar_soul.points) == 7
    assert view.radar_mood is not None
    assert len(view.radar_mood.points) == 7
    # points_attr is the SVG-ready string
    assert view.radar_soul.points_attr.count(",") == 7


async def test_view_capability_level_drops_under_distress(tmp_path) -> None:
    from clanker_soul import CapabilityLevel

    db = _wounded_db(tmp_path)
    view = build_live_view(SoulStore.get(db), "hurt")
    assert view.capability_level >= CapabilityLevel.NON_DESTRUCTIVE


async def test_view_state_context_populated_under_distress(tmp_path) -> None:
    db = _wounded_db(tmp_path)
    view = build_live_view(SoulStore.get(db), "hurt")
    assert view.state_context  # non-empty
    assert "OPERATIONAL STATE" in view.state_context


async def test_view_recent_events_have_source_attribution(tmp_path) -> None:
    db = _wounded_db(tmp_path)
    view = build_live_view(SoulStore.get(db), "hurt")
    assert view.recent_events
    sources = {ev.raw.source for ev in view.recent_events}
    assert "abuser" in sources


async def test_view_trauma_by_pattern_capped_at_top_n(tmp_path) -> None:
    """The top-N filter trims to 10; we only generate one pattern here."""
    db = _wounded_db(tmp_path)
    view = build_live_view(SoulStore.get(db), "hurt")
    assert len(view.trauma_by_pattern) <= 10
    assert any(p[0] == "EXISTENTIAL_NEGATION" for p in view.trauma_by_pattern)


# ---------------------------------------------------------------------------
# /snapshot fragment
# ---------------------------------------------------------------------------


async def test_snapshot_fragment_renders(tmp_path) -> None:
    db = _populated_db(tmp_path, "alice")
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/snapshot?agent_id=alice")
    assert res.status_code == 200
    # Should NOT include the page chrome — fragment only.
    assert "<html" not in res.text.lower()
    # Should include radar SVG and badges.
    assert "<svg" in res.text
    assert "vadugwi" in res.text.lower() or "VADUGWI" in res.text


async def test_snapshot_fragment_shows_capability_badge(tmp_path) -> None:
    db = _wounded_db(tmp_path)
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/snapshot?agent_id=hurt")
    assert res.status_code == 200
    assert "capability" in res.text.lower()


async def test_snapshot_fragment_shows_emergency_when_external(tmp_path) -> None:
    """Diverse EXTERNAL_REPORT events → crisis_signal flags emergency
    → fragment shows the emergency badge."""
    db = tmp_path / "crisis.db"
    with SoulPlugin(agent_id="agent", db_path=db) as p:
        for src in ("x.com/1", "x.com/2", "rss/3", "rss/4", "news/5"):
            p.ingest(
                Score(
                    v=20,
                    w=20,
                    u=220,
                    patterns=("EXISTENTIAL_NEGATION",),
                    direction="EXTERNAL_REPORT",
                    source=src,
                )
            )
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/snapshot?agent_id=agent")
    assert "emergency" in res.text.lower()


async def test_snapshot_fragment_shows_no_state_when_empty(tmp_path) -> None:
    db = tmp_path / "empty.db"
    SoulStore(db)
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/snapshot?agent_id=ghost")
    assert res.status_code == 200
    assert "no state yet" in res.text.lower()


async def test_index_includes_initial_snapshot_inline(tmp_path) -> None:
    """Initial page load embeds the snapshot — no flash of empty content
    while HTMX warms up. Subsequent updates come via /snapshot polling."""
    db = _populated_db(tmp_path, "alice")
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/")
    # Full page chrome
    assert "<html" in res.text.lower()
    # Plus the panel content
    assert "<svg" in res.text


async def test_index_wires_htmx_polling(tmp_path) -> None:
    db = _populated_db(tmp_path, "alice")
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/")
    assert 'hx-get="/snapshot?agent_id=alice"' in res.text
    assert 'hx-trigger="every 2s"' in res.text


async def test_custom_governor_config_propagates(tmp_path) -> None:
    """Passing a stricter config to create_app changes which level
    the dashboard reports for the same DB state."""
    db = _populated_db(tmp_path, "alice")
    # Default config + healthy mood = unrestricted.
    app_default = create_app(db)
    async with asgi_client(app_default) as client:
        res_default = await client.get("/snapshot?agent_id=alice")
    assert "unrestricted" in res_default.text.lower()

    # Absurdly strict config that always trips level 1 (W floor above max).
    strict = GovernorConfig(level1_w_floor=300, level1_v_floor=300)
    app_strict = create_app(db, governor_config=strict)
    async with asgi_client(app_strict) as client:
        res_strict = await client.get("/snapshot?agent_id=alice")
    assert "non_destructive" in res_strict.text.lower()
