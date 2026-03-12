"""Create a new task in ready_for_spec status."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new task in ready_for_spec status.")
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument("--title", required=True, help="Task title")
    parser.add_argument(
        "--depends-on",
        default=None,
        help="Comma-separated list of upstream task UUIDs this task depends on.",
    )
    parser.add_argument(
        "--capabilities",
        default=None,
        help="Comma-separated list of required capabilities (e.g. os:windows,gpu).",
    )
    return parser.parse_args()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    try:
        project_id = UUID(args.project_id)
    except ValueError:
        print(f"Error: invalid project-id: {args.project_id!r}", file=sys.stderr)
        sys.exit(1)

    dep_uuids: list[str] = []
    if args.depends_on:
        raw_deps = [d.strip() for d in args.depends_on.split(",") if d.strip()]
        for raw in raw_deps:
            try:
                UUID(raw)
            except ValueError:
                print(f"Error: invalid UUID in --depends-on: {raw!r}", file=sys.stderr)
                sys.exit(1)
            dep_uuids.append(raw)

    cap_list = (
        [c.strip() for c in args.capabilities.split(",") if c.strip()] if args.capabilities else []
    )

    from core.db import close_pool
    from core.store import PostgresStore
    from core.task_manager import TaskManager

    store = PostgresStore()
    try:
        task_manager = TaskManager(store)
        task = await task_manager.create_task(
            project_id, args.title, dep_uuids or None, required_capabilities=cap_list or None
        )

        print(f"Created task: {task.title}")
        print(f"Task ID: {task.id}")
        print(f"Status: {task.status}")
        if task.depends_on:
            print(f"Depends on: {', '.join(task.depends_on)}")
        if task.required_capabilities:
            print(f"Capabilities: {', '.join(task.required_capabilities)}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
