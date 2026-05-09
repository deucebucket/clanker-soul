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
from clanker_soul.governor import GovernorConfig
from clanker_soul.soul import SoulStore
from clanker_soul.ui.events import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    SORT_OPTIONS,
    parse_iso_date,
    query_events,
)
from clanker_soul.ui.live import build_live_view

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
    governor_config: GovernorConfig | None = None,
) -> FastAPI:
    """Build the dashboard FastAPI app for the soul.db at ``db_path``.

    ``default_agent_id`` — if multiple agents exist in the DB, pick
    this one as the default selection on the landing page. None
    means "first agent alphabetically."
    ``governor_config`` — capability/crisis thresholds. Defaults to
    standard."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"soul.db not found at {db_path}")

    store = SoulStore.get(db_path)
    cfg = governor_config or GovernorConfig()
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app = FastAPI(
        title="clanker-soul dashboard",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    def _resolve_agent(agent_id: str | None) -> tuple[list[str], str | None]:
        agents = _list_agents(store)
        selected = agent_id or default_agent_id
        if selected is None and agents:
            selected = agents[0]
        return agents, selected

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, agent_id: str | None = None) -> HTMLResponse:
        agents, selected = _resolve_agent(agent_id)
        view = build_live_view(store, selected, governor_config=cfg) if selected else None
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "db_path": str(db_path),
                "version": __version__,
                "agents": agents,
                "selected_agent": selected,
                "view": view,
            },
        )

    @app.get("/snapshot", response_class=HTMLResponse)
    async def snapshot(request: Request, agent_id: str) -> HTMLResponse:
        """HTML fragment for HTMX polling. Returns just the live-panel
        body so HTMX can swap it into the page without a full reload."""
        view = build_live_view(store, agent_id, governor_config=cfg)
        return templates.TemplateResponse(
            request, "_live_panel.html",
            {"selected_agent": agent_id, "view": view},
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_page(
        request: Request,
        agent_id: str | None = None,
        sort: str = DEFAULT_SORT,
        classification: str | None = None,
        breach: str | None = None,
        q: str | None = None,
        after: str | None = None,
        before: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        partial: int = 0,
    ) -> HTMLResponse:
        """Forensic event log. ``partial=1`` returns only the table
        fragment for HTMX swaps; default returns the full page."""
        agents, selected = _resolve_agent(agent_id)
        result = None
        if selected:
            result = query_events(
                store, selected,
                sort=sort,
                classification=classification or None,
                breach=breach or None,
                pattern_q=q or None,
                ts_after=parse_iso_date(after),
                ts_before=parse_iso_date(before),
                page=page,
                page_size=page_size,
            )
        ctx = {
            "db_path": str(db_path),
            "version": __version__,
            "agents": agents,
            "selected_agent": selected,
            "result": result,
            "filters": {
                "sort": sort, "classification": classification or "",
                "breach": breach or "", "q": q or "",
                "after": after or "", "before": before or "",
            },
            "sort_options": SORT_OPTIONS,
            "page_size": page_size,
        }
        template = "_events_table.html" if partial else "events.html"
        return templates.TemplateResponse(request, template, ctx)

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
