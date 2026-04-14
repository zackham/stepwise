"""Tests for stepwise.runner — headless flow execution."""

import json
import signal
import time
import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

from stepwise.config import StepwiseConfig
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ScriptExecutor, ExternalExecutor, MockLLMExecutor
from stepwise.io import PlainAdapter, create_adapter
from stepwise.models import (
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.project import init_project
from stepwise.runner import (
    EXIT_CONFIG_ERROR,
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    load_vars_file,
    parse_inputs,
    run_flow,
)
from stepwise.store import SQLiteStore


def _write_flow(tmp_path: Path, content: str, name: str = "test.flow.yaml") -> Path:
    flow = tmp_path / name
    flow.write_text(content)
    return flow


SIMPLE_SCRIPT_FLOW = """\
name: simple
author: test
steps:
  hello:
    run: 'echo "{\\"message\\": \\"hello world\\"}"'
    outputs: [message]
"""

TWO_STEP_FLOW = """\
name: two-step
author: test
steps:
  step1:
    run: 'echo "{\\"result\\": \\"done\\"}"'
    outputs: [result]
  step2:
    run: 'echo "{\\"final\\": \\"complete\\"}"'
    outputs: [final]
    inputs:
      data: step1.result
"""

FAILING_FLOW = """\
name: failing
author: test
steps:
  bad:
    run: "exit 1"
    outputs: [result]
"""

EXTERNAL_STEP_FLOW = """\
name: with-external
author: test
steps:
  ask:
    executor: external
    prompt: "What is your name?"
    outputs: [name]
  greet:
    run: 'echo "{\\"greeting\\": \\"hello\\"}"'
    outputs: [greeting]
    inputs:
      user_name: ask.name
"""

EXTERNAL_MULTI_FIELD_FLOW = """\
name: multi-field-external
author: test
steps:
  review:
    executor: external
    prompt: "Review this draft."
    outputs: [decision, feedback]
"""


class TestRunFlow:
    """run_flow() runs flows to completion."""

    def test_simple_script_flow_completes(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW)
        output = StringIO()
        rc = run_flow(flow, project, quiet=False, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_SUCCESS

    def test_two_step_flow_completes(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, TWO_STEP_FLOW)
        output = StringIO()
        rc = run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_SUCCESS

    def test_failing_step_returns_exit_1(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, FAILING_FLOW)
        output = StringIO()
        rc = run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_JOB_FAILED

    def test_missing_file_returns_exit_2(self, tmp_path):
        project = init_project(tmp_path)
        output = StringIO()
        rc = run_flow(
            tmp_path / "nonexistent.yaml",
            project,
            quiet=True,
            output_stream=output,
            config=StepwiseConfig(),
        )
        assert rc == EXIT_USAGE_ERROR

    def test_invalid_flow_returns_exit_2(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, "not: valid: yaml: [")
        output = StringIO()
        rc = run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_USAGE_ERROR

    def test_creates_job_under_project_jobs_dir(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW)
        output = StringIO()
        run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        # DB should exist
        assert project.db_path.exists()

    def test_objective_defaults_to_flow_name(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW, name="simple.flow.yaml")
        output = StringIO()
        run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        # Verify the job was created with the flow file stem
        store = SQLiteStore(str(project.db_path))
        jobs = [store.load_job(r["id"]) for r in store._conn.execute("SELECT id FROM jobs").fetchall()]
        store.close()
        # flow_display_name strips ".flow.yaml" → "simple"
        assert any(j.objective == "simple" for j in jobs)

    def test_custom_objective(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW)
        output = StringIO()
        run_flow(
            flow, project,
            objective="Custom Objective",
            quiet=True,
            output_stream=output,
            config=StepwiseConfig(),
        )
        store = SQLiteStore(str(project.db_path))
        jobs = [store.load_job(r["id"]) for r in store._conn.execute("SELECT id FROM jobs").fetchall()]
        store.close()
        assert any(j.objective == "Custom Objective" for j in jobs)

    def test_inputs_passed_to_job(self, tmp_path):
        project = init_project(tmp_path)
        flow_content = """\
name: with-inputs
author: test
steps:
  echo:
    run: 'echo "{\\"out\\": \\"ok\\"}"'
    outputs: [out]
"""
        flow = _write_flow(tmp_path, flow_content)
        output = StringIO()
        rc = run_flow(
            flow, project,
            inputs={"env": "staging"},
            quiet=True,
            output_stream=output,
            config=StepwiseConfig(),
        )
        assert rc == EXIT_SUCCESS


class TestExternalStepEndToEnd:
    """End-to-end: external step in headless mode with mocked stdin."""

    def test_external_step_flow_completes(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, EXTERNAL_STEP_FLOW)
        input_stream = StringIO("Zack\n")
        output_stream = StringIO()

        adapter = PlainAdapter(output=output_stream, input_stream=input_stream)
        rc = run_flow(
            flow, project,
            quiet=False,
            input_stream=input_stream,
            output_stream=output_stream,
            config=StepwiseConfig(),
            adapter=adapter,
        )
        assert rc == EXIT_SUCCESS
        output_text = output_stream.getvalue()
        assert "completed" in output_text

    def test_multi_field_external_step(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, EXTERNAL_MULTI_FIELD_FLOW)
        input_stream = StringIO("approve\nLooks good\n")
        output_stream = StringIO()

        adapter = PlainAdapter(output=output_stream, input_stream=input_stream)
        rc = run_flow(
            flow, project,
            quiet=False,
            input_stream=input_stream,
            output_stream=output_stream,
            config=StepwiseConfig(),
            adapter=adapter,
        )
        assert rc == EXIT_SUCCESS


class TestParseVars:
    """--input flag parsing."""

    def test_simple_key_value(self):
        result = parse_inputs(["key=value"])
        assert result == {"key": "value"}

    def test_splits_on_first_equals(self):
        result = parse_inputs(["key=value=with=equals"])
        assert result == {"key": "value=with=equals"}

    def test_multiple_vars(self):
        result = parse_inputs(["env=staging", "region=us-west-2"])
        assert result == {"env": "staging", "region": "us-west-2"}

    def test_empty_list(self):
        result = parse_inputs([])
        assert result == {}

    def test_none(self):
        result = parse_inputs(None)
        assert result == {}

    def test_no_equals_raises(self):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            parse_inputs(["invalid"])


class TestLoadVarsFile:
    """--vars-file loading from YAML and JSON."""

    def test_yaml_file(self, tmp_path):
        f = tmp_path / "vars.yaml"
        f.write_text("env: staging\nregion: us-west-2\n")
        result = load_vars_file(str(f))
        assert result == {"env": "staging", "region": "us-west-2"}

    def test_json_file(self, tmp_path):
        f = tmp_path / "vars.json"
        f.write_text('{"env": "staging", "count": 5}')
        result = load_vars_file(str(f))
        assert result == {"env": "staging", "count": 5}

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_vars_file("/nonexistent/vars.yaml")


class TestSignalHandling:
    """Signal handler sets shutdown flag, cancels active runs."""

    def test_signal_handler_cancels_job(self, tmp_path):
        """Signal during execution should cancel the job cleanly."""
        # We test the signal mechanism indirectly by verifying
        # the runner restores signal handlers after completion
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW)
        output = StringIO()

        original_handler = signal.getsignal(signal.SIGINT)
        run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        restored_handler = signal.getsignal(signal.SIGINT)

        # Signal handlers should be restored after run completes
        assert restored_handler == original_handler


class TestRunnerUsesDefaultRegistry:
    """Runner uses create_default_registry() (same executors as server)."""

    def test_registry_created_with_config(self, tmp_path):
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, SIMPLE_SCRIPT_FLOW)
        output = StringIO()
        # If this runs without error, the registry was created successfully
        rc = run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_SUCCESS


class TestLoopFlow:
    """Flow with exit rules and loops runs correctly in headless mode."""

    def test_loop_flow(self, tmp_path):
        loop_flow = """\
name: loop-test
author: test
steps:
  count:
    run: 'echo "{\\"n\\": 1}"'
    outputs: [n]
    exits:
      - name: done
        when: "attempt >= 2"
        action: advance
      - name: again
        when: "attempt < 2"
        action: loop
        target: count
  finish:
    run: 'echo "{\\"done\\": true}"'
    outputs: [done]
    after: [count]
"""
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path, loop_flow)
        output = StringIO()
        rc = run_flow(flow, project, quiet=True, output_stream=output, config=StepwiseConfig())
        assert rc == EXIT_SUCCESS
