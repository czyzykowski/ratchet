"""Create a new task in ready_for_spec status."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID, uuid4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new task in ready_for_spec status.")
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument("--title", required=True, help="Task title")
    parser.add_argument(
        "--depends-on",
        default=None,
        help="Comma-separated list of upstream task UUIDs this task depends on.",
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

    from core import events as ev
    from core.db import close_pool
    from core.store import PostgresStore

    store = PostgresStore()
    try:
        task_id = uuid4()

        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_CREATED,
            payload={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "title": args.title,
                "status": ev.READY_FOR_SPEC,
            },
        )

        # Dual-write to project_tasks registry so worker can discover tasks by project.
        await store.append_event(
            aggregate_id=project_id,
            aggregate_type="project_tasks",
            event_type=ev.TASK_CREATED,
            payload={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "title": args.title,
            },
        )

        if dep_uuids:
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_DEPENDENCY_ADDED,
                payload={"depends_on": dep_uuids},
            )

        print(f"Created task: {args.title}")
        print(f"Task ID: {task_id}")
        print(f"Status: {ev.READY_FOR_SPEC}")
        if dep_uuids:
            print(f"Depends on: {', '.join(dep_uuids)}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
