"""Tests for core/sandbox.py."""

from __future__ import annotations

import os

import pytest

from core.sandbox import (
    NullSandbox,
    SandboxConfig,
    SandboxRegistry,
    build_sandbox_config,
    default_registry,
)


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


# --- build_sandbox_config tests ---


def _cfg(symlinked_dirs: list[str] | None = None) -> object:
    return dict(
        worktree_path="/tmp/wt",
        project_path="/projects/repo",
        symlinked_dirs=symlinked_dirs or [],
    )


def test_build_sandbox_config_worktree_in_writable() -> None:
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert "/tmp/wt" in config.writable_paths


def test_build_sandbox_config_existing_symlink_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "realpath", lambda p: "/projects/repo/.venv")
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/projects/repo/.venv")
    config = build_sandbox_config(**_cfg([".venv"]))  # type: ignore[arg-type]
    assert "/projects/repo/.venv" in config.writable_paths


def test_build_sandbox_config_missing_symlink_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "realpath", lambda p: "/projects/repo/node_modules")
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    config = build_sandbox_config(**_cfg(["node_modules"]))  # type: ignore[arg-type]
    assert "/projects/repo/node_modules" not in config.writable_paths
    assert config.writable_paths == ["/tmp/wt"]


def test_build_sandbox_config_nix_store_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/nix/store")
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert "/nix/store" in config.readonly_paths


def test_build_sandbox_config_nix_store_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert config.readonly_paths == []


def test_build_sandbox_config_ephemeral_claude_dir() -> None:
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert os.path.expanduser("~/.claude") in config.ephemeral_home_dirs


def test_build_sandbox_config_claude_env_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {"CLAUDE_API_KEY": "x", "ANTHROPIC_API_KEY": "y"})
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert config.env.get("CLAUDE_API_KEY") == "x"
    assert config.env.get("ANTHROPIC_API_KEY") == "y"


def test_build_sandbox_config_standard_env_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin", "HOME": "/home/user", "USER": "user"})
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert config.env.get("PATH") == "/usr/bin"
    assert config.env.get("HOME") == "/home/user"
    assert config.env.get("USER") == "user"


def test_build_sandbox_config_missing_env_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {})
    config = build_sandbox_config(**_cfg())  # type: ignore[arg-type]
    assert "NIX_PATH" not in config.env
    assert "NIX_PROFILES" not in config.env
    assert "LANG" not in config.env
    assert "TERM" not in config.env
