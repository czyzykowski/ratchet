"""Unblock a task by transitioning it directly back to ready_for_implementation."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unblock a task, transitioning it back to ready_for_implementation."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID to unblock")
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
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore
    from core.task_manager import TaskManager

    store = PostgresStore()
    try:
        task_manager = TaskManager(store)
        state_machine = TaskStateMachine(store)

        task = await task_manager.get_task(task_id)
        if task is None:
            print(f"Error: task {task_id} not found.", file=sys.stderr)
            sys.exit(1)

        print(f"Task:   {task.title}")
        print(f"Status: {task.status}")

        if task.status == ev.READY_FOR_IMPLEMENTATION:
            print("Task is already ready_for_implementation.")
            return

        try:
            await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)
            print(f"Task {task_id} unblocked → ready_for_implementation.")
        except InvalidTransitionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
