"""Tests for SpecReplSession.ask() multi-turn streaming behaviour."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.claude_repl import SpecReplSession


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
