"""Unit tests for chat image support in claude_repl."""

from __future__ import annotations

import base64
from pathlib import Path

from core.claude_repl import SpecReplSession, make_user_msg


def test_make_user_msg_without_image() -> None:
    msg = make_user_msg("hello", "sess-1")
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["message"]["content"] == "hello"
    assert msg["session_id"] == "sess-1"


def test_make_user_msg_with_image() -> None:
    b64 = base64.b64encode(b"fake-image-bytes").decode()
    msg = make_user_msg("look at this", "sess-1", image_b64=b64, image_media_type="image/png")
    content = msg["message"]["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "look at this"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[1]["source"]["data"] == b64


def test_make_user_msg_with_image_none_b64_falls_back_to_text() -> None:
    """If image_b64 is None but image_media_type is provided, fall back to text-only."""
    msg = make_user_msg("hello", "sess-1", image_b64=None, image_media_type="image/png")
    assert msg["message"]["content"] == "hello"


def test_spec_repl_session_history_is_4_tuple() -> None:
    session = SpecReplSession(
        task_id="t1",
        system_prompt="sys",
        cwd="/tmp",
        history=[("user text", "assistant text", None, None)],
    )
    entry = session.history[0]
    assert len(entry) == 4
    user_text, assistant_text, image_id, image_media_type = entry
    assert user_text == "user text"
    assert assistant_text == "assistant text"
    assert image_id is None
    assert image_media_type is None


def test_spec_repl_session_history_4_tuple_with_image() -> None:
    session = SpecReplSession(
        task_id="t1",
        system_prompt="sys",
        cwd="/tmp",
        history=[("user text", "assistant text", "img-uuid", "image/png")],
    )
    user_text, assistant_text, image_id, image_media_type = session.history[0]
    assert image_id == "img-uuid"
    assert image_media_type == "image/png"


def test_spec_repl_session_default_history_is_empty() -> None:
    session = SpecReplSession(task_id="t1", system_prompt="sys", cwd="/tmp")
    assert session.history == []


def test_load_image_b64_returns_none_when_file_missing(tmp_path: Path) -> None:
    from unittest.mock import patch

    with patch("core.claude_repl.get_chat_images_dir", return_value=tmp_path):
        from core.claude_repl import _load_image_b64

        result = _load_image_b64("nonexistent-uuid")
    assert result is None


def test_load_image_b64_returns_base64_for_existing_file(tmp_path: Path) -> None:
    from unittest.mock import patch

    image_bytes = b"\x89PNG\r\n\x1a\n"
    image_file = tmp_path / "test-uuid"
    image_file.write_bytes(image_bytes)

    with patch("core.claude_repl.get_chat_images_dir", return_value=tmp_path):
        from core.claude_repl import _load_image_b64

        result = _load_image_b64("test-uuid")

    assert result == base64.b64encode(image_bytes).decode()
