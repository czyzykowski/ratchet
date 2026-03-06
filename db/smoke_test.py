"""Smoke test: append 3 events and query current_tasks materialized view."""

import asyncio
import os
import sys
import uuid


def _get_test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        print("ERROR: TEST_DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)
    return url.replace("postgresql+psycopg://", "postgresql://")


async def main() -> None:
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        print("ERROR: psycopg_pool not installed. Run: pip install psycopg-pool", file=sys.stderr)
        sys.exit(1)

    from core.state_machine import TaskStateMachine
    from core.store import PostgresStore

    url = _get_test_url()
    project_id = uuid.uuid4()
    task_id = uuid.uuid4()

    async with AsyncConnectionPool(url) as pool:
        store = PostgresStore(pool=pool)
        sm = TaskStateMachine(store)

        # Append PROJECT_CREATED event
        await store.append_event(
            aggregate_id=project_id,
            aggregate_type="project",
            event_type="project.created",
            payload={"name": "smoke-project", "repo_path": "/tmp/smoke"},
        )

        # Append TASK_CREATED event (initial status: ready_for_spec)
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type="task.created",
            payload={
                "project_id": str(project_id),
                "title": "smoke task",
                "status": "ready_for_spec",
            },
        )

        # Transition task to spec_qa via state machine
        await sm.transition(task_id, "spec_qa")

        # Refresh materialized views
        await store.refresh_views()

        # Query current_tasks and assert one row exists for our task
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, title, status FROM current_tasks WHERE id = %s",
                (str(task_id),),
            )
            row = await cur.fetchone()

        if row is None:
            print("FAIL: no row found in current_tasks for task_id", task_id, file=sys.stderr)
            sys.exit(1)

        task_uuid, title, status = row
        assert str(task_uuid) == str(task_id), f"id mismatch: {task_uuid} != {task_id}"
        assert title == "smoke task", f"title mismatch: {title!r}"
        assert status == "spec_qa", f"status mismatch: {status!r}"

        print(f"OK: task {task_uuid} | title={title!r} | status={status!r}")

    print("Smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
