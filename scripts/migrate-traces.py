"""One-off migration: move existing .md trace files into the execution_traces table.

Reads *.md files from the traces directory, parses the header to extract UUIDs and
timestamp, inserts rows skipping duplicates, and prints a summary.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

_HEADER_EXECUTION_RE = re.compile(r"^# Execution Trace: ([0-9a-f-]{36})", re.MULTILINE)
_HEADER_TASK_RE = re.compile(r"^# Task: ([0-9a-f-]{36})", re.MULTILINE)
_HEADER_SPEC_RE = re.compile(r"^# Spec: ([0-9a-f-]{36})", re.MULTILINE)
_HEADER_STARTED_RE = re.compile(r"^# Started: (.+)$", re.MULTILINE)


def _get_traces_dir() -> Path:
    env_dir = os.environ.get("RATCHET_TRACES_DIR")
    if env_dir:
        return Path(env_dir)
    xdg_data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(xdg_data_home) / "ratchet" / "traces"


def _parse_header(
    content: str,
) -> tuple[UUID, UUID, UUID, datetime] | None:
    m_exec = _HEADER_EXECUTION_RE.search(content)
    m_task = _HEADER_TASK_RE.search(content)
    m_spec = _HEADER_SPEC_RE.search(content)
    m_started = _HEADER_STARTED_RE.search(content)
    if not (m_exec and m_task and m_spec and m_started):
        return None
    try:
        execution_id = UUID(m_exec.group(1))
        task_id = UUID(m_task.group(1))
        spec_id = UUID(m_spec.group(1))
        started_at = datetime.fromisoformat(m_started.group(1).strip())
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        return execution_id, task_id, spec_id, started_at
    except (ValueError, TypeError):
        return None


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    url = database_url.replace("postgresql+psycopg://", "postgresql://")

    traces_dir = _get_traces_dir()
    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}")
        print("Nothing to migrate.")
        return

    trace_files = sorted(traces_dir.glob("*.md"))
    print(f"Found {len(trace_files)} trace file(s) in {traces_dir}")

    if not trace_files:
        return

    import psycopg

    inserted = 0
    skipped_duplicate = 0
    skipped_parse_error = 0

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for path in trace_files:
                content = path.read_text()
                parsed = _parse_header(content)
                if parsed is None:
                    print(f"  SKIP (parse error): {path.name}")
                    skipped_parse_error += 1
                    continue

                execution_id, task_id, spec_id, started_at = parsed

                cur.execute(
                    "SELECT 1 FROM execution_traces WHERE execution_id = %s",
                    (str(execution_id),),
                )
                if cur.fetchone() is not None:
                    print(f"  SKIP (duplicate): {path.name}")
                    skipped_duplicate += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO execution_traces
                        (execution_id, task_id, spec_id, content, started_at)
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
                print(f"  INSERT: {path.name}")
                inserted += 1

        conn.commit()

    print(
        f"\nMigration complete: {inserted} inserted, "
        f"{skipped_duplicate} skipped (duplicate), "
        f"{skipped_parse_error} skipped (parse error)."
    )


if __name__ == "__main__":
    main()
