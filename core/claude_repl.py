"""Persistent Claude REPL session via stream-json stdin/stdout."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any


def make_user_msg(text: str, session_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": text,
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
            "content": text,
        },
        "session_id": session_id,
        "parent_tool_use_id": None,
    }


@dataclass
class SpecReplSession:
    task_id: str
    system_prompt: str
    cwd: str
    history: list[tuple[str, str]] = field(default_factory=list)
    session_id: str = "default"

    def __post_init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def _spawn(self) -> None:
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
            "--system-prompt",
            self.system_prompt,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
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
        for user_text, assistant_text in self.history:
            await self._send(make_user_msg(user_text, "default"))
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

    async def ask(self, user_input: str) -> AsyncGenerator[str, None]:
        await self.ensure_alive()
        assert self._proc is not None
        assert self._proc.stdout is not None

        await self._send(make_user_msg(user_input, self.session_id))

        assistant_text = ""

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
                delta = event.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta":
                    chunk = str(delta["text"])
                    assistant_text += chunk
                    yield chunk

            elif etype == "result":
                result_text = event.get("result", "")
                if self.session_id == "default":
                    sid = event.get("session_id", "")
                    if sid:
                        self.session_id = str(sid)
                if result_text and not assistant_text:
                    assistant_text = str(result_text)
                    yield assistant_text
                break

        if assistant_text:
            self.history.append((user_input, assistant_text))

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
