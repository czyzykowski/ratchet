# Changelog

## [Unreleased]

### Added
- Read-only web UI (`python -m web`) with board (`GET /`), projects (`GET /projects`, `GET /projects/{id}`), and task detail (`GET /tasks/{id}`) pages rendered via Jinja2 templates; `web/board_builder.py` extracts replay helpers from `scripts/board.py` and is reused by both CLI and web routes
- `core/compiler.py` with HLS compilation logic extracted from `scripts/compile-feature.py`: `run_claude`, `build_compile_prompt`, `extract_spec`, `get_task_status`, `is_eligible`, `compile_hls`, `compile_all`
- `compile_once(store)` in `worker/runner.py` that invokes `compile_all` and returns True if any HLS was compiled
- DB migration `d2e4f6a8b1c3` adds `notify_compilation_trigger` PL/pgSQL function and `trg_notify_compilation` trigger firing on `high_level_spec.added` and `task.status_changed` (deployed) events via `ratchet_compilation_trigger` channel
- `worker/listener.py` now LISTENs on both `ratchet_task_status` and `ratchet_compilation_trigger`; yields typed 3-tuples distinguishing compilation triggers from task status events
- Worker startup catchup calls `compile_once` before `run_once`/`run_qa_once`; single-pass mode does the same
- Unit tests in `core/tests/test_compiler.py` covering `extract_spec`, `is_eligible`, and `compile_all`
- `--verbose` / `-v` flag to `scripts/board.py` to display full 36-char task UUIDs instead of truncated 8-char IDs

### Changed
- worker runs in continuous polling loop by default (30 s idle sleep); add `--once` flag for single-pass exit

### Fixed
- QA now runs tool steps (pytest, mypy, ruff) against the execution branch worktree instead of the develop branch, so new/deleted test files are correctly reflected in QA output
- `ClaudeCodeInvoker` strips `CLAUDECODE` env var before spawning claude subprocess to prevent "nested session" failures when worker runs inside a Claude Code session
- `current_projects.updated_at` now reflects `MAX(occurred_at)` across all project events instead of being locked to the creation event
- `current_executions` now includes `branch_name` column extracted from `execution.started` payload, matching the `Execution` Pydantic model
