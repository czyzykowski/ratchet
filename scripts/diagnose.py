#!/usr/bin/env python3
"""Diagnose stuck and blocked tasks with root cause analysis.

Checks:
  1. Blocked tasks — failure reasons with classification
  2. Orphaned executions — running with no worker heartbeat
  3. Stale in_progress tasks — assigned but not advancing
  4. Dependency chains — blocked tasks holding up others
  5. Suggested actions for each issue

Usage:
  python scripts/diagnose.py
  python scripts/diagnose.py --task-id <uuid>
  python scripts/diagnose.py --fix-orphans         # mark orphaned executions as failed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from uuid import UUID


def _require_db_url() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)


# ── Failure classifiers ─────────────────────────────────────────────

INFRA_PATTERNS = [
    ("nix/store", "nix_store_missing", "Nix store path missing — run `nix develop` to repopulate"),
    ("dlopen", "dylib_load_failed", "Dynamic library load failure — rebuild in nix-shell"),
    ("Library not loaded", "dylib_load_failed", "Dynamic library load failure — rebuild in nix-shell"),
    ("MODULE_NOT_FOUND", "node_module_missing", "Node module missing — run `npm install`"),
    ("ENOSPC", "disk_full", "Disk full"),
    ("permission denied", "permission_denied", "Permission denied — check file ownership"),
    ("create_worktree", "worktree_error", "Git worktree creation failed"),
    ("charmap", "encoding_error", "Encoding error in worker output"),
    ("codec", "encoding_error", "Encoding error in worker output"),
]

CODE_PATTERNS = [
    ("FAILED", "test_failure", "Test failure — code error"),
    ("ruff", "lint_failure", "Lint failure — code error"),
    ("mypy", "type_error", "Type check failure — code error"),
    ("AssertionError", "test_failure", "Test assertion failed — code error"),
    ("SyntaxError", "syntax_error", "Syntax error — code error"),
]

SYSTEM_PATTERNS = [
    ("pipeline crashed", "pipeline_crash", "Pipeline crashed — orchestrator bug or OOM"),
    ("InvalidTransition", "invalid_transition", "State machine transition error — orchestrator bug"),
    ("BLOCKED", "claude_blocked", "Claude declared BLOCKED — may be false positive from subprocess output"),
    ("non-zero exit", "claude_nonzero", "Claude exited non-zero — check trace for details"),
]


def classify_failure(reason: str) -> tuple[str, str, str]:
    """Returns (category, code, suggestion)."""
    for pattern, code, suggestion in INFRA_PATTERNS:
        if pattern.lower() in reason.lower():
            return "INFRA", code, suggestion
    for pattern, code, suggestion in CODE_PATTERNS:
        if pattern.lower() in reason.lower():
            return "CODE", code, suggestion
    for pattern, code, suggestion in SYSTEM_PATTERNS:
        if pattern.lower() in reason.lower():
            return "SYSTEM", code, suggestion
    return "UNKNOWN", "unknown", "Manual investigation needed"


# ── Queries ──────────────────────────────────────────────────────────

async def get_connection():
    import psycopg
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    return await psycopg.AsyncConnection.connect(dsn)


async def diagnose_all(fix_orphans: bool = False) -> None:
    conn = await get_connection()

    print("=" * 70)
    print("RATCHET DIAGNOSTIC REPORT")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    # ── 1. Blocked tasks ────────────────────────────────────────────
    cur = await conn.execute("""
        SELECT t.id, t.title, t.refinement_count, p.name,
               t.updated_at
        FROM current_tasks t
        JOIN current_projects p ON p.id = t.project_id
        WHERE t.status = 'blocked'
        ORDER BY t.updated_at DESC
    """)
    blocked = await cur.fetchall()

    print(f"\n── BLOCKED TASKS ({len(blocked)}) ──")
    if not blocked:
        print("  None.")
    for tid, title, refine_count, pname, updated_at in blocked:
        cur2 = await conn.execute("""
            SELECT payload->>'failure_reason'
            FROM events
            WHERE aggregate_id = %s AND aggregate_type = 'task'
              AND event_type = 'task.status_changed'
              AND payload->>'to_status' = 'blocked'
            ORDER BY sequence DESC LIMIT 1
        """, (str(tid),))
        row = await cur2.fetchone()
        reason = (row[0] or "no reason") if row else "no reason"
        category, code, suggestion = classify_failure(reason)

        age = datetime.now(timezone.utc) - updated_at
        age_str = f"{age.days}d" if age.days > 0 else f"{age.seconds // 3600}h"

        # Count executions
        cur3 = await conn.execute("""
            SELECT count(*), count(*) FILTER (WHERE status = 'running')
            FROM current_executions WHERE task_id = %s
        """, (str(tid),))
        exec_total, exec_orphaned = (await cur3.fetchone()) or (0, 0)

        print(f"\n  [{pname}] {title}")
        print(f"    ID:           {tid}")
        print(f"    Blocked for:  {age_str}  |  Refinements: {refine_count}  |  Executions: {exec_total} ({exec_orphaned} orphaned)")
        print(f"    Category:     {category} ({code})")
        print(f"    Reason:       {reason[:150].replace(chr(10), ' ')}")
        print(f"    Suggestion:   {suggestion}")
        print(f"    Action:       scripts/unblock-task.py --task-id {tid}")

    # ── 2. Orphaned executions ──────────────────────────────────────
    cur = await conn.execute("""
        SELECT e.id, e.task_id, e.branch_name, e.started_at, t.title, p.name
        FROM current_executions e
        JOIN current_tasks t ON t.id = e.task_id
        JOIN current_projects p ON p.id = t.project_id
        WHERE e.status = 'running' AND e.completed_at IS NULL
          AND e.started_at < now() - interval '2 hours'
        ORDER BY e.started_at DESC
    """)
    orphans = await cur.fetchall()

    print(f"\n── ORPHANED EXECUTIONS ({len(orphans)}) ──")
    if not orphans:
        print("  None.")
    else:
        # Group by task
        by_task: dict[str, list] = {}
        for eid, task_id, branch, started, title, pname in orphans:
            key = f"[{pname}] {title}"
            by_task.setdefault(key, []).append((eid, started))

        for task_label, execs in by_task.items():
            print(f"\n  {task_label}")
            for eid, started in execs[:5]:
                age = datetime.now(timezone.utc) - started
                print(f"    {eid}  started {age.days}d {age.seconds//3600}h ago")
            if len(execs) > 5:
                print(f"    ... and {len(execs) - 5} more")

        if fix_orphans:
            print(f"\n  Fixing {len(orphans)} orphaned executions...")
            for eid, task_id, branch, started, title, pname in orphans:
                await conn.execute("""
                    INSERT INTO events (aggregate_id, aggregate_type, event_type, payload)
                    VALUES (%s, 'execution', 'execution.completed', %s)
                """, (
                    str(eid),
                    '{"status": "failed", "failure_reason": "execution timed out (orphan cleanup)"}',
                ))
            await conn.execute("COMMIT")
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_executions")
            await conn.execute("COMMIT")
            print(f"  Done. {len(orphans)} executions marked as failed.")
        else:
            print(f"\n  Run with --fix-orphans to clean up.")

    # ── 3. Dependency chains ────────────────────────────────────────
    cur = await conn.execute("""
        SELECT t.id, t.title, t.status, t.depends_on, p.name
        FROM current_tasks t
        JOIN current_projects p ON p.id = t.project_id
        WHERE t.depends_on IS NOT NULL AND t.depends_on != '[]'::jsonb
          AND t.status NOT IN ('merged', 'abandoned', 'done')
        ORDER BY p.name
    """)
    dep_tasks = await cur.fetchall()

    if dep_tasks:
        print(f"\n── DEPENDENCY CHAINS ──")
        for tid, title, status, depends_on, pname in dep_tasks:
            dep_list = depends_on if isinstance(depends_on, list) else []
            if dep_list:
                # Check if any dep is blocked
                dep_uuids = [str(d) for d in dep_list]
                cur2 = await conn.execute("""
                    SELECT id, title, status FROM current_tasks WHERE id = ANY(%s)
                """, (dep_uuids,))
                dep_rows = await cur2.fetchall()
                blocked_deps = [r for r in dep_rows if r[2] in ('blocked',)]
                if blocked_deps:
                    print(f"\n  [{pname}] {title} ({status})")
                    print(f"    HELD UP BY:")
                    for did, dtitle, dstatus in blocked_deps:
                        print(f"      [{dstatus}] {dtitle}")

    # ── 4. Stale tasks ──────────────────────────────────────────────
    cur = await conn.execute("""
        SELECT t.id, t.title, t.status, p.name, t.updated_at
        FROM current_tasks t
        JOIN current_projects p ON p.id = t.project_id
        WHERE t.status NOT IN ('merged', 'abandoned', 'done', 'blocked',
                                'ready_for_spec', 'ready_for_implementation')
          AND t.updated_at < now() - interval '6 hours'
        ORDER BY t.updated_at
    """)
    stale = await cur.fetchall()

    if stale:
        print(f"\n── STALE ACTIVE TASKS ({len(stale)}) ──")
        for tid, title, status, pname, updated in stale:
            age = datetime.now(timezone.utc) - updated
            print(f"  [{pname}] {title}  status={status}  idle {age.days}d {age.seconds//3600}h")

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"  Blocked tasks:         {len(blocked)}")
    print(f"  Orphaned executions:   {len(orphans)}")
    print(f"  Dependency-held tasks: {sum(1 for _ in dep_tasks if _)}")
    print(f"  Stale active tasks:    {len(stale)}")
    print(f"{'=' * 70}")

    await conn.close()


async def diagnose_task(task_id: UUID) -> None:
    conn = await get_connection()

    # Task info
    cur = await conn.execute("""
        SELECT t.id, t.title, t.status, t.refinement_count,
               t.depends_on, p.name, t.updated_at
        FROM current_tasks t
        JOIN current_projects p ON p.id = t.project_id
        WHERE t.id = %s
    """, (str(task_id),))
    row = await cur.fetchone()
    if not row:
        print(f"Task {task_id} not found.")
        await conn.close()
        return

    tid, title, status, refine_count, deps, pname, updated = row

    print(f"Task:    {title}")
    print(f"Project: {pname}")
    print(f"Status:  {status}")
    print(f"Updated: {updated}")
    print(f"Deps:    {deps or 'none'}")

    # All executions
    cur = await conn.execute("""
        SELECT id, status, branch_name, failure_reason, started_at, completed_at
        FROM current_executions
        WHERE task_id = %s
        ORDER BY started_at DESC
    """, (str(task_id),))
    execs = await cur.fetchall()

    print(f"\nExecutions: {len(execs)}")
    orphaned = 0
    for eid, estatus, branch, reason, started, completed in execs:
        age = datetime.now(timezone.utc) - started
        is_orphan = estatus == "running" and completed is None and age > timedelta(hours=2)
        orphaned += int(is_orphan)
        marker = " *** ORPHAN" if is_orphan else ""
        print(f"  {str(eid)[:8]}  {estatus:12s}  {str(branch or '-')[:40]:40s}  {age.days}d ago{marker}")
        if reason:
            category, code, suggestion = classify_failure(reason)
            print(f"           [{category}/{code}] {reason[:100].replace(chr(10), ' ')}")
            print(f"           Suggestion: {suggestion}")

    if orphaned:
        print(f"\n  {orphaned} orphaned execution(s) detected.")

    # Recent events
    cur = await conn.execute("""
        SELECT event_type, payload, occurred_at
        FROM events
        WHERE aggregate_id = %s
        ORDER BY occurred_at DESC
        LIMIT 10
    """, (str(task_id),))
    events = await cur.fetchall()

    print(f"\nRecent events:")
    for etype, payload, occurred in events:
        pstr = str(payload)[:100].replace("\n", " ")
        print(f"  {occurred.strftime('%m-%d %H:%M')}  {etype:30s}  {pstr}")

    await conn.close()


if __name__ == "__main__":
    _require_db_url()
    parser = argparse.ArgumentParser(description="Diagnose stuck and blocked tasks")
    parser.add_argument("--task-id", help="Diagnose a specific task (full UUID)")
    parser.add_argument("--fix-orphans", action="store_true",
                        help="Mark orphaned running executions as failed")
    args = parser.parse_args()

    if args.task_id:
        asyncio.run(diagnose_task(UUID(args.task_id)))
    else:
        asyncio.run(diagnose_all(fix_orphans=args.fix_orphans))
