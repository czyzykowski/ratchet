# Spec N: Auto-pass task title as opening description in create-spec.py

## Objective
Remove the manual "Describe what you want to build:" input prompt from `scripts/create-spec.py` and instead pass the task title automatically as the opening user description sent to Claude.

## Success Criteria
- [ ] Lines 308–315 in `scripts/create-spec.py` (the `print` + `input` for user description) are removed
- [ ] `build_initial_prompt` is called with `task_title or ""` as `user_description`
- [ ] The history entry `{"role": "user", "content": ...}` uses the task title, not user input
- [ ] Running the script skips straight to Claude's first response without waiting for user input
- [ ] The script still prints `Task:` and `Project:` lines before invoking Claude

## Out of Scope
- Adding an optional `--description` CLI flag
- Allowing the user to augment or override the title before sending
- Any changes to `build_initial_prompt`, `build_continuation_prompt`, or `build_generation_prompt` signatures

## Technical Context
- File: `scripts/create-spec.py`
- `task_title` is already available at the point of the change (loaded via `load_task_info` at line 274)
- `build_initial_prompt(intent_md, task_title, user_description)` — third argument becomes `task_title or ""`
- The conversation loop starting at line 327 is unchanged

## Tasks
- [ ] In `scripts/create-spec.py`, remove the `print("\nDescribe what you want to build:")` and `input("> ")` block (lines 308–315)
- [ ] Replace `user_description` variable with `task_title or ""` in the `build_initial_prompt` call and the first history append

## Assumptions
- `task_title` is never `None` in practice for a valid task, but `or ""` guards against it
- The task title is descriptive enough to serve as the opening message without user elaboration

## Verification Commands
```bash
# Confirm the prompt line is gone
grep -n "Describe what you want to build" scripts/create-spec.py
# Should return no results

# Confirm task_title is used as user_description
grep -n "build_initial_prompt" scripts/create-spec.py
```

## What Exists After This Spec
`scripts/create-spec.py` starts the Claude conversation immediately upon launch using the task title as the opening message, with no manual input required from the user before the first Claude response.