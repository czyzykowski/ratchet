"""Unit tests for GET /api/executions/{execution_id}."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.router import api_router


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_execution(
    store: InMemoryStore, execution_id: uuid4, task_id: uuid4, spec_id: uuid4
) -> None:
    payload = {
        "execution_id": str(execution_id),
        "task_id": str(task_id),
        "spec_id": str(spec_id),
        "branch_name": f"execution/{execution_id}",
        "status": "running",
    }
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_STARTED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=execution_id,
        aggregate_type="execution",
        event_type=ev.EXECUTION_COMPLETED,
        payload={"execution_id": str(execution_id), "status": "completed"},
    )


def test_should_return_execution_detail_with_trace(
    client: TestClient, store: InMemoryStore
) -> None:
    execution_id = uuid4()
    task_id = uuid4()
    spec_id = uuid4()
    asyncio.get_event_loop().run_until_complete(
        _seed_execution(store, execution_id, task_id, spec_id)
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        trace_path = Path(tmp_dir) / f"{execution_id}.md"
        trace_path.write_text("# Trace content")

        with patch("web.routes.api.executions.get_traces_dir", return_value=tmp_dir):
            response = client.get(f"/api/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["execution"]["id"] == str(execution_id)
    assert data["execution"]["status"] == "completed"
    assert data["trace"] == "# Trace content"


def test_should_return_execution_detail_with_null_trace_when_trace_file_missing(
    client: TestClient, store: InMemoryStore
) -> None:
    execution_id = uuid4()
    task_id = uuid4()
    spec_id = uuid4()
    asyncio.get_event_loop().run_until_complete(
        _seed_execution(store, execution_id, task_id, spec_id)
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("web.routes.api.executions.get_traces_dir", return_value=tmp_dir):
            response = client.get(f"/api/executions/{execution_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["execution"]["status"] == "completed"
    assert data["trace"] is None


def test_should_return_404_when_execution_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/executions/{uuid4()}")
    assert response.status_code == 404
