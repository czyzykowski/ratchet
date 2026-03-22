"""Tests for worker.remote.RemoteWorker — handshake, command dispatch, heartbeat."""

from __future__ import annotations

import asyncio
import json
import subprocess
from asyncio import Queue
from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import uuid4

import pytest

from core.remote_protocol import (
    GetStatusRequest,
    OrchestratorAckMessage,
    RunCommandRequest,
    WorkerHelloMessage,
)
from worker.remote import RemoteWorker, _to_ws_url

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_to_ws_url_converts_http() -> None:
    assert _to_ws_url("http://host:8765") == "ws://host:8765"


def test_to_ws_url_converts_https() -> None:
    assert _to_ws_url("https://host:8765") == "wss://host:8765"


def test_to_ws_url_passthrough_for_ws() -> None:
    assert _to_ws_url("ws://host:8765") == "ws://host:8765"


# ---------------------------------------------------------------------------
# Mock WebSocket
# ---------------------------------------------------------------------------


class _MockWebSocket:
    """Fake WebSocket that yields pre-set messages and records sends."""

    def __init__(self, messages: list[str]) -> None:
        self._queue: Queue[str] = Queue()
        for m in messages:
            self._queue.put_nowait(m)
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        while not self._queue.empty():
            yield await self._queue.get()

    async def __aenter__(self) -> _MockWebSocket:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


def _ack_json(accepted: bool = True, worker_id: str = "w1") -> str:
    return OrchestratorAckMessage(
        type="orchestrator_ack",
        worker_id=worker_id,
        accepted=accepted,
        message=None if accepted else "bad version",
    ).model_dump_json()


# ---------------------------------------------------------------------------
# Hello / ack handshake
# ---------------------------------------------------------------------------


class TestHandshake:
    async def test_sends_worker_hello_on_connect(self) -> None:
        ws = _MockWebSocket([_ack_json()])
        worker = RemoteWorker("http://host:8765", ["cap1"], {"p1": "/path"})

        with patch(
            "websockets.asyncio.client.connect",
            return_value=ws,
        ):
            await worker._connect_and_loop()

        assert len(ws.sent) >= 1
        hello = WorkerHelloMessage.model_validate_json(ws.sent[0])
        assert hello.version == "2.0"
        assert hello.capabilities == ["cap1"]
        assert hello.projects == {"p1": "/path"}

    async def test_rejected_ack_raises_runtime_error(self) -> None:
        ws = _MockWebSocket([_ack_json(accepted=False)])
        worker = RemoteWorker("http://host:8765", [], {})

        with patch(
            "websockets.asyncio.client.connect",
            return_value=ws,
        ):
            with pytest.raises(RuntimeError, match="Registration rejected"):
                await worker._connect_and_loop()


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


class TestCommandDispatch:
    async def test_dispatches_run_command_and_sends_response(self) -> None:
        run_cmd_req = RunCommandRequest(
            type="run_command",
            request_id=str(uuid4()),
            execution_id="exec-1",
            cmd=["echo", "hello"],
            cwd="/tmp",
        )
        ws = _MockWebSocket([_ack_json(), run_cmd_req.model_dump_json()])
        worker = RemoteWorker("http://host:8765", [], {})

        mock_cp = subprocess.CompletedProcess([], 0, "hello\n", "")
        with (
            patch("websockets.asyncio.client.connect", return_value=ws),
            patch("subprocess.run", return_value=mock_cp),
        ):
            await worker._connect_and_loop()

        # hello + run_command_response
        assert len(ws.sent) >= 2
        response = json.loads(ws.sent[1])
        assert response["type"] == "run_command_response"
        assert response["success"] is True
        assert response["stdout"] == "hello\n"

    async def test_dispatches_get_status_command(self) -> None:
        status_req = GetStatusRequest(
            type="get_status",
            request_id=str(uuid4()),
        )
        ws = _MockWebSocket([_ack_json(), status_req.model_dump_json()])
        worker = RemoteWorker("http://host:8765", [], {})

        with patch("websockets.asyncio.client.connect", return_value=ws):
            await worker._connect_and_loop()

        assert len(ws.sent) >= 2
        response = json.loads(ws.sent[1])
        assert response["type"] == "get_status_response"
        assert response["success"] is True
        assert response["current_execution_id"] is None

    async def test_ignores_unknown_message_gracefully(self) -> None:
        ws = _MockWebSocket([_ack_json(), '{"type": "unknown_future_message"}'])
        worker = RemoteWorker("http://host:8765", [], {})

        with patch("websockets.asyncio.client.connect", return_value=ws):
            # Should not raise even with an unknown message type
            await worker._connect_and_loop()

        # Only hello was sent (no response for unknown message)
        assert len(ws.sent) == 1


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_heartbeat_sent_after_interval(self) -> None:
        ws = _MockWebSocket([])
        worker = RemoteWorker("http://host:8765", [], {})

        sent_heartbeats: list[str] = []
        original_send = ws.send

        async def capture_send(data: str) -> None:
            sent_heartbeats.append(data)
            await original_send(data)

        ws.send = capture_send  # type: ignore[method-assign]

        with patch("worker.remote._HEARTBEAT_INTERVAL", 0):
            task = asyncio.create_task(worker._heartbeat_loop(ws))
            # Let the heartbeat loop run at least once
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(sent_heartbeats) >= 1
        msg = json.loads(sent_heartbeats[0])
        assert msg["type"] == "heartbeat"
        assert msg["worker_id"] == worker._worker_id
