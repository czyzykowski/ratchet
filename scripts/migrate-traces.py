"""One-off migration: move existing .md trace files from the filesystem into the DB.

Parses the header block written by ClaudeCodeInvoker to extract execution_id, task_id,
spec_id, and started_at. Skips files where execution_id already exists in execution_traces.
Prints a summary at the end.

Usage:
    .venv/bin/python scripts/migrate-traces.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID


def parse_trace_header(content: str) -> tuple[UUID, UUID, UUID, datetime] | None:
    """Parse the header block of a trace file.

    Expected format:
        # Execution Trace: <uuid>

        # Task: <uuid>

        # Spec: <uuid>

        # Started: <iso-datetime>

    Returns (execution_id, task_id, spec_id, started_at) or None if parsing fails.
    """
    execution_id_match = re.search(r"^# Execution Trace:\s*([0-9a-f-]{36})", content, re.MULTILINE)
    task_id_match = re.search(r"^# Task:\s*([0-9a-f-]{36})", content, re.MULTILINE)
    spec_id_match = re.search(r"^# Spec:\s*([0-9a-f-]{36})", content, re.MULTILINE)
    started_at_match = re.search(r"^# Started:\s*(\S+)", content, re.MULTILINE)

    if not (execution_id_match and task_id_match and spec_id_match and started_at_match):
        return None

    try:
        execution_id = UUID(execution_id_match.group(1))
        task_id = UUID(task_id_match.group(1))
        spec_id = UUID(spec_id_match.group(1))
        started_at = datetime.fromisoformat(started_at_match.group(1))
    except (ValueError, OverflowError):
        return None

    return execution_id, task_id, spec_id, started_at


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Determine traces directory
    env_dir = os.environ.get("RATCHET_TRACES_DIR")
    if env_dir:
        traces_dir = Path(env_dir)
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        traces_dir = Path(xdg_data_home) / "ratchet" / "traces"

    if not traces_dir.exists():
        print(f"Traces directory does not exist: {traces_dir}")
        print("Nothing to migrate.")
        return

    trace_files = sorted(traces_dir.glob("*.md"))
    print(f"Found {len(trace_files)} .md trace file(s) in {traces_dir}")

    if not trace_files:
        print("Nothing to migrate.")
        return

    import psycopg

    database_url = os.environ["DATABASE_URL"]

    inserted = 0
    skipped_duplicate = 0
    skipped_parse_error = 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for trace_file in trace_files:
                content = trace_file.read_text()
                parsed = parse_trace_header(content)

                if parsed is None:
                    print(f"  SKIP (parse error): {trace_file.name}")
                    skipped_parse_error += 1
                    continue

                execution_id, task_id, spec_id, started_at = parsed

                # Check for existing row
                cur.execute(
                    "SELECT 1 FROM execution_traces WHERE execution_id = %s",
                    (str(execution_id),),
                )
                if cur.fetchone() is not None:
                    print(f"  SKIP (duplicate): {trace_file.name}")
                    skipped_duplicate += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO execution_traces (
                        execution_id, task_id, spec_id, content, started_at
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        str(execution_id),
                        str(task_id),
                        str(spec_id),
                        content,
                        started_at,
                    ),
                )
                print(f"  INSERT: {trace_file.name}")
                inserted += 1

        conn.commit()

    print()
    print(f"Migration complete: {inserted} inserted, {skipped_duplicate} skipped (duplicate),"
          f" {skipped_parse_error} skipped (parse error)")


if __name__ == "__main__":
    main()
