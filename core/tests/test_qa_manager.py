import uuid

import pytest

from core import events as ev
from core.qa_manager import get_pending_question, get_qa_history
from core.store import InMemoryStore


async def _append_question(store, task_id, question_index, question, execution_id):
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_INPUT_REQUESTED,
        payload={
            "question": question,
            "execution_id": str(execution_id),
            "question_index": question_index,
        },
    )


async def _append_answer(store, task_id, question_index, answer, answered_by="cli"):
    await store.append_event(
        aggregate_id=task_id,
        aggregate_type="task",
        event_type=ev.TASK_INPUT_PROVIDED,
        payload={"answer": answer, "question_index": question_index, "answered_by": answered_by},
    )


@pytest.mark.asyncio
async def test_get_qa_history_returns_empty_list_when_no_events():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    result = await get_qa_history(store, task_id)
    assert result == []


@pytest.mark.asyncio
async def test_get_qa_history_returns_unanswered_exchange_when_no_answer_provided():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "some question", execution_id)
    result = await get_qa_history(store, task_id)
    assert len(result) == 1
    exchange = result[0]
    assert exchange.answer is None
    assert exchange.answered_at is None
    assert exchange.answered_by is None
    assert exchange.question == "some question"


@pytest.mark.asyncio
async def test_get_qa_history_pairs_question_and_answer_correctly():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "some question", execution_id)
    await _append_answer(store, task_id, 0, "some answer")
    result = await get_qa_history(store, task_id)
    assert len(result) == 1
    exchange = result[0]
    assert exchange.answer == "some answer"
    assert exchange.answered_by == "cli"
    assert exchange.answered_at is not None


@pytest.mark.asyncio
async def test_get_qa_history_sorted_by_question_index():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 1, "second question", execution_id)
    await _append_question(store, task_id, 0, "first question", execution_id)
    result = await get_qa_history(store, task_id)
    assert result[0].question_index == 0
    assert result[1].question_index == 1


@pytest.mark.asyncio
async def test_get_qa_history_multiple_questions_with_partial_answers():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "question 0", execution_id)
    await _append_question(store, task_id, 1, "question 1", execution_id)
    await _append_answer(store, task_id, 0, "answer 0")
    result = await get_qa_history(store, task_id)
    assert result[0].answer == "answer 0"
    assert result[1].answer is None


@pytest.mark.asyncio
async def test_get_pending_question_returns_none_when_all_answered():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "some question", execution_id)
    await _append_answer(store, task_id, 0, "some answer")
    result = await get_pending_question(store, task_id)
    assert result is None


@pytest.mark.asyncio
async def test_get_pending_question_returns_none_when_no_questions():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    result = await get_pending_question(store, task_id)
    assert result is None


@pytest.mark.asyncio
async def test_get_pending_question_returns_unanswered_question():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "some question", execution_id)
    result = await get_pending_question(store, task_id)
    assert result is not None
    assert result.question_index == 0


@pytest.mark.asyncio
async def test_get_pending_question_returns_highest_index_when_multiple_unanswered():
    store = InMemoryStore()
    task_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await _append_question(store, task_id, 0, "question 0", execution_id)
    await _append_question(store, task_id, 1, "question 1", execution_id)
    result = await get_pending_question(store, task_id)
    assert result is not None
    assert result.question_index == 1
