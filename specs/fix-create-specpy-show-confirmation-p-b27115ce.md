# Spec: Fix create-spec.py — detect SPEC READY in every response

## Objective

After every Claude response in the conversation loop, check whether `## SPEC READY` appears in the output. If it does, immediately show the save confirmation — without requiring the user to type `go`, `done`, or `generate`.

## Success Criteria
- [ ] `## SPEC READY` in any Claude response (trigger-word or not) immediately shows the save confirmation prompt
- [ ] Typing `go`/`done`/`generate` still works and routes through the same confirmation flow
- [ ] If user declines save (`n`), history is preserved and the loop resumes
- [ ] System prompt in `build_initial_prompt` no longer instructs Claude to wait for trigger words
- [ ] Script passes `ruff` and `mypy` without new errors

## Out of Scope
- Changes to `run_claude`, `extract_spec`, `assign_spec_to_task`, `load_task_info`, or the DB layer
- Multi-spec or batch workflows
- Changing the spec file format

## Technical Context

In `scripts/create-spec.py`, the confirmation+save block (lines 308–335) lives entirely inside `if is_trigger:` (line 299). When Claude spontaneously outputs `## SPEC READY` during a regular exchange, `is_trigger` is `False` so the block is skipped and the output is appended to history as if it were a normal message.

The system prompt (line 77) reads:
```
When the user types 'done', 'generate', or 'go', produce the spec in this exact format:
```
This tells Claude to wait for an explicit trigger, which conflicts with the actual observed behavior where Claude produces the spec on its own initiative.

The fix has two parts:
1. Update the system prompt so Claude knows it may produce the spec once it has enough information.
2. Move the `## SPEC READY` detection outside the `if is_trigger:` branch so it runs after every `run_claude` call, including the initial one at line 275.

## Tasks
- [ ] In `build_initial_prompt` (`scripts/create-spec.py:77`), replace:
  ```
  When the user types 'done', 'generate', or 'go', produce the spec in this exact format:
  ```
  with:
  ```
  When you have gathered enough information and confirmed your understanding with the user, produce the spec in this exact format:
  ```
- [ ] Extract the confirmation+save block (lines 308–335) into a helper function `_handle_spec_ready(output: str, task_id: UUID, task_title: str | None, store) -> bool` that returns `True` if the spec was saved, `False` if the user declined or the marker was not found
- [ ] After the initial `run_claude` call at line 275, call `_handle_spec_ready`; if it returns `True`, break out of the entire flow and return
- [ ] In the main loop, after `run_claude` at line 297, call `_handle_spec_ready` regardless of `is_trigger`; if it returns `True`, break
- [ ] If `is_trigger` is `True` and `_handle_spec_ready` returns `False` (marker absent), print the existing warning and continue — preserving current behaviour
- [ ] Remove the now-redundant `if is_trigger:` guard around the detection block; keep `is_trigger` only to choose between `build_generation_prompt` and `build_continuation_prompt`

## Assumptions
- Claude already produces `## SPEC READY` spontaneously without trigger words (confirmed by user)
- No changes needed to the spec file format or DB layer
- `task_title` may be `None` (already handled by existing `if task_title else "spec"` guard)

## Verification Commands
```bash
# Lint and type-check
ruff check scripts/create-spec.py
.venv/bin/python -m mypy scripts/create-spec.py

# Manual test 1: run a session, answer questions, let Claude produce spec on its own
# — confirm save prompt appears without typing go/done/generate
.venv/bin/python scripts/create-spec.py --task-id <any-ready-for-spec-task-uuid>

# Manual test 2: type 'go' explicitly — save prompt must still appear
# Manual test 3: decline ('n') at save prompt — confirm conversation loop resumes
```

## What Exists After This Spec

`scripts/create-spec.py` detects `## SPEC READY` after every Claude response. The user sees the save confirmation immediately when Claude produces the spec, with no extra trigger word required. Typing `go`/`done`/`generate` remains supported as an explicit override.