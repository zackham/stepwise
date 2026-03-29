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
def external_flow(tmp_project):
    """A flow with an external step."""
    flow = tmp_project / "external.flow.yaml"
    flow.write_text("""\
name: external-test
description: Flow with external approval

steps:
  prepare:
    run: |
      python3 -c "import json; print(json.dumps({'data': 'ready'}))"
    outputs: [data]
    inputs:
      repo: $job.repo

  approve:
    executor: external
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


def _capture_stderr(argv: list[str]) -> tuple[int, str]:
    """Run CLI and capture stderr (where IOAdapter output goes)."""
    import io
    import sys

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        code = main(argv)
        output = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
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
        assert schema["externalSteps"] == []

    def test_schema_with_external_steps(self, external_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(external_flow),
        ])

        assert code == EXIT_SUCCESS
        schema = json.loads(output)
        assert schema["inputs"] == ["repo"]
        assert schema["outputs"] == ["url"]
        assert len(schema["externalSteps"]) == 1
        assert schema["externalSteps"][0]["step"] == "approve"
        assert schema["externalSteps"][0]["fields"] == ["approved", "reason"]

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

    def test_schema_includes_config(self, tmp_project):
        flow = tmp_project / "config-test.flow.yaml"
        flow.write_text("""\
name: config-test
config:
  persona:
    description: Your persona
    required: true
  style:
    type: choice
    options: [casual, formal]
    default: casual
requires:
  - name: ffmpeg
    check: "ffmpeg -version"
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      persona: $job.persona
      style: $job.style
""")
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "schema", str(flow),
        ])

        assert code == EXIT_SUCCESS
        schema = json.loads(output)
        assert "config" in schema
        assert schema["config"]["persona"]["description"] == "Your persona"
        assert schema["config"]["style"]["default"] == "casual"
        assert "requires" in schema
        assert schema["requires"][0]["name"] == "ffmpeg"


# ── Wait Mode Tests ─────────────────────────────────────────────────────


class TestWaitMode:
    def test_wait_success(self, simple_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--input", "question=What is 2+2?",
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
            # No --input question=...
        ])

        assert code == EXIT_USAGE_ERROR
        result = json.loads(output)
        assert result["status"] == "error"
        assert "question" in result["error"]
        assert "--input" in result["error"]  # actionable error message

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
            "--input", "question=test",
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
            "--input", "question=test",
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
    def test_agent_help_compact_default(self, simple_flow, external_flow, tmp_project):
        """Default compact format lists flows concisely with CLI reference."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "**simple**" in output
        assert "**external-test**" in output
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
        assert '--input question="..."' in output

    def test_agent_help_compact_shows_external_steps(self, external_flow, tmp_project):
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "approve" in output
        assert "external:" in output

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

    def test_agent_help_excludes_background_flows(self, tmp_project):
        """Background flows are excluded from agent-help output."""
        flow_interactive = tmp_project / "visible.flow.yaml"
        flow_interactive.write_text("""\
name: visible-flow
description: An interactive flow
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
""")
        flow_bg = tmp_project / "bg.flow.yaml"
        flow_bg.write_text("""\
name: background-flow
description: A background flow
visibility: background
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
""")
        flow_internal = tmp_project / "int.flow.yaml"
        flow_internal.write_text("""\
name: internal-flow
visibility: internal
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
""")

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
        ])

        assert code == EXIT_SUCCESS
        assert "visible-flow" in output
        assert "background-flow" not in output
        assert "internal-flow" not in output

    def test_agent_help_json_excludes_non_interactive(self, tmp_project):
        """JSON format also respects visibility filtering."""
        flow = tmp_project / "vis.flow.yaml"
        flow.write_text("""\
name: vis-flow
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
""")
        bg_flow = tmp_project / "bgflow.flow.yaml"
        bg_flow.write_text("""\
name: bg-flow
visibility: background
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
""")

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "agent-help",
            "--format", "json",
        ])

        assert code == EXIT_SUCCESS
        data = json.loads(output)
        names = [f["name"] for f in data["flows"]]
        assert "vis-flow" in names
        assert "bg-flow" not in names


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
            "--input", "question=test",
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


class TestWaitSuspension:
    """Test --wait suspension behavior."""

    def test_suspend_on_external_step(self, external_flow, tmp_project):
        """--wait returns suspended status when external step blocks."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow),
            "--wait",
            "--input", "repo=/tmp/test",
        ])

        assert code == 5  # EXIT_SUSPENDED
        result = json.loads(output)
        assert result["status"] == "suspended"
        assert "suspended_steps" in result
        assert "job_id" in result


class TestInputFile:
    def test_input_at_file_flag(self, simple_flow, tmp_project):
        """--input KEY=@path reads file contents as variable value."""
        question_file = tmp_project / "question.txt"
        question_file.write_text("What is the meaning of life?")

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--input", f"question=@{question_file}",
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"

    def test_input_at_file_not_found(self, simple_flow, tmp_project):
        code, _ = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--input", "question=@/nonexistent/file.txt",
        ])
        assert code == EXIT_USAGE_ERROR


# ── Info Command Tests ─────────────────────────────────────────────────


class TestInfo:
    def test_info_local_flow(self, tmp_project):
        """info command displays config vars, requirements, and readme."""
        flow = tmp_project / "my-flow.flow.yaml"
        flow.write_text("""\
name: my-flow
description: A flow with config and requirements

config:
  persona:
    description: "Your AI persona"
    type: str
    example: "You are a helpful assistant"
  max_rounds:
    description: "Maximum iterations"
    type: number
    default: 3

requires:
  - name: echo
    check: "echo ok"
    description: "Echo command"
  - name: jq
    check: "jq --version"
    description: "JSON processor"

readme: |
  # My Flow
  This is a test flow for validation.

steps:
  greet:
    run: |
      python3 -c "import json; print(json.dumps({'greeting': 'hello'}))"
    inputs:
      persona: $job.persona
    outputs: [greeting]
""")
        code, output = _capture_stderr([
            "--project-dir", str(tmp_project),
            "info", str(flow),
        ])

        assert code == EXIT_SUCCESS
        assert "my-flow" in output
        assert "persona" in output
        assert "Your AI persona" in output
        assert "max_rounds" in output
        assert "Maximum iterations" in output
        assert "default: 3" in output
        assert "echo" in output
        assert "Echo command" in output
        assert "My Flow" in output
        assert "This is a test flow for validation." in output

    def test_info_no_config_flow(self, simple_flow, tmp_project):
        """info command works on flows without config/requires/readme."""
        code, output = _capture_stderr([
            "--project-dir", str(tmp_project),
            "info", str(simple_flow),
        ])
        assert code == EXIT_SUCCESS
        assert "simple" in output
        assert "Config variables:" not in output


# ── Config Init Tests ──────────────────────────────────────────────────


class TestConfigInit:
    def test_config_init_scaffold(self, tmp_project):
        """config init scaffolds config.local.yaml with correct content."""
        flow = tmp_project / "my-flow.flow.yaml"
        flow.write_text("""\
name: my-flow

config:
  api_key:
    description: "API key for service"
    type: str
  max_retries:
    description: "Number of retries"
    type: number
    default: 3
  model:
    description: "Model to use"
    type: choice
    options: [gpt-4, claude-3]
    example: gpt-4

steps:
  run:
    run: |
      python3 -c "import json; print(json.dumps({'result': 'ok'}))"
    inputs:
      api_key: $job.api_key
    outputs: [result]
""")
        code = main([
            "--project-dir", str(tmp_project),
            "config", "init", str(flow),
        ])

        assert code == EXIT_SUCCESS

        config_path = tmp_project / "my-flow.config.local.yaml"
        assert config_path.exists()
        content = config_path.read_text()
        # Required var with no default → blank value for user to fill
        assert 'api_key: ""' in content
        # Optional var with default → commented out
        assert "# max_retries: 3" in content
        # Choice options listed
        assert "Options: gpt-4, claude-3" in content
        # Example shown
        assert "Example: gpt-4" in content

    def test_config_init_already_exists(self, tmp_project):
        """config init errors if config.local.yaml already exists."""
        flow = tmp_project / "my-flow.flow.yaml"
        flow.write_text("""\
name: my-flow

config:
  api_key:
    description: "API key"
    type: str

steps:
  run:
    run: |
      python3 -c "import json; print(json.dumps({'result': 'ok'}))"
    inputs:
      api_key: $job.api_key
    outputs: [result]
""")
        # Pre-create the config file
        config_path = tmp_project / "my-flow.config.local.yaml"
        config_path.write_text("api_key: existing\n")

        code = main([
            "--project-dir", str(tmp_project),
            "config", "init", str(flow),
        ])

        assert code == EXIT_USAGE_ERROR
        # Original content preserved
        assert config_path.read_text() == "api_key: existing\n"

    def test_config_init_no_config_block(self, tmp_project):
        """config init on flow without config block returns success with info message."""
        flow = tmp_project / "simple.flow.yaml"
        flow.write_text("""\
name: simple
steps:
  run:
    run: |
      python3 -c "import json; print(json.dumps({'result': 'ok'}))"
    outputs: [result]
""")
        code = main([
            "--project-dir", str(tmp_project),
            "config", "init", str(flow),
        ])

        assert code == EXIT_SUCCESS


# ── Wait Command (stepwise wait <job-id>) Tests ─────────────────────────


class TestWaitCommand:
    """Tests for N34: `stepwise wait <job-id>` re-attach and SIGTSTP detach."""

    def test_wait_completed_job(self, simple_flow, tmp_project):
        """stepwise wait on an already-completed job returns its result."""
        # Run a flow to completion first
        code, run_output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(simple_flow),
            "--wait",
            "--input", "question=hello",
        ])
        assert code == EXIT_SUCCESS
        job_id = json.loads(run_output)["job_id"]

        # Now re-attach via stepwise wait
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", job_id,
        ])

        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["status"] == "completed"
        assert result["job_id"] == job_id

    def test_wait_nonexistent_job(self, tmp_project):
        """stepwise wait on a nonexistent job returns error."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", "job-doesnotexist",
        ])

        assert code == EXIT_JOB_FAILED
        result = json.loads(output)
        assert "error" in result

    def test_wait_failed_job(self, tmp_project):
        """stepwise wait on a failed job returns failed status."""
        fail_flow = tmp_project / "fail.flow.yaml"
        fail_flow.write_text("""\
name: fail
steps:
  boom:
    run: |
      python3 -c "import sys; sys.exit(1)"
    outputs: [result]
""")
        code, run_output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(fail_flow),
            "--wait",
        ])
        assert code == EXIT_JOB_FAILED
        job_id = json.loads(run_output)["job_id"]

        # Re-attach after failure — should report failure
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", job_id,
        ])

        assert code == EXIT_JOB_FAILED
        result = json.loads(output)
        assert result["status"] == "failed"
        assert result["job_id"] == job_id

    def test_wait_suspended_job(self, external_flow, tmp_project):
        """stepwise wait on a suspended job returns suspended status."""
        code, run_output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow),
            "--wait",
            "--input", "repo=/tmp/test",
        ])
        assert code == 5  # EXIT_SUSPENDED
        job_id = json.loads(run_output)["job_id"]

        # Re-attach — should see suspension
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", job_id,
        ])

        assert code == 5  # EXIT_SUSPENDED
        result = json.loads(output)
        assert result["status"] == "suspended"
        assert result["job_id"] == job_id

    def test_sigtstp_detach_prints_message(self, tmp_path):
        """SIGTSTP in async wait loop writes detach message to stderr and exits 0."""
        import signal as _signal
        import os
        import threading
        import asyncio as _asyncio
        import sys as _sys
        import io

        from stepwise.project import init_project
        from stepwise.runner import _async_wait_for_job
        from stepwise.engine import AsyncEngine
        from stepwise.store import SQLiteStore
        from stepwise.config import StepwiseConfig
        from stepwise.registry_factory import create_default_registry
        from stepwise.yaml_loader import load_workflow_yaml

        project = init_project(tmp_path)
        config = StepwiseConfig()
        store = SQLiteStore(str(project.db_path))
        registry = create_default_registry(config)
        engine = AsyncEngine(
            store, registry,
            jobs_dir=str(project.jobs_dir),
            project_dir=project.dot_dir,
            config=config,
        )

        # A slow flow that won't finish before we send SIGTSTP
        slow_flow = tmp_path / "slow.flow.yaml"
        slow_flow.write_text("""\
name: slow
steps:
  waiting:
    run: |
      python3 -c "import time; time.sleep(30)"
    outputs: [done]
""")
        workflow = load_workflow_yaml(str(slow_flow))
        job = engine.create_job(
            objective="slow",
            workflow=workflow,
            inputs={},
        )
        store.save_job(job)

        old_stderr = _sys.stderr
        _sys.stderr = io.StringIO()

        exit_code = None
        try:
            async def _run():
                # Fire SIGTSTP after 150ms so the engine loop starts
                def _fire():
                    import time
                    time.sleep(0.15)
                    os.kill(os.getpid(), _signal.SIGTSTP)

                threading.Thread(target=_fire, daemon=True).start()
                return await _async_wait_for_job(engine, store, job.id)

            exit_code = _asyncio.run(_run())
            stderr_out = _sys.stderr.getvalue()
        finally:
            _sys.stderr = old_stderr
            store.close()

        assert exit_code == 0  # EXIT_SUCCESS — detach, not cancel
        assert job.id in stderr_out
        assert "stepwise wait" in stderr_out
        assert "Detached" in stderr_out
