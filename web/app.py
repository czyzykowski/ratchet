"""FastAPI application for Ratchet web UI."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
from web.templating import templates  # noqa: F401

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from core.db import close_pool, get_pool

    pool = await get_pool()
    app.state.pool = pool
    app.state.store = PostgresStore(pool)

    async def _refresh_loop() -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await app.state.store.refresh_views()
            except Exception:
                logger.warning("Periodic view refresh failed", exc_info=True)

    refresh_task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        await close_pool()


app = FastAPI(title="Ratchet", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret"),
)
app.include_router(blocked_router.router)
app.include_router(board_router.router)
app.include_router(executions_router.router)
app.include_router(features_router.router)
app.include_router(projects_router.router)
app.include_router(specs_router.router)
app.include_router(tasks_router.router)
app.include_router(worker_router.router)


def get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]
