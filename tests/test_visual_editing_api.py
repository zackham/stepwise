"""Tests for M12b Visual Editing API endpoints."""

import os
import pytest
from pathlib import Path
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app

FLOW_YAML = """\
name: test-flow
steps:
  fetch:
    run: curl http://example.com
    outputs: [data]
  analyze:
    executor: llm
    prompt: Analyze the data
    inputs:
      raw: fetch.data
    outputs: [summary]
"""


@pytest.fixture
def project_dir(tmp_path):
    """Create a temp project directory with a test flow."""
    flow_dir = tmp_path / "flows" / "test-flow"
    flow_dir.mkdir(parents=True)
    (flow_dir / "FLOW.yaml").write_text(FLOW_YAML)
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


FLOW_PATH = "flows/test-flow/FLOW.yaml"


class TestPatchStep:
    def test_patch_prompt(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "analyze", "changes": {"prompt": "New prompt text"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "New prompt text" in data["raw_yaml"]
        assert data["flow"] is not None
        assert data["errors"] == []

    def test_patch_outputs(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "analyze", "changes": {"outputs": ["summary", "details"]}},
        )
        assert resp.status_code == 200
        assert "details" in resp.json()["raw_yaml"]

    def test_patch_run_command(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "fetch", "changes": {"run": "wget http://example.com"}},
        )
        assert resp.status_code == 200
        assert "wget" in resp.json()["raw_yaml"]

    def test_patch_nonexistent_step(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "nonexistent", "changes": {"prompt": "test"}},
        )
        assert resp.status_code == 404

    def test_patch_nonexistent_flow(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": "flows/missing/FLOW.yaml", "step_name": "test", "changes": {"prompt": "test"}},
        )
        assert resp.status_code == 404

    def test_patch_preserves_other_fields(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "analyze", "changes": {"prompt": "Updated prompt"}},
        )
        data = resp.json()
        assert "fetch.data" in data["raw_yaml"]
        assert "summary" in data["raw_yaml"]

    def test_patch_returns_valid_graph(self, client):
        resp = client.post(
            "/api/flows/patch-step",
            json={"flow_path": FLOW_PATH, "step_name": "analyze", "changes": {"prompt": "New"}},
        )
        data = resp.json()
        assert "nodes" in data["graph"]
        assert "edges" in data["graph"]
        assert len(data["graph"]["nodes"]) == 2


class TestAddStep:
    def test_add_script_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "new_step", "executor": "script"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "new_step" in data["raw_yaml"]
        step_names = [n["id"] for n in data["graph"]["nodes"]]
        assert "new_step" in step_names

    def test_add_llm_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "summarize", "executor": "llm"},
        )
        assert resp.status_code == 200
        assert "summarize" in resp.json()["raw_yaml"]

    def test_add_external_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "review", "executor": "external"},
        )
        assert resp.status_code == 200
        assert "review" in resp.json()["raw_yaml"]

    def test_add_duplicate_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "fetch", "executor": "script"},
        )
        assert resp.status_code == 409

    def test_add_step_nonexistent_flow(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": "flows/missing/FLOW.yaml", "name": "test", "executor": "script"},
        )
        assert resp.status_code == 404


class TestDeleteStep:
    def test_delete_step(self, client):
        resp = client.post(
            "/api/flows/delete-step",
            json={"flow_path": FLOW_PATH, "step_name": "analyze"},
        )
        assert resp.status_code == 200
        data = resp.json()
        step_names = [n["id"] for n in data["graph"]["nodes"]]
        assert "analyze" not in step_names
        assert "fetch" in step_names

    def test_delete_step_cascades_inputs(self, client):
        resp = client.post(
            "/api/flows/delete-step",
            json={"flow_path": FLOW_PATH, "step_name": "fetch"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "fetch.data" not in data["raw_yaml"]

    def test_delete_nonexistent_step(self, client):
        resp = client.post(
            "/api/flows/delete-step",
            json={"flow_path": FLOW_PATH, "step_name": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_delete_step_nonexistent_flow(self, client):
        resp = client.post(
            "/api/flows/delete-step",
            json={"flow_path": "flows/missing/FLOW.yaml", "step_name": "test"},
        )
        assert resp.status_code == 404


class TestMtime:
    def test_get_mtime(self, client):
        resp = client.get("/api/flows/mtime", params={"path": FLOW_PATH})
        assert resp.status_code == 200
        data = resp.json()
        assert "mtime" in data
        assert "modified_at" in data
        assert isinstance(data["mtime"], float)

    def test_mtime_nonexistent_flow(self, client):
        resp = client.get("/api/flows/mtime", params={"path": "flows/missing/FLOW.yaml"})
        assert resp.status_code == 404
