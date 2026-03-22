"""Tests for the /ws/worker WebSocket endpoint in the web app context."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.remote_protocol import (
    HeartbeatMessage,
    OrchestratorAckMessage,
    WorkerHelloMessage,
    parse_orchestrator_message,
)
from core.store import InMemoryStore
from orchestrator.registry import WorkerRegistry
from web.routes.api.ws_worker import router as ws_worker_router


def _make_test_app(
    store: InMemoryStore | None = None, registry: WorkerRegistry | None = None
) -> FastAPI:
    """Create a minimal FastAPI app with the ws_worker router and mocked state."""
    app = FastAPI()
    app.state.store = store if store is not None else InMemoryStore()
    app.state.registry = registry if registry is not None else WorkerRegistry()
    app.include_router(ws_worker_router)
    return app


def _hello_msg(capabilities: list[str] | None = None) -> str:
    return WorkerHelloMessage(
        type="worker_hello",
        worker_id="w1",
        version="1.0",
        capabilities=capabilities or ["python"],
    ).model_dump_json()


def _heartbeat_msg() -> str:
    return HeartbeatMessage(
        type="heartbeat",
        worker_id="w1",
        timestamp_utc="2026-01-01T00:00:00Z",
        current_task_id=None,
    ).model_dump_json()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_should_receive_ack_when_worker_sends_hello() -> None:
    """Worker registration via WorkerHelloMessage receives OrchestratorAckMessage(accepted=True)."""
    app = _make_test_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg(["python", "git"]))
            raw = ws.receive_text()
            msg = parse_orchestrator_message(raw)
            assert isinstance(msg, OrchestratorAckMessage)
            assert msg.accepted is True
            assert msg.message == "registered"


def test_should_reject_non_hello_first_message() -> None:
    """Non-hello first message gets rejected with accepted=False."""
    app = _make_test_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_heartbeat_msg())
            raw = ws.receive_text()
            msg = parse_orchestrator_message(raw)
            assert isinstance(msg, OrchestratorAckMessage)
            assert msg.accepted is False


def test_should_add_worker_to_registry_on_registration() -> None:
    """After WorkerHelloMessage, the worker appears in registry.all_workers()."""
    registry = WorkerRegistry()
    app = _make_test_app(registry=registry)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg(["python"]))
            ws.receive_text()  # consume ack
            workers = registry.all_workers()
            assert len(workers) == 1
            assert workers[0].capabilities == ["python"]


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_should_accept_heartbeat_without_error() -> None:
    """Heartbeat message is accepted without error after registration."""
    app = _make_test_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()  # consume ack
            # Send heartbeat — should not close the connection or raise
            ws.send_text(_heartbeat_msg())
            # Connection should still be alive; send another heartbeat
            ws.send_text(_heartbeat_msg())


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_should_remove_worker_from_registry_on_disconnect() -> None:
    """Worker is removed from registry when WebSocket disconnects."""
    registry = WorkerRegistry()
    app = _make_test_app(registry=registry)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/worker") as ws:
            ws.send_text(_hello_msg())
            ws.receive_text()  # consume ack
            assert len(registry.all_workers()) == 1
        # After context exit, disconnect has occurred
        assert len(registry.all_workers()) == 0


def test_should_call_handle_disconnect_with_active_execution_on_disconnect() -> None:
    """handle_disconnect is called when a worker with an active execution disconnects."""
    registry = WorkerRegistry()
    store = InMemoryStore()
    app = _make_test_app(store=store, registry=registry)

    handle_called: list[str] = []

    async def _fake_handle_disconnect(conn: object, s: object, r: object) -> None:
        handle_called.append("called")

    with patch("web.routes.api.ws_worker.handle_disconnect", new=_fake_handle_disconnect):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/worker") as ws:
                ws.send_text(_hello_msg())
                ws.receive_text()  # consume ack
                # Assign an execution to this worker
                workers = registry.all_workers()
                registry.assign_job(workers[0].worker_id, "exec-abc")
            # disconnect — handle_disconnect should have been called

    assert "called" in handle_called
