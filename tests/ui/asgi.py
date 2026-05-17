"""ASGI test client helper for FastAPI routes.

Starlette's synchronous ``TestClient`` can hang under newer Python/anyio
combinations because it drives the app through a blocking portal thread.
Using HTTPX's async ASGI transport exercises the same routes without that
thread bridge.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx


@asynccontextmanager
async def asgi_client(app) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ANN001
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
