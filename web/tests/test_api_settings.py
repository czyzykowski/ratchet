"""Unit tests for GET /api/settings/prompts."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes.api.router import api_router


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_test_app())


def test_get_prompts_returns_200(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    assert response.status_code == 200


def test_get_prompts_response_shape(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)


def test_get_prompts_entries_have_required_keys(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    data = response.json()["data"]
    required_keys = {"category", "name", "description", "source", "template"}
    for entry in data:
        assert required_keys == set(entry.keys()), f"Entry missing or extra keys: {entry}"


def test_get_prompts_all_categories_present(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    data = response.json()["data"]
    categories = {entry["category"] for entry in data}
    expected = {"Execution", "QA", "Compilation", "Chat Sessions", "Review Engine"}
    assert expected == categories


def test_get_prompts_template_values_non_empty(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    data = response.json()["data"]
    for entry in data:
        assert isinstance(entry["template"], str) and len(entry["template"]) > 0, (
            f"Empty template for {entry['name']}"
        )


def test_get_prompts_source_values_are_relative_paths(client: TestClient) -> None:
    response = client.get("/api/settings/prompts")
    data = response.json()["data"]
    for entry in data:
        assert not entry["source"].startswith("/"), (
            f"Source path should be relative, got: {entry['source']}"
        )
