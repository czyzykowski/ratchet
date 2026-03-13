"""API: image upload and serve endpoints for design chats."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from core.claude_repl import get_chat_images_dir

_MAX_SIZE = 10 * 1024 * 1024  # 10 MB

router = APIRouter(prefix="/chat-images")


@router.post("")
async def upload_image(file: UploadFile) -> JSONResponse:
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Only image/* MIME types are accepted")

    data = await file.read()
    if len(data) > _MAX_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")

    from uuid import uuid4

    image_id = str(uuid4())
    images_dir = get_chat_images_dir()

    dest = images_dir / image_id
    dest.write_bytes(data)

    meta_dest = images_dir / f"{image_id}.meta"
    meta_dest.write_text(json.dumps({"media_type": content_type}))

    return JSONResponse({"image_id": image_id})


@router.get("/{image_id}")
async def serve_image(image_id: str) -> FileResponse:
    if "/" in image_id or "\\" in image_id or ".." in image_id:
        raise HTTPException(status_code=400, detail="Invalid image_id")

    images_dir = get_chat_images_dir()
    path = images_dir / image_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    media_type: str | None = None
    meta_path = images_dir / f"{image_id}.meta"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        media_type = meta.get("media_type")

    return FileResponse(str(path), media_type=media_type)


def get_image_media_type(image_id: str) -> str | None:
    """Look up the stored media type for an image_id."""
    meta_path = get_chat_images_dir() / f"{image_id}.meta"
    if not meta_path.exists():
        return None
    meta: dict[str, str] = json.loads(meta_path.read_text())
    return meta.get("media_type")
