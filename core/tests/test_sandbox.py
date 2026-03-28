"""Tests for core/sandbox.py."""

from __future__ import annotations

import pytest

from core.sandbox import NullSandbox, SandboxConfig, SandboxRegistry, default_registry


@pytest.mark.asyncio
async def test_null_sandbox_executes_command() -> None:
    sandbox = NullSandbox()
    result = await sandbox.start(["echo", "hello"], SandboxConfig(), cwd="/tmp")
    assert result.returncode == 0
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_null_sandbox_captures_stderr() -> None:
    sandbox = NullSandbox()
    result = await sandbox.start(["sh", "-c", "echo error >&2"], SandboxConfig(), cwd="/tmp")
    assert "error" in result.stderr


@pytest.mark.asyncio
async def test_null_sandbox_nonzero_exit() -> None:
    sandbox = NullSandbox()
    result = await sandbox.start(["sh", "-c", "exit 42"], SandboxConfig(), cwd="/tmp")
    assert result.returncode != 0


def test_null_sandbox_name() -> None:
    assert NullSandbox().name() == "null"


def test_null_sandbox_get_ephemeral_path_returns_none() -> None:
    assert NullSandbox().get_ephemeral_path("~/.claude") is None


@pytest.mark.asyncio
async def test_null_sandbox_cleanup_is_noop() -> None:
    await NullSandbox().cleanup()  # should not raise


@pytest.mark.asyncio
async def test_null_sandbox_is_available() -> None:
    assert await NullSandbox().is_available() is True


@pytest.mark.asyncio
async def test_null_sandbox_env_passthrough() -> None:
    sandbox = NullSandbox()
    result = await sandbox.start(
        ["sh", "-c", "echo $FOO"],
        SandboxConfig(env={"FOO": "bar"}),
        cwd="/tmp",
    )
    assert "bar" in result.stdout


@pytest.mark.asyncio
async def test_null_sandbox_cwd() -> None:
    sandbox = NullSandbox()
    result = await sandbox.start(["pwd"], SandboxConfig(), cwd="/tmp")
    assert "/tmp" in result.stdout


def test_registry_get_null() -> None:
    assert default_registry.get("null").name() == "null"


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError):
        default_registry.get("nonexistent")


@pytest.mark.asyncio
async def test_registry_auto_detect_fallback() -> None:
    registry = SandboxRegistry()
    result = await registry.auto_detect()
    assert result.name() == "null"


@pytest.mark.asyncio
async def test_registry_available_includes_null() -> None:
    assert "null" in await default_registry.available()
