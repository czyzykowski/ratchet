"""Postgres LISTEN/NOTIFY listener for ratchet_task_status and ratchet_compilation_trigger."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from core import events as ev

logger = logging.getLogger(__name__)

_ACTIONABLE_STATUSES = {ev.READY_FOR_IMPLEMENTATION, ev.READY_FOR_QA}


class NotificationListener:
    """Async context manager that listens on task status and compilation trigger channels.

    Opens a dedicated psycopg connection in autocommit mode (separate from the
    pool used by PostgresStore — LISTEN state is per-connection and incompatible
    with pooled connections).
    """

    def __init__(self, dsn: str, max_workers: int = 1) -> None:
        self._dsn = dsn
        self.max_workers = max_workers
        self._conn: Any = None

    async def __aenter__(self) -> NotificationListener:
        import psycopg

        dsn = self._dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self._conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        await self._conn.execute("LISTEN ratchet_task_status")
        await self._conn.execute("LISTEN ratchet_compilation_trigger")
        logger.info("Listening on ratchet_task_status and ratchet_compilation_trigger channels")
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def listen(self) -> AsyncGenerator[tuple[str, str, str], None]:
        """Yield typed 3-tuples from both notification channels.

        For ratchet_task_status notifications:
          yields ("task", task_id, status) — filtered to actionable statuses only.

        For ratchet_compilation_trigger notifications:
          yields ("compile", reason, "") — always yielded (no filtering).

        LISTEN is issued in __aenter__ so notifications are captured before startup
        catchup runs, eliminating the race where a transition fires between catchup
        finishing and LISTEN being registered.
        """
        conn = self._conn
        assert conn is not None, "NotificationListener must be used as async context manager"

        async for notify in conn.notifies():
            if notify.payload is None:
                continue
            try:
                data = json.loads(notify.payload)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid notification payload: %r", notify.payload)
                continue

            channel = notify.channel

            if channel == "ratchet_task_status":
                task_id = data.get("task_id")
                status = data.get("status")

                if not task_id or not status:
                    logger.warning("Notification missing task_id or status: %r", data)
                    continue

                if status not in _ACTIONABLE_STATUSES:
                    logger.debug("Dropping notification: task=%s status=%s", task_id, status)
                    continue

                logger.info("Received task notification: task=%s status=%s", task_id, status)
                yield "task", task_id, status

            elif channel == "ratchet_compilation_trigger":
                reason = data.get("reason", "unknown")
                logger.info("Received compilation trigger: reason=%s", reason)
                yield "compile", reason, ""
