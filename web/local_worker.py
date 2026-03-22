"""LocalWorkerManager: spawns and manages a local worker subprocess."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import signal
import sys
from datetime import UTC, datetime

from core.project_manager import ProjectManager
from core.store import Store
from worker.log_buffer import LogBuffer

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class LocalWorkerSettings:
    enabled: bool = True
    capabilities: list[str] = dataclasses.field(default_factory=list)
    port: int = 8000


class LocalWorkerManager:
    """Spawns and manages a local worker subprocess connected via WebSocket.

    The subprocess runs ``python -m worker --remote ws://localhost:{port}/ws/worker``
    and connects back to the web process as a standard remote worker.
    """

    def __init__(
        self,
        store: Store,
        settings: LocalWorkerSettings | None = None,
        log_buffer: LogBuffer | None = None,
    ) -> None:
        self._store = store
        self._settings: LocalWorkerSettings = (
            settings if settings is not None else LocalWorkerSettings()
        )
        self.log_buffer: LogBuffer = log_buffer if log_buffer is not None else LogBuffer()
        self._process: asyncio.subprocess.Process | None = None
        self._status: str = "stopped"
        self.started_at: datetime | None = None
        self.error_message: str | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def settings(self) -> LocalWorkerSettings:
        return dataclasses.replace(self._settings)

    @property
    def pid(self) -> int | None:
        if self._process is not None:
            return self._process.pid
        return None

    async def start(self) -> None:
        """Spawn the worker subprocess."""
        if self._status in ("running", "starting"):
            raise RuntimeError("Worker already running")

        if not self._settings.enabled:
            self._status = "stopped"
            return

        self._status = "starting"
        self.error_message = None

        # Discover active projects for --projects arg
        projects = await ProjectManager(self._store).list_projects()
        project_pairs = ",".join(f"{p.id}:{p.local_path}" for p in projects)

        port = self._settings.port
        ws_url = f"ws://localhost:{port}/ws/worker"

        cmd = [sys.executable, "-m", "worker", "--remote", ws_url]
        if project_pairs:
            cmd += ["--projects", project_pairs]
        if self._settings.capabilities:
            cmd += ["--capabilities", ",".join(self._settings.capabilities)]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            self._status = "error"
            self.error_message = f"failed to start subprocess: {exc}"
            logger.error("LocalWorkerManager: failed to start subprocess: %s", exc)
            return

        self._process = process
        self._status = "running"
        self.started_at = datetime.now(UTC)
        logger.info("LocalWorkerManager: started subprocess pid=%d", process.pid)

        if process.stdout:
            self._stdout_task = asyncio.create_task(
                self._pump_stream(process.stdout, "INFO")
            )
        if process.stderr:
            self._stderr_task = asyncio.create_task(
                self._pump_stream(process.stderr, "ERROR")
            )
        self._monitor_task = asyncio.create_task(self._monitor_process())

    async def _pump_stream(self, stream: asyncio.StreamReader, default_level: str) -> None:
        """Read lines from stream and publish to LogBuffer."""
        try:
            async for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self.log_buffer.append(default_level, text)
        except (asyncio.CancelledError, Exception):
            pass

    async def _monitor_process(self) -> None:
        """Wait for subprocess exit; mark status as error on unexpected exit."""
        if self._process is None:
            return
        try:
            returncode = await self._process.wait()
            if self._status == "running":
                self._status = "error"
                self.error_message = f"subprocess exited with code {returncode}"
                logger.error(
                    "LocalWorkerManager: subprocess exited unexpectedly with code %d",
                    returncode,
                )
        except asyncio.CancelledError:
            pass

    async def stop(self, graceful: bool = True) -> None:
        """Stop the worker subprocess."""
        if self._status == "stopped":
            return

        self._status = "stopping"

        if self._process is not None and self._process.returncode is None:
            if graceful:
                try:
                    self._process.send_signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=10.0)
                    except TimeoutError:
                        logger.warning("LocalWorkerManager: SIGTERM timed out, sending SIGKILL")
                        self._process.kill()
                        await self._process.wait()
                except ProcessLookupError:
                    pass
            else:
                try:
                    self._process.kill()
                    await self._process.wait()
                except ProcessLookupError:
                    pass

        for task in (self._stdout_task, self._stderr_task, self._monitor_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._process = None
        self._stdout_task = None
        self._stderr_task = None
        self._monitor_task = None
        self._status = "stopped"
        self.started_at = None
        logger.info("LocalWorkerManager: stopped")

    async def restart(self, graceful: bool = True) -> None:
        """Stop then start the worker subprocess."""
        await self.stop(graceful=graceful)
        await self.start()

    async def update_settings(self, settings: LocalWorkerSettings) -> None:
        """Update settings, restarting if currently running."""
        self._settings = settings
        if self._status == "running":
            await self.restart(graceful=True)
