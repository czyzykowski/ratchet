"""Event store: Store protocol, InMemoryStore, PostgresStore."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from core.models import Event, ExecutionTrace

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


class Store(Protocol):
    async def append_event(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, Any],
        schema_version: int = 1,
    ) -> Event: ...

    async def get_events(
        self,
        aggregate_id: UUID,
        aggregate_type: str | None = None,
    ) -> list[Event]: ...

    async def refresh_views(self) -> None: ...

    def save_trace(self, trace: ExecutionTrace) -> None: ...

    async def get_trace(self, execution_id: UUID) -> ExecutionTrace | None: ...


class InMemoryStore:
    """In-memory event store for testing. Each instance is fully isolated."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq: int = 0
        self._traces: dict[UUID, ExecutionTrace] = {}

    async def append_event(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, Any],
        schema_version: int = 1,
    ) -> Event:
        self._seq += 1
        event = Event(
            id=uuid4(),
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            event_type=event_type,
            payload=payload,
            schema_version=schema_version,
            occurred_at=datetime.now(tz=UTC),
            sequence=self._seq,
        )
        self._events.append(event)
        return event

    async def get_events(
        self,
        aggregate_id: UUID,
        aggregate_type: str | None = None,
    ) -> list[Event]:
        result = [e for e in self._events if e.aggregate_id == aggregate_id]
        if aggregate_type is not None:
            result = [e for e in result if e.aggregate_type == aggregate_type]
        return sorted(result, key=lambda e: e.sequence)

    async def refresh_views(self) -> None:
        pass  # no-op: no materialized views in memory

    def save_trace(self, trace: ExecutionTrace) -> None:
        self._traces[trace.execution_id] = trace

    async def get_trace(self, execution_id: UUID) -> ExecutionTrace | None:
        return self._traces.get(execution_id)


class PostgresStore:
    """Postgres-backed event store. Optionally accepts an external pool."""

    def __init__(self, pool: AsyncConnectionPool | None = None) -> None:
        self._pool = pool

    async def _get_pool(self) -> AsyncConnectionPool:
        if self._pool is not None:
            return self._pool
        from core.db import get_pool  # lazy import — avoids psycopg at test collection time

        return await get_pool()

    async def append_event(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, Any],
        schema_version: int = 1,
    ) -> Event:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO events (
                        aggregate_id, aggregate_type, event_type, payload, schema_version
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, aggregate_id, aggregate_type, event_type, payload,
                              schema_version, occurred_at, sequence
                    """,
                    (
                        str(aggregate_id),
                        aggregate_type,
                        event_type,
                        json.dumps(payload),
                        schema_version,
                    ),
                )
                row = await cur.fetchone()
        assert row is not None
        return Event(
            id=row[0],
            aggregate_id=row[1],
            aggregate_type=row[2],
            event_type=row[3],
            payload=row[4],
            schema_version=row[5],
            occurred_at=row[6],
            sequence=row[7],
        )

    async def get_events(
        self,
        aggregate_id: UUID,
        aggregate_type: str | None = None,
    ) -> list[Event]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                if aggregate_type is not None:
                    await cur.execute(
                        """
                        SELECT id, aggregate_id, aggregate_type, event_type, payload,
                               schema_version, occurred_at, sequence
                        FROM events
                        WHERE aggregate_id = %s AND aggregate_type = %s
                        ORDER BY sequence ASC
                        """,
                        (str(aggregate_id), aggregate_type),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, aggregate_id, aggregate_type, event_type, payload,
                               schema_version, occurred_at, sequence
                        FROM events
                        WHERE aggregate_id = %s
                        ORDER BY sequence ASC
                        """,
                        (str(aggregate_id),),
                    )
                rows = await cur.fetchall()
        return [
            Event(
                id=row[0],
                aggregate_id=row[1],
                aggregate_type=row[2],
                event_type=row[3],
                payload=row[4],
                schema_version=row[5],
                occurred_at=row[6],
                sequence=row[7],
            )
            for row in rows
        ]

    async def refresh_views(self) -> None:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute("SELECT refresh_all_views()")

    def save_trace(self, trace: ExecutionTrace) -> None:
        """Insert trace into execution_traces and append EXECUTION_TRACE_RECORDED event.

        Uses a synchronous psycopg connection since invoke() runs outside the event loop.
        """
        import os

        import psycopg

        from core import events as ev

        dsn = os.environ["DATABASE_URL"]
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """
                INSERT INTO execution_traces (
                    execution_id, task_id, spec_id, content, started_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (execution_id) DO NOTHING
                """,
                (
                    str(trace.execution_id),
                    str(trace.task_id),
                    str(trace.spec_id),
                    trace.content,
                    trace.started_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO events (
                    aggregate_id, aggregate_type, event_type, payload, schema_version
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(trace.execution_id),
                    "execution",
                    ev.EXECUTION_TRACE_RECORDED,
                    json.dumps({"execution_id": str(trace.execution_id)}),
                    1,
                ),
            )
            conn.commit()

    async def get_trace(self, execution_id: UUID) -> ExecutionTrace | None:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT execution_id, task_id, spec_id, content, started_at, created_at
                    FROM execution_traces
                    WHERE execution_id = %s
                    """,
                    (str(execution_id),),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return ExecutionTrace(
            execution_id=row[0],
            task_id=row[1],
            spec_id=row[2],
            content=row[3],
            started_at=row[4],
            created_at=row[5],
        )
