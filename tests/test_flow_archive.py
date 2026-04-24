"""Tests for flow archive/unarchive/delete functionality."""

from pathlib import Path

import pytest

from stepwise.cli import main, EXIT_SUCCESS, EXIT_JOB_FAILED
from stepwise.flow_resolution import is_archived, set_flow_archived


MINIMAL_FLOW = "name: test-flow\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"


def _make_project(tmp_path: Path) -> Path:
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir()
    (dot_dir / "db.sqlite").touch()
    return tmp_path


def _make_flow_dir(project: Path, name: str, yaml_content: str) -> Path:
    flow_dir = project / "flows" / name
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "FLOW.yaml").write_text(yaml_content)
    return flow_dir


def _make_root_flow(project: Path, filename: str, yaml_content: str) -> Path:
    path = project / filename
    path.write_text(yaml_content)
    return path


class TestHelpers:
    """Unit tests for is_archived() and set_flow_archived()."""

    def test_is_archived_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(f"name: x\narchived: true\n{MINIMAL_FLOW.split(chr(10), 1)[1]}")
        assert is_archived(p) is True

    def test_is_archived_false_missing(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert is_archived(p) is False

    def test_is_archived_false_explicit(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: x\narchived: false\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert is_archived(p) is False

    def test_is_archived_broken_yaml(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(": : : invalid :::")
        assert is_archived(p) is False

    def test_is_archived_nonexistent(self, tmp_path):
        p = tmp_path / "nope.flow.yaml"
        assert is_archived(p) is False

    def test_set_archived_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert set_flow_archived(p, True) is True
        content = p.read_text()
        assert "archived: true" in content

    def test_set_archived_false(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: test-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert set_flow_archived(p, False) is True
        content = p.read_text()
        assert "archived" not in content

    def test_set_archived_idempotent_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: test-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert set_flow_archived(p, True) is False

    def test_set_archived_idempotent_false(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert set_flow_archived(p, False) is False

    def test_round_trip_preserves_content(self, tmp_path):
        original = "name: test-flow  # important comment\ndescription: keep this\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        p = tmp_path / "flow.flow.yaml"
        p.write_text(original)

        set_flow_archived(p, True)
        assert "archived: true" in p.read_text()
        assert "# important comment" in p.read_text()

        set_flow_archived(p, False)
        restored = p.read_text()
        assert restored == original


class TestCLI:
    """Tests for stepwise flow archive/unarchive/delete commands."""

    def test_cli_archive_sets_flag(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", MINIMAL_FLOW.replace("test-flow", "my-flow"))
        monkeypatch.chdir(project)
        rc = main(["flow", "archive", "my-flow"])
        assert rc == EXIT_SUCCESS
        yaml_path = project / "flows" / "my-flow" / "FLOW.yaml"
        assert "archived: true" in yaml_path.read_text()

    def test_cli_unarchive_removes_flag(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow",
                       "name: my-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        monkeypatch.chdir(project)
        rc = main(["flow", "unarchive", "my-flow"])
        assert rc == EXIT_SUCCESS
        content = (project / "flows" / "my-flow" / "FLOW.yaml").read_text()
        assert "archived" not in content

    def test_cli_archive_idempotent(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow",
                       "name: my-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        monkeypatch.chdir(project)
        rc = main(["flow", "archive", "my-flow"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out + capsys.readouterr().err
        # Should indicate already archived (via info log)

    def test_cli_unarchive_idempotent(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", MINIMAL_FLOW.replace("test-flow", "my-flow"))
        monkeypatch.chdir(project)
        rc = main(["flow", "unarchive", "my-flow"])
        assert rc == EXIT_SUCCESS

    def test_cli_delete_single_file_with_yes(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_root_flow(project, "foo.flow.yaml", MINIMAL_FLOW.replace("test-flow", "foo"))
        monkeypatch.chdir(project)
        rc = main(["flow", "delete", "foo", "--yes"])
        assert rc == EXIT_SUCCESS
        assert not (project / "foo.flow.yaml").exists()

    def test_cli_delete_directory_with_yes(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "bar", MINIMAL_FLOW.replace("test-flow", "bar"))
        monkeypatch.chdir(project)
        rc = main(["flow", "delete", "bar", "--yes"])
        assert rc == EXIT_SUCCESS
        assert not (project / "flows" / "bar").exists()

    def test_cli_delete_confirmation_match(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", MINIMAL_FLOW.replace("test-flow", "my-flow"))
        monkeypatch.chdir(project)
        monkeypatch.setattr("builtins.input", lambda _: "my-flow")
        rc = main(["flow", "delete", "my-flow"])
        assert rc == EXIT_SUCCESS
        assert not (project / "flows" / "my-flow").exists()

    def test_cli_delete_confirmation_mismatch(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", MINIMAL_FLOW.replace("test-flow", "my-flow"))
        monkeypatch.chdir(project)
        monkeypatch.setattr("builtins.input", lambda _: "wrong")
        rc = main(["flow", "delete", "my-flow"])
        assert rc == EXIT_JOB_FAILED
        assert (project / "flows" / "my-flow" / "FLOW.yaml").exists()

    def test_cli_delete_nonexistent(self, tmp_path, monkeypatch, capsys):
        project = _make_project(tmp_path)
        monkeypatch.chdir(project)
        rc = main(["flow", "delete", "no-such-flow", "--yes"])
        assert rc != EXIT_SUCCESS


ARCHIVED_FLOW = "name: hidden\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
ACTIVE_FLOW = "name: active\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"


class TestFlowsListing:
    """Tests for archive filtering in stepwise flows."""

    def _setup_flows(self, tmp_path, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "active", ACTIVE_FLOW)
        _make_flow_dir(project, "hidden", ARCHIVED_FLOW)
        monkeypatch.chdir(project)
        return project

    def test_hides_archived_by_default(self, tmp_path, monkeypatch, capsys):
        self._setup_flows(tmp_path, monkeypatch)
        rc = main(["flows"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "active" in out
        assert "hidden" not in out

    def test_include_archived(self, tmp_path, monkeypatch, capsys):
        self._setup_flows(tmp_path, monkeypatch)
        rc = main(["flows", "--include-archived"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "active" in out
        assert "hidden" in out
        assert "[archived]" in out

    def test_archived_only(self, tmp_path, monkeypatch, capsys):
        self._setup_flows(tmp_path, monkeypatch)
        rc = main(["flows", "--archived-only"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "hidden" in out
        assert "[archived]" in out

    def test_flags_mutually_exclusive(self, tmp_path, monkeypatch, capsys):
        self._setup_flows(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            main(["flows", "--include-archived", "--archived-only"])
        assert exc_info.value.code == 2


class TestAgentHelp:
    """Tests for agent help and resolve_flow with archived flows."""

    def test_agent_help_excludes_archived(self, tmp_path):
        from stepwise.agent_help import generate_agent_help

        full_flow = "name: active-flow\nauthor: test\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        archived_full = "name: archived-flow\nauthor: test\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        project = _make_project(tmp_path)
        _make_flow_dir(project, "active-flow", full_flow)
        _make_flow_dir(project, "archived-flow", archived_full)
        output = generate_agent_help(project)
        assert "active-flow" in output
        assert "archived-flow" not in output

    def test_agent_help_includes_non_archived(self, tmp_path):
        from stepwise.agent_help import generate_agent_help

        full_flow = "name: my-flow\nauthor: test\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", full_flow)
        output = generate_agent_help(project)
        assert "my-flow" in output

    def test_resolve_flow_finds_archived(self, tmp_path, monkeypatch):
        from stepwise.flow_resolution import resolve_flow

        project = _make_project(tmp_path)
        _make_flow_dir(project, "old-flow", ARCHIVED_FLOW.replace("hidden", "old-flow"))
        monkeypatch.chdir(project)
        result = resolve_flow("old-flow", project)
        assert result.exists()
        assert "FLOW.yaml" in str(result)
