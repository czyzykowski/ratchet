"""Unit tests for /api/projects endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import events as ev
from core.models import Project
from core.store import InMemoryStore
from web.routes.api.router import api_router

_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_test_app(store: InMemoryStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    app.state.pool = MagicMock()
    app.include_router(api_router)
    return app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return TestClient(_make_test_app(store))


async def _seed_project(
    store: InMemoryStore, project_id: UUID, name: str = "Test Project"
) -> None:
    payload = {
        "project_id": str(project_id),
        "name": name,
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
    store: InMemoryStore, task_id: UUID, project_id: UUID, title: str
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
            "status": ev.READY_FOR_SPEC,
        },
    )


def test_should_return_projects_list(client: TestClient, store: InMemoryStore) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "My Project"))

    response = client.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    assert len(data["projects"]) == 1
    assert data["projects"][0]["name"] == "My Project"


def test_should_return_project_detail_with_tasks_when_project_exists(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    task_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id))
    asyncio.get_event_loop().run_until_complete(_seed_task(store, task_id, project_id, "Task A"))

    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["project"]["id"] == str(project_id)
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["title"] == "Task A"


def test_should_return_404_when_project_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.get(f"/api/projects/{uuid4()}")
    assert response.status_code == 404


def test_should_create_project_and_return_201(
    client: TestClient, store: InMemoryStore
) -> None:
    with patch(
        "web.routes.api.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
    ) as mock_register:
        project_id = uuid4()
        now = datetime.now(UTC)
        mock_register.return_value = Project(
            id=project_id,
            name="New Project",
            repo_url="/tmp/repo",
            local_path="/tmp/repo",
            status="active",
            created_at=now,
            updated_at=now,
        )
        response = client.post(
            "/api/projects", json={"name": "New Project", "path": "/tmp/repo"}
        )

    assert response.status_code == 201
    data = response.json()
    assert data["project"]["name"] == "New Project"


def test_should_return_400_when_project_creation_fails(
    client: TestClient, store: InMemoryStore
) -> None:
    from core.project_manager import OnboardingError

    with patch(
        "web.routes.api.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
    ) as mock_register:
        mock_register.side_effect = OnboardingError("Invalid path")
        response = client.post(
            "/api/projects", json={"name": "Bad Project", "path": "/nonexistent/path"}
        )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data


def test_should_update_project_and_return_200(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "Original"))

    response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "name": "Updated",
            "repo_url": "/tmp/updated",
            "local_path": "/tmp/updated",
            "config_source": "disk",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["project"]["name"] == "Updated"
    assert data["project"]["repo_url"] == "/tmp/updated"


def test_should_return_404_on_patch_when_project_not_found(
    client: TestClient, store: InMemoryStore
) -> None:
    response = client.patch(
        f"/api/projects/{uuid4()}",
        json={
            "name": "X",
            "repo_url": "/x",
            "local_path": "/x",
            "config_source": "disk",
        },
    )
    assert response.status_code == 404


def test_should_update_project_config_when_config_source_is_db(
    client: TestClient, store: InMemoryStore
) -> None:
    project_id = uuid4()
    asyncio.get_event_loop().run_until_complete(_seed_project(store, project_id, "DB Project"))

    response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "name": "DB Project",
            "repo_url": "/tmp/db",
            "local_path": "/tmp/db",
            "config_source": "db",
            "claude_md": "# Claude",
            "intent_md": "# Intent",
            "ratchet_yaml": "steps: []",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["project"]["config_source"] == "db"
    assert data["project"]["claude_md"] == "# Claude"
    assert data["project"]["intent_md"] == "# Intent"
    assert data["project"]["ratchet_yaml"] == "steps: []"


def test_should_create_project_with_config_source_db_and_file_contents(
    client: TestClient, store: InMemoryStore
) -> None:
    with patch(
        "web.routes.api.projects.ProjectManager.register_project",
        new_callable=AsyncMock,
    ) as mock_register:
        project_id = uuid4()
        now = datetime.now(UTC)
        mock_register.return_value = Project(
            id=project_id,
            name="DB Project",
            repo_url="/tmp/db",
            local_path="/tmp/db",
            status="active",
            config_source="db",
            created_at=now,
            updated_at=now,
        )
        # Seed in store so get_project works after update_project_config
        asyncio.get_event_loop().run_until_complete(
            _seed_project(store, project_id, "DB Project")
        )
        response = client.post(
            "/api/projects",
            json={
                "name": "DB Project",
                "path": "/tmp/db",
                "config_source": "db",
                "claude_md": "# Claude",
                "intent_md": "# Intent",
                "ratchet_yaml": "steps: []",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["project"]["claude_md"] == "# Claude"
    assert data["project"]["intent_md"] == "# Intent"
    assert data["project"]["ratchet_yaml"] == "steps: []"
