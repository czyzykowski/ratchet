"""Sandbox protocol: uniform interface for running commands in isolated environments."""

from __future__ import annotations

import asyncio
import os
import re
from asyncio import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SandboxConfig:
    writable_paths: list[str] = field(default_factory=list)
    readonly_paths: list[str] = field(default_factory=list)
    ephemeral_home_dirs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str


class Sandbox(Protocol):
    async def start(self, command: list[str], config: SandboxConfig, cwd: str) -> SandboxResult: ...

    async def cleanup(self) -> None: ...

    def name(self) -> str: ...

    def get_ephemeral_path(self, target: str) -> str | None: ...

    async def is_available(self) -> bool: ...


class NullSandbox:
    """No-isolation sandbox: runs commands directly via asyncio subprocess."""

    async def start(self, command: list[str], config: SandboxConfig, cwd: str) -> SandboxResult:
        merged_env = {**os.environ, **config.env}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return SandboxResult(
            returncode=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def cleanup(self) -> None:
        return None

    def name(self) -> str:
        return "null"

    def get_ephemeral_path(self, target: str) -> str | None:
        return None

    async def is_available(self) -> bool:
        return True


class SandboxRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, tuple[Callable[[], Sandbox], int]] = {}

    def register(self, name: str, factory: Callable[[], Sandbox], priority: int) -> None:
        self._backends[name] = (factory, priority)

    def get(self, name: str) -> Sandbox:
        factory, _ = self._backends[name]
        return factory()

    async def auto_detect(self) -> Sandbox:
        sorted_backends = sorted(self._backends.items(), key=lambda item: item[1][1])
        for _name, (factory, _priority) in sorted_backends:
            instance = factory()
            if await instance.is_available():
                return instance
        return NullSandbox()

    async def available(self) -> list[str]:
        sorted_backends = sorted(self._backends.items(), key=lambda item: item[1][1])
        result = []
        for name, (factory, _priority) in sorted_backends:
            instance = factory()
            if await instance.is_available():
                result.append(name)
        return result


default_registry = SandboxRegistry()
default_registry.register("null", NullSandbox, priority=999)


def build_sandbox_config(
    worktree_path: str,
    project_path: str,
    symlinked_dirs: list[str],
) -> SandboxConfig:
    """Construct a SandboxConfig for Claude Code execution in a worktree."""
    writable_paths = [worktree_path]
    for dirname in symlinked_dirs:
        joined = os.path.join(project_path, dirname)
        resolved = os.path.realpath(joined)
        if os.path.exists(resolved):
            writable_paths.append(resolved)

    readonly_paths = ["/nix/store"] if os.path.exists("/nix/store") else []

    ephemeral_home_dirs = [os.path.expanduser("~/.claude")]

    _fixed_keys = ["PATH", "HOME", "USER", "NIX_PATH", "NIX_PROFILES", "LANG", "TERM"]
    env: dict[str, str] = {}
    for key in _fixed_keys:
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in os.environ.items():
        if re.match(r"^(CLAUDE_|ANTHROPIC_)", key):
            env[key] = value

    return SandboxConfig(
        writable_paths=writable_paths,
        readonly_paths=readonly_paths,
        ephemeral_home_dirs=ephemeral_home_dirs,
        env=env,
    )
