from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class WorkerConnection:
    worker_id: str
    capabilities: list[str]
    current_execution_id: str | None
    websocket: Any
    connected_at: datetime
    channel: Any = None  # WebSocketWorkerChannel, set after registration


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerConnection] = {}

    def register(self, worker_id: str, capabilities: list[str], websocket: Any) -> WorkerConnection:
        if worker_id in self._workers:
            raise ValueError(f"Worker {worker_id!r} is already registered")
        conn = WorkerConnection(
            worker_id=worker_id,
            capabilities=capabilities,
            current_execution_id=None,
            websocket=websocket,
            connected_at=datetime.now(tz=UTC),
        )
        self._workers[worker_id] = conn
        return conn

    def unregister(self, worker_id: str) -> WorkerConnection | None:
        return self._workers.pop(worker_id, None)

    def find_available(self, required_capabilities: list[str]) -> WorkerConnection | None:
        required = set(required_capabilities)
        for worker in self._workers.values():
            if worker.current_execution_id is None and required.issubset(set(worker.capabilities)):
                return worker
        return None

    def assign_job(self, worker_id: str, execution_id: str) -> None:
        if worker_id not in self._workers:
            raise KeyError(worker_id)
        self._workers[worker_id].current_execution_id = execution_id

    def clear_job(self, worker_id: str) -> None:
        if worker_id not in self._workers:
            raise KeyError(worker_id)
        self._workers[worker_id].current_execution_id = None

    def get_worker(self, worker_id: str) -> WorkerConnection | None:
        return self._workers.get(worker_id)

    def all_workers(self) -> list[WorkerConnection]:
        return list(self._workers.values())
