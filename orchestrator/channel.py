"""WorkerChannel protocol and WebSocketWorkerChannel implementation.

Abstracts the send-request/await-response pattern over a WebSocket connection,
decoupling the pipeline sequencer from raw WebSocket transport details.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from core.remote_protocol import AnyCommandRequest, AnyCommandResponse, parse_command_response

logger = logging.getLogger(__name__)


class PipelineAbort(Exception):
    """Raised when a command fails or the transport breaks during a pipeline.

    Carries context about which step failed and why.
    """

    def __init__(
        self, step_name: str, error: str, request_id: str = "", *, transient: bool = False
    ) -> None:
        super().__init__(f"Pipeline aborted at {step_name!r}: {error}")
        self.step_name = step_name
        self.error = error
        self.request_id = request_id
        self.transient = transient


@runtime_checkable
class WorkerChannel(Protocol):
    """Protocol for sending commands to a remote worker and awaiting responses.

    Implementations handle the transport details (WebSocket framing, JSON encoding).
    The protocol is request-response: one outstanding command at a time.
    """

    @property
    def worker_id(self) -> str:
        """Identifier of the remote worker this channel connects to."""
        ...

    async def send_command(self, request: AnyCommandRequest) -> AnyCommandResponse:
        """Send a command request and await the typed response.

        Raises PipelineAbort if the response indicates failure (success=False)
        or if a transport error occurs.
        """
        ...


class WebSocketWorkerChannel:
    """WorkerChannel implementation backed by a raw WebSocket connection.

    The main WebSocket loop must call deliver_response() when it receives
    a command response message. send_command() sends requests directly on
    the websocket but awaits responses via per-request asyncio.Future objects,
    matched by request_id. This handles out-of-order responses correctly.
    """

    def __init__(self, websocket: Any, worker_id: str) -> None:
        self._websocket = websocket
        self._worker_id = worker_id
        self._pending: dict[str, asyncio.Future[str]] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def deliver_response(self, raw: str) -> None:
        """Called by the main WebSocket loop to route a response to the waiting command."""
        try:
            response = parse_command_response(raw)
        except Exception:
            logger.warning("Failed to parse command response, dropping: %s", raw[:200])
            return

        future = self._pending.pop(response.request_id, None)
        if future is not None and not future.done():
            future.set_result(raw)
        else:
            logger.warning(
                "No pending request for response request_id=%s, dropping",
                response.request_id,
            )

    async def send_command(self, request: AnyCommandRequest) -> AnyCommandResponse:
        """Serialize request, send over WebSocket, await matched response, validate."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request.request_id] = future

        try:
            await self._websocket.send_text(request.model_dump_json())
            raw = await future
        except Exception as exc:
            self._pending.pop(request.request_id, None)
            raise PipelineAbort(
                step_name=request.type,
                error=str(exc),
                request_id=request.request_id,
                transient=True,
            ) from exc

        response = parse_command_response(raw)

        if not response.success:
            raise PipelineAbort(
                step_name=request.type,
                error=getattr(response, "error", None) or "command failed",
                request_id=request.request_id,
            )

        return response
