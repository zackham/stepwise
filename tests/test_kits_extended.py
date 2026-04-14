"""Tests for kit features: new-in-kit, catalog, author requirement."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from stepwise.flow_resolution import (
    KIT_DIR_MARKER,
    FLOW_DIR_MARKER,
    discover_kits,
    discover_flows,
    resolve_flow,
)
from stepwise.yaml_loader import load_kit_yaml, load_workflow_yaml, YAMLLoadError


@pytest.fixture
def kit_project(tmp_path):
    """Create a project with a kit containing flows."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".stepwise").mkdir()
    flows = project / "flows"
    flows.mkdir()

    # Create a kit (no defaults — removed)
    kit_dir = flows / "mykit"
    kit_dir.mkdir()
    (kit_dir / KIT_DIR_MARKER).write_text(
        "name: mykit\n"
        "description: Test kit\n"
        "author: testauthor\n"
        "category: testing\n"
    )

    # Flow with author
    flow1 = kit_dir / "flow-a"
    flow1.mkdir()
    (flow1 / FLOW_DIR_MARKER).write_text(
        "name: flow-a\n"
        "description: A test flow\n"
        "author: flowauthor\n"
        "steps:\n"
        "  step1:\n"
        "    run: echo ok\n"
        "    outputs: [result]\n"
    )

    # Flow with explicit author
    flow2 = kit_dir / "flow-b"
    flow2.mkdir()
    (flow2 / FLOW_DIR_MARKER).write_text(
        "name: flow-b\n"
        "description: Flow with explicit author\n"
        "author: explicitauthor\n"
        "steps:\n"
        "  step1:\n"
        "    run: echo ok\n"
        "    outputs: [result]\n"
    )

    # Standalone flow with author
    standalone = flows / "standalone"
    standalone.mkdir()
    (standalone / FLOW_DIR_MARKER).write_text(
        "name: standalone\n"
        "description: Standalone flow\n"
        "author: standaloneauthor\n"
        "steps:\n"
        "  step1:\n"
        "    run: echo ok\n"
        "    outputs: [result]\n"
    )

    return project


class TestAuthorRequired:
    def test_flow_with_author_loads(self, kit_project):
        flow_path = kit_project / "flows" / "mykit" / "flow-a" / FLOW_DIR_MARKER
        wf = load_workflow_yaml(flow_path)
        assert wf.metadata.author == "flowauthor"

    def test_explicit_author_preserved(self, kit_project):
        flow_path = kit_project / "flows" / "mykit" / "flow-b" / FLOW_DIR_MARKER
        wf = load_workflow_yaml(flow_path)
        assert wf.metadata.author == "explicitauthor"

    def test_standalone_flow_with_author(self, kit_project):
        flow_path = kit_project / "flows" / "standalone" / FLOW_DIR_MARKER
        wf = load_workflow_yaml(flow_path)
        assert wf.metadata.author == "standaloneauthor"
        assert wf.metadata.visibility == "interactive"

    def test_missing_author_raises_error(self, tmp_path):
        """Flow without author should fail validation."""
        flow_yaml = tmp_path / "FLOW.yaml"
        flow_yaml.write_text(
            "name: no-author\n"
            "description: Missing author\n"
            "steps:\n"
            "  s:\n"
            "    run: echo ok\n"
            "    outputs: [x]\n"
        )
        with pytest.raises(YAMLLoadError, match="'author' is required"):
            load_workflow_yaml(flow_yaml)

    def test_kit_yaml_no_defaults_field(self, kit_project):
        """Kit YAML should load without a defaults field."""
        kit_yaml = kit_project / "flows" / "mykit" / "KIT.yaml"
        kit_def = load_kit_yaml(kit_yaml)
        assert kit_def.name == "mykit"
        assert kit_def.author == "testauthor"
        assert not hasattr(kit_def, "defaults")


class TestNewInKit:
    def _run_cli(self, args):
        """Run stepwise CLI via Python import to avoid stale binary issues."""
        import argparse
        from unittest.mock import patch
        from stepwise.cli import main
        with patch("sys.argv", ["stepwise"] + args):
            try:
                return main()
            except SystemExit as e:
                return e.code if e.code else 0

    def test_new_with_kit_prefix(self, kit_project):
        """stepwise new mykit/newflow creates flow inside kit."""
        rc = self._run_cli(["new", "mykit/newflow", "--project-dir", str(kit_project)])
        assert rc == 0
        flow_yaml = kit_project / "flows" / "mykit" / "newflow" / FLOW_DIR_MARKER
        assert flow_yaml.exists()
        content = flow_yaml.read_text()
        assert "mykit/newflow" in content

    def test_new_with_nonexistent_kit(self, kit_project):
        """stepwise new badkit/flow fails with clear error."""
        rc = self._run_cli(["new", "badkit/flow", "--project-dir", str(kit_project)])
        assert rc != 0

    def test_new_standalone(self, kit_project):
        """stepwise new standalone-new creates flow outside kit."""
        rc = self._run_cli(["new", "standalone-new", "--project-dir", str(kit_project)])
        assert rc == 0
        flow_yaml = kit_project / "flows" / "standalone-new" / FLOW_DIR_MARKER
        assert flow_yaml.exists()

    def test_new_duplicate_fails(self, kit_project):
        """Creating a flow that already exists fails."""
        rc = self._run_cli(["new", "mykit/flow-a", "--project-dir", str(kit_project)])
        assert rc != 0


class TestCatalog:
    def _run_cli(self, args):
        from unittest.mock import patch
        from stepwise.cli import main
        with patch("sys.argv", ["stepwise"] + args):
            try:
                return main()
            except SystemExit as e:
                return e.code if e.code else 0

    def test_catalog_output(self, kit_project, capsys):
        rc = self._run_cli(["catalog", "--project-dir", str(kit_project)])
        assert rc == 0
        output = capsys.readouterr().out
        assert "## Available Kits & Flows" in output
        assert "mykit" in output
        assert "Test kit" in output
        assert "2 flows" in output

    def test_catalog_to_file(self, kit_project, tmp_path):
        outfile = tmp_path / "catalog.md"
        rc = self._run_cli(["catalog", "--project-dir", str(kit_project), "-o", str(outfile)])
        assert rc == 0
        assert outfile.exists()
        content = outfile.read_text()
        assert "mykit" in content
