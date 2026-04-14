"""Tests for the `stepwise flows` command."""

import pytest
from pathlib import Path

from stepwise.cli import main, EXIT_SUCCESS


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal .stepwise/ project directory."""
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir()
    (dot_dir / "db.sqlite").touch()
    return tmp_path


def _make_flow_dir(project: Path, name: str, yaml_content: str) -> Path:
    """Create a flows/<name>/FLOW.yaml file."""
    flow_dir = project / "flows" / name
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "FLOW.yaml").write_text(yaml_content)
    return flow_dir


def _make_root_flow(project: Path, filename: str, yaml_content: str) -> Path:
    """Create a *.flow.yaml file in the project root."""
    path = project / filename
    path.write_text(yaml_content)
    return path


def _run_flows(monkeypatch, tmp_path, capsys):
    """Run `stepwise flows` from tmp_path and return (rc, combined_output)."""
    monkeypatch.chdir(tmp_path)
    rc = main(["flows"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    return rc, combined


class TestFlowsCommand:
    """Basic command smoke tests."""

    def test_no_flows_prints_helpful_message(self, tmp_path, capsys, monkeypatch):
        _make_project(tmp_path)
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "No flows found" in combined

    def test_table_headers_present(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "my-flow", "name: my-flow\nsteps:\n  step1:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "NAME" in combined
        assert "DESCRIPTION" in combined
        assert "STEPS" in combined
        assert "VISIBILITY" in combined

    def test_flow_name_appears_in_output(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(
            project, "research-proposal",
            "name: research-proposal\ndescription: Deep research flow\nsteps:\n  step1:\n    run: echo '{}'\n"
        )
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "research-proposal" in combined


class TestFlowsScanning:
    """Test that the correct flow files are found and parsed."""

    def test_scans_flows_subdir(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "alpha", "name: alpha\nsteps:\n  s1:\n    run: echo '{}'\n  s2:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "alpha" in combined
        # Step count should be 2
        assert "2" in combined

    def test_scans_root_flow_yaml_files(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_root_flow(project, "simple.flow.yaml", "name: simple\nsteps:\n  only:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "simple" in combined

    def test_description_shown(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(
            project, "documented",
            'name: documented\ndescription: "Does important things"\nsteps:\n  s1:\n    run: echo "{}"\n'
        )
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "Does important things" in combined

    def test_step_count_correct(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(
            project, "three-step",
            "name: three-step\nsteps:\n  a:\n    run: echo '{}'\n  b:\n    run: echo '{}'\n  c:\n    run: echo '{}'\n"
        )
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "3" in combined

    def test_multiple_flows_sorted_alphabetically(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "zebra", "name: zebra\nsteps:\n  s:\n    run: echo '{}'\n")
        _make_flow_dir(project, "apple", "name: apple\nsteps:\n  s:\n    run: echo '{}'\n")
        _make_flow_dir(project, "mango", "name: mango\nsteps:\n  s:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        # All three should appear, and apple before mango before zebra
        pos_apple = combined.find("apple")
        pos_mango = combined.find("mango")
        pos_zebra = combined.find("zebra")
        assert pos_apple != -1
        assert pos_mango != -1
        assert pos_zebra != -1
        assert pos_apple < pos_mango < pos_zebra

    def test_both_flows_dir_and_root_files_combined(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_flow_dir(project, "dir-flow", "name: dir-flow\nsteps:\n  s:\n    run: echo '{}'\n")
        _make_root_flow(project, "root.flow.yaml", "name: root\nsteps:\n  s:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "dir-flow" in combined
        assert "root" in combined

    def test_broken_yaml_does_not_crash(self, tmp_path, capsys, monkeypatch):
        """Malformed YAML in a flow file should be skipped gracefully (name falls back to dir name)."""
        project = _make_project(tmp_path)
        flow_dir = project / "flows" / "broken"
        flow_dir.mkdir(parents=True)
        (flow_dir / "FLOW.yaml").write_text(": : : invalid yaml :::")
        monkeypatch.chdir(tmp_path)
        rc = main(["flows"])
        # Should not crash — broken flow shows up with fallback name
        assert rc == EXIT_SUCCESS

    def test_no_flows_dir_still_works(self, tmp_path, capsys, monkeypatch):
        """If there's no flows/ directory at all, command works fine."""
        _make_project(tmp_path)
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "No flows found" in combined


def _make_kit(project: Path, kit_name: str, flow_names: list[str]) -> Path:
    """Create a kit with member flows."""
    kit_dir = project / "flows" / kit_name
    kit_dir.mkdir(parents=True, exist_ok=True)
    (kit_dir / "KIT.yaml").write_text(
        f"name: {kit_name}\ndescription: Test kit\n"
    )
    for name in flow_names:
        fd = kit_dir / name
        fd.mkdir(exist_ok=True)
        (fd / "FLOW.yaml").write_text(
            f"name: {name}\nsteps:\n  s:\n    run: echo '{{}}'\n    outputs: [x]\n"
        )
    return kit_dir


class TestFlowsKitGrouping:
    """Kit grouping in `stepwise flows` output."""

    def test_kit_header_appears(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_kit(project, "mykit", ["flow-a"])
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "mykit" in combined
        assert "flow-a" in combined

    def test_standalone_separate_from_kit(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_kit(project, "mykit", ["kit-flow"])
        _make_flow_dir(project, "solo", "name: solo\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [x]\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "mykit" in combined
        assert "kit-flow" in combined
        assert "solo" in combined

    def test_visibility_filter_applies_to_kit_flows(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        kit_dir = project / "flows" / "mykit"
        kit_dir.mkdir(parents=True)
        (kit_dir / "KIT.yaml").write_text("name: mykit\ndescription: Test\n")
        fd = kit_dir / "hidden"
        fd.mkdir()
        (fd / "FLOW.yaml").write_text(
            "name: hidden\nauthor: test\nvisibility: internal\nsteps:\n  s:\n    run: echo '{}'\n"
        )
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "hidden" not in combined
