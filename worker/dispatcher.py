"""ProjectDispatcher: facade for pipeline dispatch and coordination."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from core import events as ev
from core.invoker import ClaudeCodeInvoker
from core.managers import Managers
from core.models import Project, Task
from core.store import Store
from worker.event_helpers import (
    has_pending_baseline_qa_failure as _has_pending_baseline_qa_failure,  # noqa: F401
)
from worker.event_helpers import (
    should_skip_baseline_qa as _should_skip_baseline_qa,  # noqa: F401
)
from worker.task_finder import TaskFinder
from worker.worktree import QAWorktreeError  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    action: str
    task_id: UUID | None = None
    success: bool = True
    detail: str = ""


class ProjectDispatcher:
    def __init__(
        self,
        store: Store,
        invoker: ClaudeCodeInvoker,
        local_capabilities: list[str] | None = None,
    ) -> None:
        m = Managers(store)
        self._store = m.store
        self._invoker = invoker
        self._capabilities = local_capabilities or []
        self._managers = m
        self._project_manager = m.projects
        self._task_manager = m.tasks
        self._spec_manager = m.specs
        self._state_machine = m.state_machine
        self._task_finder = TaskFinder(m)

        from worker.pipelines.impl import ImplPipeline
        from worker.pipelines.merge import MergePipeline
        from worker.pipelines.qa import QAPipeline

        self._impl = ImplPipeline(m, invoker, self._task_finder, self._capabilities)
        self._qa = QAPipeline(m, invoker, self._task_finder, self._capabilities)
        self._merge = MergePipeline(m, invoker, self._task_finder, self._capabilities)

    async def _find_tasks(
        self,
        statuses: set[str],
        project_id: UUID | None = None,
        *,
        predicate: Any | None = None,
        skip_project_if_status: str | None = None,
    ) -> list[tuple[Task, Project, list[Any]]]:
        return await self._task_finder.find(
            statuses,
            project_id,
            predicate=predicate,
            skip_project_if_status=skip_project_if_status,
        )

    async def recover_orphans(self) -> int:
        orphans = await self._find_tasks(statuses={ev.IN_PROGRESS})
        reset_count = 0
        for task, project, _ in orphans:
            logger.warning(
                "Orphaned in_progress task at startup: task=%s project=%s"
                " — resetting to ready_for_implementation",
                task.id,
                project.name,
            )
            await self._state_machine.transition(
                task.id,
                ev.READY_FOR_IMPLEMENTATION,
                extra_payload={"reason": "worker restart: orphan recovery"},
            )
            reset_count += 1
        if reset_count:
            logger.info(
                "Orphan recovery: reset %d task(s) to ready_for_implementation",
                reset_count,
            )
        return reset_count

    async def list_active_projects(self) -> list[Project]:
        return await self._project_manager.list_projects()

    async def impl_once(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass task execution: find ready task, check baseline QA, execute."""
        return await self._impl.run(project_id)

    async def qa_once(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass QA execution."""
        return await self._qa.run(project_id)

    async def merge_once(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass auto-merge for local deployment mode."""
        return await self._merge.run(project_id)

    async def compile_once(self) -> DispatchResult:
        """Single-pass HLS compilation."""
        from core.compiler import compile_all

        count = await compile_all(self._store)
        if count > 0:
            logger.info("compile_once: compiled %d HLS entries", count)
            return DispatchResult(action="compile")
        logger.info("compile_once: no eligible HLS entries found")
        return DispatchResult(action="idle")

    async def poll_pr_merges(self) -> None:
        """Poll GitHub for merged PRs and transition tasks to deployed."""
        await self._merge.poll_prs()

    async def dispatch(self, project_id: UUID) -> DispatchResult:
        """Run one merge > QA > impl priority pass for a single project."""
        result = await self.merge_once(project_id=project_id)
        if result.action != "idle":
            return result
        result = await self.qa_once(project_id=project_id)
        if result.action != "idle":
            return result
        return await self.impl_once(project_id=project_id)

    async def dispatch_all(
        self, busy_projects: set[UUID] | None = None
    ) -> list[DispatchResult]:
        """Dispatch once per active non-busy project, then compile."""
        if busy_projects is None:
            busy_projects = set()

        projects = await self._project_manager.list_projects()
        to_dispatch = [p.id for p in projects if p.id not in busy_projects]

        results: list[DispatchResult] = []
        if to_dispatch:
            gathered = await asyncio.gather(
                *[self.dispatch(pid) for pid in to_dispatch],
                return_exceptions=True,
            )
            for r in gathered:
                if isinstance(r, DispatchResult):
                    results.append(r)
                else:
                    results.append(
                        DispatchResult(
                            action="idle",
                            success=False,
                            detail=str(r),
                        )
                    )

        compile_result = await self.compile_once()
        results.append(compile_result)
        return results
