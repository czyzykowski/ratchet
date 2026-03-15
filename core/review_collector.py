"""ReviewDataCollector: gathers all data needed by the retrospective review pipeline."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from core import events as ev
from core.execution_manager import _build_execution
from core.invoker import get_traces_dir
from core.models import Event, Execution, Project, ReviewScope, Spec, Task
from core.store import Store

logger = logging.getLogger(__name__)


@dataclass
class CollectedData:
    tasks: list[Task]
    specs_by_task: dict[UUID, list[Spec]]
    executions_by_task: dict[UUID, list[Execution]]
    trace_contents: dict[UUID, str]  # execution_id → content (≤8000 chars)
    qa_failures: list[dict[str, object]]  # {task_id, execution_id, failure_reason, occurred_at}
    status_histories: dict[UUID, list[Event]]  # task_id → TASK_STATUS_CHANGED events
    git_log: dict[UUID, str]  # project_id → output
    project_claude_md: dict[UUID, str]  # project_id → content or ""
    global_claude_md: str  # "" if missing or scope.include_global=False
    ratchet_yaml_content: str | None  # None if file missing
    completion_instructions_content: str  # _COMPLETION_INSTRUCTIONS from context_assembler


class ReviewDataCollector:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def collect(self, scope: ReviewScope, projects: list[Project]) -> CollectedData:
        all_tasks: list[Task] = []
        specs_by_task: dict[UUID, list[Spec]] = {}
        executions_by_task: dict[UUID, list[Execution]] = {}
        trace_contents: dict[UUID, str] = {}
        qa_failures: list[dict[str, object]] = []
        status_histories: dict[UUID, list[Event]] = {}
        git_log: dict[UUID, str] = {}
        project_claude_md: dict[UUID, str] = {}

        traces_dir = get_traces_dir()

        for project in projects:
            # 1. Find task IDs
            project_task_events = await self._store.get_events(project.id, "project_tasks")
            task_ids_seen: set[UUID] = set()
            task_ids: list[UUID] = []
            for event in project_task_events:
                tid_str = event.payload.get("task_id")
                if tid_str:
                    tid = UUID(tid_str)
                    if tid not in task_ids_seen:
                        task_ids_seen.add(tid)
                        task_ids.append(tid)

            for task_id in task_ids:
                # 2. Replay each task
                task_events = await self._store.get_events(task_id, "task")
                task = await _replay_task(task_id, task_events)
                if task is not None:
                    all_tasks.append(task)

                # 3. Status history
                status_histories[task_id] = [
                    e for e in task_events if e.event_type == ev.TASK_STATUS_CHANGED
                ]

                # 4. Spec lineage
                task_spec_events = await self._store.get_events(task_id, "task_spec")
                specs: list[Spec] = []
                for event in task_spec_events:
                    if event.event_type == ev.SPEC_CREATED:
                        p = event.payload
                        specs.append(
                            Spec(
                                id=UUID(p["spec_id"]),
                                task_id=UUID(p["task_id"]),
                                previous_spec_id=(
                                    UUID(p["previous_spec_id"])
                                    if p.get("previous_spec_id")
                                    else None
                                ),
                                content=p["content"],
                                created_at=event.occurred_at,
                            )
                        )
                specs_by_task[task_id] = specs

                # 5. Execution history
                task_exec_events = await self._store.get_events(task_id, "task_executions")
                execution_ids_seen: set[UUID] = set()
                execution_ids: list[UUID] = []
                for event in task_exec_events:
                    eid_str = event.payload.get("execution_id")
                    if eid_str:
                        eid = UUID(eid_str)
                        if eid not in execution_ids_seen:
                            execution_ids_seen.add(eid)
                            execution_ids.append(eid)

                task_executions: list[Execution] = []
                for execution_id in execution_ids:
                    execution_events = await self._store.get_events(execution_id, "execution")
                    execution = _build_execution(execution_events)
                    if execution is not None:
                        task_executions.append(execution)

                        # 6. Trace files
                        trace_path = Path(traces_dir) / f"{execution_id}.md"
                        try:
                            content = trace_path.read_text()
                            trace_contents[execution_id] = content[:8000]
                        except FileNotFoundError:
                            logger.warning(
                                "Trace file not found for execution %s", execution_id
                            )

                        # 7. QA failures from EXECUTION_FAILED events
                        for event in execution_events:
                            if event.event_type == ev.EXECUTION_FAILED:
                                failure_reason = event.payload.get("failure_reason")
                                if failure_reason:
                                    qa_failures.append(
                                        {
                                            "task_id": str(task_id),
                                            "execution_id": str(execution_id),
                                            "failure_reason": failure_reason,
                                            "occurred_at": event.occurred_at,
                                        }
                                    )

                executions_by_task[task_id] = task_executions

                # 7. QA failures from BLOCKED tasks
                if task is not None:
                    final_status = None
                    for event in status_histories[task_id]:
                        final_status = event.payload.get("to_status")
                    if final_status == ev.BLOCKED:
                        qa_failures.append(
                            {
                                "task_id": str(task_id),
                                "execution_id": None,
                                "failure_reason": "Task status is BLOCKED",
                                "occurred_at": task.updated_at,
                            }
                        )

            # 8. git log
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "-100"],
                    cwd=project.local_path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    git_log[project.id] = result.stdout
                else:
                    logger.warning(
                        "git log failed for project %s: %s", project.id, result.stderr
                    )
                    git_log[project.id] = ""
            except Exception:
                logger.warning("git log exception for project %s", project.id, exc_info=True)
                git_log[project.id] = ""

            # 9. project CLAUDE.md
            claude_md_path = Path(project.local_path) / "CLAUDE.md"
            try:
                project_claude_md[project.id] = claude_md_path.read_text()
            except FileNotFoundError:
                logger.warning("CLAUDE.md not found at %s", claude_md_path)
                project_claude_md[project.id] = ""

        # 10. global CLAUDE.md
        global_claude_md = ""
        if scope.include_global:
            global_claude_md_path = Path.home() / ".claude" / "CLAUDE.md"
            try:
                global_claude_md = global_claude_md_path.read_text()
            except FileNotFoundError:
                logger.warning("Global CLAUDE.md not found at %s", global_claude_md_path)
                global_claude_md = ""

        # 11. ratchet.yaml
        if projects:
            base_path = projects[0].local_path
        else:
            base_path = os.getcwd()
        ratchet_yaml_path = Path(base_path) / "ratchet.yaml"
        try:
            ratchet_yaml_content: str | None = ratchet_yaml_path.read_text()
        except FileNotFoundError:
            ratchet_yaml_content = None

        # 12. Completion instructions from context_assembler
        from core.context_assembler import _COMPLETION_INSTRUCTIONS
        completion_instructions_content = _COMPLETION_INSTRUCTIONS

        return CollectedData(
            tasks=all_tasks,
            specs_by_task=specs_by_task,
            executions_by_task=executions_by_task,
            trace_contents=trace_contents,
            qa_failures=qa_failures,
            status_histories=status_histories,
            git_log=git_log,
            project_claude_md=project_claude_md,
            global_claude_md=global_claude_md,
            ratchet_yaml_content=ratchet_yaml_content,
            completion_instructions_content=completion_instructions_content,
        )


async def _replay_task(task_id: UUID, events: list[Event]) -> Task | None:
    """Replay task events to build a Task model."""
    task: Task | None = None
    depends_on: list[str] = []

    for event in events:
        if event.event_type == ev.TASK_CREATED:
            p = event.payload
            task = Task(
                id=task_id,
                project_id=UUID(p["project_id"]),
                title=p.get("title", ""),
                status=p.get("status", ev.READY_FOR_SPEC),
                current_spec_id=None,
                refinement_count=0,
                created_at=event.occurred_at,
                updated_at=event.occurred_at,
                required_capabilities=p.get("required_capabilities", []),
            )
        elif event.event_type == ev.TASK_STATUS_CHANGED and task is not None:
            update: dict[str, object] = {
                "status": event.payload["to_status"],
                "updated_at": event.occurred_at,
            }
            if event.payload.get("to_status") == ev.DEPLOYED:
                update["merge_commit_sha"] = event.payload.get("merge_commit_sha")
            task = task.model_copy(update=update)
        elif event.event_type == ev.TASK_SPEC_ASSIGNED and task is not None:
            spec_id_str = event.payload.get("spec_id")
            current_spec_id = UUID(spec_id_str) if spec_id_str else None
            task = task.model_copy(
                update={
                    "current_spec_id": current_spec_id,
                    "refinement_count": task.refinement_count + 1,
                    "updated_at": event.occurred_at,
                }
            )
        elif event.event_type == ev.TASK_DEPENDENCY_ADDED:
            depends_on.extend(event.payload.get("depends_on", []))
        elif (
            event.event_type in (ev.TASK_TITLE_CHANGED, ev.TASK_TITLE_UPDATED)
            and task is not None
        ):
            task = task.model_copy(
                update={
                    "title": event.payload["title"],
                    "updated_at": event.occurred_at,
                }
            )

    if task is not None:
        task = task.model_copy(update={"depends_on": depends_on})

    return task
