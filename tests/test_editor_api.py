"""Tests for the editor API endpoints (M10): list, load, parse, save flows."""

import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ScriptExecutor
from stepwise.server import app
from stepwise.store import SQLiteStore


SIMPLE_FLOW = """\
name: simple-test
author: test
steps:
  greet:
    run: 'echo "{\\"message\\": \\"hello\\"}"'
    outputs: [message]
"""

TWO_STEP_FLOW = """\
name: two-step
author: test
steps:
  fetch:
    run: 'echo "{\\"data\\": \\"raw\\"}"'
    outputs: [data]
  process:
    executor: llm
    prompt: "Process this: {{data}}"
    inputs:
      data: fetch.data
    outputs: [result]
"""

INVALID_YAML = """\
name: bad
author: test
steps:
  broken:
    executor: llm
    outputs: [result]
"""

MALFORMED_YAML = """\
name: [unterminated
author: test
  steps:
"""


@pytest.fixture
def project_dir(tmp_path):
    """Create a temp project directory with some flow files."""
    # Single-file flow
    f1 = tmp_path / "simple.flow.yaml"
    f1.write_text(SIMPLE_FLOW)

    # Another single-file flow
    f2 = tmp_path / "pipeline.flow.yaml"
    f2.write_text(TWO_STEP_FLOW)

    # Directory flow
    dir_flow = tmp_path / "complex-flow"
    dir_flow.mkdir()
    (dir_flow / "FLOW.yaml").write_text(SIMPLE_FLOW)

    # flows/ subdirectory
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    f3 = flows_dir / "sub.flow.yaml"
    f3.write_text(SIMPLE_FLOW)

    return tmp_path


@pytest.fixture
def client(project_dir):
    """Create a TestClient with engine and project_dir configured.

    The lifespan reads STEPWISE_PROJECT_DIR env var, so we set it before
    creating the client. We also override _engine after lifespan to use
    an in-memory store.
    """
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(project_dir)
    # Use in-memory DB to avoid file conflicts
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(project_dir / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(project_dir / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        # Verify lifespan set the project dir correctly
        assert srv._project_dir == project_dir.resolve()
        yield c

    # Restore env
    os.environ.clear()
    os.environ.update(old_env)


# ── GET /api/local-flows ──────────────────────────────────────────────


class TestListLocalFlows:

    def test_list_returns_all_flows(self, client, project_dir):
        resp = client.get("/api/local-flows")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = {f["name"] for f in data}
        assert "simple" in names
        assert "pipeline" in names
        assert "complex-flow" in names
        assert "sub" in names

    def test_list_has_correct_fields(self, client, project_dir):
        resp = client.get("/api/local-flows")
        data = resp.json()
        for flow in data:
            assert "path" in flow
            assert "name" in flow
            assert "steps_count" in flow
            assert "modified_at" in flow
            assert "is_directory" in flow
            assert isinstance(flow["steps_count"], int)

    def test_directory_flow_detected(self, client, project_dir):
        resp = client.get("/api/local-flows")
        data = resp.json()
        dir_flows = [f for f in data if f["name"] == "complex-flow"]
        assert len(dir_flows) == 1
        assert dir_flows[0]["is_directory"] is True

    def test_single_file_flow_not_directory(self, client, project_dir):
        resp = client.get("/api/local-flows")
        data = resp.json()
        simple = [f for f in data if f["name"] == "simple"]
        assert len(simple) == 1
        assert simple[0]["is_directory"] is False

    def test_step_count_correct(self, client, project_dir):
        resp = client.get("/api/local-flows")
        data = resp.json()
        pipeline = [f for f in data if f["name"] == "pipeline"]
        assert len(pipeline) == 1
        assert pipeline[0]["steps_count"] == 2

    def test_empty_directory(self, tmp_path):
        """List flows in a dir with no flows."""
        old_env = os.environ.copy()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        os.environ["STEPWISE_PROJECT_DIR"] = str(empty_dir)
        os.environ["STEPWISE_DB"] = ":memory:"
        os.environ["STEPWISE_TEMPLATES"] = str(empty_dir / "_t")
        os.environ["STEPWISE_JOBS_DIR"] = str(empty_dir / "_j")
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/local-flows")
            assert resp.status_code == 200
            assert resp.json() == []
        os.environ.clear()
        os.environ.update(old_env)


# ── GET /api/flows/local/{path} ──────────────────────────────────────


class TestLoadLocalFlow:

    def test_load_single_file_flow(self, client, project_dir):
        resp = client.get("/api/flows/local/simple.flow.yaml")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "simple-test"
        assert data["raw_yaml"] == SIMPLE_FLOW
        assert "flow" in data
        assert "graph" in data
        assert data["is_directory"] is False
        assert "greet" in data["flow"]["steps"]

    def test_load_directory_flow(self, client, project_dir):
        resp = client.get("/api/flows/local/complex-flow/FLOW.yaml")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_directory"] is True

    def test_load_flow_in_subdirectory(self, client, project_dir):
        resp = client.get("/api/flows/local/flows/sub.flow.yaml")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "simple-test"

    def test_load_nonexistent_flow(self, client, project_dir):
        resp = client.get("/api/flows/local/nonexistent.flow.yaml")
        assert resp.status_code == 404

    def test_graph_structure(self, client, project_dir):
        resp = client.get("/api/flows/local/pipeline.flow.yaml")
        assert resp.status_code == 200
        graph = resp.json()["graph"]
        assert "nodes" in graph
        assert "edges" in graph
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "fetch" in node_ids
        assert "process" in node_ids
        # process depends on fetch.data → edge from fetch to process
        edge_sources = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("fetch", "process") in edge_sources

    def test_graph_node_fields(self, client, project_dir):
        resp = client.get("/api/flows/local/pipeline.flow.yaml")
        graph = resp.json()["graph"]
        fetch_node = [n for n in graph["nodes"] if n["id"] == "fetch"][0]
        assert fetch_node["executor_type"] == "script"
        assert "data" in fetch_node["outputs"]
        assert "label" in fetch_node
        assert "details" in fetch_node

    def test_path_traversal_via_symlink_blocked(self, client, project_dir):
        """Verify that symlinks resolving outside project_dir are rejected."""
        outside_file = project_dir.parent / "outside.flow.yaml"
        outside_file.write_text(SIMPLE_FLOW)
        link = project_dir / "escape.flow.yaml"
        link.symlink_to(outside_file)
        resp = client.get("/api/flows/local/escape.flow.yaml")
        assert resp.status_code == 400


# ── POST /api/flows/parse ────────────────────────────────────────────


class TestParseFlowYAML:

    def test_parse_valid_yaml(self, client):
        resp = client.post("/api/flows/parse", json={"yaml": SIMPLE_FLOW})
        assert resp.status_code == 200
        data = resp.json()
        assert data["flow"] is not None
        assert data["graph"] is not None
        assert data["errors"] == []
        assert "greet" in data["flow"]["steps"]

    def test_parse_returns_graph(self, client):
        resp = client.post("/api/flows/parse", json={"yaml": TWO_STEP_FLOW})
        data = resp.json()
        graph = data["graph"]
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) >= 1

    def test_parse_invalid_yaml_returns_errors(self, client):
        resp = client.post("/api/flows/parse", json={"yaml": INVALID_YAML})
        assert resp.status_code == 200
        data = resp.json()
        assert data["flow"] is None
        assert data["graph"] is None
        assert len(data["errors"]) > 0

    def test_parse_malformed_yaml_returns_errors(self, client):
        resp = client.post("/api/flows/parse", json={"yaml": MALFORMED_YAML})
        assert resp.status_code == 200
        data = resp.json()
        assert data["flow"] is None
        assert data["graph"] is None
        assert len(data["errors"]) > 0

    def test_parse_empty_string(self, client):
        resp = client.post("/api/flows/parse", json={"yaml": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) > 0


# ── PUT /api/flows/local/{path} ──────────────────────────────────────


class TestSaveLocalFlow:

    def test_save_overwrites_file(self, client, project_dir):
        new_yaml = """\
name: updated-flow
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"updated\\"}"'
    outputs: [msg]
"""
        resp = client.put(
            "/api/flows/local/simple.flow.yaml",
            json={"yaml": new_yaml},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated-flow"
        assert data["raw_yaml"] == new_yaml
        assert data["flow"] is not None
        assert data["graph"] is not None

        # Verify file on disk
        content = (project_dir / "simple.flow.yaml").read_text()
        assert content == new_yaml

    def test_save_creates_backup(self, client, project_dir):
        original = (project_dir / "simple.flow.yaml").read_text()
        new_yaml = """\
name: v2
author: test
steps:
  step1:
    run: 'echo "{\\"x\\": 1}"'
    outputs: [x]
"""
        resp = client.put(
            "/api/flows/local/simple.flow.yaml",
            json={"yaml": new_yaml},
        )
        assert resp.status_code == 200

        # .bak should contain the original content
        bak_path = project_dir / "simple.flow.yaml.bak"
        assert bak_path.exists()
        assert bak_path.read_text() == original

    def test_save_invalid_yaml_rejected(self, client, project_dir):
        original = (project_dir / "simple.flow.yaml").read_text()
        resp = client.put(
            "/api/flows/local/simple.flow.yaml",
            json={"yaml": INVALID_YAML},
        )
        assert resp.status_code == 400

        # File should be unchanged
        content = (project_dir / "simple.flow.yaml").read_text()
        assert content == original

    def test_save_new_file(self, client, project_dir):
        new_yaml = """\
name: brand-new
author: test
steps:
  only:
    run: 'echo "{\\"v\\": 1}"'
    outputs: [v]
"""
        resp = client.put(
            "/api/flows/local/brand-new.flow.yaml",
            json={"yaml": new_yaml},
        )
        assert resp.status_code == 200
        assert (project_dir / "brand-new.flow.yaml").exists()

        # No .bak for new files
        assert not (project_dir / "brand-new.flow.yaml.bak").exists()

    def test_save_returns_graph(self, client, project_dir):
        new_yaml = """\
name: with-graph
author: test
steps:
  a:
    run: 'echo "{\\"x\\": 1}"'
    outputs: [x]
  b:
    run: 'echo "{\\"y\\": 2}"'
    inputs:
      x: a.x
    outputs: [y]
"""
        resp = client.put(
            "/api/flows/local/simple.flow.yaml",
            json={"yaml": new_yaml},
        )
        assert resp.status_code == 200
        graph = resp.json()["graph"]
        assert len(graph["nodes"]) == 2
        edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("a", "b") in edge_pairs

    def test_save_path_traversal_via_symlink_blocked(self, client, project_dir):
        """Verify that symlinks resolving outside project_dir are rejected on save."""
        outside_dir = project_dir.parent / "outside_save"
        outside_dir.mkdir()
        link = project_dir / "escape_dir"
        link.symlink_to(outside_dir)
        resp = client.put(
            "/api/flows/local/escape_dir/evil.flow.yaml",
            json={"yaml": SIMPLE_FLOW},
        )
        assert resp.status_code == 400


# ── POST /api/local-flows (create flow) ─────────────────────────────


class TestCreateFlow:

    def test_create_flow(self, client, project_dir):
        resp = client.post("/api/local-flows", json={"name": "new-flow"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "new-flow"
        assert "path" in data
        assert (project_dir / "flows" / "new-flow" / "FLOW.yaml").exists()

    def test_create_flow_appears_in_list(self, client, project_dir):
        client.post("/api/local-flows", json={"name": "listed-flow"})
        resp = client.get("/api/local-flows")
        names = {f["name"] for f in resp.json()}
        assert "listed-flow" in names

    def test_create_duplicate_fails(self, client, project_dir):
        client.post("/api/local-flows", json={"name": "dup-flow"})
        resp = client.post("/api/local-flows", json={"name": "dup-flow"})
        assert resp.status_code == 409

    def test_create_invalid_name(self, client, project_dir):
        resp = client.post("/api/local-flows", json={"name": "bad name!"})
        assert resp.status_code == 400

    def test_create_empty_name(self, client, project_dir):
        resp = client.post("/api/local-flows", json={"name": ""})
        assert resp.status_code == 400


# ── Kit API Endpoints ─────────────────────────────────────────────────


class TestKitEndpoints:
    """Tests for /api/kits and /api/kits/{name} endpoints."""

    @pytest.fixture
    def kit_project(self, tmp_path):
        """Project dir with a kit and a standalone flow."""
        kit_dir = tmp_path / "flows" / "testkit"
        kit_dir.mkdir(parents=True)
        (kit_dir / "KIT.yaml").write_text(
            "name: testkit\ndescription: A test kit\nauthor: tester\ncategory: testing\n"
        )
        for name in ("alpha", "beta"):
            fd = kit_dir / name
            fd.mkdir()
            (fd / "FLOW.yaml").write_text(
                f"name: {name}\nsteps:\n  s:\n    run: echo '{{}}'\n    outputs: [x]\n"
            )
        solo = tmp_path / "flows" / "solo"
        solo.mkdir(parents=True, exist_ok=True)
        (solo / "FLOW.yaml").write_text(
            "name: solo\nauthor: test\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [x]\n"
        )
        return tmp_path

    @pytest.fixture
    def kit_client(self, kit_project):
        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(kit_project)
        os.environ["STEPWISE_DB"] = ":memory:"
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        os.environ.clear()
        os.environ.update(old_env)

    def test_list_kits_empty(self, client):
        """No kits → empty list."""
        resp = client.get("/api/kits")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_kits_with_kit(self, kit_client):
        resp = kit_client.get("/api/kits")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        kit = data[0]
        assert kit["name"] == "testkit"
        assert kit["description"] == "A test kit"
        assert kit["author"] == "tester"
        assert kit["category"] == "testing"
        assert kit["flow_count"] == 2
        assert sorted(kit["flow_names"]) == ["alpha", "beta"]

    def test_kit_detail_returns_flows(self, kit_client):
        resp = kit_client.get("/api/kits/testkit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "testkit"
        assert data["usage"] == ""
        assert len(data["flows"]) == 2
        flow_names = sorted(f["name"] for f in data["flows"])
        assert flow_names == ["alpha", "beta"]
        for f in data["flows"]:
            assert "steps_count" in f
            assert f["kit_name"] == "testkit"

    def test_kit_detail_not_found(self, kit_client):
        resp = kit_client.get("/api/kits/nonexistent")
        assert resp.status_code == 404

    def test_local_flows_includes_kit_name(self, kit_client):
        resp = kit_client.get("/api/local-flows")
        assert resp.status_code == 200
        flows = resp.json()
        kit_flows = [f for f in flows if f.get("kit_name") == "testkit"]
        assert len(kit_flows) == 2
        solo_flows = [f for f in flows if f["name"] == "solo"]
        assert len(solo_flows) == 1
        assert solo_flows[0].get("kit_name") is None


# ── Flow Archive API ────────────────────────────────────────────────────


class TestFlowArchiveAPI:

    @pytest.fixture
    def archive_project(self, tmp_path):
        """Project with an active and an archived flow."""
        (tmp_path / "active.flow.yaml").write_text(SIMPLE_FLOW)
        (tmp_path / "old.flow.yaml").write_text(
            "name: old\nauthor: test\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        )
        return tmp_path

    @pytest.fixture
    def archive_client(self, archive_project):
        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(archive_project)
        os.environ["STEPWISE_DB"] = ":memory:"
        os.environ["STEPWISE_TEMPLATES"] = str(archive_project / "_t")
        os.environ["STEPWISE_JOBS_DIR"] = str(archive_project / "_j")
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        os.environ.clear()
        os.environ.update(old_env)

    def test_list_excludes_archived_by_default(self, archive_client):
        resp = archive_client.get("/api/local-flows")
        assert resp.status_code == 200
        names = {f["name"] for f in resp.json()}
        assert "old" not in names

    def test_list_includes_archived_with_param(self, archive_client):
        resp = archive_client.get("/api/local-flows?include_archived=true")
        assert resp.status_code == 200
        data = resp.json()
        names = {f["name"] for f in data}
        assert "old" in names
        old = [f for f in data if f["name"] == "old"][0]
        assert old["archived"] is True

    def test_list_archived_only(self, archive_client):
        resp = archive_client.get("/api/local-flows?archived_only=true")
        assert resp.status_code == 200
        data = resp.json()
        names = {f["name"] for f in data}
        assert "old" in names
        assert "active" not in names
        # Also check for 'simple' — all non-archived should be excluded
        for f in data:
            assert f["archived"] is True

    def test_archive_endpoint(self, archive_client, archive_project):
        resp = archive_client.post("/api/flows/local/active.flow.yaml/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        content = (archive_project / "active.flow.yaml").read_text()
        assert "archived: true" in content

    def test_unarchive_endpoint(self, archive_client, archive_project):
        resp = archive_client.post("/api/flows/local/old.flow.yaml/unarchive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "unarchived"
        content = (archive_project / "old.flow.yaml").read_text()
        assert "archived" not in content

    def test_archive_nonexistent(self, archive_client):
        resp = archive_client.post("/api/flows/local/nope.flow.yaml/archive")
        assert resp.status_code == 404
