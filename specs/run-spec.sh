#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <spec-file>" >&2
  echo "  e.g. $0 specs/03-spec-lineage.md" >&2
  exit 1
fi

SPEC_FILE="$1"

if [[ ! -f "$SPEC_FILE" ]]; then
  echo "Error: file not found: $SPEC_FILE" >&2
  exit 1
fi

DIR="$(dirname "$SPEC_FILE")"
BASENAME="$(basename "$SPEC_FILE" .md)"
OUTPUT_FILE="$DIR/$BASENAME.output.md"

PROMPT="You are executing the implementation spec at \`$SPEC_FILE\`. Your job is to implement every task in the ## Tasks checklist, then verify every item in the ## Success Criteria checklist.

## Instructions

1. Read \`$SPEC_FILE\` in full before starting.
2. Check what already exists in the repo — do not recreate files that are already correct.
3. Execute each task in the ## Tasks list in order. For each task:
   - Do the work (create/edit files, run commands)
   - If a task cannot be completed, STOP and report:
     - Which task failed
     - Exact error message or reason
     - What is missing (e.g. missing env var, missing DB user, missing tool)
     - What the user must do to unblock you
4. After all tasks, run the ## Verification Commands:
   \`\`\`
   ruff check .
   python db/smoke_test.py
   alembic upgrade head
   \`\`\`
5. For each verification command, report: PASS or FAIL with exact output.

## Blocker Report Format

If you hit a blocker, output this and stop:

\`\`\`
BLOCKED: <task name>

Reason: <what went wrong>

Missing:
- <item 1>
- <item 2>

User action required:
<exact steps the user must take to unblock>

Resume: re-run this prompt after fixing the above
\`\`\`

## Constraints

- Working directory: /home/lukasz/Code/ratchet
- Do NOT start or configure Postgres
- Do NOT create Postgres users or databases
- Only create files in: core/, db/, docs/, and repo root config files
- Use DATABASE_URL from .env or environment for alembic
- Use TEST_DATABASE_URL from .env or environment for smoke_test.py
- Load .env.example values as defaults if .env is not present, but warn the user

## Final Report Format

After successful completion:

\`\`\`
COMPLETED: $SPEC_FILE

Tasks completed: N/N
Verification:
  ruff check .: PASS
  alembic upgrade head: PASS
  python db/smoke_test.py: PASS

Files created/modified:
- <file 1>
- <file 2>
\`\`\`"

echo "Running: $SPEC_FILE -> $OUTPUT_FILE"
claude -p "$PROMPT" --allowedTools "Bash,Read,Write,Edit,Glob,Grep" | tee "$OUTPUT_FILE"
