"""One-off migration: move existing .md trace files into the execution_traces DB table.

Reads *.md files from traces dir, parses the header to extract UUIDs and timestamp,
inserts rows into execution_traces (skipping duplicates), and prints a summary.

Usage:
    .venv/bin/python scripts/migrate-traces.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from uuid import UUID

import psycopg

from core.invoker import get_traces_dir


def _parse_header(content: str) -> tuple[UUID, UUID, UUID, str] | None:
    """Parse trace file header and return (execution_id, task_id, spec_id, started_at) or None."""
    execution_id_match = re.search(r"^# Execution Trace: ([0-9a-f-]+)$", content, re.MULTILINE)
    task_id_match = re.search(r"^# Task: ([0-9a-f-]+)$", content, re.MULTILINE)
    spec_id_match = re.search(r"^# Spec: ([0-9a-f-]+)$", content, re.MULTILINE)
    started_at_match = re.search(r"^# Started: (.+)$", content, re.MULTILINE)

    if not all([execution_id_match, task_id_match, spec_id_match, started_at_match]):
        return None

    try:
        execution_id = UUID(execution_id_match.group(1))
        task_id = UUID(task_id_match.group(1))
        spec_id = UUID(spec_id_match.group(1))
        started_at = started_at_match.group(1).strip()
        return execution_id, task_id, spec_id, started_at
    except (ValueError, AttributeError):
        return None


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Convert psycopg DSN format if needed
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")

    traces_dir = Path(get_traces_dir())
    trace_files = sorted(traces_dir.glob("*.md"))

    if not trace_files:
        print(f"No .md trace files found in {traces_dir}")
        return

    print(f"Found {len(trace_files)} trace file(s) in {traces_dir}")

    inserted = 0
    skipped = 0
    errors = 0

    with psycopg.connect(dsn) as conn:
        for trace_file in trace_files:
            try:
                content = trace_file.read_text()
            except OSError as exc:
                print(f"  ERROR reading {trace_file.name}: {exc}")
                errors += 1
                continue

            parsed = _parse_header(content)
            if parsed is None:
                print(f"  SKIP {trace_file.name}: could not parse header")
                skipped += 1
                continue

            execution_id, task_id, spec_id, started_at = parsed

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO execution_traces (execution_id, task_id, spec_id, content, started_at)
                    VALUES (%s, %s, %s, %s, %s::timestamptz)
                    ON CONFLICT (execution_id) DO NOTHING
                    """,
                    (str(execution_id), str(task_id), str(spec_id), content, started_at),
                )
                if cur.rowcount == 0:
                    print(f"  SKIP {trace_file.name}: execution_id already exists")
                    skipped += 1
                else:
                    print(f"  INSERT {trace_file.name}")
                    inserted += 1

        conn.commit()

    print(f"\nSummary: {inserted} inserted, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
