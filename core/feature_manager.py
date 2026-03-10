"""Feature manager: creates features, manages high-level specs, tracks compilation."""

from __future__ import annotations

from uuid import UUID, uuid4

from core import events as ev
from core.models import Feature, HighLevelSpec
from core.store import Store

# Well-known aggregate ID used as a registry for all feature events.
_FEATURES_REGISTRY_ID = UUID("00000000-0000-0000-0000-000000000002")


class FeatureManager:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def create_feature(
        self,
        project_id: UUID,
        title: str,
        description: str,
    ) -> Feature:
        """Create a new feature.

        Appends FEATURE_CREATED event under both the feature aggregate and the
        project_features registry so list_features(project_id) can find all features.
        Returns Feature model.
        """
        feature_id = uuid4()
        payload = {
            "feature_id": str(feature_id),
            "project_id": str(project_id),
            "title": title,
            "description": description,
        }
        event = await self._store.append_event(
            aggregate_id=feature_id,
            aggregate_type="feature",
            event_type=ev.FEATURE_CREATED,
            payload=payload,
        )
        # Dual-write to registry so list_features(project_id) can replay.
        await self._store.append_event(
            aggregate_id=project_id,
            aggregate_type="project_features",
            event_type=ev.FEATURE_CREATED,
            payload=payload,
        )
        return Feature(
            id=feature_id,
            project_id=project_id,
            title=title,
            description=description,
            created_at=event.occurred_at,
            updated_at=event.occurred_at,
        )

    async def get_feature(self, feature_id: UUID) -> Feature | None:
        """Return feature by id, or None if not found."""
        feature_events = await self._store.get_events(feature_id, "feature")
        for event in feature_events:
            if event.event_type == ev.FEATURE_CREATED:
                p = event.payload
                return Feature(
                    id=UUID(p["feature_id"]),
                    project_id=UUID(p["project_id"]),
                    title=p["title"],
                    description=p["description"],
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                )
        return None

    async def list_features(self, project_id: UUID) -> list[Feature]:
        """Return all features for a project ordered by created_at ascending."""
        registry_events = await self._store.get_events(project_id, "project_features")
        features: dict[UUID, Feature] = {}
        for event in registry_events:
            if event.event_type == ev.FEATURE_CREATED:
                p = event.payload
                fid = UUID(p["feature_id"])
                features[fid] = Feature(
                    id=fid,
                    project_id=UUID(p["project_id"]),
                    title=p["title"],
                    description=p["description"],
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                )
        return sorted(features.values(), key=lambda f: f.created_at)

    async def add_high_level_spec(
        self,
        feature_id: UUID,
        title: str,
        order: int,
        content: str,
        dependencies: list[UUID],
    ) -> HighLevelSpec:
        """Add a high-level spec to a feature.

        Appends HIGH_LEVEL_SPEC_ADDED event under the feature aggregate.
        Returns HighLevelSpec model.
        """
        hls_id = uuid4()
        payload = {
            "hls_id": str(hls_id),
            "feature_id": str(feature_id),
            "title": title,
            "order": order,
            "content": content,
            "dependencies": [str(d) for d in dependencies],
        }
        await self._store.append_event(
            aggregate_id=feature_id,
            aggregate_type="feature",
            event_type=ev.HIGH_LEVEL_SPEC_ADDED,
            payload=payload,
        )
        return HighLevelSpec(
            id=hls_id,
            feature_id=feature_id,
            task_id=None,
            title=title,
            order=order,
            content=content,
            compiled=False,
            dependencies=dependencies,
        )

    async def get_high_level_specs(self, feature_id: UUID) -> list[HighLevelSpec]:
        """Return all high-level specs for a feature in order ascending.

        Replays HIGH_LEVEL_SPEC_ADDED and HIGH_LEVEL_SPEC_COMPILED events.
        """
        feature_events = await self._store.get_events(feature_id, "feature")

        specs: dict[UUID, HighLevelSpec] = {}
        for event in feature_events:
            if event.event_type == ev.HIGH_LEVEL_SPEC_ADDED:
                p = event.payload
                hls_id = UUID(p["hls_id"])
                specs[hls_id] = HighLevelSpec(
                    id=hls_id,
                    feature_id=UUID(p["feature_id"]),
                    task_id=None,
                    title=p["title"],
                    order=p["order"],
                    content=p["content"],
                    compiled=False,
                    dependencies=[UUID(d) for d in p.get("dependencies", [])],
                )
            elif event.event_type == ev.HIGH_LEVEL_SPEC_COMPILED:
                p = event.payload
                hls_id = UUID(p["hls_id"])
                if hls_id in specs:
                    existing = specs[hls_id]
                    specs[hls_id] = HighLevelSpec(
                        id=existing.id,
                        feature_id=existing.feature_id,
                        task_id=UUID(p["task_id"]),
                        title=existing.title,
                        order=existing.order,
                        content=existing.content,
                        compiled=True,
                        dependencies=existing.dependencies,
                    )

        return sorted(specs.values(), key=lambda s: s.order)

    async def mark_compiled(self, hls_id: UUID, task_id: UUID, feature_id: UUID) -> None:
        """Mark a high-level spec as compiled by appending HIGH_LEVEL_SPEC_COMPILED event.

        Sets compiled=True and task_id on the HighLevelSpec.
        """
        await self._store.append_event(
            aggregate_id=feature_id,
            aggregate_type="feature",
            event_type=ev.HIGH_LEVEL_SPEC_COMPILED,
            payload={
                "hls_id": str(hls_id),
                "feature_id": str(feature_id),
                "task_id": str(task_id),
            },
        )

    async def get_feature_status(self, feature_id: UUID) -> str:
        """Derive feature status from its high-level specs and their task statuses.

        Returns one of: draft, generated, in_progress, done.

        - draft: no high-level specs, or none are compiled
        - generated: all compiled specs have tasks in early statuses
        - in_progress: ≥1 task past ready_for_implementation
        - done: all tasks deployed
        """
        specs = await self.get_high_level_specs(feature_id)
        compiled = [s for s in specs if s.compiled]

        if not compiled:
            return ev.FEATURE_DRAFT

        # Fetch task statuses for all compiled specs
        task_statuses: list[str] = []
        for spec in compiled:
            if spec.task_id is None:
                continue
            task_events = await self._store.get_events(spec.task_id, "task")
            status = ev.READY_FOR_SPEC
            for event in task_events:
                if event.event_type == ev.TASK_CREATED:
                    status = event.payload.get("status", ev.READY_FOR_SPEC)
                elif event.event_type == ev.TASK_STATUS_CHANGED:
                    status = event.payload["to_status"]
            task_statuses.append(status)

        if not task_statuses:
            return ev.FEATURE_DRAFT

        early_statuses = {ev.READY_FOR_SPEC, ev.SPEC_QA, ev.READY_FOR_IMPLEMENTATION}
        advanced_statuses = {ev.IN_PROGRESS, ev.BLOCKED, ev.READY_FOR_QA, ev.READY_FOR_DEPLOYMENT}

        if all(s == ev.DEPLOYED for s in task_statuses):
            return ev.FEATURE_DONE

        if any(s in advanced_statuses or s == ev.DEPLOYED for s in task_statuses):
            return ev.FEATURE_IN_PROGRESS

        return ev.FEATURE_GENERATED
