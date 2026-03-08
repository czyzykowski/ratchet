# Spec 14: Interactive Spec Creation Script

## Objective

Add `scripts/create-spec.py` — an interactive conversational script that guides the user through defining a task via back-and-forth dialogue with Claude, then generates a spec in the standard format and assigns it to the task automatically.

## Success Criteria

### Core Flow

- [ ] `scripts/create-spec.py --task-id <uuid>` starts interactive session
- [ ] Script reads task title and project `local_path` from database
- [ ] Script reads `INTENT.md` from `<local_path>/docs/INTENT.md` and injects into initial prompt
- [ ] `claude -p` invoked with `--allowedTools Read,Glob` and `cwd=local_path`
- [ ] Initial prompt includes: INTENT.md content, task title, user's opening description, and conversation instructions (see Prompt section)
- [ ] Claude asks clarifying questions one at a time in terminal
- [ ] User answers in terminal — input read via `input()` prompt
- [ ] Each exchange appended to conversation history (list of `{role, content}` dicts)
- [ ] Conversation continues until spec generation is triggered

### Spec Generation

- [ ] User triggers spec generation by typing `done`, `generate`, or `go`
- [ ] Script sends final message instructing Claude to produce spec in standard format
- [ ] Claude outputs spec preceded by `## SPEC READY` marker on its own line
- [ ] Script detects `## SPEC READY` marker in Claude output
- [ ] Spec content extracted — everything after `## SPEC READY` marker
- [ ] Script displays extracted spec to user and asks: `Save and assign this spec? [y/n]`
- [ ] On `y` — spec written to `specs/<task-short-title>-<task-id[:8]>.md` in Ratchet repo root
- [ ] On `y` — `add-spec.py` logic called directly (not subprocess) to assign spec and advance task
- [ ] On `n` — user can continue conversation or type `done` again to regenerate
- [ ] Confirmation printed: `Spec saved to specs/<filename> and assigned to task`

### Conversation Management

- [ ] History managed as list of `{"role": "user"|"assistant", "content": str}` dicts
- [ ] Each `claude -p` call receives full history serialized as JSON in prompt
- [ ] Claude output appended to history as assistant turn
- [ ] User input appended to history as user turn
- [ ] History passed to next `claude -p` call — full conversation replay each turn
- [ ] `RATCHET_DEBUG=1` prints full prompt sent to Claude each turn

### Error Handling

- [ ] Exits with error if task not found
- [ ] Exits with error if task not in `ready_for_spec` status
- [ ] Exits with error if `INTENT.md` not found at `local_path/docs/INTENT.md`
- [ ] Exits with error if `claude` binary not found in PATH
- [ ] Ctrl+C handled gracefully — prints `Session ended, spec not saved` and exits cleanly
- [ ] If `## SPEC READY` marker not found in Claude output after `done` — prints warning and continues conversation

### General

- [ ] `ruff check .` passes
- [ ] `pytest core/tests/ worker/tests/ -v` passes with no regressions
- [ ] Manually verified: run against a real task, complete full conversation, spec assigned
- [ ] Commit: `git add -A && git commit -m "spec(14): interactive spec creation script"`

## Out of Scope

- Do not implement spec summaries or project memory
- Do not implement semantic search over source
- Do not modify worker, invoker, execution manager, or state machine
- Do not add new database migrations
- Do not implement multi-project support — assumes single project context from task
- Do not implement conversation save/resume

## Technical Context

- Language: Python 3.12
- `claude -p <prompt> --allowedTools Read,Glob` — read-only file access, no execution
- `cwd=local_path` — Claude Code runs in project directory, reads CLAUDE.md automatically
- Conversation history serialized as JSON array in prompt to each `claude -p` call
- `ClaudeCodeInvoker` in `core/invoker.py` can be reused or adapted — but it captures full output to trace file, which may not suit interactive use. Consider calling `claude -p` directly via subprocess with stdout streamed to terminal
- `subprocess.run` with `capture_output=False` streams Claude output directly to terminal
- History injection pattern: append `\n\nConversation so far:\n{json.dumps(history)}` to prompt
- Spec filename: `slugify(task.title[:40]).replace(' ', '-').lower() + '-' + str(task.id)[:8] + '.md'`
- `add-spec.py` logic: import and call `SpecManager` directly rather than subprocess

## Initial Prompt Template

````
You are helping design a software task for the Ratchet project.

## Project Intent
{intent_md_content}

## Task
Title: {task_title}

## Your Role
Help the user think through this task by asking clarifying questions one at a time.
Questions should be specific and concrete — multiple choice where possible, open-ended when necessary.
Only one question per message. No preamble before the question.

After sufficient clarification, describe your understanding of the task in chunks of 200-300 words,
asking after each chunk whether it looks right. Keep to chunked format even when the picture is clear.

When the user types 'done', 'generate', or 'go', produce the spec in this exact format:

## SPEC READY
# Spec N: <title>

## Objective
...

## Success Criteria
- [ ] ...

## Out of Scope
...

## Technical Context
...

## Tasks
- [ ] ...

## Assumptions
...

## Verification Commands
```bash
...
````

## What Exists After This Spec

...

Use the standard Ratchet spec format exactly as shown. Be specific about file paths,
function names, and test requirements. Read the codebase to understand current patterns
before generating the spec.

## User's Opening Description

{user_description}

```

## Conversation History Injection
Each subsequent `claude -p` call appends conversation history to the prompt:
```

{initial_prompt}

## Conversation History

{json.dumps(history, indent=2)}

## Latest User Message

{latest_user_input}

````

## Tasks
- [ ] Create `scripts/create-spec.py`
- [ ] Read task and project from database using PostgresStore
- [ ] Read INTENT.md from project local_path
- [ ] Prompt user for opening description via `input()`
- [ ] Build initial prompt with INTENT.md, task title, description, instructions
- [ ] Implement conversation loop — `claude -p` call, stream output, read user input, append to history
- [ ] Detect `done`/`generate`/`go` trigger — send spec generation instruction
- [ ] Detect `## SPEC READY` marker in output — extract spec content
- [ ] Prompt user to confirm spec — `[y/n]`
- [ ] On confirm — write spec file to `specs/` directory, call SpecManager to assign
- [ ] Handle Ctrl+C gracefully
- [ ] Add `RATCHET_DEBUG=1` support — print full prompt each turn
- [ ] Run `pytest core/tests/ worker/tests/ -v` — confirm no regressions
- [ ] Run `ruff check .` — fix all errors
- [ ] Manual end-to-end verification against a real task
- [ ] Commit: `git add -A && git commit -m "spec(14): interactive spec creation script"`

## Assumptions
- Postgres running on 127.0.0.1:5432, DATABASE_URL set
- `claude` binary available in PATH
- Task is in `ready_for_spec` status
- Project has `docs/INTENT.md` at `local_path`
- Ratchet repo root is current working directory when script is invoked
- `specs/` directory exists at Ratchet repo root
- `.venv/bin/python` used for invocation

## Verification Commands
```bash
ruff check .
pytest core/tests/ worker/tests/ -v
````

## What Exists After This Spec

```
scripts/
  create-spec.py  — interactive conversational spec creation
                    takes task-id, guides user through Q&A with Claude,
                    generates spec in standard format, assigns to task automatically
```

Writing specs no longer requires manual authoring. The user describes a task conversationally,
Claude asks clarifying questions with access to the full codebase, and the resulting spec
is automatically formatted and assigned. This is the primary interface for spec creation going forward.
