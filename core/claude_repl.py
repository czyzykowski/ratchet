"""Persistent Claude REPL session via stream-json stdin/stdout."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class _QueueDone:
    """Sentinel value marking end of an ask_detached queue."""


_QUEUE_DONE = _QueueDone()


def get_chat_images_dir() -> Path:
    """Return path to chat-images directory, creating it if needed."""
    xdg_data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(xdg_data_home) / "ratchet" / "chat-images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_user_msg(
    text: str,
    session_id: str,
    image_b64: str | None = None,
    image_media_type: str | None = None,
) -> dict[str, Any]:
    if image_b64 and image_media_type:
        content: str | list[dict[str, Any]] = [
            {"type": "text", "text": text},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": image_b64,
                },
            },
        ]
    else:
        content = text
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": content,
        },
        "session_id": session_id,
        "parent_tool_use_id": None,
    }


def make_assistant_msg(text: str, session_id: str) -> dict[str, Any]:
    """Used when replaying history into a fresh process."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "session_id": session_id,
        "parent_tool_use_id": None,
    }


def _load_image_b64(image_id: str) -> str | None:
    """Load image bytes from disk and return as base64 string, or None if not found."""
    img_path = get_chat_images_dir() / image_id
    if not img_path.exists():
        return None
    return base64.b64encode(img_path.read_bytes()).decode()


@dataclass
class SpecReplSession:
    task_id: str
    system_prompt: str
    cwd: str
    history: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    session_id: str = "default"
    model: str = "claude-opus-4-6"

    def __post_init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._in_flight_task: asyncio.Task[None] | None = None

    async def _spawn(self) -> None:
        limit = 10 * 1024 * 1024  # 10 MB — Claude can output large JSON lines
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        self._proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools",
            "Read,Glob,WebSearch,Bash",
            "--model",
            self.model,
            "--system-prompt",
            self.system_prompt,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=limit,
        )

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _send(self, obj: dict[str, Any]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        line = json.dumps(obj) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _replay_history(self) -> None:
        for user_text, assistant_text, image_id, image_media_type in self.history:
            if not assistant_text:
                continue  # skip incomplete exchanges — empty assistant messages crash subprocess
            if "[Request interrupted by user]" in user_text:
                continue  # skip interrupted markers — cause error_during_execution on replay
            image_b64: str | None = None
            if image_id and image_media_type:
                image_b64 = _load_image_b64(image_id)
            await self._send(make_user_msg(user_text, "default", image_b64, image_media_type))
            await self._send(make_assistant_msg(assistant_text, "default"))

    async def ensure_alive(self) -> None:
        if self._is_alive():
            return
        self.session_id = "default"
        await self._spawn()
        if self.history:
            await self._replay_history()

    def _try_capture_session_id(self, event: dict[str, Any]) -> None:
        if self.session_id != "default":
            return
        sid = event.get("session_id") or event.get("message", {}).get("session_id")
        if sid and sid != "default":
            self.session_id = str(sid)

    async def ask(
        self,
        user_input: str,
        image_id: str | None = None,
        image_media_type: str | None = None,
    ) -> AsyncGenerator[str | None, None]:
        await self.ensure_alive()
        assert self._proc is not None
        assert self._proc.stdout is not None

        image_b64: str | None = None
        if image_id and image_media_type:
            image_b64 = _load_image_b64(image_id)

        await self._send(make_user_msg(user_input, self.session_id, image_b64, image_media_type))

        assistant_text = ""
        sub_texts: list[str] = []
        saw_tool_use = False
        full_text = ""

        while True:
            line_bytes = await self._proc.stdout.readline()
            if not line_bytes:
                break

            line = line_bytes.decode(errors="replace").strip()
            if not line:
                continue

            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue

            self._try_capture_session_id(event)
            etype = event.get("type")

            if etype == "stream_event":
                inner_event = event.get("event", {})
                inner_type = inner_event.get("type", "")
                if inner_type == "message_start":
                    if assistant_text:
                        sub_texts.append(assistant_text)
                        assistant_text = ""
                        yield None
                else:
                    delta = inner_event.get("delta", {})
                    delta_type = delta.get("type", "")
                    if delta_type == "text_delta":
                        chunk = str(delta["text"])
                        assistant_text += chunk
                        yield chunk
                    elif delta_type == "input_json_delta":
                        saw_tool_use = True

            elif etype == "result":
                result_text = event.get("result", "")
                if self.session_id == "default":
                    sid = event.get("session_id", "")
                    if sid:
                        self.session_id = str(sid)
                if assistant_text:
                    sub_texts.append(assistant_text)
                    assistant_text = ""
                full_text = "\n\n".join(sub_texts) if sub_texts else ""
                if not full_text:
                    if result_text:
                        full_text = str(result_text)
                        yield full_text
                    elif saw_tool_use:
                        # Claude used tools but produced no visible text — emit a placeholder
                        # so the UI isn't silently empty.
                        placeholder = "*(Reading codebase…)*"
                        full_text = placeholder
                        yield placeholder
                break

        if full_text:
            self.history.append((user_input, full_text, image_id, image_media_type))

    async def ask_detached(
        self,
        user_input: str,
        on_complete: Callable[[str], Awaitable[None]],
        image_id: str | None = None,
        image_media_type: str | None = None,
    ) -> asyncio.Queue[Any]:
        """Run ask() as a non-cancellable background task feeding a queue.

        If a previous ask is still in-flight, waits for it to finish first.
        The returned queue yields str chunks, None for new-message boundaries,
        and a _QUEUE_DONE sentinel when the response is complete.
        on_complete(full_text) is called after generation regardless of whether
        the queue is being read (i.e. immune to SSE client disconnects).
        """
        if self._in_flight_task is not None and not self._in_flight_task.done():
            await self._in_flight_task

        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def _run() -> None:
            full_text = ""
            async for chunk in self.ask(user_input, image_id, image_media_type):
                await queue.put(chunk)
                if chunk is not None:
                    full_text += chunk
            await queue.put(_QUEUE_DONE)
            await on_complete(full_text)

        self._in_flight_task = asyncio.create_task(_run())
        return queue

    async def close(self) -> None:
        if self._proc is not None:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except (TimeoutError, ProcessLookupError):
                self._proc.kill()
            self._proc = None
