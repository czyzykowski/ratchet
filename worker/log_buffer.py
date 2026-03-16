"""LogBuffer: in-memory ring buffer for worker log entries with pub/sub streaming."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    message: str


class LogBuffer:
    """Ring buffer storing log entries with subscriber fan-out for SSE streaming."""

    def __init__(self, maxlen: int = 500) -> None:
        self._buffer: deque[LogEntry] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue[LogEntry]] = set()

    def append(self, level: str, message: str) -> LogEntry:
        entry = LogEntry(timestamp=datetime.now(UTC), level=level, message=message)
        self._buffer.append(entry)
        for queue in self._subscribers:
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass
        return entry

    def get_recent(self, n: int = 100) -> list[LogEntry]:
        entries = list(self._buffer)
        return entries[-n:] if n < len(entries) else entries

    def subscribe(self) -> asyncio.Queue[LogEntry]:
        queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LogEntry]) -> None:
        self._subscribers.discard(queue)


class WorkerLogHandler(logging.Handler):
    """Bridges the worker.* logger hierarchy to a LogBuffer."""

    def __init__(self, log_buffer: LogBuffer) -> None:
        super().__init__()
        self.log_buffer = log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        self.log_buffer.append(record.levelname, self.format(record))
