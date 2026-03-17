# Changelog

## [Unreleased]

### Added
- `web/app.py` auto-starts embedded `WorkerService` on lifespan startup and stops it on shutdown; reads `WORKER_ENABLED`, `WORKER_WATCHDOG_TIMEOUT`, `WORKER_MAX_WORKERS`, `WORKER_CAPABILITIES` from env vars; exposes `app.state.worker_log_buffer`
- Auto-merge for local deployment mode via `merge_once()` in `worker/runner.py`; records `TASK_AUTO_MERGE_FAILED` event on failure instead of transitioning to `BLOCKED`
- `core/merge.py`: `MergeResult` dataclass and `squash_merge()` helper encapsulating worktree creation, squash merge, Claude-assisted conflict resolution, commit, ref update, and branch cleanup
- Feature lifecycle status (`idea`, `in_clarification`, `defined`, `generated`, `in_progress`, `done`) now included in `GET /api/features` and `GET /api/features/{feature_id}` responses
- Feature status badges in web UI features list (`FeaturesPage`) and feature detail (`FeatureDetailPage`) pages
- `--features` flag on `scripts/board.py` to display feature board grouped by lifecycle status
- `execution_traces` Postgres table stores trace content; `EXECUTION_TRACE_RECORDED` event marks each trace in the event log
- `ExecutionTrace` Pydantic model in `core/models.py`
- `Store.save_trace()` and `Store.get_trace()` protocol methods; implemented in `InMemoryStore` and `PostgresStore`
- `scripts/migrate-traces.py`: one-off migration of existing `.md` trace files into `execution_traces` table
- `FEATURE_IDEA = "idea"` and `FEATURE_IN_CLARIFICATION = "in_clarification"` feature status constants in `core/events.py`

### Changed
- `scripts/merge-task.py` refactored to call `squash_merge()` from `core/merge.py` instead of inline git subprocess calls
- `ClaudeCodeInvoker` now accepts `store: Store` (required); traces saved to DB instead of filesystem
- `InvocationResult.trace_path` replaced with `trace_id: UUID`
- `web/routes/api/executions.py` reads traces via `store.get_trace()` instead of filesystem
- `core/review_collector.py` reads traces via `store.get_trace()` instead of filesystem
- Renamed `FEATURE_DRAFT` to `FEATURE_DEFINED = "defined"` in `core/events.py`; `FeatureManager.get_feature_status()` now returns `"defined"` where it previously returned `"draft"`
- Board tab and Focus tab pipeline strip no longer render the `spec_qa` column/stage; the status remains functional in the backend and API

### Fixed
- QA pipeline `build-spa` step now runs `npm ci` first so worktrees without `node_modules` can build the SPA
- Web UI deploy endpoint now runs deploy hooks (SPA build was being skipped on UI-triggered deploys)
- `test_task_fields` expected set updated to include `merge_commit_sha`; migration `c1d2e3f4a5b6` adds `merge_commit_sha` column to `current_tasks` view

### Added
- scripts/run-review.py: CLI entry point for the Retrospective Insight Reviewer
- PR-based deployment mode: `deployment: {mode: pr, base_branch: develop}` in `ratchet.yaml` causes the deploy endpoint to push the execution branch and open a GitHub PR instead of squash-merging locally
- `DeploymentConfig` dataclass and `load_deployment_config()` in `core/qa_runner.py`; returns `DeploymentConfig(mode="local")` when section is absent
- `TASK_PR_CREATED` event constant in `core/events.py` with payload `{pr_url, pr_number, branch}`
- `poll_pr_merges()` in `worker/runner.py` polls GitHub every 300 seconds and transitions merged PRs from `ready_for_deployment` to `deployed`
- JobDispatcher in orchestrator/dispatcher.py with dispatch_pending, _dispatch_one, and dispatch_loop
- `remote_worker/` package: `ClaudeAuthError`, `verify_claude_auth()`, `RemoteWorkerClient` with WebSocket reconnect loop; `python -m remote_worker --orchestrator URL --capabilities ...` CLI
- `AssignTaskMessage.git_bundle_b64: str` and `ExecutionCompletedMessage.patch: str` fields in `core/remote_protocol.py`
- `websockets>=12.0` runtime dependency
- `get_next_task()` filters tasks by capability match: tasks whose `required_capabilities` is not a subset of the worker's `local_capabilities` are skipped silently
- `--capabilities CAP1,CAP2` CLI flag on `python -m worker` to specify comma-separated local worker capabilities
- `GET /api/tasks/{task_id}/qa` endpoint returns full Q&A history and pending question for a task
- `POST /api/tasks/{task_id}/answer` endpoint records a human answer to a pending question, validated against `WAITING_FOR_INPUT` status; `answered_by` is always `"spa"`
- Post-deploy hooks via `deploy:` section in `ratchet.yaml`; `deploy-task.py` and web deploy route run all steps unconditionally, record `task.deploy_hooks_run` event with full results, and always transition to `deployed`; `--skip-deploy-hooks` CLI flag and matching web checkbox bypass hook execution entirely
- "New Task" link on project detail page pointing to `/projects/{id}/tasks/new`
- Dependency `<select>` in new-task form now excludes tasks in `deployed` or `abandoned` status
- Read-only web UI (`python -m web`) with board (`GET /`), projects (`GET /projects`, `GET /projects/{id}`), and task detail (`GET /tasks/{id}`) pages rendered via Jinja2 templates; `web/board_builder.py` extracts replay helpers from `scripts/board.py` and is reused by both CLI and web routes
- `core/compiler.py` with HLS compilation logic extracted from `scripts/compile-feature.py`: `run_claude`, `build_compile_prompt`, `extract_spec`, `get_task_status`, `is_eligible`, `compile_hls`, `compile_all`
- `compile_once(store)` in `worker/runner.py` that invokes `compile_all` and returns True if any HLS was compiled
- DB migration `d2e4f6a8b1c3` adds `notify_compilation_trigger` PL/pgSQL function and `trg_notify_compilation` trigger firing on `high_level_spec.added` and `task.status_changed` (deployed) events via `ratchet_compilation_trigger` channel
- `worker/listener.py` now LISTENs on both `ratchet_task_status` and `ratchet_compilation_trigger`; yields typed 3-tuples distinguishing compilation triggers from task status events
- Worker startup catchup calls `compile_once` before `run_once`/`run_qa_once`; single-pass mode does the same
- Unit tests in `core/tests/test_compiler.py` covering `extract_spec`, `is_eligible`, and `compile_all`
- `--verbose` / `-v` flag to `scripts/board.py` to display full 36-char task UUIDs instead of truncated 8-char IDs

### Changed
- `WAITING_FOR_INPUT` state now only allows transitions to `IN_PROGRESS` or `ABANDONED`; `BLOCKED` transition removed
- `transition()` now requires `execution_id` in `extra_payload` when targeting `WAITING_FOR_INPUT`
- worker runs in continuous polling loop by default (30 s idle sleep); add `--once` flag for single-pass exit

### Fixed
- SPA no longer requires a page reload to show baseline QA failures; DB migration `d4e5f6a7b8c9` extends the `notify_task_events` trigger to also fire on `task.baseline_qa_failed` and `task.baseline_qa_retry` events
- QA now runs tool steps (pytest, mypy, ruff) against the execution branch worktree instead of the develop branch, so new/deleted test files are correctly reflected in QA output
- `ClaudeCodeInvoker` strips `CLAUDECODE` env var before spawning claude subprocess to prevent "nested session" failures when worker runs inside a Claude Code session
- `current_projects.updated_at` now reflects `MAX(occurred_at)` across all project events instead of being locked to the creation event
- `current_executions` now includes `branch_name` column extracted from `execution.started` payload, matching the `Execution` Pydantic model
