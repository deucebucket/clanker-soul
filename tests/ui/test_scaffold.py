"""UI scaffold — app factory, landing page, health endpoint.

Skips cleanly if the ``[ui]`` extra isn't installed
(``pytest.importorskip``). Subsequent UI tests (events log, config
panel, simulator) live in sibling files.
"""

from __future__ import annotations

import pytest

# Skip the entire module unless [ui] extra is installed.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from tests.ui.asgi import asgi_client

from clanker_soul import Score, SoulPlugin


def _populated_db(tmp_path) -> str:
    """Build a soul.db with two agents that have some history,
    so the landing page has real content to render."""
    db = tmp_path / "ui.db"
    with SoulPlugin(agent_id="alice", db_path=db) as p:
        p.ingest(Score(v=200, w=210, patterns=("AFFIRMATION",)))
    with SoulPlugin(agent_id="bob", db_path=db) as p:
        p.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",), direction="SELF_DIRECTED"))
    return str(db)


# ---------------------------------------------------------------------------
# Module shape
# ---------------------------------------------------------------------------


async def test_ui_module_exposes_launch_and_create_app() -> None:
    from clanker_soul.ui import create_app, launch

    assert callable(create_app)
    assert callable(launch)


async def test_create_app_raises_when_db_missing(tmp_path) -> None:
    from clanker_soul.ui import create_app

    with pytest.raises(FileNotFoundError):
        create_app(tmp_path / "nope.db")


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


async def test_index_renders_with_no_agents(tmp_path) -> None:
    from clanker_soul.ui import create_app
    from clanker_soul.soul import SoulStore

    db = tmp_path / "empty.db"
    SoulStore(db)  # creates schema, no rows
    app = create_app(db)

    async with asgi_client(app) as client:
        res = await client.get("/")
    assert res.status_code == 200
    assert "no agents found" in res.text.lower()


async def test_index_lists_agents_from_populated_db(tmp_path) -> None:
    from clanker_soul.ui import create_app

    db = _populated_db(tmp_path)
    app = create_app(db)

    async with asgi_client(app) as client:
        res = await client.get("/")
    assert res.status_code == 200
    assert "alice" in res.text
    assert "bob" in res.text


async def test_index_honors_agent_id_query_param(tmp_path) -> None:
    from clanker_soul.ui import create_app

    db = _populated_db(tmp_path)
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/?agent_id=bob")
    assert res.status_code == 200
    # The selected option should reflect the query param. Tailwind
    # renders the agent id inside the form; we check for the
    # 'selected' attribute on bob's <option>.
    assert 'value="bob" selected' in res.text


async def test_index_honors_default_agent_id(tmp_path) -> None:
    from clanker_soul.ui import create_app

    db = _populated_db(tmp_path)
    app = create_app(db, default_agent_id="bob")
    async with asgi_client(app) as client:
        res = await client.get("/")
    assert 'value="bob" selected' in res.text


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health_returns_json(tmp_path) -> None:
    from clanker_soul import __version__
    from clanker_soul.ui import create_app

    db = _populated_db(tmp_path)
    app = create_app(db)
    async with asgi_client(app) as client:
        res = await client.get("/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["version"] == __version__
    assert payload["agent_count"] == 2


# ---------------------------------------------------------------------------
# CLI dispatcher actually finds the module now
# ---------------------------------------------------------------------------


async def test_cli_ui_subcommand_no_longer_emits_install_hint(
    tmp_path, monkeypatch, capsys
) -> None:
    """Once [ui] is installed, ``clanker-soul ui`` should NOT print
    the install-hint stub. Instead it tries to launch — we monkeypatch
    uvicorn.Server.run so the test doesn't actually bind a port."""
    import uvicorn
    from clanker_soul.__main__ import main

    db = _populated_db(tmp_path)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)

    rc = main(["ui", "--db", db])
    err = capsys.readouterr().err
    assert "[ui] not installed" not in err
    assert rc == 0
