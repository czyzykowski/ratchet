"""Managers facade: single entry point for all store-backed manager instances."""

from __future__ import annotations

from core.execution_manager import ExecutionManager
from core.feature_manager import FeatureManager
from core.project_manager import ProjectManager
from core.review_manager import ReviewManager
from core.spec_manager import SpecManager
from core.state_machine import TaskStateMachine
from core.store import Store
from core.task_manager import TaskManager


class Managers:
    """Builds all standard managers from a single Store."""

    __slots__ = (
        "store",
        "projects",
        "tasks",
        "specs",
        "state_machine",
        "features",
        "reviews",
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.projects = ProjectManager(store)
        self.tasks = TaskManager(store)
        self.specs = SpecManager(store)
        self.state_machine = TaskStateMachine(store)
        self.features = FeatureManager(store)
        self.reviews = ReviewManager(store)

    def execution(self, local_path: str) -> ExecutionManager:
        """ExecutionManager requires a repo path, so it stays a factory method."""
        return ExecutionManager(self.store, local_path)
