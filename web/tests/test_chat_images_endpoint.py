"""Unit tests for POST /api/chat-images and GET /api/chat-images/{image_id}."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes.api import chat_images as chat_images_router


def _make_app(images_dir: Path) -> FastAPI:
    app = FastAPI()
    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        app.include_router(chat_images_router.router, prefix="/api")
    return app


@pytest.fixture
def images_dir(tmp_path: Path) -> Path:
    d = tmp_path / "chat-images"
    d.mkdir()
    return d


@pytest.fixture
def client(images_dir: Path) -> TestClient:
    app = _make_app(images_dir)
    return TestClient(app)


def _png_bytes() -> bytes:
    """Minimal valid-looking PNG header bytes."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def test_should_upload_valid_image_and_return_image_id(
    tmp_path: Path, images_dir: Path
) -> None:
    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    png = _png_bytes()
    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.post(
            "/api/chat-images",
            files={"file": ("test.png", io.BytesIO(png), "image/png")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "image_id" in data
    image_id = data["image_id"]
    assert (images_dir / image_id).exists()
    assert (images_dir / f"{image_id}.meta").exists()


def test_should_reject_oversized_file(tmp_path: Path, images_dir: Path) -> None:
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.post(
            "/api/chat-images",
            files={"file": ("big.png", io.BytesIO(oversized), "image/png")},
        )

    assert resp.status_code == 413
    assert "10 MB" in resp.json()["detail"]


def test_should_reject_non_image_mime_type(tmp_path: Path, images_dir: Path) -> None:
    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.post(
            "/api/chat-images",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert resp.status_code == 415
    assert "image" in resp.json()["detail"].lower()


def test_should_serve_uploaded_image(tmp_path: Path, images_dir: Path) -> None:
    import json

    image_id = "test-serve-uuid"
    png = _png_bytes()
    (images_dir / image_id).write_bytes(png)
    (images_dir / f"{image_id}.meta").write_text(json.dumps({"media_type": "image/png"}))

    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.get(f"/api/chat-images/{image_id}")

    assert resp.status_code == 200
    assert resp.content == png


def test_should_return_404_for_missing_image(tmp_path: Path, images_dir: Path) -> None:
    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.get("/api/chat-images/does-not-exist")

    assert resp.status_code == 404


def test_should_reject_path_traversal_in_image_id(tmp_path: Path, images_dir: Path) -> None:
    app = FastAPI()
    app.include_router(chat_images_router.router, prefix="/api")
    client = TestClient(app)

    with patch("web.routes.api.chat_images.get_chat_images_dir", return_value=images_dir):
        resp = client.get("/api/chat-images/../etc/passwd")

    assert resp.status_code in (400, 404)
