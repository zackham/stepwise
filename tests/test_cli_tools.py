"""Integration tests for M7b CLI tools: schema, --wait, --async, output, fulfill, agent-help."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.cli import main, EXIT_SUCCESS, EXIT_JOB_FAILED, EXIT_USAGE_ERROR


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal stepwise project in a temp dir."""
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir()
    (dot_dir / "db.sqlite").touch()
    (dot_dir / "templates").mkdir()
    (dot_dir / "jobs").mkdir()
    return tmp_path


@pytest.fixture
def simple_flow(tmp_project):
    """A simple 2-step flow that uses $job.question."""
    flow = tmp_project / "simple.flow.yaml"
    flow.write_text("""\
name: simple
description: A simple test flow

steps:
  ask:
    run: |
      python3 -c "
      import json, os
      q = os.environ.get('question', 'default')
      print(json.dumps({'answer': f'Answer to: {q}'}))
      "
    outputs: [answer]
    inputs:
      question: $job.question

  format:
    run: |
      python3 -c "
      import json, os
      a = os.environ.get('answer', '')
      print(json.dumps({'formatted': f'>> {a}'}))
      "
    outputs: [formatted]
    inputs:
      answer: ask.answer
""")
    return flow


@pytest.fixture
def human_flow(tmp_project):
    """A flow with a human step."""
    flow = tmp_project / "human.flow.yaml"
    flow.write_text("""\
name: human-test
description: Flow with human approval

steps:
  prepare:
    run: |
      python3 -c "import json; print(json.dumps({'data': 'ready'}))"
    outputs: [data]
    inputs:
      repo: $job.repo

  approve:
    executor: human
    prompt: "Review and approve"
    outputs: [approved, reason]
    inputs:
      data: prepare.data

  deploy:
    run: |
      python3 -c "import json, os; print(json.dumps({'url': 'https://example.com'}))"
    outputs: [url]
    inputs:
      approved: approve.approved
""")
    return flow


@pytest.fixture
def no_input_flow(tmp_project):
    """A flow with no $job inputs."""
    flow = tmp_project / "standalone.flow.yaml"
    flow.write_text("""\
name: standalone
description: A self-contained flow

steps:
  generate:
    run: |
      python3 -c "import json; print(json.dumps({'content': 'hello world'}))"
    outputs: [content]
""")
    return flow


def _capture_stdout(argv: list[str]) -> tuple[int, str]:
    """Run CLI and capture stdout."""
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = main(argv)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, output


# ── Schema Tests ────────────────────────────────────────────────────────


class TestSchema:
    def test_schema_basic(self, simple_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(simple_flow),
        ])

        assert code == EXIT_SUCCESS
        schema = json.loads(output)
        assert schema["name"] == "simple"
        assert schema["description"] == "A simple test flow"
        assert schema["inputs"] == ["question"]
        assert schema["outputs"] == ["formatted"]
        assert schema["humanSteps"] == []

    def test_schema_with_human_steps(self, human_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(human_flow),
        ])

        assert code == EXIT_SUCCESS
        schema = json.loads(output)
        assert schema["inputs"] == ["repo"]
        assert schema["outputs"] == ["url"]
        assert len(schema["humanSteps"]) == 1
        assert schema["humanSteps"][0]["step"] == "approve"
        assert schema["humanSteps"][0]["fields"] == ["approved", "reason"]

    def test_schema_no_inputs(self, no_input_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(no_input_flow),
        ])

        assert code == EXIT_SUCCESS
        schema = json.loads(output)
        assert schema["inputs"] == []
        assert schema["outputs"] == ["content"]

    def test_schema_file_not_found(self, tmp_project):
        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", "/nonexistent/flow.yaml",
        ])
        assert code == EXIT_USAGE_ERROR

    def test_schema_invalid_yaml(self, tmp_project):
        bad = tmp_project / "bad.flow.yaml"
        bad.write_text("this is not yaml: [")
        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(bad),
        ])
        assert code == EXIT_USAGE_ERROR


# ── Wait Mode Tests ─────────────────────────────────────────────────────


class TestWaitMode:
    def test_wait_success(self, simple_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--var", "question=What is 2+2?",
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"
        assert "job_id" in result
        assert len(result["outputs"]) >= 1
        assert "duration_seconds" in result

    def test_wait_missing_input(self, simple_flow, tmp_project):
        """Missing required input returns structured error."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            # No --var question=...
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert result["status"] == "error"
        assert "question" in result["error"]
        assert "--var" in result["error"]  # actionable error message

    def test_wait_file_not_found(self, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", "/nonexistent.flow.yaml",
            "--wait",
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert result["status"] == "error"

    def test_wait_no_input_flow(self, no_input_flow, tmp_project):
        """Flow with no required inputs runs fine."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(no_input_flow),
            "--wait",
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"
        assert result["outputs"][0]["content"] == "hello world"


# ── Output Command Tests ────────────────────────────────────────────────


class TestOutputCommand:
    def test_output_after_completion(self, simple_flow, tmp_project):
        """Run a flow, then retrieve its outputs."""
        # Run flow first
        code, run_output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--var", "question=test",
        ])
        assert code == EXIT_SUCCESS
        job_id = json.loads(run_output)["job_id"]

        # Retrieve outputs
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id,
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"
        assert len(result["outputs"]) >= 1

    def test_output_full_scope(self, simple_flow, tmp_project):
        """--scope full includes per-step details."""
        code, run_output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--var", "question=test",
        ])
        assert code == EXIT_SUCCESS
        job_id = json.loads(run_output)["job_id"]

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id,
            "--scope", "full",
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert "steps" in result
        assert "cost_usd" in result
        assert "event_count" in result

    def test_output_not_found(self, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", "job-nonexistent",
        ])

        assert code == EXIT_JOB_FAILED
        result = json.loads(output)
        assert "error" in result


# ── Fulfill Command Tests ───────────────────────────────────────────────


class TestFulfillCommand:
    def test_fulfill_invalid_json(self, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", "run-fake", "not json",
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "Invalid JSON" in result["error"]

    def test_fulfill_non_dict_payload(self, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", "run-fake", '"just a string"',
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "must be a JSON object" in result["error"]

    def test_fulfill_run_not_found(self, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", "run-nonexistent", '{"field": "value"}',
        ])

        # Should fail because run doesn't exist
        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "error" in result

    def test_fulfill_stdin(self, tmp_project):
        """--stdin reads JSON payload from stdin."""
        import io
        import sys

        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"field": "value"}')
        try:
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "fulfill", "run-nonexistent", "--stdin",
            ])
        finally:
            sys.stdin = old_stdin

        # Should parse JSON fine, fail on run-not-found (not on missing payload)
        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "error" in result
        assert "Invalid JSON" not in result["error"]

    def test_fulfill_dash_stdin(self, tmp_project):
        """'-' as payload reads from stdin."""
        import io
        import sys

        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"approved": true}')
        try:
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "fulfill", "run-nonexistent", "-",
            ])
        finally:
            sys.stdin = old_stdin

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "error" in result
        assert "Invalid JSON" not in result["error"]

    def test_fulfill_no_payload_no_stdin(self, tmp_project):
        """Missing payload without --stdin returns error."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", "run-fake",
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert "No payload" in result["error"]


# ── Agent Help Tests ────────────────────────────────────────────────────


class TestAgentHelp:
    def test_agent_help_compact_default(self, simple_flow, human_flow, tmp_project):
        """Default compact format lists flows concisely with CLI reference."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "**simple**" in output
        assert "**human-test**" in output
        assert "--wait" in output
        # Compact includes CLI reference section
        assert "stepwise run" in output
        assert "stepwise status" in output
        assert "stepwise fulfill" in output

    def test_agent_help_compact_shows_inputs(self, simple_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "question" in output
        assert '--var question="..."' in output

    def test_agent_help_compact_shows_human_steps(self, human_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "approve" in output
        assert "human:" in output

    def test_agent_help_json_format(self, simple_flow, tmp_project):
        """--format json returns structured JSON."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--format", "json",
        ])

        assert code == EXIT_SUCCESS
        data = json.loads(output)
        assert data["count"] == 1
        assert data["flows"][0]["name"] == "simple"
        assert "question" in data["flows"][0]["inputs"]
        assert "formatted" in data["flows"][0]["outputs"]
        assert "run" in data["flows"][0]

    def test_agent_help_full_format(self, simple_flow, tmp_project):
        """--format full produces verbose markdown."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--format", "full",
        ])

        assert code == EXIT_SUCCESS
        assert "# Stepwise Flows" in output
        assert "### simple" in output
        assert "## Quick Reference" in output

    def test_agent_help_empty_project(self, tmp_project):
        """No flows found → helpful message."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "No flows found" in output

    def test_agent_help_empty_json(self, tmp_project):
        """No flows in JSON → empty array."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--format", "json",
        ])

        assert code == EXIT_SUCCESS
        data = json.loads(output)
        assert data["flows"] == []
        assert data["count"] == 0

    def test_agent_help_update_uses_full_format(self, simple_flow, tmp_project):
        """--update implies full format for readability in docs."""
        target = tmp_project / "CLAUDE.md"
        target.write_text("# My Project\n\nSome content.\n")

        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--update", str(target),
        ])

        assert code == EXIT_SUCCESS
        text = target.read_text()
        assert "<!-- stepwise-agent-help -->" in text
        assert "<!-- /stepwise-agent-help -->" in text
        assert "# My Project" in text  # original content preserved
        assert "# Stepwise Flows" in text  # full format header
        assert "simple" in text

    def test_agent_help_update_replaces(self, simple_flow, tmp_project):
        """--update replaces existing section between markers."""
        target = tmp_project / "CLAUDE.md"
        target.write_text(
            "# Project\n\n"
            "<!-- stepwise-agent-help -->\nOLD CONTENT\n<!-- /stepwise-agent-help -->\n\n"
            "# Other\n"
        )

        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--update", str(target),
        ])

        assert code == EXIT_SUCCESS
        text = target.read_text()
        assert "OLD CONTENT" not in text
        assert "simple" in text
        assert "# Other" in text  # preserved

    def test_agent_help_flows_dir(self, tmp_project):
        """--flows-dir overrides discovery path."""
        custom = tmp_project / "custom"
        custom.mkdir()
        flow = custom / "my.flow.yaml"
        flow.write_text("""\
name: custom-flow
description: A custom flow
steps:
  hello:
    run: 'echo "{}"'
    outputs: [msg]
""")

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--flows-dir", str(custom),
        ])

        assert code == EXIT_SUCCESS
        assert "custom-flow" in output


# ── Var-File Tests ──────────────────────────────────────────────────────


class TestOutputJsonStandalone:
    """Test --output json without --wait (headless mode with JSON result)."""

    def test_output_json_success(self, simple_flow, tmp_project):
        """--output json prints JSON after headless completion."""
        code, output = _capture_stdout([
            "-q",
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--output", "json",
            "--var", "question=test",
        ])

        assert code == EXIT_SUCCESS
        # Should contain JSON on stdout
        result = json.loads(output.strip())
        assert result["status"] == "completed"
        assert "outputs" in result

    def test_output_json_failure(self, tmp_project):
        """--output json prints failure JSON after headless failure."""
        fail_flow = tmp_project / "fail.flow.yaml"
        fail_flow.write_text("""\
name: fail
steps:
  boom:
    run: |
      python3 -c "import sys; print('kaboom', file=sys.stderr); sys.exit(1)"
    outputs: [result]
""")

        code, output = _capture_stdout([
            "-q",
            "--project-dir", str(tmp_project),
            "run", str(fail_flow),
            "--output", "json",
        ])

        assert code == EXIT_JOB_FAILED
        result = json.loads(output.strip())
        assert result["status"] == "failed"
        assert result["failed_step"] == "boom"


class TestWaitTimeout:
    """Test --wait --timeout behavior."""

    def test_timeout_on_human_step(self, human_flow, tmp_project):
        """--wait --timeout returns timeout status when human step blocks."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(human_flow),
            "--wait",
            "--timeout", "1",  # 1 second timeout
            "--var", "repo=/tmp/test",
        ])

        assert code == 3  # EXIT_TIMEOUT
        result = json.loads(output)
        assert result["status"] == "timeout"
        assert result["timeout_seconds"] == 1
        assert "job_id" in result


class TestVarFile:
    def test_var_file_flag(self, simple_flow, tmp_project):
        """--var-file reads file contents as variable value."""
        question_file = tmp_project / "question.txt"
        question_file.write_text("What is the meaning of life?")

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--var-file", f"question={question_file}",
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"

    def test_var_file_not_found(self, simple_flow, tmp_project):
        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--var-file", "question=/nonexistent/file.txt",
        ])
        assert code == EXIT_USAGE_ERROR
