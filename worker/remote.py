"""Remote worker: connects to orchestrator via WebSocket and dispatches commands."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import websockets.asyncio.client

from core.remote_protocol import (
    CancelTaskMessage,
    HeartbeatMessage,
    OrchestratorAckMessage,
    WorkerHelloMessage,
    parse_orchestrator_message,
)
from worker.executor import CommandExecutor

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 30
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 60.0
_BACKOFF_FACTOR = 2.0


def _to_ws_url(url: str) -> str:
    """Convert http/https URL to ws/wss."""
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    return url


class RemoteWorker:
    """Connects to orchestrator, handles command protocol via WebSocket."""

    def __init__(
        self,
        orchestrator_url: str,
        capabilities: list[str],
        projects: dict[str, str],
    ) -> None:
        self._orchestrator_url = orchestrator_url
        self._capabilities = capabilities
        self._projects = projects
        self._worker_id = str(uuid4())

    async def run(self) -> None:
        """Main entry point with reconnection loop (exponential backoff)."""
        backoff = _BACKOFF_MIN
        while True:
            try:
                await self._connect_and_loop()
                backoff = _BACKOFF_MIN
            except Exception as exc:
                logger.warning(
                    "Connection lost: %s. Reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)

    async def _connect_and_loop(self) -> None:
        """Single connection lifecycle: connect, handshake, command loop."""
        ws_url = _to_ws_url(self._orchestrator_url)

        async with websockets.asyncio.client.connect(
            ws_url, max_size=100 * 1024 * 1024
        ) as ws:
            hello = WorkerHelloMessage(
                type="worker_hello",
                worker_id=self._worker_id,
                version="2.0",
                capabilities=self._capabilities,
                projects=self._projects,
            )
            await ws.send(hello.model_dump_json())

            raw_ack = await ws.recv()
            ack = OrchestratorAckMessage.model_validate_json(str(raw_ack))
            if not ack.accepted:
                raise RuntimeError(f"Registration rejected: {ack.message}")

            executor = CommandExecutor(self._worker_id, self._projects)
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

            try:
                async for raw in ws:
                    await self._handle_message(str(raw), ws, executor)
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _handle_message(
        self,
        raw: str,
        ws: Any,
        executor: CommandExecutor,
    ) -> None:
        """Parse and dispatch a single orchestrator message."""
        try:
            msg = parse_orchestrator_message(raw)
        except Exception as exc:
            logger.warning("Failed to parse orchestrator message: %s", exc)
            return

        if isinstance(msg, CancelTaskMessage):
            logger.info("Received cancel_task for %s", msg.task_id)
            return

        if isinstance(msg, OrchestratorAckMessage):
            logger.warning("Unexpected orchestrator_ack in command loop")
            return

        # Remaining message types are command requests
        try:
            response = await executor.handle(msg)  # type: ignore[arg-type]
            await ws.send(response.model_dump_json())
        except Exception as exc:
            logger.error("Error handling command %s: %s", msg.type, exc)

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Send HeartbeatMessage every _HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            heartbeat = HeartbeatMessage(
                type="heartbeat",
                worker_id=self._worker_id,
                timestamp_utc=datetime.now(UTC).isoformat(),
                current_task_id=None,
            )
            try:
                await ws.send(heartbeat.model_dump_json())
            except Exception as exc:
                logger.warning("Failed to send heartbeat: %s", exc)
                break
