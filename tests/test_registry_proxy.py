"""Tests for M11 Registry Proxy API endpoints."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app


SAMPLE_FLOW_YAML = """\
name: quick-task
author: test
steps:
  do_it:
    executor: llm
    prompt: Do the thing
    outputs: [result]
"""

SAMPLE_REGISTRY_FLOW = {
    "name": "quick-task",
    "slug": "quick-task",
    "author": "alice",
    "version": 1,
    "description": "A simple task flow",
    "yaml": SAMPLE_FLOW_YAML,
    "steps": 1,
    "loops": 0,
    "has_for_each": False,
    "executor_types": ["llm"],
    "downloads": 42,
    "featured": False,
}

SAMPLE_SEARCH_RESULT = {
    "flows": [SAMPLE_REGISTRY_FLOW],
    "total": 1,
}


@pytest.fixture
def project_dir(tmp_path):
    """Create a temp project directory."""
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    return tmp_path


@pytest.fixture
def client(project_dir):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(project_dir)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(project_dir / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(project_dir / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        assert srv._project_dir == project_dir.resolve()
        yield c

    os.environ.clear()
    os.environ.update(old_env)


class TestRegistrySearch:
    @patch("stepwise.registry_client.search_flows")
    def test_search_returns_results(self, mock_search, client):
        mock_search.return_value = SAMPLE_SEARCH_RESULT
        resp = client.get("/api/registry/search", params={"q": "task"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["flows"]) == 1
        assert data["flows"][0]["slug"] == "quick-task"
        mock_search.assert_called_once_with(query="task", sort="downloads", limit=20)

    @patch("stepwise.registry_client.search_flows")
    def test_search_with_filters(self, mock_search, client):
        mock_search.return_value = {"flows": [], "total": 0}
        # Unknown query params (like `tag`) are accepted by FastAPI but
        # ignored by the route — reflect that in the assertion.
        resp = client.get(
            "/api/registry/search",
            params={"q": "test", "tag": "utility", "sort": "newest", "limit": 5},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once_with(query="test", sort="newest", limit=5)

    @patch("stepwise.registry_client.search_flows")
    def test_search_empty_query(self, mock_search, client):
        mock_search.return_value = {"flows": [], "total": 0}
        resp = client.get("/api/registry/search")
        assert resp.status_code == 200
        mock_search.assert_called_once_with(query="", sort="downloads", limit=20)

    @patch("stepwise.registry_client.search_flows")
    def test_search_registry_error(self, mock_search, client):
        from stepwise.registry_client import RegistryError
        mock_search.side_effect = RegistryError("Connection refused")
        resp = client.get("/api/registry/search", params={"q": "test"})
        assert resp.status_code == 502


class TestRegistryFlowDetail:
    @patch("stepwise.registry_client.fetch_flow")
    def test_fetch_flow_detail(self, mock_fetch, client):
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.get("/api/registry/flow/quick-task")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "quick-task"
        assert data["author"] == "alice"
        assert "graph" in data
        assert len(data["graph"]["nodes"]) >= 1

    @patch("stepwise.registry_client.fetch_flow")
    def test_fetch_flow_not_found(self, mock_fetch, client):
        from stepwise.registry_client import RegistryError
        mock_fetch.side_effect = RegistryError("Flow not found", 404)
        resp = client.get("/api/registry/flow/nonexistent")
        assert resp.status_code == 404

    @patch("stepwise.registry_client.fetch_flow")
    def test_fetch_flow_builds_graph(self, mock_fetch, client):
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.get("/api/registry/flow/quick-task")
        data = resp.json()
        nodes = data["graph"]["nodes"]
        assert any(n["id"] == "do_it" for n in nodes)


class TestRegistryInstall:
    @patch("stepwise.registry_client.fetch_flow")
    def test_install_creates_directory_flow(self, mock_fetch, client, project_dir):
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.post(
            "/api/registry/install",
            json={"slug": "quick-task"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "flows/quick-task/FLOW.yaml"
        assert data["is_directory"] is True
        assert data["name"] == "quick-task"
        # Verify file was created
        flow_file = project_dir / "flows" / "quick-task" / "FLOW.yaml"
        assert flow_file.exists()
        assert "quick-task" in flow_file.read_text()

    @patch("stepwise.registry_client.fetch_flow")
    def test_install_creates_origin_json(self, mock_fetch, client, project_dir):
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.post(
            "/api/registry/install",
            json={"slug": "quick-task"},
        )
        assert resp.status_code == 200
        origin_file = project_dir / "flows" / "quick-task" / ".origin.json"
        assert origin_file.exists()
        origin = json.loads(origin_file.read_text())
        assert origin["slug"] == "quick-task"
        assert origin["author"] == "alice"
        assert origin["registry"] == "stepwise.run"

    @patch("stepwise.registry_client.fetch_flow")
    def test_install_collision(self, mock_fetch, client, project_dir):
        # Pre-create the directory
        (project_dir / "flows" / "quick-task").mkdir(parents=True)
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.post(
            "/api/registry/install",
            json={"slug": "quick-task"},
        )
        assert resp.status_code == 409

    @patch("stepwise.registry_client.fetch_flow")
    def test_install_not_found(self, mock_fetch, client):
        from stepwise.registry_client import RegistryError
        mock_fetch.side_effect = RegistryError("Not found", 404)
        resp = client.post(
            "/api/registry/install",
            json={"slug": "nonexistent"},
        )
        assert resp.status_code == 404

    @patch("stepwise.registry_client.fetch_flow")
    def test_install_returns_valid_flow(self, mock_fetch, client):
        mock_fetch.return_value = dict(SAMPLE_REGISTRY_FLOW)
        resp = client.post(
            "/api/registry/install",
            json={"slug": "quick-task"},
        )
        data = resp.json()
        assert data["flow"] is not None
        assert data["errors"] == []
        assert len(data["graph"]["nodes"]) >= 1
