#!/usr/bin/env python3
"""Show all blocked tasks with their failure reasons.

Usage: python scripts/blocked-reasons.py [--project <name>]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter


def _require_db_url() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)


async def main(project_filter: str | None = None) -> None:
    import psycopg

    conn = await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    )

    # Get blocked tasks
    if project_filter:
        cur = await conn.execute(
            """
            SELECT t.id, t.title, p.name
            FROM current_tasks t
            JOIN current_projects p ON p.id = t.project_id
            WHERE t.status = 'blocked' AND p.name = %s
            ORDER BY t.created_at
            """,
            (project_filter,),
        )
    else:
        cur = await conn.execute(
            """
            SELECT t.id, t.title, p.name
            FROM current_tasks t
            JOIN current_projects p ON p.id = t.project_id
            WHERE t.status = 'blocked'
            ORDER BY p.name, t.created_at
            """
        )

    tasks = await cur.fetchall()
    if not tasks:
        print("No blocked tasks.")
        await conn.close()
        return

    # Get failure reasons
    patterns: Counter[str] = Counter()
    for tid, title, pname in tasks:
        cur2 = await conn.execute(
            """
            SELECT payload->>'failure_reason'
            FROM events
            WHERE aggregate_id = %s AND aggregate_type = 'task'
              AND event_type = 'task.status_changed'
              AND payload->>'to_status' = 'blocked'
            ORDER BY sequence DESC LIMIT 1
            """,
            (str(tid),),
        )
        row = await cur2.fetchone()
        reason = (row[0] or "no reason") if row else "no reason"
        short = reason[:120].replace("\n", " ")
        print(f"[{pname}] {title[:50]}")
        print(f"  {short}")
        print()

        # Categorize
        if "charmap" in reason or "codec" in reason:
            patterns["encoding error"] += 1
        elif "InvalidTransition" in reason:
            patterns["invalid transition"] += 1
        elif "pipeline crashed" in reason:
            patterns["pipeline crash"] += 1
        elif "create_worktree" in reason:
            patterns["worktree error"] += 1
        elif ".venv" in reason or "not found" in reason:
            patterns["missing dependency"] += 1
        elif "FAILED" in reason and "test" in reason.lower():
            patterns["test failure"] += 1
        elif "lint" in reason.lower() or "ruff" in reason.lower():
            patterns["lint failure"] += 1
        elif "BLOCKED" in reason or "non-zero" in reason:
            patterns["claude blocked/failed"] += 1
        elif "merge" in reason.lower() or "conflict" in reason.lower():
            patterns["merge conflict"] += 1
        else:
            patterns["other"] += 1

    if len(tasks) > 1:
        print("--- Summary ---")
        for pattern, count in patterns.most_common():
            print(f"  {count:3d}x  {pattern}")

    await conn.close()


if __name__ == "__main__":
    _require_db_url()
    parser = argparse.ArgumentParser(description="Show blocked tasks with failure reasons")
    parser.add_argument("--project", help="Filter by project name")
    args = parser.parse_args()
    asyncio.run(main(args.project))
