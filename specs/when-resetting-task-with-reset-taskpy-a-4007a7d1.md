# Spec: inject-qa-failure-context

## Objective

When a task is reset to `ready_for_implementation` (via `task-reset.py --reuse-spec`) after being BLOCKED with a QA failure reason, the agent executing the retry should see the previous QA failure text appended to its prompt. This is handled in `ContextAssembler` so that the spec stays clean and all context enrichment lives in one place.

## Success Criteria
- [ ] `build_prompt` in `core/context_assembler.py` accepts an optional `qa_feedback: str | None` parameter and, when present, appends a `## Previous Attempt Feedback` section after the spec and before the completion instructions
- [ ] `ContextAssembler.assemble()` looks up the task's event history for the most recent `TASK_STATUS_CHANGED` event with `to_status == "blocked"` and a non-empty `failure_reason` payload field; passes this value as `qa_feedback` to `build_prompt`
- [ ] When no prior BLOCKED event exists (first execution), prompt is unchanged from current behavior
- [ ] Unit tests cover: prompt includes QA feedback when available, prompt excludes section when no BLOCKED history, section appears after spec and before completion instructions
- [ ] All existing tests continue to pass

## Out of Scope
- Changes to `task-reset.py` itself
- Storing QA feedback as a separate entity or on the spec
- Including feedback from older BLOCKED events (only the most recent)

## Technical Context

- `core/context_assembler.py:86` — `build_prompt(intent_content, spec_content)` returns the assembled prompt string
- `core/context_assembler.py:106` — `ContextAssembler.assemble(execution_id)` extracts `task_id` from the `EXECUTION_STARTED` event payload; already has store access to replay task events
- BLOCKED events are `TASK_STATUS_CHANGED` events (`aggregate_type="task"`) with payload `{"to_status": "blocked", "failure_reason": "..."}` — see `runner.py:397-401` and `state_machine.py:75-79`
- `core/tests/test_context_assembler.py` — existing test patterns to follow; uses `InMemoryStore`, seeds via `_seed_store` helper

## Tasks
- [ ] Add `qa_feedback: str | None = None` parameter to `build_prompt` in `core/context_assembler.py`; when provided, append `\n\n## Previous Attempt Feedback\n{qa_feedback}` between the spec section and `_COMPLETION_INSTRUCTIONS`
- [ ] In `ContextAssembler.assemble()`, after resolving `task_id`, replay task events to find the most recent `TASK_STATUS_CHANGED` event where `payload["to_status"] == "blocked"` and `payload.get("failure_reason")`; store as `qa_feedback: str | None`
- [ ] Pass `qa_feedback` to `build_prompt`
- [ ] Add unit tests in `core/tests/test_context_assembler.py`:
  - `test_build_prompt_includes_qa_feedback_section_when_provided` — verifies `## Previous Attempt Feedback` and feedback text present
  - `test_build_prompt_excludes_qa_feedback_section_when_none` — verifies section absent when `qa_feedback=None`
  - `test_build_prompt_qa_feedback_section_order` — verifies spec appears before feedback, feedback before completion instructions
  - `test_assemble_includes_qa_feedback_when_task_was_blocked` — seeds store with a BLOCKED `TASK_STATUS_CHANGED` event, verifies feedback in assembled prompt
  - `test_assemble_excludes_qa_feedback_when_task_never_blocked` — no BLOCKED event, section absent

## Assumptions
- Only `to_status == "blocked"` events with a non-empty `failure_reason` are considered; other BLOCKED transitions (e.g., implementation BLOCKED without a reason) are skipped silently
- Most recent qualifying BLOCKED event wins; the replay iterates in sequence order and the last match is used

## Verification Commands
```bash
cd /home/lukasz/Code/ratchet
.venv/bin/python -m pytest core/tests/test_context_assembler.py -v
.venv/bin/python -m pytest core/tests/ -v
```

## What Exists After This Spec

`ContextAssembler.assemble()` automatically enriches the execution prompt with QA failure feedback whenever the task has a prior BLOCKED history. No script changes are required — any re-execution after a QA block will carry the failure context forward to the agent.