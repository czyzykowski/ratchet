#!/usr/bin/env python3
"""Diagnose stuck and blocked tasks with root cause analysis.

Checks:
  1. Blocked tasks — failure reasons with classification
  2. Orphaned executions — running with no worker heartbeat
  3. Stale in_progress tasks — assigned but not advancing
  4. Dependency chains — blocked tasks holding up others
  5. Execution waste — how many cycles were spent on infra vs code errors
  6. Suggested actions for each issue

Actions:
  --fix-orphans              Mark orphaned running executions as failed
  --fix-orphans --task-id X  Fix orphans for a specific task only
  --unblock-infra            Unblock all tasks whose last failure is INFRA-classified
  --check-false-positives    Scan recent traces for COMPLETED markers on BLOCKED tasks

Usage:
  python scripts/diagnose.py
  python scripts/diagnose.py --task-id <uuid>
  python scripts/diagnose.py --fix-orphans
  python scripts/diagnose.py --unblock-infra
  python scripts/diagnose.py --check-false-positives
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID


def _require_db_url() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)


# ── Failure classifiers ─────────────────────────────────────────────

INFRA_PATTERNS = [
    ("nix/store", "nix_store_missing", "Nix store path missing — run `nix develop` to repopulate"),
    ("dlopen", "dylib_load_failed", "Dynamic library load failure — rebuild in nix-shell"),
    ("Library not loaded", "dylib_load_failed",
     "Dynamic library load failure — rebuild in nix-shell"),
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
    ("InvalidTransition", "invalid_transition",
     "State machine transition error — orchestrator bug"),
    ("[INFRA]", "infra_classified", "Already classified as infrastructure by QA pipeline"),
    ("non-zero exit", "claude_nonzero", "Claude exited non-zero — check trace for details"),
    ("BLOCKED", "claude_blocked", "Claude declared BLOCKED — check trace for false positive"),
]


def classify_failure(reason: str) -> tuple[str, str, str]:
    """Returns (category, code, suggestion)."""
    # Check for [INFRA] prefix first — already classified by QA pipeline
    if reason.startswith("[INFRA]"):
        return "INFRA", "qa_classified", "Already classified as infra by QA pipeline"
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


async def _refresh_views(conn) -> None:
    """Refresh all materialized views after data changes."""
    try:
        await conn.execute("SELECT refresh_all_views()")
        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_executions")
        await conn.execute("COMMIT")


async def _fix_orphaned_executions(conn, orphans: list, task_filter: UUID | None = None) -> int:
    """Mark orphaned executions as failed. Returns count fixed."""
    to_fix = orphans
    if task_filter:
        to_fix = [o for o in orphans if o[1] == task_filter]

    if not to_fix:
        print("  No orphaned executions to fix.")
        return 0

    print(f"  Fixing {len(to_fix)} orphaned executions...")
    for eid, task_id, branch, started, title, pname in to_fix:
        await conn.execute("""
            INSERT INTO events (aggregate_id, aggregate_type, event_type, payload)
            VALUES (%s, 'execution', 'execution.failed', %s)
        """, (
            str(eid),
            '{"status": "failed", "failure_reason": "execution timed out (orphan cleanup)", '
            '"execution_id": "' + str(eid) + '"}',
        ))
    await conn.execute("COMMIT")
    await _refresh_views(conn)
    print(f"  Done. {len(to_fix)} executions marked as failed.")
    return len(to_fix)


async def _get_orphaned_executions(conn, task_filter: UUID | None = None) -> list:
    """Query orphaned running executions."""
    if task_filter:
        cur = await conn.execute("""
            SELECT e.id, e.task_id, e.branch_name, e.started_at, t.title, p.name
            FROM current_executions e
            JOIN current_tasks t ON t.id = e.task_id
            JOIN current_projects p ON p.id = t.project_id
            WHERE e.status = 'running' AND e.completed_at IS NULL
              AND e.started_at < now() - interval '2 hours'
              AND e.task_id = %s
            ORDER BY e.started_at DESC
        """, (str(task_filter),))
    else:
        cur = await conn.execute("""
            SELECT e.id, e.task_id, e.branch_name, e.started_at, t.title, p.name
            FROM current_executions e
            JOIN current_tasks t ON t.id = e.task_id
            JOIN current_projects p ON p.id = t.project_id
            WHERE e.status = 'running' AND e.completed_at IS NULL
              AND e.started_at < now() - interval '2 hours'
            ORDER BY e.started_at DESC
        """)
    return await cur.fetchall()


async def diagnose_all(
    fix_orphans: bool = False,
    unblock_infra: bool = False,
    check_false_positives: bool = False,
) -> None:
    conn = await get_connection()

    print("=" * 70)
    print("RATCHET DIAGNOSTIC REPORT")
    print(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
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

    infra_blocked: list[tuple] = []  # tasks to unblock if --unblock-infra
    category_counts: Counter[str] = Counter()

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
        category_counts[category] += 1

        if category == "INFRA":
            infra_blocked.append((tid, title, pname, reason))

        age = datetime.now(UTC) - updated_at
        age_str = f"{age.days}d" if age.days > 0 else f"{age.seconds // 3600}h"

        # Count executions
        cur3 = await conn.execute("""
            SELECT count(*), count(*) FILTER (WHERE status = 'running')
            FROM current_executions WHERE task_id = %s
        """, (str(tid),))
        exec_total, exec_orphaned = (await cur3.fetchone()) or (0, 0)

        print(f"\n  [{pname}] {title}")
        print(f"    ID:           {tid}")
        print(
            f"    Blocked for:  {age_str}  |  Refinements: {refine_count}"
            f"  |  Executions: {exec_total} ({exec_orphaned} orphaned)"
        )
        print(f"    Category:     {category} ({code})")
        print(f"    Reason:       {reason[:150].replace(chr(10), ' ')}")
        print(f"    Suggestion:   {suggestion}")
        print(f"    Action:       scripts/unblock-task.py --task-id {tid}")

    # ── 2. Orphaned executions ──────────────────────────────────────
    orphans = await _get_orphaned_executions(conn)

    print(f"\n── ORPHANED EXECUTIONS ({len(orphans)}) ──")
    if not orphans:
        print("  None.")
    else:
        by_task: dict[str, list] = {}
        for eid, task_id, branch, started, title, pname in orphans:
            key = f"[{pname}] {title}"
            by_task.setdefault(key, []).append((eid, started))

        for task_label, execs in by_task.items():
            print(f"\n  {task_label}")
            for eid, started in execs[:5]:
                age = datetime.now(UTC) - started
                print(f"    {eid}  started {age.days}d {age.seconds//3600}h ago")
            if len(execs) > 5:
                print(f"    ... and {len(execs) - 5} more")

        if fix_orphans:
            await _fix_orphaned_executions(conn, orphans)
        else:
            print("\n  Run with --fix-orphans to clean up.")

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

    held_up_count = 0
    if dep_tasks:
        print("\n── DEPENDENCY CHAINS ──")
        for tid, title, status, depends_on, pname in dep_tasks:
            dep_list = depends_on if isinstance(depends_on, list) else []
            if dep_list:
                dep_uuids = [str(d) for d in dep_list]
                cur2 = await conn.execute("""
                    SELECT id, title, status FROM current_tasks WHERE id = ANY(%s)
                """, (dep_uuids,))
                dep_rows = await cur2.fetchall()
                blocked_deps = [r for r in dep_rows if r[2] in ('blocked',)]
                if blocked_deps:
                    held_up_count += 1
                    print(f"\n  [{pname}] {title} ({status})")
                    print("    HELD UP BY:")
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
            age = datetime.now(UTC) - updated
            print(f"  [{pname}] {title}  status={status}  idle {age.days}d {age.seconds//3600}h")

    # ── 5. Execution waste analysis ─────────────────────────────────
    cur = await conn.execute("""
        SELECT e.failure_reason, p.name
        FROM current_executions e
        JOIN current_tasks t ON t.id = e.task_id
        JOIN current_projects p ON p.id = t.project_id
        WHERE e.status = 'failed' AND e.failure_reason IS NOT NULL
          AND e.started_at > now() - interval '7 days'
    """)
    recent_failures = await cur.fetchall()

    if recent_failures:
        waste: Counter[str] = Counter()
        for reason, pname in recent_failures:
            cat, _, _ = classify_failure(reason or "")
            waste[cat] += 1

        print("\n── EXECUTION WASTE (last 7 days) ──")
        total = sum(waste.values())
        for cat in ["INFRA", "CODE", "SYSTEM", "UNKNOWN"]:
            if waste[cat]:
                pct = waste[cat] * 100 // total
                print(f"  {cat:8s}  {waste[cat]:3d} executions  ({pct}%)")
        print(f"  {'TOTAL':8s}  {total:3d} executions")

    # ── 6. False-positive BLOCKED check ─────────────────────────────
    if check_false_positives:
        print("\n── FALSE-POSITIVE BLOCKED CHECK ──")
        import re
        completed_re = re.compile(r"^\s*COMPLETED:", re.MULTILINE)

        cur = await conn.execute("""
            SELECT t.id, t.title, p.name, e.id as exec_id
            FROM current_tasks t
            JOIN current_projects p ON p.id = t.project_id
            JOIN current_executions e ON e.task_id = t.id
            WHERE t.status = 'blocked'
              AND e.failure_reason LIKE '%%BLOCKED%%'
            ORDER BY e.started_at DESC
        """)
        candidates = await cur.fetchall()

        found = 0
        checked_tasks: set[str] = set()
        for tid, title, pname, exec_id in candidates:
            if str(tid) in checked_tasks:
                continue
            checked_tasks.add(str(tid))

            cur2 = await conn.execute("""
                SELECT content FROM execution_traces WHERE execution_id = %s
            """, (str(exec_id),))
            trace_row = await cur2.fetchone()
            if not trace_row or not trace_row[0]:
                continue

            if completed_re.search(trace_row[0]):
                found += 1
                print(f"  [{pname}] {title}")
                print(f"    Execution {exec_id} has COMPLETED: marker but was marked BLOCKED")
                print(f"    Action: scripts/unblock-task.py --task-id {tid}")

        if found == 0:
            print("  No false positives detected.")

    # ── 7. Unblock infra tasks ──────────────────────────────────────
    if unblock_infra and infra_blocked:
        print(f"\n── UNBLOCKING {len(infra_blocked)} INFRA-BLOCKED TASKS ──")
        for tid, title, pname, reason in infra_blocked:
            await conn.execute("""
                INSERT INTO events (aggregate_id, aggregate_type, event_type, payload)
                VALUES (%s, 'task', 'task.status_changed', %s)
            """, (
                str(tid),
                '{"status": "ready_for_implementation", '
                '"from_status": "blocked", '
                '"to_status": "ready_for_implementation", '
                '"reason": "diagnose --unblock-infra"}',
            ))
            print(f"  [{pname}] {title} -> ready_for_implementation")
        await conn.execute("COMMIT")
        await _refresh_views(conn)
        print(f"  Done. {len(infra_blocked)} tasks unblocked.")
    elif unblock_infra:
        print("\n  No INFRA-blocked tasks to unblock.")

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"  Blocked tasks:         {len(blocked)}", end="")
    if category_counts:
        parts = [f"{v} {k.lower()}" for k, v in category_counts.most_common()]
        print(f"  ({', '.join(parts)})")
    else:
        print()
    print(f"  Orphaned executions:   {len(orphans)}")
    print(f"  Dependency-held tasks: {held_up_count}")
    print(f"  Stale active tasks:    {len(stale)}")
    print(f"{'=' * 70}")

    await conn.close()


async def diagnose_task(task_id: UUID, fix_orphans: bool = False) -> None:
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
    waste: Counter[str] = Counter()
    for eid, estatus, branch, reason, started, completed in execs:
        age = datetime.now(UTC) - started
        is_orphan = estatus == "running" and completed is None and age > timedelta(hours=2)
        orphaned += int(is_orphan)
        marker = " *** ORPHAN" if is_orphan else ""
        br = str(branch or '-')[:40]
        print(f"  {str(eid)[:8]}  {estatus:12s}  {br:40s}  {age.days}d ago{marker}")
        if reason:
            category, code, suggestion = classify_failure(reason)
            waste[category] += 1
            print(f"           [{category}/{code}] {reason[:100].replace(chr(10), ' ')}")
            print(f"           Suggestion: {suggestion}")

    if orphaned:
        print(f"\n  {orphaned} orphaned execution(s) detected.")
        if fix_orphans:
            task_orphans = await _get_orphaned_executions(conn, task_filter=task_id)
            await _fix_orphaned_executions(conn, task_orphans, task_filter=task_id)

    # Execution waste for this task
    if waste:
        total = sum(waste.values())
        parts = [f"{v} {k.lower()}" for k, v in waste.most_common()]
        print(f"\n  Failure breakdown: {', '.join(parts)} ({total} total)")

    # False-positive BLOCKED check — scan traces for COMPLETED marker
    if status == "blocked":
        import re
        completed_re = re.compile(r"^\s*COMPLETED:", re.MULTILINE)

        cur = await conn.execute("""
            SELECT e.id, et.content
            FROM current_executions e
            LEFT JOIN execution_traces et ON et.execution_id = e.id
            WHERE e.task_id = %s AND e.failure_reason LIKE '%%BLOCKED%%'
            ORDER BY e.started_at DESC
            LIMIT 3
        """, (str(task_id),))
        trace_rows = await cur.fetchall()

        for exec_id, trace_content in trace_rows:
            if trace_content and completed_re.search(trace_content):
                print(f"\n  *** FALSE POSITIVE: execution {exec_id} has COMPLETED: marker in trace")
                print("      but was marked BLOCKED — likely substring false positive")
                print(f"      Action: scripts/unblock-task.py --task-id {task_id}")
                break

    # Recent events
    cur = await conn.execute("""
        SELECT event_type, payload, occurred_at
        FROM events
        WHERE aggregate_id = %s
        ORDER BY occurred_at DESC
        LIMIT 10
    """, (str(task_id),))
    events = await cur.fetchall()

    print("\nRecent events:")
    for etype, payload, occurred in events:
        pstr = str(payload)[:100].replace("\n", " ")
        print(f"  {occurred.strftime('%m-%d %H:%M')}  {etype:30s}  {pstr}")

    # Suggested actions
    print("\nActions:")
    if orphaned and not fix_orphans:
        print(f"  scripts/diagnose.py --task-id {task_id} --fix-orphans")
    if status == "blocked":
        print(f"  scripts/unblock-task.py --task-id {task_id}")
        print(f"  scripts/task-reset.py --task-id {task_id} --reuse-spec")

    await conn.close()


if __name__ == "__main__":
    _require_db_url()
    parser = argparse.ArgumentParser(description="Diagnose stuck and blocked tasks")
    parser.add_argument("--task-id", help="Diagnose a specific task (full UUID)")
    parser.add_argument("--fix-orphans", action="store_true",
                        help="Mark orphaned running executions as failed")
    parser.add_argument("--unblock-infra", action="store_true",
                        help="Unblock all tasks whose last failure is INFRA-classified")
    parser.add_argument("--check-false-positives", action="store_true",
                        help="Scan traces for COMPLETED markers on BLOCKED tasks")
    args = parser.parse_args()

    if args.task_id:
        asyncio.run(diagnose_task(UUID(args.task_id), fix_orphans=args.fix_orphans))
    else:
        asyncio.run(diagnose_all(
            fix_orphans=args.fix_orphans,
            unblock_infra=args.unblock_infra,
            check_false_positives=args.check_false_positives,
        ))
