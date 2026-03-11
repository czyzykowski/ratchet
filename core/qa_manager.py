"""QA manager: retrieves Q&A history and pending questions for a task."""
from __future__ import annotations

from uuid import UUID

from core import events as ev
from core.models import QAExchange
from core.store import Store


async def get_qa_history(store: Store, task_id: UUID) -> list[QAExchange]:
    all_events = await store.get_events(task_id, "task")
    exchanges: dict[int, QAExchange] = {}

    for event in all_events:
        if event.event_type == ev.TASK_INPUT_REQUESTED:
            idx = event.payload["question_index"]
            exchanges[idx] = QAExchange(
                question_index=idx,
                question=event.payload["question"],
                answer=None,
                execution_id=UUID(event.payload["execution_id"]),
                asked_at=event.occurred_at,
                answered_at=None,
                answered_by=None,
            )
        elif event.event_type == ev.TASK_INPUT_PROVIDED:
            idx = event.payload["question_index"]
            if idx in exchanges:
                exchange = exchanges[idx]
                exchange.answer = event.payload["answer"]
                exchange.answered_at = event.occurred_at
                exchange.answered_by = event.payload["answered_by"]

    return sorted(exchanges.values(), key=lambda x: x.question_index)


async def get_pending_question(store: Store, task_id: UUID) -> QAExchange | None:
    history = await get_qa_history(store, task_id)
    unanswered = [x for x in history if x.answer is None]
    if not unanswered:
        return None
    return max(unanswered, key=lambda x: x.question_index)
