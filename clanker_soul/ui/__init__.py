"""``clanker_soul.ui`` — the dashboard subpackage.

Optional, gated behind the ``[ui]`` extra. Install with:

    pip install 'clanker-soul[ui]'

Top-level entry point is :py:func:`launch`, which the CLI's
``clanker-soul ui`` subcommand calls. Hosts that want to mount the
app under their own ASGI server can use :py:func:`create_app`
directly.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from clanker_soul.ui.app import create_app


def launch(
    db_path: Path | str,
    *,
    agent_id: str | None = None,
    port: int = 7900,
    host: str = "127.0.0.1",
    log_level: str = "info",
) -> int:
    """Build the dashboard app and run it under uvicorn.

    Returns the uvicorn exit code (0 on graceful shutdown). Blocks
    until the server stops. Hosts that want non-blocking embedding
    should call :py:func:`create_app` instead and run their own
    uvicorn / hypercorn / etc.

    ``host``: defaults to localhost so the dashboard isn't accidentally
    exposed to the network. Set to ``"0.0.0.0"`` to listen on all
    interfaces (typical for containerized deployments)."""
    app = create_app(db_path, default_agent_id=agent_id)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    server.run()
    return 0


__all__ = ["launch", "create_app"]
