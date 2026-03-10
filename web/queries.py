"""Database query functions for web UI — direct reads against materialized views."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID


async def get_task(conn: Any, task_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, project_id, title, status, current_spec_id, refinement_count,"
            " created_at, updated_at FROM current_tasks WHERE id = %s",
            (str(task_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "project_id": row[1],
            "title": row[2],
            "status": row[3],
            "current_spec_id": row[4],
            "refinement_count": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }


async def get_project(conn: Any, project_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, name, repo_url, local_path, status, created_at, updated_at"
            " FROM current_projects WHERE id = %s",
            (str(project_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "repo_url": row[2],
            "local_path": row[3],
            "status": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }


async def get_spec(conn: Any, spec_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, task_id, previous_spec_id, content, created_at"
            " FROM current_specs WHERE id = %s",
            (str(spec_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "task_id": row[1],
            "previous_spec_id": row[2],
            "content": row[3],
            "created_at": row[4],
        }


async def get_task_specs(conn: Any, task_id: UUID) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, task_id, previous_spec_id, content, created_at"
            " FROM current_specs WHERE task_id = %s ORDER BY created_at ASC",
            (str(task_id),),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "task_id": row[1],
            "previous_spec_id": row[2],
            "content": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


async def get_task_executions(conn: Any, task_id: UUID) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, task_id, spec_id, status, failure_reason, branch_name,"
            " started_at, completed_at"
            " FROM current_executions WHERE task_id = %s ORDER BY started_at DESC",
            (str(task_id),),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "task_id": row[1],
            "spec_id": row[2],
            "status": row[3],
            "failure_reason": row[4],
            "branch_name": row[5],
            "started_at": row[6],
            "completed_at": row[7],
        }
        for row in rows
    ]


async def get_task_dependencies(conn: Any, task_id: UUID) -> list[dict[str, Any]]:
    """Return task dicts for all dependencies from the latest dependency_added event."""
    dep_uuids: list[str] = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT payload->'depends_on'
            FROM events
            WHERE aggregate_type = 'task'
              AND aggregate_id = %s
              AND event_type = 'task.dependency_added'
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (str(task_id),),
        )
        row = await cur.fetchone()
        if row is not None and row[0] is not None:
            raw = row[0]
            if isinstance(raw, str):
                raw = json.loads(raw)
            dep_uuids = list(raw)

    if not dep_uuids:
        return []

    deps: list[dict[str, Any]] = []
    async with conn.cursor() as cur:
        for dep_id_str in dep_uuids:
            await cur.execute(
                "SELECT id, project_id, title, status, current_spec_id, refinement_count,"
                " created_at, updated_at FROM current_tasks WHERE id = %s",
                (dep_id_str,),
            )
            dep_row = await cur.fetchone()
            if dep_row is not None:
                deps.append(
                    {
                        "id": dep_row[0],
                        "project_id": dep_row[1],
                        "title": dep_row[2],
                        "status": dep_row[3],
                        "current_spec_id": dep_row[4],
                        "refinement_count": dep_row[5],
                        "created_at": dep_row[6],
                        "updated_at": dep_row[7],
                    }
                )
            else:
                deps.append(
                    {
                        "id": dep_id_str,
                        "title": dep_id_str,
                        "status": None,
                    }
                )
    return deps


async def get_task_qa_failure_reason(conn: Any, task_id: UUID) -> str | None:
    """Return failure_reason from the latest task.status_changed event with to_status='blocked'.

    Returns None if no such event exists or if the event has no failure_reason.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT payload->>'failure_reason'
            FROM events
            WHERE aggregate_type = 'task'
              AND aggregate_id = %s
              AND event_type = 'task.status_changed'
              AND payload->>'to_status' = 'blocked'
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (str(task_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return row[0] or None


async def get_project_tasks(conn: Any, project_id: UUID) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, project_id, title, status, current_spec_id, refinement_count,"
            " created_at, updated_at FROM current_tasks WHERE project_id = %s"
            " ORDER BY created_at ASC",
            (str(project_id),),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "project_id": row[1],
            "title": row[2],
            "status": row[3],
            "current_spec_id": row[4],
            "refinement_count": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
        for row in rows
    ]
