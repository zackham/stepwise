"""Tests for flow discovery, name-based resolution, and `stepwise new`."""

import pytest
from pathlib import Path

from stepwise.flow_resolution import (
    FlowResolutionError,
    FlowInfo,
    discover_flows,
    resolve_flow,
)
from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.project import init_project


SIMPLE_FLOW = """\
name: test
steps:
  hello:
    run: 'echo "hello"'
    outputs: [msg]
"""


# ── resolve_flow ─────────────────────────────────────────────────────


class TestResolveFlowExactPath:
    """resolve_flow with exact file or directory paths."""

    def test_exact_file_path(self, tmp_path):
        flow = tmp_path / "my.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        assert resolve_flow(str(flow)) == flow

    def test_exact_directory_with_marker(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        marker = flow_dir / "FLOW.yaml"
        marker.write_text(SIMPLE_FLOW)
        assert resolve_flow(str(flow_dir)) == marker

    def test_exact_directory_without_marker_errors(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow(str(flow_dir))

    def test_path_with_slash_not_found(self, tmp_path):
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("subdir/missing.yaml", project_dir=tmp_path)

    def test_yaml_extension_not_found(self, tmp_path):
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("missing.yaml", project_dir=tmp_path)


class TestResolveFlowByName:
    """resolve_flow with flow names that trigger discovery."""

    def test_finds_directory_flow(self, tmp_path):
        flow_dir = tmp_path / "flows" / "my-flow"
        flow_dir.mkdir(parents=True)
        marker = flow_dir / "FLOW.yaml"
        marker.write_text(SIMPLE_FLOW)
        assert resolve_flow("my-flow", project_dir=tmp_path) == marker

    def test_finds_single_file_flow(self, tmp_path):
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        flow = flows_dir / "my-flow.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        assert resolve_flow("my-flow", project_dir=tmp_path) == flow

    def test_directory_takes_precedence_over_file(self, tmp_path):
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        # Create both forms
        dir_flow = flows_dir / "my-flow"
        dir_flow.mkdir()
        marker = dir_flow / "FLOW.yaml"
        marker.write_text(SIMPLE_FLOW)
        file_flow = flows_dir / "my-flow.flow.yaml"
        file_flow.write_text(SIMPLE_FLOW)

        result = resolve_flow("my-flow", project_dir=tmp_path)
        assert result == marker

    def test_project_root_searched_first(self, tmp_path):
        # Flow in project root
        root_dir = tmp_path / "my-flow"
        root_dir.mkdir()
        root_marker = root_dir / "FLOW.yaml"
        root_marker.write_text(SIMPLE_FLOW)

        # Also flow in flows/
        flows_dir = tmp_path / "flows" / "my-flow"
        flows_dir.mkdir(parents=True)
        flows_marker = flows_dir / "FLOW.yaml"
        flows_marker.write_text(SIMPLE_FLOW)

        result = resolve_flow("my-flow", project_dir=tmp_path)
        assert result == root_marker

    def test_dotdir_searched(self, tmp_path):
        dot_dir = tmp_path / ".stepwise" / "flows"
        dot_dir.mkdir(parents=True)
        flow = dot_dir / "hidden.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        assert resolve_flow("hidden", project_dir=tmp_path) == flow

    def test_invalid_name_errors(self, tmp_path):
        with pytest.raises(FlowResolutionError, match="Invalid flow name"):
            resolve_flow("bad name!", project_dir=tmp_path)

    def test_path_traversal_rejected(self, tmp_path):
        """Paths with / are treated as path lookups, not name resolution."""
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("../traversal", project_dir=tmp_path)

    def test_not_found_lists_searched_dirs(self, tmp_path):
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("nonexistent", project_dir=tmp_path)

    def test_shadow_warning(self, tmp_path, capsys):
        """Multiple matches across search dirs produces stderr warning."""
        # Project root
        root_dir = tmp_path / "dupe"
        root_dir.mkdir()
        (root_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        # flows/ directory (different search dir)
        flows_dir = tmp_path / "flows" / "dupe"
        flows_dir.mkdir(parents=True)
        (flows_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        result = resolve_flow("dupe", project_dir=tmp_path)
        # Should pick the first one (project root)
        assert result == root_dir / "FLOW.yaml"
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "multiple" in err.lower()


# ── discover_flows ───────────────────────────────────────────────────


class TestDiscoverFlows:
    """discover_flows finds all flows in a project."""

    def test_finds_directory_and_file_flows(self, tmp_path):
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        # Directory flow
        df = flows_dir / "dir-flow"
        df.mkdir()
        (df / "FLOW.yaml").write_text(SIMPLE_FLOW)
        # File flow
        (flows_dir / "file-flow.flow.yaml").write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        names = {f.name for f in result}
        assert names == {"dir-flow", "file-flow"}

    def test_deduplicates_by_resolved_path(self, tmp_path):
        # Same flow in project root (via symlink or exact same resolved path)
        flow = tmp_path / "solo.flow.yaml"
        flow.write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        assert sum(1 for f in result if f.name == "solo") == 1

    def test_directory_flow_takes_precedence(self, tmp_path):
        """If both my-flow/ and my-flow.flow.yaml exist, directory wins."""
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        df = flows_dir / "my-flow"
        df.mkdir()
        (df / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flows_dir / "my-flow.flow.yaml").write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        my_flows = [f for f in result if f.name == "my-flow"]
        assert len(my_flows) == 1
        assert my_flows[0].is_directory is True

    def test_empty_project_returns_empty(self, tmp_path):
        assert discover_flows(tmp_path) == []

    def test_flow_info_fields(self, tmp_path):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        assert len(result) == 1
        info = result[0]
        assert isinstance(info, FlowInfo)
        assert info.name == "test"
        assert info.path == flow
        assert info.is_directory is False


# ── stepwise new ─────────────────────────────────────────────────────


class TestNewCommand:
    """stepwise new creates a flow directory."""

    def test_creates_directory_flow(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "new", "my-flow"])
        assert rc == EXIT_SUCCESS
        marker = tmp_path / "flows" / "my-flow" / "FLOW.yaml"
        assert marker.exists()
        content = marker.read_text()
        assert "name: my-flow" in content
        assert "hello from my-flow" in content
        out = capsys.readouterr().out
        assert "Created" in out

    def test_existing_directory_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "flows" / "exists").mkdir(parents=True)
        rc = main(["--project-dir", str(tmp_path), "new", "exists"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_invalid_name_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "new", "bad name!"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "Invalid flow name" in err


# ── CLI integration: name resolution ─────────────────────────────────


class TestCLINameResolution:
    """CLI commands work with flow names, not just paths."""

    def _setup_project(self, tmp_path):
        """Create a project with a named flow."""
        init_project(tmp_path)
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir(exist_ok=True)
        flow_dir = flows_dir / "greet"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text("""\
name: greet
steps:
  hello:
    run: 'echo "{\\"message\\": \\"hi\\"}"'
    outputs: [message]
""")

    def test_validate_by_name(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "validate", "greet"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "1 steps" in out

    def test_schema_by_name(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "schema", "greet"])
        assert rc == EXIT_SUCCESS
        import json
        output = json.loads(capsys.readouterr().out)
        assert output["name"] == "greet"

    def test_run_by_name(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "run", "greet", "--wait"])
        assert rc == EXIT_SUCCESS
        import json
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "completed"

    def test_run_not_found_json_error(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "run", "nonexistent", "--wait"])
        assert rc == EXIT_USAGE_ERROR
        import json
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "not found" in output["error"].lower()
