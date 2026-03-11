"""Unit tests for GET/POST /tasks/{id}/deploy."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from core import events as ev
from core.store import InMemoryStore
from web.routes.tasks import router

_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(SessionMiddleware, secret_key="test-secret")
    test_app.state.store = store
    test_app.state.pool = MagicMock()
    test_app.include_router(router)

    @test_app.get("/_session")
    async def session_peek(request: Request) -> JSONResponse:
        return JSONResponse(dict(request.session))

    return test_app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store), follow_redirects=False)


async def _setup_task(
    store: InMemoryStore,
    task_id: UUID,
    project_id: UUID,
    title: str,
    status: str,
    branch_name: str | None = None,
) -> None:
    """Set up a task in the store with the given status."""
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": ev.READY_FOR_SPEC,
        },
    )
    if status != ev.READY_FOR_SPEC:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={"from_status": ev.READY_FOR_SPEC, "to_status": status},
        )
    if branch_name:
        execution_id = uuid4()
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={
                "execution_id": str(execution_id),
                "branch_name": branch_name,
            },
        )


def _fake_task(task_id: UUID, project_id: UUID, title: str, status: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": task_id,
        "project_id": project_id,
        "title": title,
        "status": status,
        "current_spec_id": None,
        "refinement_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _fake_project(project_id: UUID, local_path: str = "/tmp/repo") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": project_id,
        "name": "My Project",
        "repo_url": local_path,
        "local_path": local_path,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_deploy_confirm_wrong_status(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(store, task_id, project_id, "My Task", ev.IN_PROGRESS)

    with patch("web.routes.tasks.PostgresStore") as mock_store_cls:
        mock_store_cls.return_value = store
        response = client.get(f"/tasks/{task_id}/deploy")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deploy_skip_merge(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run") as mock_subprocess,
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on"},
        )

    # Assert no subprocess calls were made
    mock_subprocess.assert_not_called()

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    session = client.get("/_session").json()
    assert "Deployed" in session.get("flash", "")


@pytest.mark.asyncio
async def test_deploy_git_failure(store: InMemoryStore, client: TestClient) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    git_error = subprocess.CalledProcessError(1, ["git", "checkout"], stderr=b"branch not found")

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run", side_effect=git_error),
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop"},
        )

    assert response.status_code == 400
    assert "branch not found" in response.text


@pytest.mark.asyncio
async def test_deploy_runs_hooks_and_records_event(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id, local_path="/tmp/repo")

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    from core.qa_runner import QaConfig, QaStep, QaStepResult

    fake_deploy_config = QaConfig(steps=[QaStep(name="notify", command="echo deployed")])
    fake_hook_results = [
        QaStepResult(step_name="notify", command="echo deployed", returncode=0, output="deployed\n")
    ]

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run"),
        patch(
            "web.routes.tasks.load_deploy_config", return_value=fake_deploy_config
        ) as mock_load,
        patch(
            "web.routes.tasks.run_deploy_steps", return_value=fake_hook_results
        ) as mock_run,
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on"},
        )

    assert response.status_code == 303
    mock_load.assert_called_once_with("/tmp/repo")
    mock_run.assert_called_once_with(fake_deploy_config, "/tmp/repo")

    task_events = await store.get_events(task_id, "task")
    hook_events = [e for e in task_events if e.event_type == ev.TASK_DEPLOY_HOOKS_RUN]
    assert len(hook_events) == 1
    assert hook_events[0].payload["steps"][0]["name"] == "notify"
    assert hook_events[0].payload["steps"][0]["returncode"] == 0


@pytest.mark.asyncio
async def test_deploy_skip_deploy_hooks_skips_hook_execution(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run"),
        patch("web.routes.tasks.load_deploy_config") as mock_load,
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on", "skip_deploy_hooks": "on"},
        )

    assert response.status_code == 303
    mock_load.assert_not_called()

    task_events = await store.get_events(task_id, "task")
    hook_events = [e for e in task_events if e.event_type == ev.TASK_DEPLOY_HOOKS_RUN]
    assert len(hook_events) == 0


@pytest.mark.asyncio
async def test_deploy_no_deploy_section_records_no_event(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id)

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run"),
        patch("web.routes.tasks.load_deploy_config", return_value=None),
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on"},
        )

    assert response.status_code == 303

    task_events = await store.get_events(task_id, "task")
    hook_events = [e for e in task_events if e.event_type == ev.TASK_DEPLOY_HOOKS_RUN]
    assert len(hook_events) == 0


@pytest.mark.asyncio
async def test_deploy_hooks_failure_does_not_prevent_deployed_transition(
    store: InMemoryStore, client: TestClient
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    await _setup_task(
        store, task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT, "feat/my-task"
    )

    fake_task = _fake_task(task_id, project_id, "My Task", ev.READY_FOR_DEPLOYMENT)
    fake_proj = _fake_project(project_id, local_path="/tmp/repo")

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    from core.qa_runner import QaConfig, QaStep, QaStepResult

    fake_deploy_config = QaConfig(steps=[QaStep(name="notify", command="fail-cmd")])
    fake_hook_results = [
        QaStepResult(step_name="notify", command="fail-cmd", returncode=1, output="error\n")
    ]

    with (
        patch("web.routes.tasks.PostgresStore") as mock_store_cls,
        patch("web.routes.tasks.queries.get_task", new_callable=AsyncMock, return_value=fake_task),
        patch(
            "web.routes.tasks.queries.get_project", new_callable=AsyncMock, return_value=fake_proj
        ),
        patch("web.routes.tasks.subprocess.run"),
        patch("web.routes.tasks.load_deploy_config", return_value=fake_deploy_config),
        patch("web.routes.tasks.run_deploy_steps", return_value=fake_hook_results),
    ):
        mock_store_cls.return_value = store
        client.app.state.pool = mock_pool  # type: ignore[union-attr]
        response = client.post(
            f"/tasks/{task_id}/deploy",
            data={"target_branch": "develop", "skip_merge": "on"},
        )

    assert response.status_code == 303

    from core.state_machine import TaskStateMachine

    status = await TaskStateMachine(store).get_current_status(task_id)
    assert status == ev.DEPLOYED
