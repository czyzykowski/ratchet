# Spec N: Add --skip-merge to deploy script

## Objective
Add a `--skip-merge` flag to `scripts/deploy-task.py` that, when present, skips all git operations (branch lookup, checkout, squash-merge, commit, branch deletion) and advances the task directly to `deployed` status.

## Success Criteria
- [ ] `--skip-merge` flag accepted by `parse_args()` with no required arguments
- [ ] When `--skip-merge` is set, lines 82–96 (branch lookup from execution events) are skipped entirely
- [ ] When `--skip-merge` is set, lines 101–110 (`git checkout`) are skipped
- [ ] When `--skip-merge` is set, lines 112–124 (`git merge --squash`) are skipped
- [ ] When `--skip-merge` is set, lines 126–137 (`git commit`) are skipped
- [ ] When `--skip-merge` is set, lines 139–149 (`git branch -D`) are skipped
- [ ] Task still transitions to `deployed` via `state_machine.transition(task_id, ev.DEPLOYED)`
- [ ] Success message reads `"Deployed task '<title>' — skipped merge"` when flag is used
- [ ] Without `--skip-merge`, existing behaviour is unchanged

## Out of Scope
- Changes to any other script or module
- Any changes to state machine logic or event types

## Technical Context
`scripts/deploy-task.py` squash-merges an execution branch and advances task status to `deployed`. The branch name is looked up from `EXECUTION_STARTED` events (lines 82–96). When a task was deployed manually or its branch was already cleaned up, the branch no longer exists and the git steps will fail. `--skip-merge` lets operators mark such tasks deployed without touching git.

The project's `local_path` is still needed to resolve the project, but it is not used for any git operations in skip-merge mode. The `local_path` variable and project lookup remain required because they confirm the task is associated with a valid project.

## Tasks
- [ ] Add `--skip-merge` boolean argument to `parse_args()` in `scripts/deploy-task.py`
- [ ] After the project lookup (line 80), branch the logic: if `args.skip_merge`, jump directly to the `state_machine.transition` call, skipping lines 82–149
- [ ] Update the success print at line 157 to use `"Deployed task {title!r} — skipped merge"` when `args.skip_merge` is true, otherwise keep existing message

## Assumptions
- `project_id` and `title` must still be resolved from task events (lines 62–80) even in skip-merge mode, to confirm the task exists and to produce a meaningful success message
- No test file currently exists for `deploy-task.py`; adding one is out of scope for this spec

## Verification Commands
```bash
# Confirm flag appears in help
.venv/bin/python scripts/deploy-task.py --help | grep skip-merge

# Dry-run: task must be in ready_for_deployment — check a real task id
# .venv/bin/python scripts/deploy-task.py --task-id <uuid> --skip-merge
```

## What Exists After This Spec
`scripts/deploy-task.py` accepts `--skip-merge`. Operators can mark tasks as deployed when the execution branch no longer exists, without triggering git errors.