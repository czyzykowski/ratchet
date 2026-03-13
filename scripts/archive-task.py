"""Abandon a task by transitioning it to the 'abandoned' terminal status."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Abandon a task, transitioning it to the 'abandoned' terminal status."
    )
    parser.add_argument("--task-id", required=True, help="Task UUID to abandon")
    parser.add_argument("--reason", default=None, help="Optional reason for abandonment")
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

    answer = input(f"Abandon task {task_id}? [y/N]: ").strip().lower()
    if answer != "y":
        print("Aborted.")
        sys.exit(0)

    from core import events as ev
    from core.db import close_pool
    from core.state_machine import InvalidTransitionError, TaskStateMachine
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        sm = TaskStateMachine(store)
        extra = {"reason": args.reason} if args.reason else None
        await sm.transition(task_id, ev.ABANDONED, extra_payload=extra)
        print(f"Task {task_id} abandoned.")
    except InvalidTransitionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
