# Remote Worker

Remote workers allow task execution on machines other than the orchestrator host — including Windows machines running Claude Code natively.

## Architecture

```
Linux orchestrator host                  Remote worker (any OS)
┌────────────────────────────┐           ┌──────────────────────────────┐
│  Postgres                  │           │  python -m remote_worker     │
│  orchestrator/server.py    │◄──────────│    RemoteWorkerClient        │
│    WorkerRegistry          │ WebSocket │    ClaudeCodeInvoker         │
│    JobDispatcher           │──────────►│    git (bundle → clone)      │
│    /ws/worker              │           │    claude CLI                │
└────────────────────────────┘           └──────────────────────────────┘
```

The orchestrator maintains a persistent WebSocket connection with each worker. Tasks are dispatched as git bundles; results come back as unified diffs (patches).

## How It Works

1. Worker connects and sends `worker_hello` with its capabilities list
2. Orchestrator acknowledges with `orchestrator_ack`
3. Dispatcher polls every 2 seconds for `ready_for_implementation` tasks
4. When a matching task is found, orchestrator sends `assign_task` containing:
   - The spec content
   - A base64-encoded git bundle of the project repo (full history)
   - Project metadata (name, intent, ratchet.yaml)
5. Worker clones the bundle to a temp directory, runs `claude -p` with the assembled prompt
6. On success: worker sends `execution_completed` with a unified diff patch
7. On failure: worker sends `execution_failed` with a reason string
8. Orchestrator applies the patch to the main repo and transitions the task to `ready_for_qa`
9. If the worker disconnects mid-execution, the task is returned to `ready_for_implementation`

## Running the Orchestrator

The orchestrator runs on the same host as Postgres and the project repositories.

```bash
# Requires DATABASE_URL
export DATABASE_URL=postgresql+psycopg://ratchet:password@127.0.0.1:5432/ratchet

python -m orchestrator --host 0.0.0.0 --port 8765
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8765` | Bind port |

HTTP endpoints:

- `GET /health` — returns `{"status": "ok", "workers": N}`
- `GET /workers` — lists connected workers with capabilities and current job

## Running a Remote Worker

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | With project dependencies installed |
| `claude` CLI | Must be in PATH and authenticated |
| `git` | Must be in PATH |
| PostgreSQL client library | Required by psycopg (`libpq`). On Windows: install via the [PostgreSQL Windows installer](https://www.postgresql.org/download/windows/) and ensure `libpq.dll` is on PATH |
| Network access | Must reach orchestrator on port 8765 (HTTP/WebSocket) |

No database access is required — the worker communicates exclusively through the orchestrator.

### Starting a Worker

```bash
python -m remote_worker --orchestrator http://<orchestrator-host>:8765 --capabilities claude_code,python
```

Options:

| Flag | Required | Description |
|------|----------|-------------|
| `--orchestrator` | Yes | Orchestrator base URL, e.g. `http://192.168.1.10:8765` |
| `--capabilities` | No | Comma-separated capability tags advertised to the orchestrator |

On startup the worker verifies that `claude --version` works and makes a test API call. If either fails, it exits with a clear error before attempting to connect.

The worker reconnects automatically on disconnect with exponential backoff (1 s → 2 s → 4 s … capped at 60 s).

### Windows Setup

Windows workers run natively — no WSL required.

1. Install [Python 3.11+](https://www.python.org/downloads/windows/)
2. Install [Git for Windows](https://git-scm.com/download/win)
3. Install [Claude Code](https://claude.ai/download) and authenticate: `claude login`
4. Install [PostgreSQL client libraries](https://www.postgresql.org/download/windows/) (needed for `libpq.dll`)
5. Install project dependencies:
   ```cmd
   pip install websockets pydantic
   ```
6. Start the worker:
   ```cmd
   python -m remote_worker --orchestrator http://<host>:8765 --capabilities claude_code
   ```

`nix develop` wrapping is automatically skipped on Windows even if the project contains `flake.nix`.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `RATCHET_TRACES_DIR` | Where to write execution trace files. Defaults to `~/.local/share/ratchet/traces/` (or `%USERPROFILE%\.local\share\ratchet\traces\` on Windows) |

## Capabilities

Capabilities are arbitrary strings used to route tasks to specific workers. A task is only dispatched to a worker whose capability set is a superset of `task.required_capabilities`.

Example usage:

```bash
# Worker with GPU access
python -m remote_worker --orchestrator http://host:8765 --capabilities claude_code,gpu

# Windows-only worker
python -m remote_worker --orchestrator http://host:8765 --capabilities claude_code,windows
```

Tasks declare required capabilities via the `required_capabilities` field. A task with no required capabilities will be dispatched to any available worker.

## Protocol Reference

All messages are JSON over WebSocket with a `type` discriminator field.

### Worker → Orchestrator

| Message | When sent | Key fields |
|---------|-----------|------------|
| `worker_hello` | On connect | `worker_id`, `version`, `capabilities` |
| `heartbeat` | Periodically | `worker_id`, `current_task_id`, `timestamp_utc` |
| `execution_started` | Task picked up | `task_id`, `execution_id`, `timestamp_utc` |
| `execution_completed` | Claude finished successfully | `task_id`, `execution_id`, `patch` (unified diff) |
| `execution_failed` | Claude failed or crashed | `task_id`, `execution_id`, `failure_reason` |
| `question_asked` | Claude asked a question | `task_id`, `execution_id`, `question_index`, `question` |
| `log_line` | Diagnostic logging | `level`, `message`, `timestamp_utc` |

### Orchestrator → Worker

| Message | When sent | Key fields |
|---------|-----------|------------|
| `orchestrator_ack` | After `worker_hello` | `accepted`, `message` |
| `assign_task` | Task dispatched | `task_id`, `spec_id`, `spec_content`, `git_bundle_b64`, project metadata |
| `provide_answer` | Answering a question | `task_id`, `question_index`, `answer` |
| `cancel_task` | Cancellation requested | `task_id`, `reason` |

### Git Transfer

- **Orchestrator → Worker**: full git bundle (`git bundle create --all HEAD`) base64-encoded inside `assign_task`
- **Worker → Orchestrator**: unified diff (`git diff HEAD`) as a plain string inside `execution_completed`

The worker clones from the bundle into a temporary directory, runs Claude Code there, then diffs against HEAD. The orchestrator applies the diff to the worktree created for the execution using `git apply`.

## Disconnect Handling

If a worker disconnects while executing a task:

1. Orchestrator records a `TASK_WORKER_DISCONNECTED` audit event
2. Execution is marked as failed with reason `"worker disconnected"`
3. Task is returned to `ready_for_implementation`
4. The task will be re-dispatched to the next available worker
