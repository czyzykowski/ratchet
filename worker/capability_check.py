"""Capability matching: merge task and project required capabilities."""

from __future__ import annotations

from core.models import Project, Task


def effective_capabilities(task: Task, project: Project) -> set[str]:
    """Return the union of task and project required capabilities."""
    caps: set[str] = set(task.required_capabilities)
    if project.required_capabilities:
        caps |= set(project.required_capabilities)
    return caps
