"""FastAPI application for Ratchet web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.store import PostgresStore, Store
from web.routes import blocked as blocked_router
from web.routes import board as board_router
from web.routes import executions as executions_router
from web.routes import features as features_router
from web.routes import projects as projects_router
from web.routes import specs as specs_router
from web.routes import tasks as tasks_router
from web.routes import worker as worker_router
from web.routes.api import events as api_events_router
from web.routes.api.router import api_router
from web.templating import templates  # noqa: F401

logger = logging.getLogger(__name__)


async def _listen_task_events(queues: set[asyncio.Queue[str]]) -> None:
    database_url = os.environ["DATABASE_URL"]
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        await conn.execute("LISTEN task_events")
        async for notify in conn.notifies():
            payload = json.dumps({"task_id": notify.payload})
            for q in set(queues):
                q.put_nowait(payload)
    except asyncio.CancelledError:
        pass
    finally:
        await conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from core.db import close_pool, get_pool

    pool = await get_pool()
    app.state.pool = pool
    app.state.store = PostgresStore(pool)
    sse_queues: set[asyncio.Queue[str]] = set()
    app.state.sse_queues = sse_queues
    app.state.sse_clients = []

    async def _refresh_loop() -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await app.state.store.refresh_views()
            except Exception:
                logger.warning("Periodic view refresh failed", exc_info=True)

    refresh_task = asyncio.create_task(_refresh_loop())
    listen_task = asyncio.create_task(_listen_task_events(sse_queues))
    try:
        yield
    finally:
        refresh_task.cancel()
        listen_task.cancel()
        for task in (refresh_task, listen_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await close_pool()


app = FastAPI(title="Ratchet", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret"),
)
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="web_static",
)
app.include_router(blocked_router.router)
app.include_router(board_router.router)
app.include_router(executions_router.router)
app.include_router(features_router.router)
app.include_router(projects_router.router)
app.include_router(specs_router.router)
app.include_router(tasks_router.router)
app.include_router(worker_router.router)
app.include_router(api_router)
app.include_router(api_events_router.router)


_SPA_DIST = os.path.join(os.path.dirname(__file__), "spa", "dist")
if os.path.isdir(os.path.join(_SPA_DIST, "assets")):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_SPA_DIST, "assets")),
        name="spa-assets",
    )

_UI_DIST = os.path.join(os.path.dirname(__file__), "..", "ui", "dist")
if os.path.isdir(_UI_DIST) and not os.path.isdir(os.path.join(_SPA_DIST, "assets")):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_UI_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        return FileResponse(os.path.join(_UI_DIST, "index.html"))


def get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]
