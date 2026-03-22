"""Tests for orchestrator/channel.py — WorkerChannel protocol and WebSocketWorkerChannel."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from core.remote_protocol import (
    GetProjectStatusRequest,
    GetProjectStatusResponse,
)
from orchestrator.channel import PipelineAbort, WebSocketWorkerChannel


def _make_request() -> GetProjectStatusRequest:
    return GetProjectStatusRequest(
        type="get_project_status",
        request_id=str(uuid4()),
        project_id=str(uuid4()),
    )


def _success_response(request_id: str) -> str:
    resp = GetProjectStatusResponse(
        type="get_project_status_response",
        request_id=request_id,
        success=True,
        exists=True,
        head_commit="abc123",
    )
    return resp.model_dump_json()


def _failure_response(request_id: str) -> str:
    resp = GetProjectStatusResponse(
        type="get_project_status_response",
        request_id=request_id,
        success=False,
        exists=False,
        error="project not found",
    )
    return resp.model_dump_json()


@pytest.mark.asyncio
async def test_send_command_happy_path_returns_typed_response() -> None:
    req = _make_request()
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(return_value=_success_response(req.request_id))

    channel = WebSocketWorkerChannel(ws, worker_id="w1")
    resp = await channel.send_command(req)

    assert isinstance(resp, GetProjectStatusResponse)
    assert resp.success is True
    assert resp.head_commit == "abc123"
    ws.send_text.assert_called_once_with(req.model_dump_json())


@pytest.mark.asyncio
async def test_send_command_raises_pipeline_abort_on_success_false() -> None:
    req = _make_request()
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(return_value=_failure_response(req.request_id))

    channel = WebSocketWorkerChannel(ws, worker_id="w1")
    with pytest.raises(PipelineAbort) as exc_info:
        await channel.send_command(req)

    assert exc_info.value.step_name == "get_project_status"
    assert "project not found" in exc_info.value.error
    assert exc_info.value.request_id == req.request_id


@pytest.mark.asyncio
async def test_send_command_raises_pipeline_abort_on_transport_error() -> None:
    req = _make_request()
    ws = AsyncMock()
    ws.send_text = AsyncMock(side_effect=ConnectionError("socket closed"))

    channel = WebSocketWorkerChannel(ws, worker_id="w1")
    with pytest.raises(PipelineAbort) as exc_info:
        await channel.send_command(req)

    assert exc_info.value.step_name == "get_project_status"
    assert "socket closed" in exc_info.value.error


@pytest.mark.asyncio
async def test_send_command_raises_pipeline_abort_on_request_id_mismatch() -> None:
    req = _make_request()
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    # Return a response with a different request_id
    ws.receive_text = AsyncMock(return_value=_success_response("wrong-id"))

    channel = WebSocketWorkerChannel(ws, worker_id="w1")
    with pytest.raises(PipelineAbort) as exc_info:
        await channel.send_command(req)

    assert "mismatch" in exc_info.value.error.lower()


def test_worker_id_property() -> None:
    channel = WebSocketWorkerChannel(AsyncMock(), worker_id="worker-42")
    assert channel.worker_id == "worker-42"
