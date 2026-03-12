"""Tests for orchestrator FastAPI server — health, workers, and WebSocket lifecycle."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.remote_protocol import (
    ExecutionCompletedMessage,
    HeartbeatMessage,
    OrchestratorAckMessage,
    WorkerHelloMessage,
    parse_orchestrator_message,
)
from orchestrator.server import app


def _hello_msg(capabilities: list[str] | None = None) -> str:
    return WorkerHelloMessage(
        type="worker_hello",
        worker_id="w1",
        version="1.0",
        capabilities=capabilities or ["python"],
    ).model_dump_json()


def _completed_msg(execution_id: str = "exec-1") -> str:
    return ExecutionCompletedMessage(
        type="execution_completed",
        worker_id="w1",
        task_id="task-1",
        execution_id=execution_id,
        patch="",
        timestamp_utc="2026-01-01T00:00:00Z",
    ).model_dump_json()


def _heartbeat_msg() -> str:
    return HeartbeatMessage(
        type="heartbeat",
        worker_id="w1",
        timestamp_utc="2026-01-01T00:00:00Z",
        current_task_id=None,
    ).model_dump_json()


# ---------------------------------------------------------------------------
# HTTP endpoint tests (sync TestClient to properly trigger lifespan)
# ---------------------------------------------------------------------------


def test_health_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workers": 0}


def test_workers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        response = client.get("/workers")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# WebSocket tests (sync TestClient)
# ---------------------------------------------------------------------------


def test_websocket_registration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg(["python", "git"]))
            raw = ws.receive_text()
            msg = parse_orchestrator_message(raw)
            assert isinstance(msg, OrchestratorAckMessage)
            assert msg.accepted is True
            assert msg.message == "registered"


def test_websocket_wrong_first_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_heartbeat_msg())
            raw = ws.receive_text()
            msg = parse_orchestrator_message(raw)
            assert isinstance(msg, OrchestratorAckMessage)
            assert msg.accepted is False


def test_health_shows_connected_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()  # consume ack
            response = client.get("/health")
            assert response.json()["workers"] == 1


def test_workers_shows_registered_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg(["python"]))
            ws.receive_text()  # consume ack
            response = client.get("/workers")
            workers = response.json()
            assert len(workers) == 1
            assert workers[0]["capabilities"] == ["python"]
            assert "id" in workers[0]
            assert "current_execution_id" in workers[0]
            assert "connected_at" in workers[0]


def test_execution_completed_clears_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()  # consume ack

            # Manually assign a job via registry
            registry = app.state.registry
            workers = registry.all_workers()
            assert len(workers) == 1
            worker_id = workers[0].worker_id
            registry.assign_job(worker_id, "exec-1")
            assert registry.all_workers()[0].current_execution_id == "exec-1"

            ws.send_text(_completed_msg("exec-1"))
            # Give server a moment to process; poll workers endpoint
            response = client.get("/workers")
            assert response.json()[0]["current_execution_id"] is None


def test_disconnect_removes_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()  # consume ack
            response = client.get("/health")
            assert response.json()["workers"] == 1
        # websocket context exited — disconnect occurred
        response = client.get("/health")
        assert response.json()["workers"] == 0


def test_health_after_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()
        response = client.get("/health")
        assert response.json() == {"status": "ok", "workers": 0}
