"""Board route: GET / — serves React SPA."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from web.board_builder import load_board  # noqa: F401 — kept for use by /api/board

router = APIRouter()


@router.get("/", response_class=FileResponse)
async def board(request: Request) -> FileResponse:
    routes_dir = os.path.dirname(__file__)
    spa_index = os.path.join(os.path.dirname(routes_dir), "spa", "dist", "index.html")
    return FileResponse(spa_index)
