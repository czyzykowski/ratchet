"""Squash merge execution branch and advance task to deployed."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Squash merge execution branch and advance task to deployed."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID")
    parser.add_argument(
        "--branch",
        default="develop",
        help="Target branch to merge into (default: develop)",
    )
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Skip all git operations and advance task directly to deployed status",
    )
    parser.add_argument(
        "--skip-deploy-hooks",
        action="store_true",
        help="Skip deploy hook execution entirely",
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    try:
        task_id = UUID(args.task_id)
    except ValueError:
        print(f"Error: invalid task-id: {args.task_id!r}", file=sys.stderr)
        sys.exit(1)

    from core import events as ev
    from core.db import close_pool
    from core.project_manager import ProjectManager
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        state_machine = TaskStateMachine(store)
        project_manager = ProjectManager(store)

        current_status = await state_machine.get_current_status(task_id)
        if current_status is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        if current_status != ev.READY_FOR_DEPLOYMENT:
            print(
                f"Error: task must be in ready_for_deployment status, got {current_status!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        task_events = await store.get_events(task_id, "task")
        title = ""
        project_id = None
        for event in task_events:
            if event.event_type == ev.TASK_CREATED:
                title = event.payload.get("title", "")
                pid_str = event.payload.get("project_id")
                if pid_str:
                    project_id = UUID(pid_str)
                break

        if project_id is None:
            print("Error: could not determine project_id for task.", file=sys.stderr)
            sys.exit(1)

        project = await project_manager.get_project(project_id)
        if project is None:
            print(f"Error: project {project_id} not found.", file=sys.stderr)
            sys.exit(1)

        if not args.skip_merge:
            execution_events = await store.get_events(task_id, "task_executions")
            branch_name = None
            spec_id = None
            for event in reversed(execution_events):
                if event.event_type == ev.EXECUTION_STARTED:
                    bn = event.payload.get("branch_name")
                    si = event.payload.get("spec_id")
                    if bn:
                        branch_name = bn
                        spec_id = UUID(si) if si else None
                        break

            if branch_name is None:
                print(
                    "Error: branch_name not found in execution events for this task.",
                    file=sys.stderr,
                )
                sys.exit(1)

            local_path = project.local_path
            target_branch = args.branch

            try:
                subprocess.run(
                    ["git", "checkout", target_branch],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                print(f"Error: git checkout failed: {exc.stderr.decode()}", file=sys.stderr)
                sys.exit(1)

            try:
                subprocess.run(
                    ["git", "merge", "--squash", branch_name],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as merge_exc:
                merge_output = merge_exc.stderr.decode()
                print(
                    "Merge conflict detected, attempting Claude-assisted resolution...",
                    file=sys.stderr,
                )

                # Get list of conflicted files
                conflict_result = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=U"],
                    cwd=local_path,
                    capture_output=True,
                    text=True,
                )
                conflicted_files = [
                    f for f in conflict_result.stdout.strip().splitlines() if f
                ]

                # Fetch spec content
                from uuid import uuid4

                from core.context_assembler import (
                    ExecutionContext,
                    build_conflict_resolution_prompt,
                    read_intent,
                )
                from core.invoker import ClaudeCodeInvoker

                spec_content = ""
                if spec_id is not None:
                    spec_events = await store.get_events(spec_id, "spec")
                    for spec_event in spec_events:
                        if spec_event.event_type == ev.SPEC_CREATED:
                            spec_content = spec_event.payload.get("content", "")
                            break

                intent_content = read_intent(local_path)
                prompt = build_conflict_resolution_prompt(
                    intent_content=intent_content,
                    spec_content=spec_content,
                    conflicted_files=conflicted_files,
                    merge_output=merge_output,
                )

                resolution_execution_id = uuid4()
                context = ExecutionContext(
                    execution_id=resolution_execution_id,
                    task_id=task_id,
                    spec_id=spec_id or task_id,
                    worktree_path=local_path,
                    prompt=prompt,
                )
                result = ClaudeCodeInvoker().invoke(context)

                if result.status == "completed":
                    print("Conflict resolution succeeded, continuing deployment.", file=sys.stderr)
                else:
                    subprocess.run(
                        ["git", "merge", "--abort"],
                        cwd=local_path,
                        capture_output=True,
                    )
                    failure_reason = (
                        f"Merge conflict: {merge_output}\n"
                        f"Conflict resolution failed: {result.failure_reason}"
                    )
                    print(f"Error: {failure_reason}", file=sys.stderr)
                    try:
                        await state_machine.transition(
                            task_id,
                            ev.BLOCKED,
                            extra_payload={"failure_reason": failure_reason},
                        )
                    except InvalidTransitionError as exc:
                        print(f"Error transitioning to blocked: {exc}", file=sys.stderr)
                    sys.exit(1)

            commit_msg = f"feat: {title} (task/{task_id})"
            try:
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                print(f"Error: git commit failed: {exc.stderr.decode()}", file=sys.stderr)
                sys.exit(1)

            try:
                subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                print(
                    f"Warning: git branch -d failed: {exc.stderr.decode()}", file=sys.stderr
                )

        from core.qa_runner import load_deploy_config, run_deploy_steps

        hook_summary: str
        if args.skip_deploy_hooks:
            hook_summary = "(deploy hooks skipped)"
        else:
            deploy_config = load_deploy_config(project.local_path)
            if deploy_config is None or not deploy_config.steps:
                hook_summary = "(no deploy hooks)"
            else:
                hook_results = run_deploy_steps(deploy_config, project.local_path)
                failed_count = sum(1 for r in hook_results if r.returncode != 0)
                await store.append_event(
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
                hook_summary = f"({len(hook_results)} deploy hooks run, {failed_count} failed)"

        try:
            await state_machine.transition(task_id, ev.DEPLOYED)
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.skip_merge:
            print(f"Deployed task {title!r} — skipped merge {hook_summary}")
        else:
            print(f"Deployed task {title!r} — merged to {args.branch} {hook_summary}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
