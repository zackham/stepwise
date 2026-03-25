"""Tests for agent output bridge: auto-promote, env vars, prompt instructions, error messages."""

import json
import tempfile
from pathlib import Path

import pytest

from stepwise.agent import AgentExecutor, MockAgentBackend, _build_agent_env
from stepwise.executors import ExecutionContext
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)

from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ────────────────────────────────────────────────────────────


def _make_backend():
    backend = MockAgentBackend()
    backend.set_auto_complete()
    return backend


def _make_executor(backend=None, output_mode="effect", output_path=None,
                   user_set_output_mode=False, **extra_config):
    if backend is None:
        backend = _make_backend()
    return AgentExecutor(
        backend=backend,
        prompt="test prompt",
        output_mode=output_mode,
        output_path=output_path,
        _user_set_output_mode=user_set_output_mode,
        **extra_config,
    )


def _make_context(step_name="test-step", attempt=1, workspace=None):
    return ExecutionContext(
        job_id="job-test-123",
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or "/tmp/test-workspace",
        idempotency=f"{step_name}-{attempt}",
    )


# ── Step 1a: Auto-promotion logic ─────────────────────────────────────


class TestAutoPromote:
    def test_auto_promote_when_outputs_declared(self):
        ex = _make_executor(output_fields=["result"])
        assert ex.output_mode == "file"
        assert ex._auto_promoted is True

    def test_no_promote_when_explicit_effect(self):
        ex = _make_executor(output_fields=["result"], user_set_output_mode=True)
        assert ex.output_mode == "effect"
        assert ex._auto_promoted is False

    def test_no_promote_when_no_outputs(self):
        ex = _make_executor()
        assert ex.output_mode == "effect"
        assert ex._auto_promoted is False

    def test_no_promote_when_stream_result(self):
        ex = _make_executor(output_mode="stream_result", output_fields=["result"])
        assert ex.output_mode == "stream_result"
        assert ex._auto_promoted is False

    def test_explicit_file_not_flagged_auto(self):
        ex = _make_executor(output_mode="file", output_fields=["result"],
                            user_set_output_mode=True)
        assert ex.output_mode == "file"
        assert ex._auto_promoted is False


# ── Step 4a: Error message tests ──────────────────────────────────────


class TestErrorMessages:
    def test_missing_output_file_error_includes_path_and_fields(self):
        from stepwise.agent import AgentStatus
        ex = _make_executor(output_fields=["result", "score"])
        # Auto-promoted to file mode
        assert ex.output_mode == "file"

        workspace = tempfile.mkdtemp()
        state = {
            "working_dir": workspace,
            "output_file": "test-step-output.json",
            "output_path": f"{workspace}/fake.jsonl",
        }
        agent_status = AgentStatus(state="completed", exit_code=0)

        envelope = ex._extract_output(state, "file", agent_status)
        assert envelope.artifact["output_file_missing"] is True
        assert "_error" in envelope.artifact
        assert "test-step-output.json" in envelope.artifact["_error"]
        assert "result" in envelope.artifact["_error"]
        assert "score" in envelope.artifact["_error"]
        assert "FileNotFoundError" in envelope.artifact["_error"]

    def test_malformed_json_error_includes_context(self):
        from stepwise.agent import AgentStatus
        ex = _make_executor(output_fields=["result"])

        workspace = tempfile.mkdtemp()
        output_file = Path(workspace) / "test-step-output.json"
        output_file.write_text("not valid json {{{")

        state = {
            "working_dir": workspace,
            "output_file": "test-step-output.json",
            "output_path": f"{workspace}/fake.jsonl",
        }
        agent_status = AgentStatus(state="completed", exit_code=0)

        envelope = ex._extract_output(state, "file", agent_status)
        assert envelope.artifact["output_file_missing"] is True
        assert "_error" in envelope.artifact
        assert "JSONDecodeError" in envelope.artifact["_error"]


# ── Step 2a: Env var injection tests ──────────────────────────────────


class TestEnvVarInjection:
    def test_env_has_stepwise_output_file_when_outputs_declared(self):
        workspace = tempfile.mkdtemp()
        ctx = _make_context(step_name="analyze", workspace=workspace)
        env = _build_agent_env(
            config={"output_fields": ["summary"]},
            context=ctx,
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "STEPWISE_OUTPUT_FILE" in env
        assert env["STEPWISE_OUTPUT_FILE"].endswith("analyze-output.json")
        assert Path(env["STEPWISE_OUTPUT_FILE"]).is_absolute()

    def test_env_no_output_file_when_no_outputs(self):
        workspace = tempfile.mkdtemp()
        ctx = _make_context(workspace=workspace)
        env = _build_agent_env(
            config={},
            context=ctx,
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "STEPWISE_OUTPUT_FILE" not in env

    def test_env_has_step_io_and_attempt(self):
        workspace = tempfile.mkdtemp()
        step_io = Path(workspace) / ".stepwise" / "step-io"
        ctx = _make_context(step_name="my-step", attempt=3, workspace=workspace)
        env = _build_agent_env(
            config={},
            context=ctx,
            step_io=step_io,
            working_dir=workspace,
        )
        assert env["STEPWISE_STEP_NAME"] == "my-step"
        assert env["STEPWISE_ATTEMPT"] == "3"
        assert env["STEPWISE_STEP_IO"] == str(step_io)

    def test_env_output_file_uses_custom_output_path(self):
        workspace = tempfile.mkdtemp()
        ctx = _make_context(workspace=workspace)
        env = _build_agent_env(
            config={"output_fields": ["x"], "output_path": "custom.json"},
            context=ctx,
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert env["STEPWISE_OUTPUT_FILE"].endswith("custom.json")


# ── Step 3a: Prompt instruction tests ─────────────────────────────────


class TestPromptInstructions:
    def test_prompt_includes_output_instructions(self):
        ex = _make_executor(output_fields=["summary", "score"])
        ctx = _make_context(step_name="analyze")
        prompt = ex._render_prompt({}, ctx)
        assert "<stepwise-output>" in prompt
        assert "</stepwise-output>" in prompt
        assert '"summary"' in prompt
        assert '"score"' in prompt
        assert "STEPWISE_OUTPUT_FILE" in prompt
        assert "analyze-output.json" in prompt
        # JSON example block
        assert '"summary": "<summary value>"' in prompt

    def test_prompt_no_instructions_for_effect_mode(self):
        ex = _make_executor(output_fields=["x"], user_set_output_mode=True)
        # Stays effect because user explicitly set it
        assert ex.output_mode == "effect"
        ctx = _make_context()
        prompt = ex._render_prompt({}, ctx)
        assert "<stepwise-output>" not in prompt

    def test_prompt_no_instructions_without_outputs(self):
        ex = _make_executor()
        ctx = _make_context()
        prompt = ex._render_prompt({}, ctx)
        assert "<stepwise-output>" not in prompt

    def test_prompt_uses_custom_output_path(self):
        ex = _make_executor(output_fields=["x"], output_path="results.json")
        ctx = _make_context(step_name="analyze")
        prompt = ex._render_prompt({}, ctx)
        assert "results.json" in prompt
        assert "analyze-output.json" not in prompt

    def test_prompt_output_instructions_after_emit_flow(self):
        ex = _make_executor(output_fields=["result"], emit_flow=True)
        ctx = _make_context()
        prompt = ex._render_prompt({}, ctx)
        # Both emit_flow and output instructions present
        assert "emit.flow.yaml" in prompt or "Flow Emission" in prompt
        assert "<stepwise-output>" in prompt
        # Output instructions come after emit_flow instructions
        emit_pos = prompt.find("Flow Emission") if "Flow Emission" in prompt else prompt.find("emit.flow.yaml")
        output_pos = prompt.find("<stepwise-output>")
        assert output_pos > emit_pos
