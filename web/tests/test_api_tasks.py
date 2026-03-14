"""Unit tests for /api/tasks endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.state.sse_clients = []
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(store: InMemoryStore, project_id: UUID) -> None:
    payload = {
        "project_id": str(project_id),
        "name": "Test Project",
        "repo_url": "/tmp/test",
        "local_path": "/tmp/test",
        "status": "active",
    }
    await store.append_event(
        aggregate_id=_REGISTRY_ID,
        aggregate_type="projects",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project",
        event_type=ev.PROJECT_CREATED,
        payload=payload,
    )


async def _seed_task(
    store: InMemoryStore,
    task_id: UUID,
    project_id: UUID,
    title: str,
    status: str = ev.READY_FOR_SPEC,
) -> None:
    await store.append_event(
        aggregate_id=project_id,
        aggregate_type="project_tasks",
        event_type=ev.TASK_CREATED,
        payload={"task_id": str(task_id), "project_id": str(project_id), "title": title},
    )
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_CREATED,
        payload={
            "task_id": str(task_id),
            "project_id": str(project_id),
            "title": title,
            "status": status,
        },
    )


def test_should_return_task_detail_with_specs_and_executions_when_task_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(_seed_task(store, task_id, project_id, "My Task"))

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["id"] == str(task_id)
    assert data["task"]["title"] == "My Task"
    assert data["specs"] == []
    assert data["executions"] == []
    assert data["qa_failure"] is None


def test_should_return_404_when_task_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/tasks/{uuid4()}")
    assert response.status_code == 404


def test_should_create_task_and_return_201(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))

    response = client.post(
        "/api/tasks", json={"project_id": str(project_id), "title": "New Task"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["task"]["title"] == "New Task"
    assert data["task"]["status"] == ev.READY_FOR_SPEC
    assert data["task"]["project_id"] == str(project_id)


def test_should_update_task_title(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Old Title")
    )

    response = client.patch(f"/api/tasks/{task_id}", json={"title": "New Title"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["id"] == str(task_id)


def test_should_assign_spec_to_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    response = client.post(
        f"/api/tasks/{task_id}/spec", json={"content": "Do the thing"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.READY_FOR_IMPLEMENTATION
    assert data["spec"]["content"] == "Do the thing"
    assert data["spec"]["task_id"] == str(task_id)


def test_should_reset_blocked_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    async def _block_task() -> None:
        # Transition through states to reach BLOCKED
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.READY_FOR_SPEC,
                "to_status": ev.SPEC_QA,
                "status": ev.SPEC_QA,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.SPEC_QA,
                "to_status": ev.READY_FOR_IMPLEMENTATION,
                "status": ev.READY_FOR_IMPLEMENTATION,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.READY_FOR_IMPLEMENTATION,
                "to_status": ev.IN_PROGRESS,
                "status": ev.IN_PROGRESS,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={
                "from_status": ev.IN_PROGRESS,
                "to_status": ev.BLOCKED,
                "status": ev.BLOCKED,
            },
        )

    asyncio.get_event_loop().run_until_complete(_block_task())

    response = client.post(f"/api/tasks/{task_id}/reset")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.READY_FOR_IMPLEMENTATION


def test_should_return_400_when_resetting_non_blocked_task(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    response = client.post(f"/api/tasks/{task_id}/reset")
    assert response.status_code == 400


def test_should_deploy_task(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "My Task", ev.READY_FOR_SPEC)
    )

    async def _setup_deploy() -> None:
        # Advance to READY_FOR_DEPLOYMENT
        for from_s, to_s in [
            (ev.READY_FOR_SPEC, ev.SPEC_QA),
            (ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION),
            (ev.READY_FOR_IMPLEMENTATION, ev.IN_PROGRESS),
            (ev.IN_PROGRESS, ev.READY_FOR_QA),
            (ev.READY_FOR_QA, ev.READY_FOR_DEPLOYMENT),
        ]:
            await store.append_event(
                aggregate_id=task_id,
                aggregate_type="task",
                event_type=ev.TASK_STATUS_CHANGED,
                payload={"from_status": from_s, "to_status": to_s, "status": to_s},
            )
        # Add execution branch record
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task_executions",
            event_type=ev.EXECUTION_STARTED,
            payload={
                "execution_id": str(execution_id),
                "task_id": str(task_id),
                "spec_id": str(uuid4()),
                "branch_name": f"execution/{execution_id}",
                "status": "completed",
            },
        )

    asyncio.get_event_loop().run_until_complete(_setup_deploy())

    with patch("web.routes.api.tasks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        response = client.post(f"/api/tasks/{task_id}/merge")

    assert response.status_code == 200
    data = response.json()
    assert data["task"]["status"] == ev.DEPLOYED
    assert mock_run.call_count >= 3  # checkout, merge, commit


# ── Q&A endpoint tests ─────────────────────────────────────────────────────────


async def _seed_waiting_for_input(store: InMemoryStore, task_id: UUID, project_id: UUID) -> None:
    """Seed a task and transition it through states to WAITING_FOR_INPUT."""
    await _seed_task(store, task_id, project_id, "QA Task", ev.READY_FOR_SPEC)
    for from_s, to_s in [
        (ev.READY_FOR_SPEC, ev.SPEC_QA),
        (ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION),
        (ev.READY_FOR_IMPLEMENTATION, ev.IN_PROGRESS),
        (ev.IN_PROGRESS, ev.WAITING_FOR_INPUT),
    ]:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_STATUS_CHANGED,
            payload={"from_status": from_s, "to_status": to_s, "status": to_s},
        )


def test_should_return_qa_history_for_task(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_waiting_for_input(store, task_id, project_id)
    )

    async def _seed_qa() -> None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_REQUESTED,
            payload={
                "question": "What is the answer?",
                "execution_id": str(execution_id),
                "question_index": 0,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_REQUESTED,
            payload={
                "question": "What is the second question?",
                "execution_id": str(execution_id),
                "question_index": 1,
            },
        )
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_PROVIDED,
            payload={"answer": "42", "question_index": 0, "answered_by": "spa"},
        )

    asyncio.get_event_loop().run_until_complete(_seed_qa())

    response = client.get(f"/api/tasks/{task_id}/qa")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["question_index"] == 0
    assert data["history"][0]["answer"] == "42"
    assert data["history"][1]["question_index"] == 1
    assert data["history"][1]["answer"] is None
    assert data["pending"] is not None
    assert data["pending"]["question_index"] == 1


def test_should_return_empty_qa_when_no_questions_asked(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "No QA Task")
    )

    response = client.get(f"/api/tasks/{task_id}/qa")
    assert response.status_code == 200
    data = response.json()
    assert data["history"] == []
    assert data["pending"] is None


def test_should_submit_answer_to_pending_question(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_waiting_for_input(store, task_id, project_id)
    )

    async def _seed_question() -> None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_REQUESTED,
            payload={
                "question": "Which approach?",
                "execution_id": str(execution_id),
                "question_index": 0,
            },
        )

    asyncio.get_event_loop().run_until_complete(_seed_question())

    response = client.post(
        f"/api/tasks/{task_id}/answer",
        json={"answer": "Option A", "question_index": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exchange"]["answer"] == "Option A"
    assert data["exchange"]["question_index"] == 0
    assert data["exchange"]["answered_by"] == "spa"


def test_should_return_404_when_answering_nonexistent_task(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.post(
        f"/api/tasks/{uuid4()}/answer",
        json={"answer": "anything", "question_index": 0},
    )
    assert response.status_code == 404


def test_should_return_400_when_task_not_waiting_for_input(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Ready Task", ev.READY_FOR_SPEC)
    )

    response = client.post(
        f"/api/tasks/{task_id}/answer",
        json={"answer": "too early", "question_index": 0},
    )
    assert response.status_code == 400


def test_should_return_400_when_question_index_does_not_match_pending(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    execution_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_waiting_for_input(store, task_id, project_id)
    )

    async def _seed_question() -> None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_INPUT_REQUESTED,
            payload={
                "question": "Choose wisely",
                "execution_id": str(execution_id),
                "question_index": 0,
            },
        )

    asyncio.get_event_loop().run_until_complete(_seed_question())

    response = client.post(
        f"/api/tasks/{task_id}/answer",
        json={"answer": "stale answer", "question_index": 5},
    )
    assert response.status_code == 400


def test_should_return_pr_info_when_task_pr_created_event_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Deploy Task")
    )

    async def _seed_pr_event() -> None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_PR_CREATED,
            payload={
                "pr_url": "https://github.com/org/repo/pull/42",
                "pr_number": 42,
                "branch": "feat/my-branch",
            },
        )

    asyncio.get_event_loop().run_until_complete(_seed_pr_event())

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["pr_info"]["pr_url"] == "https://github.com/org/repo/pull/42"
    assert data["pr_info"]["pr_number"] == 42
    assert data["pr_info"]["branch"] == "feat/my-branch"


def test_should_return_deploy_hooks_when_task_deploy_hooks_run_event_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Deploy Task")
    )

    async def _seed_hooks_event() -> None:
        await store.append_event(
            aggregate_id=task_id,
            aggregate_type="task",
            event_type=ev.TASK_DEPLOY_HOOKS_RUN,
            payload={
                "steps": [
                    {
                        "name": "lint",
                        "command": "ruff check .",
                        "returncode": 0,
                        "output": "ok",
                    },
                    {
                        "name": "test",
                        "command": "pytest",
                        "returncode": 1,
                        "output": "failed",
                    },
                ]
            },
        )

    asyncio.get_event_loop().run_until_complete(_seed_hooks_event())

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["deploy_hooks"]) == 2
    assert data["deploy_hooks"][0]["name"] == "lint"
    assert data["deploy_hooks"][0]["returncode"] == 0
    assert data["deploy_hooks"][1]["name"] == "test"
    assert data["deploy_hooks"][1]["returncode"] == 1


def test_should_return_null_pr_info_and_deploy_hooks_when_no_deployment_events(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(
        _seed_task(store, task_id, project_id, "Plain Task")
    )

    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["pr_info"] is None
    assert data["deploy_hooks"] is None
