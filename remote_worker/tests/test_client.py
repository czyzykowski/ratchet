"""Tests for remote_worker.client — auth verification and job execution flow."""

from __future__ import annotations

import base64
import subprocess
from asyncio import Queue
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from core.invoker import InvocationResult
from core.remote_protocol import (
    AssignTaskMessage,
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    OrchestratorAckMessage,
)
from remote_worker.client import ClaudeAuthError, RemoteWorkerClient, verify_claude_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(  # type: ignore[type-arg]
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    cp: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )
    return cp


def _make_assign_msg(
    *,
    status: str = "completed",
    failure_reason: str | None = None,
) -> AssignTaskMessage:
    return AssignTaskMessage.model_validate(
        {
            "type": "assign_task",
            "task_id": str(uuid4()),
            "spec_id": str(uuid4()),
            "spec_content": "do the thing",
            "project_id": str(uuid4()),
            "project_name": "test",
            "project_local_path": "/tmp/repo",
            "project_intent_md": "intent",
            "project_ratchet_yaml": None,
            "project_config_source": "db",
            "git_bundle_b64": base64.b64encode(b"bundle").decode(),
        }
    )


# ---------------------------------------------------------------------------
# verify_claude_auth tests
# ---------------------------------------------------------------------------


class TestVerifyClaudeAuth:
    def test_verify_claude_auth_success(self) -> None:
        version_cp = _make_completed_process(returncode=0, stdout="claude v1.0\n")
        api_cp = _make_completed_process(returncode=0, stdout="OK\n")
        with patch("subprocess.run", side_effect=[version_cp, api_cp]):
            verify_claude_auth()  # should not raise

    def test_verify_claude_auth_binary_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ClaudeAuthError, match="not found"):
                verify_claude_auth()

    def test_verify_claude_auth_version_fails(self) -> None:
        cp = _make_completed_process(returncode=1, stderr="error")
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(ClaudeAuthError):
                verify_claude_auth()

    def test_verify_claude_auth_api_check_fails(self) -> None:
        version_cp = _make_completed_process(returncode=0, stdout="claude v1.0\n")
        api_cp = _make_completed_process(returncode=1, stderr="auth error")
        with patch("subprocess.run", side_effect=[version_cp, api_cp]):
            with pytest.raises(ClaudeAuthError):
                verify_claude_auth()

    def test_verify_claude_auth_no_ok_in_output(self) -> None:
        version_cp = _make_completed_process(returncode=0, stdout="claude v1.0\n")
        api_cp = _make_completed_process(returncode=0, stdout="hello there\n")
        with patch("subprocess.run", side_effect=[version_cp, api_cp]):
            with pytest.raises(ClaudeAuthError, match="unexpected output"):
                verify_claude_auth()

    def test_verify_claude_auth_version_timeout(self) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("claude", 10),
        ):
            with pytest.raises(ClaudeAuthError):
                verify_claude_auth()

    def test_verify_claude_auth_api_timeout(self) -> None:
        version_cp = _make_completed_process(returncode=0, stdout="claude v1.0\n")
        with patch(
            "subprocess.run",
            side_effect=[version_cp, subprocess.TimeoutExpired("claude", 30)],
        ):
            with pytest.raises(ClaudeAuthError):
                verify_claude_auth()


# ---------------------------------------------------------------------------
# RemoteWorkerClient job flow tests
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

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        while not self._queue.empty():
            yield await self._queue.get()

    async def __aenter__(self) -> _MockWebSocket:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


def _ack_json(accepted: bool = True) -> str:
    return OrchestratorAckMessage(
        type="orchestrator_ack",
        worker_id="w1",
        accepted=accepted,
        message=None if accepted else "bad version",
    ).model_dump_json()


def _assign_json(assign_msg: AssignTaskMessage) -> str:
    return assign_msg.model_dump_json()


def _make_invocation_result(
    status: str = "completed", failure_reason: str | None = None
) -> InvocationResult:
    return InvocationResult(
        execution_id=uuid4(),
        status=status,
        failure_reason=failure_reason,
        trace_id=uuid4(),
    )


def _setup_client_mocks(
    ws: _MockWebSocket,
    invocation_result: InvocationResult,
    tmpdir: str = "/tmp/fake-tmpdir",
    patch_content: str = "--- a\n+++ b\n",
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Returns (mock_connect, mock_invoker_cls, mock_extract, mock_create_patch)."""
    mock_connect = MagicMock()
    mock_connect.return_value = ws

    mock_invoker_instance = MagicMock()
    mock_invoker_instance.invoke.return_value = invocation_result
    mock_invoker_cls = MagicMock(return_value=mock_invoker_instance)

    mock_extract = MagicMock()
    mock_create_patch = MagicMock(return_value=patch_content)

    return mock_connect, mock_invoker_cls, mock_extract, mock_create_patch


class TestRemoteWorkerClientFlow:
    @pytest.mark.asyncio
    async def test_happy_path_sends_completed(self) -> None:
        assign_msg = _make_assign_msg()
        ws = _MockWebSocket([_ack_json(), _assign_json(assign_msg)])
        inv_result = _make_invocation_result(status="completed")
        tmpdir = "/tmp/fake-tmpdir"

        mock_connect, mock_invoker_cls, mock_extract, mock_create_patch = (
            _setup_client_mocks(ws, inv_result, tmpdir=tmpdir)
        )

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
            patch("remote_worker.client.ClaudeCodeInvoker", mock_invoker_cls),
            patch("remote_worker.client.git_transfer.extract_bundle", mock_extract),
            patch(
                "remote_worker.client.git_transfer.create_patch",
                mock_create_patch,
            ),
            patch("tempfile.mkdtemp", return_value=tmpdir),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            await client.run()

        # Should have sent: hello, execution_started, execution_completed
        assert len(ws.sent) == 3
        completed_raw = ws.sent[2]
        completed = ExecutionCompletedMessage.model_validate_json(completed_raw)
        assert completed.patch == "--- a\n+++ b\n"
        assert completed.task_id == assign_msg.task_id
        mock_rmtree.assert_called_once_with(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_failed_invocation_sends_failed(self) -> None:
        assign_msg = _make_assign_msg()
        ws = _MockWebSocket([_ack_json(), _assign_json(assign_msg)])
        inv_result = _make_invocation_result(status="failed", failure_reason="bad")

        mock_connect, mock_invoker_cls, mock_extract, mock_create_patch = (
            _setup_client_mocks(ws, inv_result)
        )

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
            patch("remote_worker.client.ClaudeCodeInvoker", mock_invoker_cls),
            patch("remote_worker.client.git_transfer.extract_bundle", mock_extract),
            patch("remote_worker.client.git_transfer.create_patch", mock_create_patch),
            patch("tempfile.mkdtemp", return_value="/tmp/fake"),
            patch("shutil.rmtree"),
        ):
            await client.run()

        assert len(ws.sent) == 3
        failed = ExecutionFailedMessage.model_validate_json(ws.sent[2])
        assert failed.failure_reason == "bad"

    @pytest.mark.asyncio
    async def test_crashed_invocation_sends_failed(self) -> None:
        assign_msg = _make_assign_msg()
        ws = _MockWebSocket([_ack_json(), _assign_json(assign_msg)])
        inv_result = _make_invocation_result(
            status="crashed", failure_reason="process exited with code 1"
        )

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
            patch(
                "remote_worker.client.ClaudeCodeInvoker",
                MagicMock(return_value=MagicMock(invoke=MagicMock(return_value=inv_result))),
            ),
            patch("remote_worker.client.git_transfer.extract_bundle"),
            patch("remote_worker.client.git_transfer.create_patch"),
            patch("tempfile.mkdtemp", return_value="/tmp/fake"),
            patch("shutil.rmtree"),
        ):
            await client.run()

        assert len(ws.sent) == 3
        failed = ExecutionFailedMessage.model_validate_json(ws.sent[2])
        assert "crashed" in failed.failure_reason or "process" in failed.failure_reason

    @pytest.mark.asyncio
    async def test_cancel_before_extraction_skips_invoke(self) -> None:
        import json as _json

        from core.remote_protocol import CancelTaskMessage

        assign_msg = _make_assign_msg()
        cancel_json = CancelTaskMessage(
            type="cancel_task", task_id=assign_msg.task_id, reason="user cancelled"
        ).model_dump_json()

        # Cancel arrives first, then assign
        ws = _MockWebSocket([_ack_json(), cancel_json, _assign_json(assign_msg)])

        mock_invoker_instance = MagicMock()
        mock_invoker_cls = MagicMock(return_value=mock_invoker_instance)

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
            patch("remote_worker.client.ClaudeCodeInvoker", mock_invoker_cls),
            patch("remote_worker.client.git_transfer.extract_bundle"),
            patch("remote_worker.client.git_transfer.create_patch"),
            patch("tempfile.mkdtemp", return_value="/tmp/fake"),
            patch("shutil.rmtree"),
        ):
            await client.run()

        # invoke should NOT have been called
        mock_invoker_instance.invoke.assert_not_called()
        # Only hello + execution_started sent (no completed/failed)
        sent_types = [_json.loads(raw).get("type") for raw in ws.sent]
        assert "execution_completed" not in sent_types
        assert "execution_failed" not in sent_types

    @pytest.mark.asyncio
    async def test_tmpdir_cleaned_up_on_exception(self) -> None:
        from core.git_transfer import GitTransferError

        assign_msg = _make_assign_msg()
        ws = _MockWebSocket([_ack_json(), _assign_json(assign_msg)])
        tmpdir = "/tmp/fake-cleanup"

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
            patch(
                "remote_worker.client.git_transfer.extract_bundle",
                side_effect=GitTransferError("bundle error"),
            ),
            patch("tempfile.mkdtemp", return_value=tmpdir),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            # The exception from extract_bundle propagates through _handle_assignment
            # but shutil.rmtree must still run (finally block)
            try:
                await client.run()
            except Exception:
                pass

        mock_rmtree.assert_called_once_with(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_ack_rejected_raises(self) -> None:
        ws = _MockWebSocket([_ack_json(accepted=False)])

        client = RemoteWorkerClient("http://host:8765", [])
        with (
            patch("remote_worker.client.websockets.asyncio.client.connect", return_value=ws),
        ):
            with pytest.raises(RuntimeError, match="Registration rejected"):
                await client.run()
