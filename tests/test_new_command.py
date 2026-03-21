"""Tests for `stepwise new` command — template generation and validation."""

import yaml
import pytest
from pathlib import Path

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.yaml_loader import load_workflow_yaml


class TestNewCommand:
    def _init_project(self, tmp_path):
        """Initialize a stepwise project in tmp_path."""
        main(["--project-dir", str(tmp_path), "init", "--no-skill"])

    def test_new_creates_three_step_template(self, tmp_path, capsys):
        self._init_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "new", "test-flow"])
        assert rc == EXIT_SUCCESS

        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        assert flow_file.exists()

        data = yaml.safe_load(flow_file.read_text())
        assert len(data["steps"]) == 3
        assert set(data["steps"].keys()) == {"gather-info", "analyze", "format-report"}

    def test_new_template_has_correct_dependencies(self, tmp_path):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        data = yaml.safe_load(flow_file.read_text())

        # analyze depends on gather-info
        analyze_inputs = data["steps"]["analyze"]["inputs"]
        assert analyze_inputs["topic"] == "gather-info.topic"
        assert analyze_inputs["timestamp"] == "gather-info.timestamp"

        # format-report depends on gather-info and analyze
        report_inputs = data["steps"]["format-report"]["inputs"]
        assert report_inputs["topic"] == "gather-info.topic"
        assert report_inputs["summary"] == "analyze.summary"

    def test_new_template_uses_llm_executor(self, tmp_path):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        data = yaml.safe_load(flow_file.read_text())

        assert data["steps"]["analyze"]["executor"] == "llm"
        assert "prompt" in data["steps"]["analyze"]

    def test_new_template_validates(self, tmp_path):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"

        # Should parse without errors
        wf = load_workflow_yaml(str(flow_file))
        assert len(wf.steps) == 3

    def test_new_template_has_comments(self, tmp_path):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        text = flow_file.read_text()

        assert "# " in text  # has comments
        assert "script" in text.lower() or "run:" in text.lower()
        assert "llm" in text.lower()

    def test_new_rejects_existing_directory(self, tmp_path, capsys):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])

        rc = main(["--project-dir", str(tmp_path), "new", "test-flow"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already exists" in err.lower()

    def test_new_rejects_invalid_name(self, tmp_path, capsys):
        self._init_project(tmp_path)
        rc = main(["--project-dir", str(tmp_path), "new", "bad flow name!"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "invalid" in err.lower()

    def test_new_template_has_config_block(self, tmp_path):
        self._init_project(tmp_path)
        main(["--project-dir", str(tmp_path), "new", "test-flow"])
        flow_file = tmp_path / "flows" / "test-flow" / "FLOW.yaml"
        data = yaml.safe_load(flow_file.read_text())

        assert "config" in data
        assert "topic" in data["config"]
