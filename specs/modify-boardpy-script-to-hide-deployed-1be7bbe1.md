# Spec 1: Hide deployed tasks by default in board.py

## Objective
Modify `scripts/board.py` to exclude `DEPLOYED` tasks from the default board view, and add a `--deployed` flag that shows only deployed tasks (mirroring the existing `--abandoned` pattern).

## Success Criteria
- [ ] Default board view does not show tasks with status `ev.DEPLOYED`
- [ ] `--deployed` flag shows only deployed tasks with count, formatted identically to `--abandoned`
- [ ] `ev.DEPLOYED` is removed from `STATUS_ORDER`
- [ ] Running `python scripts/board.py` excludes deployed tasks
- [ ] Running `python scripts/board.py --deployed` shows only deployed tasks and exits

## Out of Scope
- Changes to any file other than `scripts/board.py`
- Changes to `STATUS_LABELS`

## Technical Context
`scripts/board.py` currently includes `ev.DEPLOYED` in `STATUS_ORDER` (line 22), so deployed tasks appear in the default board. The `--abandoned` pattern (lines 107–121) provides the exact model to follow for `--deployed`. In the default path, `ev.ABANDONED` is skipped via an explicit `continue` check (lines 126–127); the same check must be added for `ev.DEPLOYED`.

## Tasks
- [ ] Add `--deployed` argument to the `argparse` parser (after `--abandoned`, same pattern)
- [ ] Add `--deployed` handling block after the `--abandoned` block (lines 107–121): filter `all_tasks` for `ev.DEPLOYED`, print count and tasks, `return`
- [ ] Remove `ev.DEPLOYED` from `STATUS_ORDER` list (line 21)
- [ ] Add `if status == ev.DEPLOYED: continue` check alongside the existing `ev.ABANDONED` skip (lines 126–127)

## Assumptions
- `ev.DEPLOYED` is already defined in `core/events.py`
- `STATUS_LABELS[ev.DEPLOYED]` already exists (line 32) — no change needed

## Verification Commands
```bash
# Confirm deployed tasks are excluded from default board
.venv/bin/python scripts/board.py

# Confirm --deployed flag shows only deployed tasks
.venv/bin/python scripts/board.py --deployed

# Confirm --abandoned still works
.venv/bin/python scripts/board.py --abandoned
```

## What Exists After This Spec

`scripts/board.py` has a `--deployed` flag. The default board view shows only active tasks (excludes both `ABANDONED` and `DEPLOYED`). Passing `--deployed` shows only deployed tasks.