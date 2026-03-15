"""One-off script: migrate existing .md trace files from filesystem into execution_traces table."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from core.invoker import get_traces_dir

    traces_dir = Path(get_traces_dir())
    trace_files = sorted(traces_dir.glob("*.md"))

    if not trace_files:
        print(f"No .md trace files found in {traces_dir}")
        return

    print(f"Found {len(trace_files)} trace file(s) in {traces_dir}")

    # Regex patterns to parse header fields
    exec_re = re.compile(r"# Execution Trace: ([0-9a-f-]{36})")
    task_re = re.compile(r"# Task: ([0-9a-f-]{36})")
    spec_re = re.compile(r"# Spec: ([0-9a-f-]{36})")
    started_re = re.compile(r"# Started: (.+)")

    url = os.environ.get("DATABASE_URL", "")
    dsn = url.replace("postgresql+psycopg://", "postgresql://")

    import psycopg

    inserted = 0
    skipped = 0
    errors = 0

    with psycopg.connect(dsn) as conn:
        for trace_file in trace_files:
            try:
                content = trace_file.read_text()
                exec_match = exec_re.search(content)
                task_match = task_re.search(content)
                spec_match = spec_re.search(content)
                started_match = started_re.search(content)

                if not all([exec_match, task_match, spec_match, started_match]):
                    print(f"  SKIP {trace_file.name}: could not parse header fields")
                    skipped += 1
                    continue

                assert exec_match and task_match and spec_match and started_match
                execution_id = exec_match.group(1)
                task_id = task_match.group(1)
                spec_id = spec_match.group(1)
                started_at_str = started_match.group(1).strip()

                # Check if already exists
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM execution_traces WHERE execution_id = %s",
                        (execution_id,),
                    )
                    if cur.fetchone() is not None:
                        print(f"  SKIP {trace_file.name}: already in DB")
                        skipped += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO execution_traces
                            (execution_id, task_id, spec_id, content, started_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (execution_id, task_id, spec_id, content, started_at_str),
                    )
                conn.commit()
                print(f"  INSERT {trace_file.name}")
                inserted += 1

            except Exception as exc:
                print(f"  ERROR {trace_file.name}: {exc}")
                errors += 1

    print(f"\nDone: {inserted} inserted, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
