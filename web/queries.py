"""Database query functions for web UI — direct reads against materialized views."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from core import events as ev
from core.models import ChatSession


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


async def get_chat_session_by_context(
    pool: Any, context_id: UUID
) -> ChatSession | None:
    """Look up an existing chat session by context_id using the materialized view.

    Returns a fully reconstructed ChatSession with messages, or None if not found.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, session_type, context_type, created_at"
                " FROM current_chat_sessions WHERE context_id = %s LIMIT 1",
                (str(context_id),),
            )
            row = await cur.fetchone()
    if row is None:
        return None

    session_id = row[0]
    session_type = row[1]
    context_type = row[2]
    created_at = row[3]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT payload FROM events"
                " WHERE aggregate_id = %s AND aggregate_type = 'chat_session'"
                " AND event_type = 'chat_session.message_added'"
                " ORDER BY sequence ASC",
                (str(session_id),),
            )
            rows = await cur.fetchall()

    messages = [
        (
            r[0]["user_input"],
            r[0]["assistant_text"],
            r[0].get("image_id"),
            r[0].get("image_media_type"),
        )
        for r in rows
    ]
    return ChatSession(
        id=session_id,
        session_type=session_type,
        context_id=context_id,
        context_type=context_type,
        created_at=created_at,
        messages=messages,
    )


async def get_chat_session_by_id(
    pool: Any, session_id: UUID
) -> ChatSession | None:
    """Look up an existing chat session by its own ID."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT context_id, session_type, context_type, created_at"
                " FROM current_chat_sessions WHERE id = %s LIMIT 1",
                (str(session_id),),
            )
            row = await cur.fetchone()
    if row is None:
        return None

    context_id = row[0]
    session_type = row[1]
    context_type = row[2]
    created_at = row[3]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT payload FROM events"
                " WHERE aggregate_id = %s AND aggregate_type = 'chat_session'"
                " AND event_type = 'chat_session.message_added'"
                " ORDER BY sequence ASC",
                (str(session_id),),
            )
            rows = await cur.fetchall()

    messages = [
        (
            r[0]["user_input"],
            r[0]["assistant_text"],
            r[0].get("image_id"),
            r[0].get("image_media_type"),
        )
        for r in rows
    ]
    return ChatSession(
        id=session_id,
        session_type=session_type,
        context_id=context_id,
        context_type=context_type,
        created_at=created_at,
        messages=messages,
    )


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


async def get_task_baseline_qa_failure(conn: Any, task_id: UUID) -> str | None:
    """Return pending baseline QA failure output if not cleared by retry or force-execute."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT payload->>'failure_output'
            FROM events
            WHERE aggregate_type = 'task'
              AND aggregate_id = %s
              AND event_type = 'task.baseline_qa_failed'
              AND sequence > COALESCE(
                  (SELECT MAX(sequence)
                   FROM events
                   WHERE aggregate_id = %s
                     AND aggregate_type = 'task'
                     AND event_type IN ('task.baseline_qa_retry', 'task.force_execute')),
                  0)
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (str(task_id), str(task_id)),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return row[0] or None


async def get_task_pr_and_deploy_info(
    conn: Any, task_id: UUID
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Return (pr_info, deploy_hooks) from task.pr_created and task.merge_hooks_run events."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT event_type, payload
            FROM events
            WHERE aggregate_type = 'task'
              AND aggregate_id = %s
              AND event_type IN ('task.pr_created', 'task.merge_hooks_run')
            ORDER BY sequence ASC
            """,
            (str(task_id),),
        )
        rows = await cur.fetchall()

    pr_info: dict[str, Any] | None = None
    deploy_hooks: list[dict[str, Any]] | None = None
    for event_type, payload in rows:
        if event_type == ev.TASK_PR_CREATED:
            pr_info = payload
        elif event_type == ev.TASK_DEPLOY_HOOKS_RUN:
            deploy_hooks = payload.get("steps")
    return pr_info, deploy_hooks


async def get_task_feature_backlink(
    conn: Any, task_id: UUID
) -> tuple[str | None, str | None]:
    """Return (feature_id, feature_title) for the feature that compiled this task.

    Returns (None, None) if no feature backlink exists.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT hls.feature_id::text, f.title
            FROM current_high_level_specs hls
            JOIN current_features f ON f.id = hls.feature_id
            WHERE hls.task_id = %s
            LIMIT 1
            """,
            (str(task_id),),
        )
        row = await cur.fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


async def get_task_detail(conn: Any, task_id: UUID) -> dict[str, Any] | None:
    """Assemble full task detail response using view queries and targeted event queries.

    Returns None if the task is not found. Otherwise returns a dict with all fields
    needed by GET /api/tasks/{task_id}.
    """
    # 1. Query current_tasks for task (includes depends_on)
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, project_id, title, status, current_spec_id, refinement_count,"
            " created_at, updated_at, required_capabilities, merge_commit_sha, depends_on"
            " FROM current_tasks WHERE id = %s",
            (str(task_id),),
        )
        task_row = await cur.fetchone()
    if task_row is None:
        return None

    depends_on_raw = task_row[10]
    if isinstance(depends_on_raw, str):
        depends_on_raw = json.loads(depends_on_raw)
    depends_on_list: list[str] = [str(d) for d in (depends_on_raw or [])]

    required_caps_raw = task_row[8]
    if isinstance(required_caps_raw, str):
        required_caps_raw = json.loads(required_caps_raw)
    required_caps: list[str] = list(required_caps_raw or [])

    task_dict = {
        "id": str(task_row[0]),
        "project_id": str(task_row[1]),
        "title": task_row[2],
        "status": task_row[3],
        "current_spec_id": str(task_row[4]) if task_row[4] is not None else None,
        "refinement_count": task_row[5],
        "created_at": task_row[6].isoformat(),
        "updated_at": task_row[7].isoformat(),
        "required_capabilities": required_caps,
        "merge_commit_sha": task_row[9],
        "depends_on": depends_on_list,
    }

    project_id = task_row[1]

    # 2. Query current_projects for project_name
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT name FROM current_projects WHERE id = %s",
            (str(project_id),),
        )
        proj_row = await cur.fetchone()
    project_name: str | None = proj_row[0] if proj_row is not None else None

    # 3. Query current_specs for spec lineage
    specs_raw = await get_task_specs(conn, task_id)
    specs_data = [
        {
            "id": str(s["id"]),
            "task_id": str(s["task_id"]),
            "previous_spec_id": str(s["previous_spec_id"]) if s["previous_spec_id"] else None,
            "content": s["content"],
            "created_at": s["created_at"].isoformat(),
        }
        for s in specs_raw
    ]

    # 4. Query current_executions for execution history
    executions_raw = await get_task_executions(conn, task_id)
    executions_data = [
        {
            "id": str(e["id"]),
            "task_id": str(e["task_id"]),
            "spec_id": str(e["spec_id"]),
            "status": e["status"],
            "failure_reason": e["failure_reason"],
            "branch_name": e["branch_name"],
            "started_at": e["started_at"].isoformat(),
            "completed_at": e["completed_at"].isoformat() if e["completed_at"] else None,
        }
        for e in executions_raw
    ]

    # 5. Query events for qa_failure (reuse existing function)
    qa_failure = await get_task_qa_failure_reason(conn, task_id)

    # 6. Query events for baseline_qa_failure
    baseline_qa_failure = await get_task_baseline_qa_failure(conn, task_id)

    # 7. Query events for pr_info and deploy_hooks (combined)
    pr_info, deploy_hooks = await get_task_pr_and_deploy_info(conn, task_id)

    # 8. Query current_high_level_specs for feature backlink
    feature_id, feature_title = await get_task_feature_backlink(conn, task_id)

    return {
        "task": task_dict,
        "project_name": project_name,
        "specs": specs_data,
        "executions": executions_data,
        "dependencies": depends_on_list,
        "qa_failure": qa_failure,
        "baseline_qa_failure": baseline_qa_failure,
        "pr_info": pr_info,
        "deploy_hooks": deploy_hooks,
        "feature_id": feature_id,
        "feature_title": feature_title,
    }


async def get_in_progress_task_ids(pool: Any) -> list[UUID]:
    """Return IDs of all tasks currently in 'in_progress' status.

    Queries the current_tasks materialized view directly.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM current_tasks WHERE status = 'in_progress'"
            )
            rows = await cur.fetchall()
    return [UUID(str(row[0])) for row in rows]


async def get_board_tasks(conn: Any) -> list[dict[str, Any]]:
    """Return all non-terminal tasks with project names in a single query.

    Includes baseline_qa_failure via a subquery (only computed for ready_for_implementation tasks).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
                t.id,
                t.project_id,
                p.name AS project_name,
                t.title,
                t.status,
                t.current_spec_id IS NOT NULL AS has_spec,
                t.refinement_count,
                t.updated_at,
                t.depends_on,
                t.required_capabilities,
                CASE WHEN t.status = 'ready_for_implementation' THEN (
                    SELECT e.payload->>'failure_output'
                    FROM events e
                    WHERE e.aggregate_type = 'task'
                      AND e.aggregate_id = t.id
                      AND e.event_type = 'task.baseline_qa_failed'
                      AND e.sequence > COALESCE(
                          (SELECT MAX(e2.sequence)
                           FROM events e2
                           WHERE e2.aggregate_id = t.id::text
                             AND e2.aggregate_type = 'task'
                             AND e2.event_type IN (
                                 'task.baseline_qa_retry', 'task.force_execute')),
                          0)
                    ORDER BY e.sequence DESC
                    LIMIT 1
                ) END AS baseline_qa_failure
            FROM current_tasks t
            JOIN current_projects p ON p.id = t.project_id
            WHERE t.status NOT IN ('merged', 'abandoned')
            ORDER BY t.created_at ASC
            """
        )
        rows = await cur.fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        depends_on_raw = row[8]
        if isinstance(depends_on_raw, str):
            depends_on_raw = json.loads(depends_on_raw)
        depends_on_list: list[str] = [str(d) for d in (depends_on_raw or [])]

        required_caps_raw = row[9]
        if isinstance(required_caps_raw, str):
            required_caps_raw = json.loads(required_caps_raw)
        required_caps: list[str] = list(required_caps_raw or [])

        result.append({
            "id": row[0],
            "project_id": row[1],
            "project_name": row[2],
            "title": row[3],
            "status": row[4],
            "has_spec": row[5],
            "refinement_count": row[6],
            "updated_at": row[7],
            "depends_on": depends_on_list,
            "required_capabilities": required_caps,
            "baseline_qa_failure": row[10],
        })
    return result
