# Spec: Handle merge conflicts in deploy-task.py with Claude-assisted recovery

## Objective
When `git merge --squash` fails during task deployment due to conflicts, attempt a single automated recovery pass using `ClaudeCodeInvoker`. Claude receives the project intent, the original task spec, and the list of conflicted files — it resolves the conflicts and stages the files without committing. If resolution succeeds, deployment continues normally. If it fails, abort the merge and transition the task to `blocked`.

## Success Criteria
- [ ] When `git merge --squash` exits non-zero, `deploy-task.py` detects the conflict and does not immediately exit
- [ ] Conflicted file list is captured via `git diff --name-only --diff-filter=U`
- [ ] `build_conflict_resolution_prompt()` is added to `core/context_assembler.py` and returns a correctly structured prompt
- [ ] `deploy-task.py` constructs an `ExecutionContext` with `worktree_path=local_path` and invokes `ClaudeCodeInvoker`
- [ ] On `result.status == "completed"`: deployment commits and cleans up branch as normal
- [ ] On any other status: `git merge --abort` is run, task transitions to `blocked` with failure reason including merge conflict output and Claude's failure reason
- [ ] `spec_id` is extracted from the `EXECUTION_STARTED` event payload alongside `branch_name` in `deploy-task.py`
- [ ] Unit tests for `build_conflict_resolution_prompt()` in `core/tests/test_context_assembler.py` verify prompt structure

## Out of Scope
- Retry loops (single attempt only)
- Automated conflict resolution without Claude
- Changes to the worker loop or execution pipeline
- Conflict resolution for rebase-based workflows

## Technical Context

**`deploy-task.py`** runs `git merge --squash <branch_name>` in `project.local_path`. The `EXECUTION_STARTED` event payload (already fetched as `execution_events`) contains `branch_name`, `spec_id`, and `task_id`.

**`context_assembler.py`** exposes standalone `read_intent(worktree_path)` and `build_prompt(intent_content, spec_content)` functions. `ExecutionContext` is a plain dataclass. A new `build_conflict_resolution_prompt(intent_content, spec_content, conflicted_files, merge_output)` function will assemble the recovery prompt without reusing the standard commit-before-COMPLETED instructions.

**`ClaudeCodeInvoker.invoke(context)`** runs `claude -p <prompt>` as a subprocess with `cwd=context.worktree_path`. Returns `InvocationResult` with `status` one of `completed | failed | crashed`.

The conflict-resolution prompt must instruct Claude to:
- Resolve all conflicts in the listed files
- Run `git add <file>` for each resolved file
- NOT commit (the deployment script commits)
- Output `COMPLETED:` when all conflicts are staged, or `BLOCKED:` with reason if it cannot resolve

## Tasks
- [ ] In `core/context_assembler.py`: add `build_conflict_resolution_prompt(intent_content: str, spec_content: str, conflicted_files: list[str], merge_output: str) -> str` that assembles a prompt with sections: project intent, original spec, conflict details (file list + raw merge output), and conflict-specific completion instructions (stage only, no commit)
- [ ] In `core/tests/test_context_assembler.py`: add tests for `build_conflict_resolution_prompt` — verify all four sections appear in output, verify conflicted filenames are included, verify the prompt does NOT contain the standard commit instruction
- [ ] In `scripts/deploy-task.py`: when fetching `branch_name` from `EXECUTION_STARTED` payload, also extract `spec_id` from the same event
- [ ] In `scripts/deploy-task.py`: wrap the `git merge --squash` call to catch `CalledProcessError`, then:
  1. Capture merge stderr as `merge_output`
  2. Run `git diff --name-only --diff-filter=U` to get `conflicted_files`
  3. Fetch spec content from store by replaying `spec` events for `spec_id`
  4. Read `intent_content` via `read_intent(local_path)`
  5. Build prompt via `build_conflict_resolution_prompt(...)`
  6. Construct `ExecutionContext(execution_id=uuid4(), task_id=task_id, spec_id=spec_id, worktree_path=local_path, prompt=prompt)`
  7. Call `ClaudeCodeInvoker().invoke(context)`
  8. If `result.status == "completed"`: proceed to `git commit` step as normal
  9. If not: run `git merge --abort`, print failure to stderr, transition task to `blocked` with `failure_reason=f"Merge conflict: {merge_output}\nConflict resolution failed: {result.failure_reason}"`, then `sys.exit(1)`

## Assumptions
- `EXECUTION_STARTED` payload always contains `spec_id` (confirmed from `context_assembler.py` line: `spec_id = UUID(p["spec_id"])`)
- After a failed `git merge --squash`, `git merge --abort` cleanly restores the repo to the pre-merge state
- `ClaudeCodeInvoker` can be instantiated without arguments and used standalone in a script context
- `read_intent()` can read from `local_path` directly (the main repo has `docs/INTENT.md`)
- The `SPEC_CREATED` event stores spec content in `payload["content"]` (confirmed from `ContextAssembler.assemble()`)

## Verification Commands
```bash
# Run unit tests
.venv/bin/python -m pytest core/tests/test_context_assembler.py -v -k "conflict"

# Full test suite
.venv/bin/python -m pytest core/tests/ -v

# Type check
.venv/bin/python -m mypy core/ worker/ web/

# Lint
.venv/bin/ruff check .
```

## What Exists After This Spec

`deploy-task.py` is self-healing for merge conflicts: it makes one Claude-assisted attempt to resolve conflicts before giving up and transitioning the task to `blocked`. `build_conflict_resolution_prompt()` is a tested, standalone utility in `context_assembler.py`. The deployment workflow remains fully manual (script-driven) but no longer requires human intervention for resolvable merge conflicts.