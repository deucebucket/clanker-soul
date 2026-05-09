"""FastAPI app factory for the clanker-soul dashboard.

The factory pattern (``create_app(db_path) -> FastAPI``) keeps the
app constructable without spinning up uvicorn — handy for tests and
for hosts that want to mount this as a sub-app under their own
ASGI server.

This module is only loaded when the ``[ui]`` extra is installed.
The CLI (``clanker_soul/__main__.py``) does ``try: from
clanker_soul.ui import launch`` and falls through to a stub when the
extra isn't present.

Routes added in this scaffold PR:
  - ``GET /``       — landing page (db file, agents, version)
  - ``GET /health`` — JSON liveness probe (for tests + ops)

Subsequent PRs (#26-#29) will add ``/`` (live panel),
``/events``, ``/config``, ``/simulate``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from clanker_soul import __version__
from clanker_soul.soul import SoulStore

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _list_agents(store: SoulStore) -> list[str]:
    """Union of agent ids found in any v0.2 table. Some agents only
    appear in events (never saved soul), some only in soul_state."""
    with store.lock:
        rows = store.connection.execute(
            "SELECT agent_id FROM soul_state "
            "UNION SELECT agent_id FROM events "
            "UNION SELECT agent_id FROM pulse_log "
            "UNION SELECT agent_id FROM config_overrides"
        ).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def create_app(
    db_path: Path | str,
    *,
    default_agent_id: str | None = None,
) -> FastAPI:
    """Build the dashboard FastAPI app for the soul.db at ``db_path``.

    ``default_agent_id`` — if multiple agents exist in the DB, pick
    this one as the default selection on the landing page. None
    means "first agent alphabetically."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"soul.db not found at {db_path}")

    store = SoulStore.get(db_path)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app = FastAPI(
        title="clanker-soul dashboard",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, agent_id: str | None = None) -> HTMLResponse:
        agents = _list_agents(store)
        selected = agent_id or default_agent_id
        if selected is None and agents:
            selected = agents[0]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "db_path": str(db_path),
                "version": __version__,
                "agents": agents,
                "selected_agent": selected,
            },
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "version": __version__,
            "db_path": str(db_path),
            "agent_count": len(_list_agents(store)),
        })

    return app


__all__ = ["create_app"]
