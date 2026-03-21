"""Tests for flow discovery, name-based resolution, and `stepwise new`."""

import pytest
from pathlib import Path

from stepwise.flow_resolution import (
    FlowResolutionError,
    FlowInfo,
    discover_flows,
    parse_registry_ref,
    registry_flow_dir,
    resolve_flow,
    resolve_registry_flow,
)
from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.yaml_loader import load_workflow_yaml
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

    def test_user_level_flows_searched(self, tmp_path, monkeypatch):
        """~/.stepwise/flows/ is searched for flow names."""
        fake_home = tmp_path / "fakehome"
        user_flows = fake_home / ".stepwise" / "flows"
        user_flows.mkdir(parents=True)
        flow = user_flows / "global-flow.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # project_dir has no matching flow, but user-level does
        project = tmp_path / "project"
        project.mkdir()
        assert resolve_flow("global-flow", project_dir=project) == flow

    def test_project_takes_precedence_over_user_level(self, tmp_path, monkeypatch):
        """Project flows shadow user-level flows."""
        fake_home = tmp_path / "fakehome"
        user_flows = fake_home / ".stepwise" / "flows"
        user_flows.mkdir(parents=True)
        (user_flows / "shared.flow.yaml").write_text(SIMPLE_FLOW)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        project = tmp_path / "project"
        proj_flows = project / "flows"
        proj_flows.mkdir(parents=True)
        proj_flow = proj_flows / "shared.flow.yaml"
        proj_flow.write_text(SIMPLE_FLOW)

        result = resolve_flow("shared", project_dir=project)
        assert result == proj_flow

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
        assert "gather-info:" in content
        assert "analyze:" in content
        assert "format-report:" in content
        assert "executor: llm" in content
        assert "#" in content  # has inline comments
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Created" in combined

    def test_new_template_validates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        marker = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        wf = load_workflow_yaml(str(marker))
        assert wf.validate() == []
        assert wf.warnings() == []
        assert len(wf.steps) == 3
        assert set(wf.steps.keys()) == {"gather-info", "analyze", "format-report"}
        assert wf.steps["analyze"].executor.type == "llm"
        # analyze depends on gather-info
        analyze_sources = {ib.source_step for ib in wf.steps["analyze"].inputs}
        assert "gather-info" in analyze_sources
        # format-report depends on analyze
        report_sources = {ib.source_step for ib in wf.steps["format-report"].inputs}
        assert "analyze" in report_sources

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
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "1 steps" in combined

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


# ── Registry ref parsing ──────────────────────────────────────────────


class TestParseRegistryRef:
    def test_author_name(self):
        assert parse_registry_ref("@bob:code-review") == ("bob", "code-review")

    def test_author_only(self):
        # @bob without colon — not a valid registry ref (need author:name)
        assert parse_registry_ref("@bob") is None

    def test_bare_name(self):
        assert parse_registry_ref("code-review") is None

    def test_path(self):
        assert parse_registry_ref("flows/test.flow.yaml") is None

    def test_empty(self):
        assert parse_registry_ref("") is None


# ── Registry flow resolution ─────────────────────────────────────────


class TestResolveRegistryFlow:
    def test_resolves_cached_flow(self, tmp_path):
        # Set up .stepwise/registry/@alice/my-flow/FLOW.yaml
        reg_dir = tmp_path / ".stepwise" / "registry" / "@alice" / "my-flow"
        reg_dir.mkdir(parents=True)
        (reg_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        result = resolve_registry_flow("alice", "my-flow", tmp_path)
        assert result == reg_dir / "FLOW.yaml"

    def test_not_cached_raises(self, tmp_path):
        with pytest.raises(FlowResolutionError, match="not cached"):
            resolve_registry_flow("alice", "missing-flow", tmp_path)

    def test_different_authors_dont_collide(self, tmp_path):
        # alice and bob both have "code-review"
        for author in ("alice", "bob"):
            reg_dir = tmp_path / ".stepwise" / "registry" / f"@{author}" / "code-review"
            reg_dir.mkdir(parents=True)
            (reg_dir / "FLOW.yaml").write_text(f"name: code-review\nauthor: {author}\n")

        alice_path = resolve_registry_flow("alice", "code-review", tmp_path)
        bob_path = resolve_registry_flow("bob", "code-review", tmp_path)
        assert alice_path != bob_path
        assert "alice" in alice_path.read_text()
        assert "bob" in bob_path.read_text()

    def test_registry_flow_dir(self, tmp_path):
        result = registry_flow_dir("alice", "my-flow", tmp_path)
        assert result == tmp_path / ".stepwise" / "registry" / "@alice" / "my-flow"

    def test_registry_flow_not_found_by_bare_name(self, tmp_path):
        """Bare name resolution does NOT find registry flows."""
        reg_dir = tmp_path / ".stepwise" / "registry" / "@alice" / "my-flow"
        reg_dir.mkdir(parents=True)
        (reg_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        # resolve_flow with bare name should NOT find it
        with pytest.raises(FlowResolutionError):
            resolve_flow("my-flow", tmp_path)

    def test_local_flow_not_affected_by_registry(self, tmp_path):
        """Local flow and registry flow with same name are independent."""
        # Local flow
        local_dir = tmp_path / "flows" / "my-flow"
        local_dir.mkdir(parents=True)
        (local_dir / "FLOW.yaml").write_text("name: my-flow\nauthor: me\n")

        # Registry flow
        reg_dir = tmp_path / ".stepwise" / "registry" / "@alice" / "my-flow"
        reg_dir.mkdir(parents=True)
        (reg_dir / "FLOW.yaml").write_text("name: my-flow\nauthor: alice\n")

        # Bare name resolves to local
        local_path = resolve_flow("my-flow", tmp_path)
        assert "me" in local_path.read_text()

        # @alice:my-flow resolves to registry
        reg_path = resolve_registry_flow("alice", "my-flow", tmp_path)
        assert "alice" in reg_path.read_text()
