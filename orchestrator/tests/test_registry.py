"""Unit tests for WorkerRegistry — no database required."""
from __future__ import annotations

import pytest

from orchestrator.registry import WorkerConnection, WorkerRegistry


def _make_registry() -> WorkerRegistry:
    return WorkerRegistry()


def _fake_ws() -> object:
    return object()


def test_register_returns_worker_connection() -> None:
    registry = _make_registry()
    ws = _fake_ws()
    conn = registry.register("w1", ["python"], ws)
    assert isinstance(conn, WorkerConnection)
    assert conn.worker_id == "w1"
    assert conn.capabilities == ["python"]
    assert conn.current_execution_id is None
    assert conn.websocket is ws


def test_register_duplicate_raises_value_error() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    with pytest.raises(ValueError):
        registry.register("w1", ["docker"], _fake_ws())


def test_unregister_returns_connection() -> None:
    registry = _make_registry()
    conn = registry.register("w1", ["python"], _fake_ws())
    result = registry.unregister("w1")
    assert result is conn


def test_unregister_unknown_returns_none() -> None:
    registry = _make_registry()
    assert registry.unregister("nonexistent") is None


def test_all_workers_empty() -> None:
    registry = _make_registry()
    assert registry.all_workers() == []


def test_all_workers_returns_all() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    registry.register("w2", ["docker"], _fake_ws())
    assert len(registry.all_workers()) == 2


def test_find_available_returns_idle_worker() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    result = registry.find_available(["python"])
    assert result is not None
    assert result.worker_id == "w1"


def test_find_available_superset_match() -> None:
    registry = _make_registry()
    registry.register("w1", ["python", "docker"], _fake_ws())
    result = registry.find_available(["python"])
    assert result is not None
    assert result.worker_id == "w1"


def test_find_available_returns_none_when_capabilities_not_matched() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    assert registry.find_available(["docker"]) is None


def test_find_available_returns_none_when_busy() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    registry.assign_job("w1", "exec-1")
    assert registry.find_available([]) is None


def test_find_available_empty_required_capabilities() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    result = registry.find_available([])
    assert result is not None


def test_assign_job_sets_execution_id() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    registry.assign_job("w1", "exec-1")
    assert registry.all_workers()[0].current_execution_id == "exec-1"


def test_assign_job_unknown_raises_key_error() -> None:
    registry = _make_registry()
    with pytest.raises(KeyError):
        registry.assign_job("nonexistent", "exec-1")


def test_clear_job_clears_execution_id() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    registry.assign_job("w1", "exec-1")
    registry.clear_job("w1")
    assert registry.all_workers()[0].current_execution_id is None


def test_clear_job_unknown_raises_key_error() -> None:
    registry = _make_registry()
    with pytest.raises(KeyError):
        registry.clear_job("nonexistent")


def test_find_available_skips_busy_picks_idle() -> None:
    registry = _make_registry()
    registry.register("w1", ["python"], _fake_ws())
    registry.register("w2", ["python"], _fake_ws())
    registry.assign_job("w1", "exec-1")
    result = registry.find_available(["python"])
    assert result is not None
    assert result.worker_id == "w2"
