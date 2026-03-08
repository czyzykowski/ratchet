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
        "--branch", default="develop", help="Target branch to merge into (default: develop)"
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

        execution_events = await store.get_events(task_id, "task_executions")
        branch_name = None
        for event in reversed(execution_events):
            if event.event_type == ev.EXECUTION_STARTED:
                bn = event.payload.get("branch_name")
                if bn:
                    branch_name = bn
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
        except subprocess.CalledProcessError as exc:
            print(f"Error: git merge --squash failed: {exc.stderr.decode()}", file=sys.stderr)
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
                ["git", "branch", "-d", branch_name],
                cwd=local_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"Warning: git branch -d failed: {exc.stderr.decode()}", file=sys.stderr)

        try:
            await state_machine.transition(task_id, ev.DEPLOYED)
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Deployed task {title!r} — merged to {target_branch}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
