"""Tests for agent output bridge: auto-promote, env vars, prompt instructions, error messages."""

import json
import tempfile
from pathlib import Path

import pytest

from stepwise.agent import AgentExecutor, MockAgentBackend
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
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or "/tmp/test-workspace",
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
