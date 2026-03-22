"""WorkerChannel protocol and WebSocketWorkerChannel implementation.

Abstracts the send-request/await-response pattern over a WebSocket connection,
decoupling the pipeline sequencer from raw WebSocket transport details.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.remote_protocol import AnyCommandRequest, AnyCommandResponse, parse_command_response


class PipelineAbort(Exception):
    """Raised when a command fails or the transport breaks during a pipeline.

    Carries context about which step failed and why.
    """

    def __init__(self, step_name: str, error: str, request_id: str = "") -> None:
        super().__init__(f"Pipeline aborted at {step_name!r}: {error}")
        self.step_name = step_name
        self.error = error
        self.request_id = request_id


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
    """WorkerChannel implementation backed by a raw WebSocket connection."""

    def __init__(self, websocket: Any, worker_id: str) -> None:
        self._websocket = websocket
        self._worker_id = worker_id

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def send_command(self, request: AnyCommandRequest) -> AnyCommandResponse:
        """Serialize request, send over WebSocket, receive response, validate."""
        try:
            await self._websocket.send_text(request.model_dump_json())
            raw = await self._websocket.receive_text()
        except Exception as exc:
            raise PipelineAbort(
                step_name=request.type,
                error=str(exc),
                request_id=request.request_id,
            ) from exc

        response = parse_command_response(raw)

        if response.request_id != request.request_id:
            raise PipelineAbort(
                step_name=request.type,
                error=(
                    f"Request ID mismatch: expected {request.request_id!r},"
                    f" got {response.request_id!r}"
                ),
                request_id=request.request_id,
            )

        if not response.success:
            raise PipelineAbort(
                step_name=request.type,
                error=getattr(response, "error", None) or "command failed",
                request_id=request.request_id,
            )

        return response
