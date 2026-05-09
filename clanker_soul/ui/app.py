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

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from clanker_soul import __version__
from clanker_soul.governor import GovernorConfig
from clanker_soul.overrides import ConfigOverrides
from clanker_soul.presets import ALL as PRESETS
from clanker_soul.soul import SoulStore
from clanker_soul.ui.config import (
    apply_field_override,
    build_config_view,
    clear_field_override,
)
from clanker_soul.ui.events import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    SORT_OPTIONS,
    parse_iso_date,
    query_events,
)
from clanker_soul.ui.live import build_live_view
from clanker_soul.ui.simulator import (
    parse_config as parse_sim_config,
    parse_soul as parse_sim_soul,
    replay_events,
)

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

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(
        request: Request,
        agent_id: str | None = None,
        partial: int = 0,
    ) -> HTMLResponse:
        agents, selected = _resolve_agent(agent_id)
        overrides = ConfigOverrides(store)
        view = build_config_view(overrides, selected) if selected else None
        ctx = {
            "db_path": str(db_path),
            "version": __version__,
            "agents": agents,
            "selected_agent": selected,
            "view": view,
            "presets": PRESETS,
        }
        template = "_config_panel.html" if partial else "config.html"
        return templates.TemplateResponse(request, template, ctx)

    @app.post("/config/override", response_class=HTMLResponse)
    async def config_override(
        request: Request,
        agent_id: str = Form(...),
        section: str = Form(...),
        field: str = Form(...),
        value: str = Form(...),
    ) -> HTMLResponse:
        overrides = ConfigOverrides(store)
        try:
            apply_field_override(overrides, agent_id, section, field, value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        view = build_config_view(overrides, agent_id)
        return templates.TemplateResponse(
            request, "_config_panel.html",
            {"selected_agent": agent_id, "view": view, "presets": PRESETS},
        )

    @app.post("/config/clear", response_class=HTMLResponse)
    async def config_clear(
        request: Request,
        agent_id: str = Form(...),
        section: str | None = Form(None),
        field: str | None = Form(None),
    ) -> HTMLResponse:
        overrides = ConfigOverrides(store)
        if section and field:
            try:
                clear_field_override(overrides, agent_id, section, field)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            overrides.set(agent_id, physics={}, soul={})
        view = build_config_view(overrides, agent_id)
        return templates.TemplateResponse(
            request, "_config_panel.html",
            {"selected_agent": agent_id, "view": view, "presets": PRESETS},
        )

    @app.post("/config/preset", response_class=HTMLResponse)
    async def config_preset(
        request: Request,
        agent_id: str = Form(...),
        preset: str = Form(...),
    ) -> HTMLResponse:
        if preset not in PRESETS:
            raise HTTPException(status_code=400, detail=f"unknown preset: {preset}")
        overrides = ConfigOverrides(store)
        PRESETS[preset].apply(overrides, agent_id)
        view = build_config_view(overrides, agent_id)
        return templates.TemplateResponse(
            request, "_config_panel.html",
            {"selected_agent": agent_id, "view": view, "presets": PRESETS},
        )

    @app.get("/simulate", response_class=HTMLResponse)
    async def simulate_page(
        request: Request,
        agent_id: str | None = None,
    ) -> HTMLResponse:
        """Render the simulator form. Result fragment is fetched via
        POST /simulate/run."""
        agents, selected = _resolve_agent(agent_id)
        # Pre-populate form fields with the agent's *current* live config
        # so operators tweak from where they are, not from defaults.
        from clanker_soul.ui.config import PHYSICS_FIELDS, SOUL_FIELDS
        prefill_soul = {f.name: 128 for f in SOUL_FIELDS}
        prefill_physics = {}
        if selected:
            from clanker_soul.physics import PhysicsConfig
            from clanker_soul.soul import SoulState
            base_soul = SoulState()
            base_phys = PhysicsConfig()
            bundle = ConfigOverrides(store).get(selected)
            for f in SOUL_FIELDS:
                prefill_soul[f.name] = int(
                    bundle.soul.get(f.name, getattr(base_soul, f.name))
                )
            for f in PHYSICS_FIELDS:
                prefill_physics[f.name] = float(
                    bundle.physics.get(f.name, getattr(base_phys, f.name))
                )
        ctx = {
            "db_path": str(db_path),
            "version": __version__,
            "agents": agents,
            "selected_agent": selected,
            "physics_fields": PHYSICS_FIELDS,
            "soul_fields": SOUL_FIELDS,
            "prefill_soul": prefill_soul,
            "prefill_physics": prefill_physics,
            "default_n_events": 100,
        }
        return templates.TemplateResponse(request, "simulate.html", ctx)

    @app.post("/simulate/run", response_class=HTMLResponse)
    async def simulate_run(request: Request) -> HTMLResponse:
        """Run the replay and render the result fragment. POST so big
        forms don't end up in URLs and so form data parses cleanly."""
        form = await request.form()
        form_dict = {k: str(v) for k, v in form.items()}
        agent_id = form_dict.get("agent_id", "").strip()
        if not agent_id:
            raise HTTPException(status_code=400, detail="agent_id is required")
        try:
            n_events = int(form_dict.get("n_events", "100"))
        except ValueError:
            raise HTTPException(status_code=400, detail="n_events must be an integer")
        n_events = max(1, min(1000, n_events))

        try:
            sim_soul = parse_sim_soul(form_dict)
            sim_config = parse_sim_config(form_dict)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        from clanker_soul.eventlog import SqliteEventLog
        log = SqliteEventLog(store)
        records_desc = log.read_ingest(agent_id, limit=n_events)
        # read_ingest returns newest-first; replay needs oldest-first.
        records = list(reversed(records_desc))

        result = replay_events(
            records, sim_soul, sim_config, agent_id=agent_id,
        )
        return templates.TemplateResponse(
            request, "_simulate_result.html",
            {
                "result": result,
                "selected_agent": agent_id,
                "no_events": len(records) == 0,
            },
        )

    @app.post("/simulate/apply")
    async def simulate_apply(
        request: Request,
        agent_id: str = Form(...),
    ) -> RedirectResponse:
        """Take the simulator's submitted soul + physics fields and write
        them as overrides on the live agent. Explicit operator action —
        the simulator never auto-applies."""
        form = await request.form()
        form_dict = {k: str(v) for k, v in form.items()}
        try:
            sim_soul = parse_sim_soul(form_dict)
            sim_config = parse_sim_config(form_dict)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        from clanker_soul.physics import PhysicsConfig
        from clanker_soul.soul import SoulState
        from clanker_soul.ui.config import PHYSICS_FIELDS
        base_phys = PhysicsConfig()
        base_soul = SoulState()
        # Only persist fields that *differ from defaults* — otherwise we
        # pollute the override bundle with no-op rows.
        physics_overrides = {
            m.name: getattr(sim_config, m.name)
            for m in PHYSICS_FIELDS
            if getattr(sim_config, m.name) != getattr(base_phys, m.name)
        }
        soul_overrides = {
            f: getattr(sim_soul, f)
            for f in ("v", "a", "d", "u", "g", "w", "i")
            if getattr(sim_soul, f) != getattr(base_soul, f)
        }
        overrides = ConfigOverrides(store)
        overrides.set(agent_id,
                      physics=physics_overrides, soul=soul_overrides)
        # Send the operator to /config so they can see what landed.
        return RedirectResponse(
            url=f"/config?agent_id={agent_id}", status_code=303,
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
