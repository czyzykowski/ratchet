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
from orchestrator.dispatcher import dispatch_loop
from orchestrator.recovery import RecoveryManager
from orchestrator.registry import WorkerRegistry
from web.local_worker import LocalWorkerManager, LocalWorkerSettings
from web.routes import blocked as blocked_router
from web.routes import board as board_router
from web.routes import worker as worker_router
from web.routes.api import architecture_sessions as architecture_sessions_router
from web.routes.api import chat_images as chat_images_router
from web.routes.api import events as api_events_router
from web.routes.api import feature_sessions as feature_sessions_router
from web.routes.api import project_chat_sessions as project_chat_sessions_router
from web.routes.api import spec_sessions as spec_sessions_router
from web.routes.api.router import api_router
from web.routes.api.ws_worker import router as ws_worker_router
from web.templating import templates  # noqa: F401
from worker.log_buffer import LogBuffer

logger = logging.getLogger(__name__)


def _local_worker_settings_from_env() -> LocalWorkerSettings:
    """Read local worker configuration from environment variables."""
    capabilities_raw = os.environ.get("WORKER_CAPABILITIES", "")
    capabilities = [c.strip() for c in capabilities_raw.split(",") if c.strip()]
    enabled = os.environ.get("WORKER_ENABLED", "true").lower() in ("true", "1", "yes")
    port = int(os.environ.get("WEB_PORT", "8000"))
    return LocalWorkerSettings(
        enabled=enabled,
        capabilities=capabilities,
        port=port,
    )


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
    store = PostgresStore(pool)
    app.state.store = store

    from core.managers import Managers
    app.state.managers = Managers(store)
    sse_queues: set[asyncio.Queue[str]] = set()
    app.state.sse_queues = sse_queues
    app.state.sse_clients = []
    app.state.spec_sessions = {}
    app.state.feature_sessions = {}
    app.state.project_chat_sessions = {}
    app.state.architecture_sessions = {}

    # Worker registry shared between WebSocket endpoint and dispatch loop
    registry = WorkerRegistry()
    app.state.registry = registry

    # Local worker subprocess
    log_buffer = LogBuffer()
    local_worker = LocalWorkerManager(
        store=store,
        settings=_local_worker_settings_from_env(),
        log_buffer=log_buffer,
    )
    app.state.local_worker = local_worker
    # Backward-compat alias used by existing worker API routes
    app.state.worker_service = local_worker
    app.state.worker_log_buffer = log_buffer
    await local_worker.start()

    # Crash recovery — identify orphaned in-progress tasks and wait for reconnects
    recovery_manager = RecoveryManager(
        store,
        registry,
        grace_period=float(os.environ.get("RECOVERY_GRACE_PERIOD_SECONDS", "60")),
    )
    recovery_actions = await recovery_manager.recover_in_progress_tasks(pool)
    if recovery_actions:
        asyncio.create_task(recovery_manager.wait_and_resolve(recovery_actions))

    # Dispatch loop — routes ready tasks to connected workers
    dispatch_enabled = os.environ.get("DISPATCH_ENABLED", "true").lower() in (
        "true", "1", "yes",
    )
    # Create a notification queue for the dispatch loop — fed by _listen_task_events
    dispatch_notify_queue: asyncio.Queue[str] = asyncio.Queue()
    sse_queues.add(dispatch_notify_queue)

    dispatch_task: asyncio.Task[None] | None = None
    if dispatch_enabled:
        dispatch_task = asyncio.create_task(
            dispatch_loop(store, registry, notification_queue=dispatch_notify_queue)
        )

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
        if dispatch_task is not None:
            dispatch_task.cancel()
        for task in (refresh_task, listen_task, dispatch_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for session in list(app.state.spec_sessions.values()):
            await session.close()
        app.state.spec_sessions.clear()
        for session in list(app.state.feature_sessions.values()):
            await session.close()
        app.state.feature_sessions.clear()
        for session in list(app.state.project_chat_sessions.values()):
            await session.close()
        app.state.project_chat_sessions.clear()
        for session in list(app.state.architecture_sessions.values()):
            await session.close()
        app.state.architecture_sessions.clear()
        await local_worker.stop(graceful=True)
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
app.include_router(worker_router.router)
app.include_router(api_router)
app.include_router(ws_worker_router)
app.include_router(api_events_router.router)
app.include_router(spec_sessions_router.router, prefix="/api")
app.include_router(feature_sessions_router.router, prefix="/api")
app.include_router(project_chat_sessions_router.router, prefix="/api")
app.include_router(architecture_sessions_router.router, prefix="/api")
app.include_router(chat_images_router.router, prefix="/api")


_SPA_DIST = os.path.join(os.path.dirname(__file__), "spa", "dist")
if os.path.isdir(os.path.join(_SPA_DIST, "assets")):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_SPA_DIST, "assets")),
        name="spa-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        return FileResponse(os.path.join(_SPA_DIST, "index.html"))

_UI_DIST = os.path.join(os.path.dirname(__file__), "..", "ui", "dist")
if os.path.isdir(_UI_DIST) and not os.path.isdir(os.path.join(_SPA_DIST, "assets")):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_UI_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def ui_fallback(full_path: str) -> FileResponse:
        return FileResponse(os.path.join(_UI_DIST, "index.html"))


def get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]
