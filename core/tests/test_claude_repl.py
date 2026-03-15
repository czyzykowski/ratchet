"""Tests for SpecReplSession.ask() multi-turn streaming behaviour."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.claude_repl import SpecReplSession, _QueueDone


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def _text_delta_event(text: str) -> bytes:
    return _line({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    })


def _message_start_event() -> bytes:
    return _line({
        "type": "stream_event",
        "event": {"type": "message_start", "message": {}},
    })


def _result_event(session_id: str = "sess-1") -> bytes:
    return _line({
        "type": "result",
        "result": "",
        "session_id": session_id,
    })


def _make_session() -> SpecReplSession:
    return SpecReplSession(
        task_id="test-task",
        system_prompt="test",
        cwd="/tmp",
    )


def _mock_stdout(lines: list[bytes]) -> AsyncMock:
    """Create a mock stdout that returns lines one by one then empty bytes."""
    mock = AsyncMock()
    mock.readline = AsyncMock(side_effect=lines + [b""])
    return mock


@pytest.mark.asyncio
async def test_should_yield_none_boundary_when_message_start_arrives_mid_stream() -> None:
    """ask() yields None between sub-turns when message_start arrives after text."""
    session = _make_session()

    stdout_lines = [
        _message_start_event(),          # first message_start — no text yet, no None
        _text_delta_event("Hello"),
        _text_delta_event(" world"),
        _message_start_event(),           # second message_start — text accumulated, yields None
        _text_delta_event("Second turn"),
        _result_event(),
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = _mock_stdout(stdout_lines)
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()

    with patch.object(session, "ensure_alive", new=AsyncMock()):
        session._proc = mock_proc
        chunks = []
        async for chunk in session.ask("test input"):
            chunks.append(chunk)

    assert None in chunks, "Expected None sentinel between sub-turns"
    none_idx = chunks.index(None)
    text_before = "".join(c for c in chunks[:none_idx] if c is not None)
    text_after = "".join(c for c in chunks[none_idx + 1:] if c is not None)
    assert "Hello world" in text_before or text_before == "Hello world"
    assert text_after == "Second turn"


@pytest.mark.asyncio
async def test_should_not_yield_none_on_first_message_start() -> None:
    """ask() does NOT yield None for the very first message_start (no text yet)."""
    session = _make_session()

    stdout_lines = [
        _message_start_event(),          # first message_start — assistant_text is empty
        _text_delta_event("Only turn"),
        _result_event(),
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = _mock_stdout(stdout_lines)
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()

    with patch.object(session, "ensure_alive", new=AsyncMock()):
        session._proc = mock_proc
        chunks = []
        async for chunk in session.ask("test input"):
            chunks.append(chunk)

    assert None not in chunks, "Should not yield None when first message_start has no prior text"
    assert chunks == ["Only turn"]


@pytest.mark.asyncio
async def test_should_join_sub_turn_texts_in_history_with_double_newline() -> None:
    """History stores sub-turn texts joined with double newline."""
    session = _make_session()

    stdout_lines = [
        _message_start_event(),
        _text_delta_event("First part"),
        _message_start_event(),
        _text_delta_event("Second part"),
        _result_event(),
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = _mock_stdout(stdout_lines)
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()

    with patch.object(session, "ensure_alive", new=AsyncMock()):
        session._proc = mock_proc
        async for _ in session.ask("test input"):
            pass

    assert len(session.history) == 1
    _user, assistant_text, _img_id, _img_mt = session.history[0]
    assert assistant_text == "First part\n\nSecond part"


@pytest.mark.asyncio
async def test_should_persist_via_on_complete_when_sse_is_cancelled() -> None:
    """on_complete is called with full text even when the queue reader stops early."""
    session = _make_session()

    stdout_lines = [
        _message_start_event(),
        _text_delta_event("Hello"),
        _text_delta_event(" world"),
        _result_event(),
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = _mock_stdout(stdout_lines)
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()

    on_complete_calls: list[str] = []

    async def on_complete(full_text: str) -> None:
        on_complete_calls.append(full_text)

    with patch.object(session, "ensure_alive", new=AsyncMock()):
        session._proc = mock_proc
        queue = await session.ask_detached("test input", on_complete)

        # Read only the first chunk, then stop (simulating SSE client disconnect)
        first = await queue.get()
        assert isinstance(first, str), "Expected first queue item to be a text chunk"

        # Background task should complete independently, calling on_complete
        assert session._in_flight_task is not None
        await session._in_flight_task

    assert len(on_complete_calls) == 1
    assert "Hello" in on_complete_calls[0]
    assert "world" in on_complete_calls[0]


@pytest.mark.asyncio
async def test_should_drain_previous_in_flight_before_starting_new_ask() -> None:
    """msg2's ask does not start reading stdout until msg1's task is done.

    Verified by outcome: a single mock stdout serving msg1 then msg2 lines is
    read correctly when ask_detached serialises the two calls.  If msg2 started
    reading before msg1 finished it would receive msg1's remaining lines and
    on_complete would be called with the wrong text.
    """
    session = _make_session()

    # Single mock stdout that serves both responses sequentially.
    all_lines = [
        _message_start_event(),
        _text_delta_event("response1"),
        _result_event("sess-1"),
        _message_start_event(),
        _text_delta_event("response2"),
        _result_event("sess-1"),
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout = _mock_stdout(all_lines)
    mock_proc.stdin = AsyncMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()

    on_complete_calls: list[str] = []

    async def on_complete(full_text: str) -> None:
        on_complete_calls.append(full_text)

    with patch.object(session, "ensure_alive", new=AsyncMock()):
        session._proc = mock_proc

        # Start msg1 — returns queue1 immediately, task1 runs in background.
        # Queue is unbounded so task1 can complete even without a reader.
        await session.ask_detached("msg1", on_complete)

        # ask_detached("msg2") awaits task1 internally before creating task2.
        # Because asyncio.Queue is unbounded, task1 can drain all stdout lines
        # into queue1 and call on_complete even while queue1 is not being read,
        # so the await inside ask_detached unblocks naturally.
        queue2 = await session.ask_detached("msg2", on_complete)

        # task1 must be done before task2 was created, so on_complete("response1")
        # has already been called at this point.
        assert len(on_complete_calls) == 1
        assert on_complete_calls[0] == "response1"

        # Drain queue2 and let task2 finish.
        while True:
            chunk = await queue2.get()
            if isinstance(chunk, _QueueDone):
                break

        if session._in_flight_task:
            await session._in_flight_task

    assert len(on_complete_calls) == 2
    assert on_complete_calls[1] == "response2"
