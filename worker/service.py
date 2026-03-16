"""WorkerService: managed async lifecycle wrapper around notification_loop."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.invoker import ClaudeCodeInvoker
from core.store import PostgresStore
from worker.log_buffer import LogBuffer, WorkerLogHandler
from worker.runner import notification_loop

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class WorkerSettings:
    watchdog_timeout: int = 300
    max_workers: int = 1
    local_capabilities: list[str] = dataclasses.field(default_factory=list)
    enabled: bool = True


class WorkerService:
    """Managed async lifecycle wrapper around notification_loop.

    Allows the web UI to start, stop, restart, and reconfigure the embedded
    worker without process restarts.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        dsn: str,
        settings: WorkerSettings | None = None,
        log_buffer: LogBuffer | None = None,
    ) -> None:
        self._pool = pool
        self._dsn = dsn
        self._settings: WorkerSettings = settings if settings is not None else WorkerSettings()
        self._task: asyncio.Task[None] | None = None
        self._invoker: ClaudeCodeInvoker | None = None
        self._status: str = "stopped"
        self.started_at: datetime | None = None
        self.error_message: str | None = None
        self.log_buffer: LogBuffer = log_buffer if log_buffer is not None else LogBuffer()
        self._log_handler: WorkerLogHandler | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def settings(self) -> WorkerSettings:
        return dataclasses.replace(self._settings)

    async def start(self) -> None:
        """Launch notification_loop as an asyncio task."""
        if self._status in ("running", "starting"):
            raise RuntimeError("Worker already running")

        if not self._settings.enabled:
            self._status = "stopped"
            return

        self._status = "starting"
        self.error_message = None

        handler = WorkerLogHandler(self.log_buffer)
        logging.getLogger("worker").addHandler(handler)
        self._log_handler = handler

        store = PostgresStore(pool=self._pool)
        invoker = ClaudeCodeInvoker(store=store, watchdog_timeout=self._settings.watchdog_timeout)
        self._invoker = invoker

        task = asyncio.ensure_future(
            notification_loop(
                store,
                invoker,
                self._dsn,
                max_workers=self._settings.max_workers,
                local_capabilities=self._settings.local_capabilities,
            )
        )
        self._task = task

        def _on_loop_done(t: asyncio.Task[None]) -> None:
            exc = t.exception() if not t.cancelled() else None
            if t.cancelled():
                self._status = "stopped"
            elif exc is not None:
                self._status = "error"
                self.error_message = str(exc)
                logger.error("Worker loop exited with error: %s", exc)
            else:
                # Clean return — shouldn't happen normally
                self._status = "stopped"

        task.add_done_callback(_on_loop_done)

        self._status = "running"
        self.started_at = datetime.now(UTC)

    async def stop(self, graceful: bool = True) -> None:
        """Stop the running worker loop."""
        if self._status == "stopped":
            return

        self._status = "stopping"

        if graceful and self._invoker is not None:
            self._invoker.terminate()

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

        if self._log_handler is not None:
            logging.getLogger("worker").removeHandler(self._log_handler)
            self._log_handler = None

        self._status = "stopped"
        self._task = None
        self._invoker = None
        self.started_at = None

    async def restart(self, graceful: bool = True) -> None:
        """Stop then start the worker."""
        await self.stop(graceful=graceful)
        await self.start()

    async def update_settings(self, settings: WorkerSettings) -> None:
        """Update settings, triggering a graceful restart if currently running."""
        self._settings = settings
        if self._status == "running":
            await self.restart(graceful=True)
