"""Project manager: project registration, validation, and lifecycle."""

from __future__ import annotations

import pathlib
from uuid import UUID, uuid4

from core import events as ev
from core.models import Event, Project
from core.store import Store

# Well-known aggregate ID used as a registry for all project events.
# Dual-writing allows list_projects() to replay all project events without scanning the full store.
_PROJECTS_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000001")


class OnboardingError(Exception):
    """Raised when a repo fails onboarding validation. Message states which requirement failed."""


def validate_repo(local_path: str, config_source: str = "disk") -> None:
    """Validate that local_path meets Ratchet onboarding contract.

    Raises OnboardingError with descriptive message on first failed requirement:
      - "repo path does not exist: <path>"
      - "not a git repository: <path>"
      - "CLAUDE.md not found at repo root: <path>"  (skipped when config_source == "db")
      - "docs/INTENT.md not found: <path>"          (skipped when config_source == "db")
    Returns None on success.
    """
    path = pathlib.Path(local_path)
    if not path.exists():
        raise OnboardingError(f"repo path does not exist: {local_path}")
    if not (path / ".git").exists():
        raise OnboardingError(f"not a git repository: {local_path}")
    if config_source != "db":
        if not (path / "CLAUDE.md").exists():
            raise OnboardingError(f"CLAUDE.md not found at repo root: {local_path}")
        if not (path / "docs" / "INTENT.md").exists():
            raise OnboardingError(f"docs/INTENT.md not found: {local_path}")


class ProjectManager:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def register_project(
        self,
        name: str,
        repo_url: str,
        local_path: str,
        config_source: str = "disk",
        required_capabilities: list[str] | None = None,
    ) -> Project:
        """Validate repo and register as a managed project.

        Calls validate_repo(local_path, config_source) — raises OnboardingError on failure.
        Appends PROJECT_CREATED event on success.
        Returns Project model.
        """
        if required_capabilities is None:
            required_capabilities = []
        validate_repo(local_path, config_source)
        project_id = uuid4()
        payload = {
            "project_id": str(project_id),
            "name": name,
            "repo_url": repo_url,
            "local_path": local_path,
            "status": "active",
            "config_source": config_source,
            "required_capabilities": required_capabilities,
        }
        event = await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project",
            event_type=ev.PROJECT_CREATED,
            payload=payload,
        )
        # Dual-write to registry so list_projects() can replay all project events.
        await self._store.append_event(
            aggregate_id=_PROJECTS_REGISTRY_ID,
            aggregate_type="projects",
            event_type=ev.PROJECT_CREATED,
            payload=payload,
        )
        return Project(
            id=project_id,
            name=name,
            repo_url=repo_url,
            local_path=local_path,
            status="active",
            config_source=config_source,
            required_capabilities=required_capabilities,
            created_at=event.occurred_at,
            updated_at=event.occurred_at,
        )

    async def update_project(
        self,
        project_id: UUID,
        name: str,
        repo_url: str,
        local_path: str,
        config_source: str = "disk",
        required_capabilities: list[str] | None = None,
    ) -> Project:
        """Update scalar fields of an existing project.

        Raises ValueError if project not found.
        Appends PROJECT_UPDATED event (dual-written to registry).
        Returns updated Project.
        """
        if required_capabilities is None:
            required_capabilities = []
        existing = await self.get_project(project_id)
        if existing is None:
            raise ValueError("project not found")
        payload = {
            "project_id": str(project_id),
            "name": name,
            "repo_url": repo_url,
            "local_path": local_path,
            "config_source": config_source,
            "required_capabilities": required_capabilities,
        }
        event = await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project",
            event_type=ev.PROJECT_UPDATED,
            payload=payload,
        )
        await self._store.append_event(
            aggregate_id=_PROJECTS_REGISTRY_ID,
            aggregate_type="projects",
            event_type=ev.PROJECT_UPDATED,
            payload=payload,
        )
        return existing.model_copy(update={
            "name": name,
            "repo_url": repo_url,
            "local_path": local_path,
            "config_source": config_source,
            "required_capabilities": required_capabilities,
            "updated_at": event.occurred_at,
        })

    async def update_project_config(
        self,
        project_id: UUID,
        claude_md: str | None,
        intent_md: str | None,
        ratchet_yaml: str | None,
    ) -> None:
        """Store config file contents for a project in the database.

        Appends PROJECT_CONFIG_UPDATED event (dual-written to registry).
        """
        payload = {
            "project_id": str(project_id),
            "claude_md": claude_md,
            "intent_md": intent_md,
            "ratchet_yaml": ratchet_yaml,
        }
        await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project",
            event_type=ev.PROJECT_CONFIG_UPDATED,
            payload=payload,
        )
        await self._store.append_event(
            aggregate_id=_PROJECTS_REGISTRY_ID,
            aggregate_type="projects",
            event_type=ev.PROJECT_CONFIG_UPDATED,
            payload=payload,
        )

    async def get_project(self, project_id: UUID) -> Project | None:
        """Return project by id, or None if not found.

        Derived by replaying PROJECT_CREATED and PROJECT_CONFIG_UPDATED events.
        """
        project_events = await self._store.get_events(project_id, "project")
        project: Project | None = None
        for event in project_events:
            if event.event_type == ev.PROJECT_CREATED:
                p = event.payload
                project = Project(
                    id=UUID(p["project_id"]),
                    name=p["name"],
                    repo_url=p["repo_url"],
                    local_path=p["local_path"],
                    status=p["status"],
                    config_source=p.get("config_source", "disk"),
                    required_capabilities=p.get("required_capabilities", []),
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                )
            elif event.event_type == ev.PROJECT_UPDATED and project is not None:
                p = event.payload
                project = project.model_copy(update={
                    "name": p["name"],
                    "repo_url": p["repo_url"],
                    "local_path": p["local_path"],
                    "config_source": p.get("config_source", "disk"),
                    "required_capabilities": p.get("required_capabilities", []),
                    "updated_at": event.occurred_at,
                })
            elif event.event_type == ev.PROJECT_CONFIG_UPDATED and project is not None:
                p = event.payload
                project = project.model_copy(update={
                    "claude_md": p.get("claude_md"),
                    "intent_md": p.get("intent_md"),
                    "ratchet_yaml": p.get("ratchet_yaml"),
                    "updated_at": event.occurred_at,
                })
        return project

    async def list_projects(self) -> list[Project]:
        """Return all active (non-archived) projects ordered by created_at ascending.

        Derived by replaying PROJECT_CREATED, PROJECT_CONFIG_UPDATED, and PROJECT_ARCHIVED events.
        """
        registry_events = await self._store.get_events(_PROJECTS_REGISTRY_ID, "projects")

        projects: dict[UUID, Project] = {}
        archived_ids: set[UUID] = set()

        for event in registry_events:
            if event.event_type == ev.PROJECT_CREATED:
                p = event.payload
                project_id = UUID(p["project_id"])
                projects[project_id] = Project(
                    id=project_id,
                    name=p["name"],
                    repo_url=p["repo_url"],
                    local_path=p["local_path"],
                    status=p["status"],
                    config_source=p.get("config_source", "disk"),
                    required_capabilities=p.get("required_capabilities", []),
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                )
            elif event.event_type == ev.PROJECT_UPDATED:
                project_id = UUID(event.payload["project_id"])
                if project_id in projects:
                    p = event.payload
                    projects[project_id] = projects[project_id].model_copy(update={
                        "name": p["name"],
                        "repo_url": p["repo_url"],
                        "local_path": p["local_path"],
                        "config_source": p.get("config_source", "disk"),
                        "required_capabilities": p.get("required_capabilities", []),
                        "updated_at": event.occurred_at,
                    })
            elif event.event_type == ev.PROJECT_CONFIG_UPDATED:
                project_id = UUID(event.payload["project_id"])
                if project_id in projects:
                    p = event.payload
                    projects[project_id] = projects[project_id].model_copy(update={
                        "claude_md": p.get("claude_md"),
                        "intent_md": p.get("intent_md"),
                        "ratchet_yaml": p.get("ratchet_yaml"),
                        "updated_at": event.occurred_at,
                    })
            elif event.event_type == ev.PROJECT_ARCHIVED:
                archived_ids.add(UUID(event.payload["project_id"]))

        active = [p for pid, p in projects.items() if pid not in archived_ids]
        return sorted(active, key=lambda p: p.created_at)

    async def archive_project(self, project_id: UUID) -> Event:
        """Archive a project — it will no longer appear in list_projects().

        Appends PROJECT_ARCHIVED event.
        Returns the appended event.
        """
        payload = {
            "project_id": str(project_id),
            "status": "archived",
        }
        event = await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project",
            event_type=ev.PROJECT_ARCHIVED,
            payload=payload,
        )
        # Dual-write to registry so list_projects() sees the archived status.
        await self._store.append_event(
            aggregate_id=_PROJECTS_REGISTRY_ID,
            aggregate_type="projects",
            event_type=ev.PROJECT_ARCHIVED,
            payload=payload,
        )
        return event
