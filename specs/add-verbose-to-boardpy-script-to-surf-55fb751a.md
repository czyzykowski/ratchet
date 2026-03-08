# Spec 15: Add --verbose flag to board.py to show full task IDs

## Objective

Add a `--verbose` (`-v`) CLI flag to `scripts/board.py`. When passed, task IDs are displayed as full 36-character UUIDs instead of the current 8-character truncated form. All other output remains unchanged.

## Success Criteria
- [ ] `python scripts/board.py` displays 8-char truncated IDs (existing behaviour preserved)
- [ ] `python scripts/board.py --verbose` displays full 36-char UUIDs
- [ ] `python scripts/board.py -v` is accepted as a short alias
- [ ] Output format is otherwise identical in both modes

## Out of Scope
- Showing additional fields (project ID, spec path, timestamps) in verbose mode
- Changes to any other script

## Technical Context

`scripts/board.py` line 109 computes `short_id = str(task["id"])[:8]` and line 114 uses it in the printed line. The fix is to:

1. Parse CLI arguments with `argparse` at the top of `main()`.
2. Compute `task_id_display = str(task["id"]) if args.verbose else str(task["id"])[:8]`.
3. Replace `short_id` with `task_id_display` in the `print(f"  [{...}]...")` call.

No new dependencies needed — `argparse` is stdlib.

## Tasks
- [ ] Add `argparse` import to `scripts/board.py`
- [ ] Parse `--verbose` / `-v` flag in `main()` before any database access
- [ ] Replace hardcoded `[:8]` slice with a conditional based on `args.verbose`
- [ ] Update `CHANGELOG.md` under `[Unreleased] > Added`

## Assumptions
- No existing tests cover `board.py` output format; no new tests required for this trivial presentation change.

## Verification Commands
```bash
# Default (truncated)
.venv/bin/python scripts/board.py

# Verbose (full UUIDs)
.venv/bin/python scripts/board.py --verbose
.venv/bin/python scripts/board.py -v
```

## What Exists After This Spec

`scripts/board.py` accepts an optional `--verbose` / `-v` flag. With it, full 36-char task UUIDs appear in the board output; without it, the existing 8-char truncated form is shown.