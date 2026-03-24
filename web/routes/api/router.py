"""Top-level API router — aggregates all /api/* sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from web.routes.api import (
    architecture_sessions,
    board,
    executions,
    feature_sessions,
    features,
    project_chat_sessions,
    projects,
    specs,
    tasks,
    worker,
    worker_logs,
    workers,
)

api_router = APIRouter(prefix="/api", tags=["api"])
api_router.include_router(board.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(worker.router)
api_router.include_router(worker_logs.router)
api_router.include_router(workers.router)
api_router.include_router(features.router)
api_router.include_router(executions.router)
api_router.include_router(feature_sessions.router)
api_router.include_router(project_chat_sessions.router)
api_router.include_router(architecture_sessions.router)
api_router.include_router(specs.router)
