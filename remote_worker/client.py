"""Remote worker client: connects to orchestrator via WebSocket and executes tasks."""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
import traceback
from datetime import UTC, datetime
from uuid import UUID, uuid4

import websockets
import websockets.asyncio.client

from core import git_transfer
from core.context_assembler import ExecutionContext, build_prompt
from core.invoker import ClaudeCodeInvoker
from core.remote_protocol import (
    AssignTaskMessage,
    CancelTaskMessage,
    ExecutionCompletedMessage,
    ExecutionFailedMessage,
    ExecutionStartedMessage,
    OrchestratorAckMessage,
    WorkerHelloMessage,
    parse_orchestrator_message,
)
from core.store import InMemoryStore

logger = logging.getLogger(__name__)

# Git bundles can be large; default websockets limit is 1MB.
WS_MAX_MESSAGE_SIZE = 100 * 1024 * 1024  # 100 MB


class ClaudeAuthError(Exception):
    """Raised when Claude binary authentication or API check fails."""


def verify_claude_auth() -> None:
    """Verify claude binary is present and API is accessible.

    Raises ClaudeAuthError with a descriptive message on any failure.
    """
    # Check binary exists and version works
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise ClaudeAuthError("claude binary not found on PATH")
    except subprocess.TimeoutExpired:
        raise ClaudeAuthError("claude --version timed out")

    if result.returncode != 0:
        raise ClaudeAuthError(
            f"claude --version failed: {result.stderr.strip()}"
        )

    # Check API access
    try:
        api_result = subprocess.run(
            ["claude", "-p", "respond with the word OK"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise ClaudeAuthError("Claude API check timed out after 30s")

    if api_result.returncode != 0:
        raise ClaudeAuthError(
            f"Claude API check failed (exit {api_result.returncode}):"
            f" {api_result.stderr.strip()}"
        )

    if "ok" not in api_result.stdout.lower():
        raise ClaudeAuthError(
            f"Claude API check returned unexpected output: {api_result.stdout[:200]!r}"
        )


class RemoteWorkerClient:
    def __init__(self, orchestrator_url: str, capabilities: list[str]) -> None:
        self._orchestrator_url = orchestrator_url
        self._capabilities = capabilities
        self._worker_id = str(uuid4())
        self._cancel_flag = False

    async def run(self) -> None:
        """Connect to orchestrator and process tasks until disconnected."""
        ws_url = self._orchestrator_url
        ws_url = ws_url.replace("https://", "wss://")
        ws_url = ws_url.replace("http://", "ws://")
        ws_url = ws_url.rstrip("/") + "/ws/worker"

        async with websockets.asyncio.client.connect(ws_url, max_size=WS_MAX_MESSAGE_SIZE) as ws:
            hello = WorkerHelloMessage(
                type="worker_hello",
                worker_id=self._worker_id,
                version="1.0",
                capabilities=self._capabilities,
            )
            await ws.send(hello.model_dump_json())

            async for raw in ws:
                msg = parse_orchestrator_message(str(raw))
                if isinstance(msg, OrchestratorAckMessage):
                    if not msg.accepted:
                        raise RuntimeError(f"Registration rejected: {msg.message}")
                elif isinstance(msg, AssignTaskMessage):
                    try:
                        await self._handle_assignment(ws, msg)
                    except Exception:
                        logger.exception("Task execution failed for task=%s", msg.task_id)
                        try:
                            failed = ExecutionFailedMessage(
                                type="execution_failed",
                                worker_id=self._worker_id,
                                task_id=msg.task_id,
                                execution_id="",
                                failure_reason=traceback.format_exc(),
                                timestamp_utc=datetime.now(UTC).isoformat(),
                            )
                            await ws.send(failed.model_dump_json())
                        except Exception:
                            logger.exception("Failed to send error report")
                elif isinstance(msg, CancelTaskMessage):
                    self._cancel_flag = True

    async def _handle_assignment(
        self, ws: websockets.asyncio.client.ClientConnection, msg: AssignTaskMessage
    ) -> None:
        execution_id = uuid4()
        print(f"[remote-worker] Assigned task={msg.task_id}", flush=True)

        started = ExecutionStartedMessage(
            type="execution_started",
            worker_id=self._worker_id,
            task_id=msg.task_id,
            execution_id=str(execution_id),
            branch_name=None,
            timestamp_utc=datetime.now(UTC).isoformat(),
        )
        await ws.send(started.model_dump_json())
        print("[remote-worker] Sent execution_started", flush=True)

        tmpdir = tempfile.mkdtemp()
        try:
            if self._cancel_flag:
                return

            bundle_bytes = base64.b64decode(msg.git_bundle_b64)
            print(f"[remote-worker] Extracting bundle to {tmpdir}", flush=True)
            git_transfer.extract_bundle(bundle_bytes, tmpdir)
            print("[remote-worker] Bundle extracted", flush=True)

            if self._cancel_flag:
                return

            intent_content = msg.project_intent_md or ""
            prompt = build_prompt(intent_content, msg.spec_content)
            context = ExecutionContext(
                execution_id=execution_id,
                task_id=UUID(msg.task_id),
                spec_id=UUID(msg.spec_id),
                worktree_path=tmpdir,
                prompt=prompt,
            )

            print(f"[remote-worker] Invoking Claude in {tmpdir}", flush=True)
            invoker = ClaudeCodeInvoker(store=InMemoryStore())
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: invoker.invoke(context, allow_project_root=True)
            )
            print(f"[remote-worker] Invocation result: {result.status}", flush=True)

            if self._cancel_flag:
                return

            if result.status == "completed":
                patch = git_transfer.create_patch(tmpdir)
                completed = ExecutionCompletedMessage(
                    type="execution_completed",
                    worker_id=self._worker_id,
                    task_id=msg.task_id,
                    execution_id=str(execution_id),
                    patch=patch,
                    timestamp_utc=datetime.now(UTC).isoformat(),
                )
                await ws.send(completed.model_dump_json())
            else:
                failed = ExecutionFailedMessage(
                    type="execution_failed",
                    worker_id=self._worker_id,
                    task_id=msg.task_id,
                    execution_id=str(execution_id),
                    failure_reason=result.failure_reason or "invocation failed",
                    timestamp_utc=datetime.now(UTC).isoformat(),
                )
                await ws.send(failed.model_dump_json())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
