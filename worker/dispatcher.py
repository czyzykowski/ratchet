"""ProjectDispatcher: owns task discovery, priority dispatch, and manager construction."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess as _subprocess
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from core import events as ev
from core import qa_manager
from core.context_assembler import (
    ContextAssembler,
    ExecutionContext,
    read_intent,
)
from core.execution_manager import ExecutionManager
from core.invoker import ClaudeCodeInvoker
from core.managers import Managers
from core.merge import squash_merge
from core.models import Project, Spec, Task
from core.models_config import WORKER_MODEL
from core.qa_runner import (
    build_review_prompt,
    check_baseline_qa,
    get_git_diff,
    load_deployment_config,
    load_merge_config,
    load_qa_config,
    parse_review_output,
    run_auto_fixes,
    run_merge_steps,
    run_qa_steps,
)
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_executor import ExecutionOutcome, ExecutionResult, TaskExecutor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worktree helpers (moved from runner.py)
# ---------------------------------------------------------------------------


class QAWorktreeError(Exception):
    """Raised when a QA worktree cannot be created."""


def _find_existing_worktree(project_path: str, branch: str) -> str | None:
    """Return the path of an existing worktree checked out on branch, or None."""
    result = _subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("branch ") and current_path:
            reported = line[len("branch "):]
            if reported == f"refs/heads/{branch}" or reported == branch:
                return current_path
    return None


def _safe_symlink(src: str, dst: str) -> None:
    """Create a relative symlink dst -> src, skipping if dst exists or src == dst."""
    abs_src = os.path.abspath(src)
    abs_dst = os.path.abspath(dst)
    if abs_src == abs_dst:
        logger.warning("Skipping symlink: src == dst (%s)", abs_src)
        return
    if os.path.lexists(dst):
        return
    if not os.path.exists(src):
        return
    relative_target = os.path.relpath(abs_src, os.path.dirname(abs_dst))
    os.symlink(relative_target, dst)


def _create_baseline_worktree(project_path: str) -> str:
    """Create a temporary worktree on HEAD for baseline QA."""
    import uuid as _uuid

    baseline_id = str(_uuid.uuid4())[:8]
    wt_path = os.path.join(project_path, ".worktrees", f"baseline-{baseline_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", "--detach", wt_path],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise QAWorktreeError(
            f"git worktree add (baseline) failed: {result.stderr.strip()}"
        )
    _safe_symlink(os.path.join(project_path, ".venv"), os.path.join(wt_path, ".venv"))
    _safe_symlink(
        os.path.join(project_path, "node_modules"), os.path.join(wt_path, "node_modules")
    )
    _safe_symlink(
        os.path.join(project_path, "web", "spa", "node_modules"),
        os.path.join(wt_path, "web", "spa", "node_modules"),
    )
    _safe_symlink(os.path.join(project_path, ".env"), os.path.join(wt_path, ".env"))
    _safe_symlink(os.path.join(project_path, ".deno"), os.path.join(wt_path, ".deno"))
    return wt_path


def _create_qa_worktree(project_path: str, execution_branch: str) -> tuple[str, bool]:
    """Create a temporary worktree on the execution branch for QA testing."""
    import uuid as _uuid

    qa_id = str(_uuid.uuid4())[:8]
    qa_path = os.path.join(project_path, ".worktrees", f"qa-{qa_id}")
    result = _subprocess.run(
        ["git", "worktree", "add", qa_path, execution_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        existing = _find_existing_worktree(project_path, execution_branch)
        if existing:
            logger.info(
                "Reusing existing worktree at %s for branch %s",
                existing,
                execution_branch,
            )
            return existing, False
        raise QAWorktreeError(
            f"git worktree add failed for branch {execution_branch!r}:"
            f" {result.stderr.strip()}"
        )
    _safe_symlink(os.path.join(project_path, ".venv"), os.path.join(qa_path, ".venv"))
    _safe_symlink(
        os.path.join(project_path, "node_modules"), os.path.join(qa_path, "node_modules")
    )
    _safe_symlink(os.path.join(project_path, ".env"), os.path.join(qa_path, ".env"))
    _safe_symlink(os.path.join(project_path, ".deno"), os.path.join(qa_path, ".deno"))
    return qa_path, True


def _remove_qa_worktree(project_path: str, qa_path: str) -> None:
    """Remove a temporary QA worktree."""
    try:
        _subprocess.run(
            ["git", "worktree", "remove", "--force", qa_path],
            cwd=project_path,
            check=False,
            capture_output=True,
        )
    except Exception:
        logger.warning("Failed to remove QA worktree at %s", qa_path)


# ---------------------------------------------------------------------------
# Pure helpers (moved from runner.py)
# ---------------------------------------------------------------------------


def _has_pending_baseline_qa_failure(task_events: list[Any]) -> bool:
    """True if the most recent baseline QA failure has no retry or force-execute after it."""
    last_failed_seq: int | None = None
    last_cleared_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type in (ev.TASK_BASELINE_QA_RETRY, ev.TASK_FORCE_EXECUTE):
            last_cleared_seq = max(last_cleared_seq or 0, event.sequence)
    if last_failed_seq is None:
        return False
    return last_cleared_seq is None or last_failed_seq > last_cleared_seq


def _should_skip_baseline_qa(task_events: list[Any]) -> bool:
    """True if force-execute (not mere retry) was requested after the last baseline QA failure."""
    last_failed_seq: int | None = None
    last_force_seq: int | None = None
    for event in task_events:
        if event.event_type == ev.TASK_BASELINE_QA_FAILED:
            last_failed_seq = event.sequence
        elif event.event_type == ev.TASK_FORCE_EXECUTE:
            last_force_seq = event.sequence
    if last_force_seq is None:
        return False
    return last_failed_seq is None or last_force_seq > last_failed_seq


async def _apply_execution_outcome(
    state_machine: TaskStateMachine,
    task: Task,
    result: ExecutionResult,
) -> None:
    """Translate an ExecutionResult into task state transitions."""
    if result.outcome == ExecutionOutcome.COMPLETED:
        await state_machine.transition(task.id, ev.READY_FOR_QA)
    else:
        failure_reason = result.failure_reason or result.outcome.value
        await state_machine.transition(
            task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
        )


def _gh_command(args: list[str], project_local_path: str) -> _subprocess.CompletedProcess[str]:
    """Run a gh command, wrapped in nix develop if flake.nix is present."""
    from pathlib import Path

    if (Path(project_local_path) / "flake.nix").exists():
        return _subprocess.run(
            ["nix", "develop", "--command", "gh"] + args,
            cwd=project_local_path,
            capture_output=True,
            text=True,
        )
    return _subprocess.run(
        ["gh"] + args,
        cwd=project_local_path,
        capture_output=True,
        text=True,
    )


def _get_qa_fix_attempts(task_events: list[Any]) -> int:
    """Read qa_fix_attempts from the latest TASK_STATUS_CHANGED event payload (default 0)."""
    attempts = 0
    for event in reversed(task_events):
        if event.event_type == ev.TASK_STATUS_CHANGED:
            val = event.payload.get("qa_fix_attempts")
            if val is not None:
                attempts = int(val)
                break
    return attempts


# ---------------------------------------------------------------------------
# DispatchResult and ProjectDispatcher
# ---------------------------------------------------------------------------


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
        self._project_manager = m.projects
        self._task_manager = m.tasks
        self._spec_manager = m.specs
        self._state_machine = m.state_machine

    async def _find_tasks(
        self,
        statuses: set[str],
        project_id: UUID | None = None,
        *,
        predicate: Any | None = None,
        skip_project_if_status: str | None = None,
    ) -> list[tuple[Task, Project, list[Any]]]:
        active_projects = await self._project_manager.list_projects()
        if project_id is not None:
            active_projects = [p for p in active_projects if p.id == project_id]

        results: list[tuple[Task, Project, list[Any]]] = []

        for project in active_projects:
            project_task_events = await self._store.get_events(project.id, "project_tasks")

            task_ids_seen: set[UUID] = set()
            task_ids_ordered: list[UUID] = []
            for event in project_task_events:
                tid_str = event.payload.get("task_id")
                if tid_str:
                    tid = UUID(tid_str)
                    if tid not in task_ids_seen:
                        task_ids_seen.add(tid)
                        task_ids_ordered.append(tid)

            # Check for skip_project_if_status
            if skip_project_if_status is not None:
                skip = False
                all_tasks: list[tuple[Task | None, UUID]] = []
                for task_id in task_ids_ordered:
                    task = await self._task_manager.get_task(task_id)
                    all_tasks.append((task, task_id))
                    if task is not None and task.status == skip_project_if_status:
                        skip = True
                        break
                if skip:
                    continue
                # Use pre-loaded tasks
                for task, task_id in all_tasks:
                    if task is None or task.status not in statuses:
                        continue
                    task_events: list[Any] = []
                    if predicate is not None:
                        task_events = await self._store.get_events(task_id, "task")
                        if not predicate(task, task_events):
                            continue
                    results.append((task, project, task_events))
            else:
                for task_id in task_ids_ordered:
                    task = await self._task_manager.get_task(task_id)
                    if task is None or task.status not in statuses:
                        continue
                    task_events = []
                    if predicate is not None:
                        task_events = await self._store.get_events(task_id, "task")
                        if not predicate(task, task_events):
                            continue
                    results.append((task, project, task_events))

        results.sort(key=lambda r: r[0].created_at)
        return results

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
        candidates = await self._find_tasks(
            statuses={ev.READY_FOR_IMPLEMENTATION, ev.WAITING_FOR_INPUT},
            project_id=project_id,
            predicate=lambda t, _: set(t.required_capabilities).issubset(
                set(self._capabilities)
            ),
            skip_project_if_status=ev.IN_PROGRESS,
        )

        # Async secondary filters
        task: Task | None = None
        project: Project | None = None
        spec: Spec | None = None
        for t, p, task_events in candidates:
            if t.status == ev.WAITING_FOR_INPUT:
                pending = await qa_manager.get_pending_question(self._store, t.id)
                if pending is not None:
                    continue

            if t.depends_on:
                unmet = False
                for dep_id_str in t.depends_on:
                    try:
                        dep_id = UUID(dep_id_str)
                    except ValueError:
                        logger.debug(
                            "Task %s has invalid dep UUID %s, skipping",
                            t.id,
                            dep_id_str,
                        )
                        unmet = True
                        break
                    dep_task = await self._task_manager.get_task(dep_id)
                    if dep_task is None or dep_task.status != ev.DEPLOYED:
                        logger.debug(
                            "Task %s skipped: dep %s not deployed", t.id, dep_id_str
                        )
                        unmet = True
                        break
                if unmet:
                    continue

            s = await self._spec_manager.get_current_spec(t.id)
            if s is None:
                logger.warning("Task %s has no spec assigned, skipping", t.id)
                continue

            task, project, spec = t, p, s
            break

        if task is None or project is None or spec is None:
            logger.info("No tasks ready for implementation.")
            return DispatchResult(action="idle")

        # Resume path for waiting_for_input tasks
        if task.status == ev.WAITING_FOR_INPUT:
            execution_manager = ExecutionManager(self._store, project.local_path)
            execution = await execution_manager.get_current_execution(task.id)
            if execution is None:
                logger.error(
                    "No running execution for waiting_for_input task=%s, blocking",
                    task.id,
                )
                await self._state_machine.transition(
                    task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
                )
                await self._state_machine.transition(task.id, ev.BLOCKED)
                return DispatchResult(action="impl", task_id=task.id, success=False)
            await self._state_machine.transition(
                task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
            )
            logger.info(
                "Resuming execution after input: task=%s execution=%s",
                task.id,
                execution.id,
            )
            executor = TaskExecutor(
                execution_manager, ContextAssembler(self._store), self._invoker
            )
            exec_result = await executor.resume(task, project, execution.id)
            await _apply_execution_outcome(self._state_machine, task, exec_result)
            return DispatchResult(action="impl", task_id=task.id)

        # Baseline QA check
        task_events = await self._store.get_events(task.id, "task")
        skip_baseline = _should_skip_baseline_qa(task_events)

        if not skip_baseline:
            ratchet_yaml = (
                project.ratchet_yaml if project.config_source == "db" else None
            )
            try:
                baseline_worktree = await asyncio.to_thread(
                    _create_baseline_worktree, project.local_path
                )
            except QAWorktreeError as exc:
                logger.warning(
                    "Baseline QA worktree creation failed for project=%s: %s"
                    " — skipping baseline check",
                    project.name,
                    exc,
                )
                baseline_failures: list[Any] = []
            else:
                try:
                    baseline_failures = await asyncio.to_thread(
                        check_baseline_qa, baseline_worktree, ratchet_yaml
                    )
                finally:
                    await asyncio.to_thread(
                        _remove_qa_worktree, project.local_path, baseline_worktree
                    )
            if baseline_failures:
                combined = "\n\n".join(
                    f"Step '{r.step_name}':\n{r.output}" for r in baseline_failures
                )
                logger.warning(
                    "Baseline QA failed for project=%s — skipping task=%s.\n%s",
                    project.name,
                    task.id,
                    combined,
                )
                if not _has_pending_baseline_qa_failure(task_events):
                    await self._store.append_event(
                        aggregate_id=task.id,
                        aggregate_type="task",
                        event_type=ev.TASK_BASELINE_QA_FAILED,
                        payload={"failure_output": combined},
                    )
                return DispatchResult(action="idle")

        execution_manager = ExecutionManager(self._store, project.local_path)

        logger.info(
            "Starting execution: task=%s project=%s spec=%s",
            task.id,
            project.name,
            spec.id,
        )

        await self._state_machine.transition(
            task.id, ev.IN_PROGRESS, extra_payload={"qa_fix_attempts": 0}
        )

        executor = TaskExecutor(
            execution_manager, ContextAssembler(self._store), self._invoker
        )
        exec_result = await executor.execute(task, spec, project)

        logger.info(
            "Execution %s: task=%s execution=%s trace=%s",
            exec_result.outcome.value,
            task.id,
            exec_result.execution_id,
            exec_result.trace_id,
        )

        await _apply_execution_outcome(self._state_machine, task, exec_result)
        return DispatchResult(action="impl", task_id=task.id)

    async def qa_once(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass QA execution."""
        candidates = await self._find_tasks(
            statuses={ev.READY_FOR_QA},
            project_id=project_id,
        )

        task: Task | None = None
        project: Project | None = None
        spec: Spec | None = None
        for t, p, _ in candidates:
            s = await self._spec_manager.get_current_spec(t.id)
            if s is None:
                logger.warning("Task %s has no spec assigned, skipping", t.id)
                continue
            task, project, spec = t, p, s
            break

        if task is None or project is None or spec is None:
            logger.info("No QA tasks ready.")
            return DispatchResult(action="idle")

        # Load QA config
        qa_ratchet_yaml = (
            project.ratchet_yaml if project.config_source == "db" else None
        )
        config = load_qa_config(project.local_path, qa_ratchet_yaml)
        if config is None:
            logger.info(
                "No QA config found for task=%s, transitioning to ready_for_merge",
                task.id,
            )
            await self._state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
            return DispatchResult(action="qa", task_id=task.id)

        # Look up execution branch
        execution_events = await self._store.get_events(task.id, "task_executions")
        execution_branch: str | None = None
        for event in reversed(execution_events):
            if event.event_type == ev.EXECUTION_STARTED:
                bn = event.payload.get("branch_name")
                if bn:
                    execution_branch = bn
                break

        if not execution_branch:
            failure_reason = "QA cannot run: no execution branch found for task"
            logger.error("task=%s: %s", task.id, failure_reason)
            await self._state_machine.transition(
                task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
            )
            return DispatchResult(action="qa", task_id=task.id, success=False)

        try:
            qa_path, qa_worktree_owned = await asyncio.to_thread(
                _create_qa_worktree, project.local_path, execution_branch
            )
        except QAWorktreeError as exc:
            failure_reason = f"QA worktree creation failed: {exc}"
            logger.error("task=%s: %s", task.id, failure_reason)
            await self._state_machine.transition(
                task.id, ev.BLOCKED, extra_payload={"failure_reason": failure_reason}
            )
            return DispatchResult(action="qa", task_id=task.id, success=False)

        try:
            await asyncio.to_thread(run_auto_fixes, config, qa_path)
            step_results = await asyncio.to_thread(run_qa_steps, config, qa_path)

            failed_steps = [r for r in step_results if r.returncode != 0]

            if failed_steps:
                task_events = await self._store.get_events(task.id, "task")
                qa_fix_attempts = _get_qa_fix_attempts(task_events)

                if qa_fix_attempts >= config.max_fix_attempts:
                    combined_output = "\n\n".join(
                        f"Step '{r.step_name}':\n{r.output}" for r in failed_steps
                    )
                    logger.info(
                        "Max fix attempts reached for task=%s, transitioning to blocked",
                        task.id,
                    )
                    await self._state_machine.transition(
                        task.id,
                        ev.BLOCKED,
                        extra_payload={
                            "failure_reason": combined_output,
                            "qa_fix_attempts": qa_fix_attempts,
                        },
                    )
                    return DispatchResult(action="qa", task_id=task.id)

                failed_output = "\n\n".join(
                    f"Step '{r.step_name}' (exit {r.returncode}):\n{r.output}"
                    for r in failed_steps
                )
                fix_prompt = (
                    f"{spec.content}\n\n"
                    f"QA tools found errors after implementation was marked complete:"
                    f"\n{failed_output}"
                )
                fix_context = ExecutionContext(
                    execution_id=uuid4(),
                    task_id=task.id,
                    spec_id=spec.id,
                    worktree_path=qa_path,
                    prompt=fix_prompt,
                )
                logger.info(
                    "Auto-fix attempt %d/%d for task=%s",
                    qa_fix_attempts + 1,
                    config.max_fix_attempts,
                    task.id,
                )
                await asyncio.to_thread(self._invoker.invoke, fix_context)
                await self._state_machine.transition(
                    task.id,
                    ev.READY_FOR_QA,
                    extra_payload={"qa_fix_attempts": qa_fix_attempts + 1},
                )
                return DispatchResult(action="qa", task_id=task.id)
        finally:
            if qa_worktree_owned:
                await asyncio.to_thread(
                    _remove_qa_worktree, project.local_path, qa_path
                )

        # All steps pass — run Claude review
        diff = get_git_diff(project.local_path, execution_branch)
        review_prompt = build_review_prompt(spec.content, diff, step_results)

        review_proc = await asyncio.to_thread(
            _subprocess.run,
            ["claude", "-p", "--model", WORKER_MODEL, "--allowedTools", "Bash,Read,Glob,Grep"],
            input=review_prompt,
            cwd=project.local_path,
            capture_output=True,
            text=True,
        )
        review_output = review_proc.stdout + review_proc.stderr

        review_result = parse_review_output(review_output)

        if review_result.verdict == "passed":
            logger.info("QA review passed for task=%s", task.id)
            await self._state_machine.transition(task.id, ev.READY_FOR_DEPLOYMENT)
        else:
            logger.info("QA review failed for task=%s", task.id)
            await self._state_machine.transition(
                task.id,
                ev.BLOCKED,
                extra_payload={"failure_reason": review_result.full_output},
            )

        return DispatchResult(action="qa", task_id=task.id)

    async def merge_once(self, project_id: UUID | None = None) -> DispatchResult:
        """Single-pass auto-merge for local deployment mode."""

        def _no_auto_merge_failed(task: Task, task_events: list[Any]) -> bool:
            return not any(
                e.event_type == ev.TASK_AUTO_MERGE_FAILED for e in task_events
            )

        candidates = await self._find_tasks(
            statuses={ev.READY_FOR_DEPLOYMENT},
            project_id=project_id,
            predicate=_no_auto_merge_failed,
        )

        # Post-filter: deployment mode, execution branch
        merge_candidates: list[tuple[Task, str, str, str, str]] = []
        for task, project, _ in candidates:
            ratchet_yaml = (
                project.ratchet_yaml if project.config_source == "db" else None
            )
            deployment_config = load_deployment_config(
                project.local_path, ratchet_yaml
            )
            if deployment_config.mode != "local":
                continue

            execution_events = await self._store.get_events(
                task.id, "task_executions"
            )
            branch_name: str | None = None
            spec_id_val: UUID | None = None
            for event in reversed(execution_events):
                if event.event_type == ev.EXECUTION_STARTED:
                    bn = event.payload.get("branch_name")
                    si = event.payload.get("spec_id")
                    if bn:
                        branch_name = bn
                        spec_id_val = UUID(si) if si else None
                        break

            if branch_name is None:
                logger.warning(
                    "merge_once: task=%s has no execution branch, skipping",
                    task.id,
                )
                continue

            spec_content = ""
            if spec_id_val is not None:
                spec_events = await self._store.get_events(spec_id_val, "spec")
                for spec_event in spec_events:
                    if spec_event.event_type == ev.SPEC_CREATED:
                        spec_content = spec_event.payload.get("content", "")
                        break

            merge_candidates.append(
                (
                    task,
                    project.local_path,
                    deployment_config.base_branch,
                    branch_name,
                    spec_content,
                )
            )

        if not merge_candidates:
            logger.info("merge_once: no eligible tasks for auto-merge.")
            return DispatchResult(action="idle")

        merge_candidates.sort(key=lambda c: c[0].created_at)
        task_m, local_path, target_branch, execution_branch, spec_content = (
            merge_candidates[0]
        )
        task_id = task_m.id

        try:
            intent_content = read_intent(local_path)
        except Exception as exc:
            logger.warning(
                "merge_once: could not read INTENT.md for task=%s: %s",
                task_id,
                exc,
            )
            intent_content = ""

        logger.info(
            "merge_once: merging task=%s branch=%s into %s",
            task_id,
            execution_branch,
            target_branch,
        )
        merge_result = await asyncio.to_thread(
            squash_merge,
            local_path,
            execution_branch,
            target_branch,
            task_m.title,
            task_id,
            self._store,
            self._invoker,
            spec_content,
            intent_content,
        )

        if not merge_result.success:
            failure_reason = merge_result.failure_reason or "merge failed"
            logger.warning(
                "merge_once: merge failed for task=%s: %s", task_id, failure_reason
            )
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_AUTO_MERGE_FAILED,
                payload={"failure_reason": failure_reason},
            )
            return DispatchResult(
                action="merge", task_id=task_id, success=False, detail=failure_reason
            )

        merge_config = load_merge_config(local_path)
        if merge_config is not None and merge_config.steps:
            hook_results = await asyncio.to_thread(
                run_merge_steps, merge_config, local_path
            )
            await self._store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPLOY_HOOKS_RUN,
                payload={
                    "steps": [
                        {
                            "name": r.step_name,
                            "command": r.command,
                            "returncode": r.returncode,
                            "output": r.output,
                        }
                        for r in hook_results
                    ]
                },
            )

        await self._state_machine.transition(task_id, ev.DEPLOYED)
        logger.info(
            "merge_once: task=%s deployed (merge_commit_sha=%s)",
            task_id,
            merge_result.new_sha,
        )
        return DispatchResult(action="merge", task_id=task_id)

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
        candidates = await self._find_tasks(statuses={ev.READY_FOR_DEPLOYMENT})

        for task, project, _ in candidates:
            task_events = await self._store.get_events(task.id, "task")
            pr_number: int | None = None
            for event in reversed(task_events):
                if event.event_type == ev.TASK_PR_CREATED:
                    pr_number = event.payload.get("pr_number")
                    break

            if pr_number is None:
                continue

            cwd = project.local_path or os.getcwd()
            _pr_num = int(pr_number)
            _cwd = str(cwd)
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _gh_command(
                    ["pr", "view", str(_pr_num), "--json", "state,mergeCommit"],
                    _cwd,
                ),
            )
            if proc.returncode != 0:
                logger.warning(
                    "gh pr view failed for task=%s pr=%s: %s",
                    task.id,
                    pr_number,
                    proc.stderr.strip(),
                )
                continue

            try:
                import json as _json

                state_data = _json.loads(proc.stdout)
                pr_state = state_data.get("state", "")
            except Exception:
                logger.warning(
                    "Failed to parse gh pr view output for task=%s", task.id
                )
                continue

            if pr_state == "MERGED":
                logger.info(
                    "PR %s merged — transitioning task=%s to deployed",
                    pr_number,
                    task.id,
                )
                sha = (state_data.get("mergeCommit") or {}).get("oid") or None
                extra_payload = {"merge_commit_sha": sha} if sha else None
                await self._state_machine.transition(
                    task.id, ev.DEPLOYED, extra_payload=extra_payload
                )

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
