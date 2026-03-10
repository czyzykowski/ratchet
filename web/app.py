"""FastAPI application for Ratchet web UI."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from core.store import PostgresStore, Store
from web.routes import board as board_router
from web.routes import features as features_router
from web.routes import projects as projects_router
from web.routes import specs as specs_router
from web.routes import tasks as tasks_router
from web.templating import templates  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from core.db import close_pool, get_pool

    pool = await get_pool()
    app.state.pool = pool
    app.state.store = PostgresStore(pool)
    yield
    await close_pool()


app = FastAPI(title="Ratchet", lifespan=lifespan)
app.include_router(board_router.router)
app.include_router(features_router.router)
app.include_router(projects_router.router)
app.include_router(specs_router.router)
app.include_router(tasks_router.router)


def get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]
