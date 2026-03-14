from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from core import events as ev
from core.models import ReviewRun, ReviewScope, Suggestion
from core.store import Store

# Fixed registry aggregate ID — all REVIEW_RUN_STARTED events go here
REVIEW_REGISTRY_ID = UUID("00000000-0000-0000-0000-526576696577")  # "Review" in hex


class ReviewManager:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def start_run(self, scope: ReviewScope) -> ReviewRun:
        registry_events = await self._store.get_events(
            REVIEW_REGISTRY_ID, "review_registry"
        )

        previous_run_id: UUID | None = None
        latest_occurred_at: datetime | None = None

        for event in registry_events:
            if event.event_type != ev.REVIEW_RUN_STARTED:
                continue
            payload = event.payload
            prior_project_ids = [
                UUID(pid) for pid in payload.get("scope", {}).get("project_ids", [])
            ]
            prior_include_global = payload.get("scope", {}).get(
                "include_global", False
            )
            overlap = bool(
                set(scope.project_ids) & set(prior_project_ids)
            ) or (scope.include_global and prior_include_global)
            if overlap:
                if latest_occurred_at is None or event.occurred_at > latest_occurred_at:
                    latest_occurred_at = event.occurred_at
                    previous_run_id = UUID(payload["review_run_id"])

        run_id = uuid.uuid4()
        started_at = datetime.now(tz=UTC)
        payload = {
            "review_run_id": str(run_id),
            "scope": scope.model_dump(mode="json"),
            "started_at": started_at.isoformat(),
            "previous_run_id": str(previous_run_id) if previous_run_id else None,
        }

        await self._store.append_event(
            REVIEW_REGISTRY_ID, "review_registry", ev.REVIEW_RUN_STARTED, payload
        )
        await self._store.append_event(
            run_id, "review_run", ev.REVIEW_RUN_STARTED, payload
        )

        return ReviewRun(
            id=run_id,
            scope=scope,
            started_at=started_at,
            completed_at=None,
            suggestion_count=0,
            applied_count=0,
            dismissed_count=0,
            previous_run_id=previous_run_id,
        )

    async def complete_run(
        self, review_run_id: UUID, suggestions: list[Suggestion]
    ) -> ReviewRun:
        applied = sum(1 for s in suggestions if s.status == "applied")
        dismissed = sum(1 for s in suggestions if s.status == "dismissed")
        completed_at = datetime.now(tz=UTC)
        payload = {
            "review_run_id": str(review_run_id),
            "completed_at": completed_at.isoformat(),
            "suggestion_count": len(suggestions),
            "applied_count": applied,
            "dismissed_count": dismissed,
        }

        await self._store.append_event(
            review_run_id, "review_run", ev.REVIEW_RUN_COMPLETED, payload
        )

        result = await self.get_run(review_run_id)
        assert result is not None
        return result

    async def record_suggestion(self, suggestion: Suggestion) -> None:
        payload = suggestion.model_dump(mode="json")
        await self._store.append_event(
            suggestion.review_run_id,
            "review_run",
            ev.REVIEW_SUGGESTION_CREATED,
            payload,
        )

    async def apply_suggestion(
        self, review_run_id: UUID, suggestion_id: UUID, target_path: str
    ) -> None:
        await self._store.append_event(
            review_run_id,
            "review_run",
            ev.REVIEW_SUGGESTION_APPLIED,
            {
                "review_run_id": str(review_run_id),
                "suggestion_id": str(suggestion_id),
                "target_path": target_path,
            },
        )

    async def dismiss_suggestion(
        self, review_run_id: UUID, suggestion_id: UUID
    ) -> None:
        await self._store.append_event(
            review_run_id,
            "review_run",
            ev.REVIEW_SUGGESTION_DISMISSED,
            {
                "review_run_id": str(review_run_id),
                "suggestion_id": str(suggestion_id),
            },
        )

    async def skip_suggestion(
        self, review_run_id: UUID, suggestion_id: UUID
    ) -> None:
        await self._store.append_event(
            review_run_id,
            "review_run",
            ev.REVIEW_SUGGESTION_SKIPPED,
            {
                "review_run_id": str(review_run_id),
                "suggestion_id": str(suggestion_id),
            },
        )

    async def get_run(self, review_run_id: UUID) -> ReviewRun | None:
        run_events = await self._store.get_events(review_run_id, "review_run")
        if not run_events:
            return None

        run_id: UUID | None = None
        scope: ReviewScope | None = None
        started_at: datetime | None = None
        previous_run_id: UUID | None = None
        completed_at: datetime | None = None
        suggestion_count = 0
        applied_count = 0
        dismissed_count = 0

        for event in run_events:
            if event.event_type == ev.REVIEW_RUN_STARTED:
                run_id = UUID(event.payload["review_run_id"])
                scope = ReviewScope(**event.payload["scope"])
                started_at = datetime.fromisoformat(event.payload["started_at"])
                prev = event.payload.get("previous_run_id")
                previous_run_id = UUID(prev) if prev else None
            elif event.event_type == ev.REVIEW_RUN_COMPLETED:
                completed_at = datetime.fromisoformat(event.payload["completed_at"])
                suggestion_count = event.payload["suggestion_count"]
                applied_count = event.payload["applied_count"]
                dismissed_count = event.payload["dismissed_count"]

        if run_id is None or scope is None or started_at is None:
            return None

        return ReviewRun(
            id=run_id,
            scope=scope,
            started_at=started_at,
            completed_at=completed_at,
            suggestion_count=suggestion_count,
            applied_count=applied_count,
            dismissed_count=dismissed_count,
            previous_run_id=previous_run_id,
        )

    async def list_runs(self, limit: int = 20) -> list[ReviewRun]:
        registry_events = await self._store.get_events(
            REVIEW_REGISTRY_ID, "review_registry"
        )
        seen: set[UUID] = set()
        run_ids: list[UUID] = []
        for event in registry_events:
            if event.event_type == ev.REVIEW_RUN_STARTED:
                rid = UUID(event.payload["review_run_id"])
                if rid not in seen:
                    seen.add(rid)
                    run_ids.append(rid)

        runs: list[ReviewRun] = []
        for rid in run_ids:
            run = await self.get_run(rid)
            if run is not None:
                runs.append(run)

        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    async def get_previous_run_summary(self, previous_run_id: UUID) -> str:
        run_events = await self._store.get_events(previous_run_id, "review_run")

        suggestion_data: dict[str, dict[str, Any]] = {}
        for event in run_events:
            if event.event_type == ev.REVIEW_SUGGESTION_CREATED:
                sid = str(event.payload["id"])
                suggestion_data[sid] = dict(event.payload)
                suggestion_data[sid]["status"] = "pending"
            elif event.event_type == ev.REVIEW_SUGGESTION_APPLIED:
                sid = str(event.payload["suggestion_id"])
                if sid in suggestion_data:
                    suggestion_data[sid]["status"] = "applied"
            elif event.event_type == ev.REVIEW_SUGGESTION_DISMISSED:
                sid = str(event.payload["suggestion_id"])
                if sid in suggestion_data:
                    suggestion_data[sid]["status"] = "dismissed"
            elif event.event_type == ev.REVIEW_SUGGESTION_SKIPPED:
                sid = str(event.payload["suggestion_id"])
                if sid in suggestion_data:
                    suggestion_data[sid]["status"] = "skipped"

        n = len(suggestion_data)
        header = f"Previous review run {previous_run_id} — {n} suggestions:"
        lines = [header]
        for s in sorted(suggestion_data.values(), key=lambda x: int(x.get("order", 0))):
            line = (
                f"[{s.get('order')}] {s.get('title')} | "
                f"target: {s.get('target_path')} | "
                f"confidence: {s.get('confidence')} | "
                f"priority: {s.get('priority')} | "
                f"status: {s.get('status')}"
            )
            lines.append(line)

        return "\n".join(lines)
