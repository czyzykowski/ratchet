#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <spec-file>" >&2
  exit 1
fi

SPEC_FILE="$1"
if [[ ! -f "$SPEC_FILE" ]]; then
  echo "Error: file not found: $SPEC_FILE" >&2
  exit 1
fi

# Load environment
if [[ -f .env ]]; then
  set -a; source .env; set +a
elif [[ -f .env.example ]]; then
  echo "Warning: .env not found, using .env.example defaults" >&2
  set -a; source .env.example; set +a
else
  echo "Error: neither .env nor .env.example found" >&2
  exit 1
fi

DIR="$(dirname "$SPEC_FILE")"
BASENAME="$(basename "$SPEC_FILE" .md)"
OUTPUT_FILE="$DIR/$BASENAME.output.md"

PROMPT="You are executing the implementation spec at \`$SPEC_FILE\`.
Your job is to implement every task in the ## Tasks checklist, then verify every item
in the ## Success Criteria checklist.

## Instructions
1. Read \`$SPEC_FILE\` in full before starting.
2. Read \`CLAUDE.md\` at the repo root for codebase conventions.
3. Check what already exists — do not recreate files that are already correct.
4. Execute each task in the ## Tasks list in order. For each task:
   - Do the work (create/edit files, run commands)
   - If a task cannot be completed, STOP and output the blocker report below.
5. Run every command listed in the spec's ## Verification Commands section.
   Report PASS or FAIL with exact output for each.
6. If all tasks and verifications pass, commit:
   git add -A && git commit -m \"spec($BASENAME): all tasks complete\"
   Include the commit hash in the final report.

## Blocker Report Format
If you hit a blocker, output this and stop:
\`\`\`
BLOCKED: <task name>
Reason: <what went wrong>
Missing:
- <item 1>
User action required:
<exact steps the user must take to unblock>
Resume: re-run this script after fixing the above
\`\`\`

## Constraints
- Working directory: $(pwd)
- Do NOT start or configure Postgres
- Do NOT create Postgres users or databases
- DATABASE_URL and TEST_DATABASE_URL are already set in environment

## Final Report Format
\`\`\`
COMPLETED: $SPEC_FILE
Tasks completed: N/N
Verification:
  <command>: PASS
Files created/modified:
- <file>
Commit: <hash>
\`\`\`"

echo "Running: $SPEC_FILE -> $OUTPUT_FILE"
claude -p "$PROMPT" --allowedTools "Bash,Read,Write,Edit,Glob,Grep" | tee "$OUTPUT_FILE"
