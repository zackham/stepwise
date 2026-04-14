"""Tests for finalize_surviving on ScriptExecutor and LLMExecutor.

Reattach codepath: when the server restarts mid-run, the engine calls
`finalize_surviving(executor_state)` on the inner executor for each RUNNING
step. Previously only the agent executor implemented this — script and llm
steps raised TypeError, breaking recovery for any flow that mixed executors.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.executors import LLMExecutor, ScriptExecutor
from tests.mock_llm_client import MockLLMClient


# ── ScriptExecutor.finalize_surviving ────────────────────────────────


def _write_files(tmpdir: Path, stdout: str, stderr: str = "", exitcode: int | None = 0):
    """Create the on-disk files that ScriptExecutor.start() would have written."""
    stdout_path = tmpdir / "step-1.stdout"
    stderr_path = tmpdir / "step-1.stderr"
    exitcode_path = tmpdir / "step-1.exitcode"
    stdout_path.write_text(stdout)
    stderr_path.write_text(stderr)
    if exitcode is not None:
        exitcode_path.write_text(str(exitcode))
    return stdout_path, stderr_path, exitcode_path


class TestScriptExecutorFinalizeSurviving:
    def test_alive_pid_waits_then_captures(self):
        """Alive PID → finalize polls until process dies, then captures output."""
        tmpdir = Path(tempfile.mkdtemp())
        stdout_path, stderr_path, _ = _write_files(
            tmpdir, stdout='{"result": "ok", "value": 42}', exitcode=0,
        )
        executor = ScriptExecutor(command="ignored")

        # Simulate liveness: alive for 3 calls, then dead.
        liveness = iter([True, True, True, False])
        call_count = {"sleeps": 0, "alive_checks": 0}

        def fake_alive(pid):
            call_count["alive_checks"] += 1
            return next(liveness)

        def fake_sleep(_seconds):
            call_count["sleeps"] += 1

        with patch("stepwise.process_lifecycle._is_pid_alive", side_effect=fake_alive), \
             patch("stepwise.executors.time.sleep", side_effect=fake_sleep):
            result = executor.finalize_surviving({
                "pid": 12345,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })

        # Polled while alive, slept between checks
        assert call_count["alive_checks"] == 4
        assert call_count["sleeps"] == 3

        assert result.type == "data"
        assert result.envelope.artifact == {"result": "ok", "value": 42}
        assert result.envelope.executor_meta.get("reattached") is True
        assert result.envelope.executor_meta.get("return_code") == 0
        assert "failed" not in (result.executor_state or {})

    def test_dead_pid_with_exit_code_zero_succeeds(self):
        """Dead PID + exit code 0 + JSON stdout → success result."""
        tmpdir = Path(tempfile.mkdtemp())
        stdout_path, stderr_path, _ = _write_files(
            tmpdir, stdout='{"data": "captured"}', exitcode=0,
        )
        executor = ScriptExecutor(command="ignored")

        with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False):
            result = executor.finalize_surviving({
                "pid": 12345,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })

        assert result.type == "data"
        assert result.envelope.artifact == {"data": "captured"}
        assert (result.executor_state or {}).get("failed") is not True

    def test_dead_pid_with_nonzero_exit_code_fails(self):
        """Dead PID + non-zero exit code → failed run with error."""
        tmpdir = Path(tempfile.mkdtemp())
        stdout_path, stderr_path, _ = _write_files(
            tmpdir, stdout="partial", stderr="boom", exitcode=2,
        )
        executor = ScriptExecutor(command="ignored")

        with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False):
            result = executor.finalize_surviving({
                "pid": 12345,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })

        assert result.type == "data"
        assert result.executor_state["failed"] is True
        assert "boom" in result.executor_state["error"]
        assert result.envelope.executor_meta["return_code"] == 2

    def test_dead_pid_no_exit_code_fails_with_clear_error(self):
        """Dead PID + no exitcode file → marked failed (cannot confirm success)."""
        tmpdir = Path(tempfile.mkdtemp())
        stdout_path, stderr_path, _ = _write_files(
            tmpdir, stdout="some partial output", exitcode=None,
        )
        executor = ScriptExecutor(command="ignored")

        with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False):
            result = executor.finalize_surviving({
                "pid": 12345,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            })

        assert result.type == "data"
        assert result.executor_state["failed"] is True
        assert "exit code not captured" in result.executor_state["error"].lower()
        # Partial output preserved for debugging
        assert result.envelope.artifact["stdout"] == "some partial output"

    def test_missing_paths_in_executor_state_fails(self):
        """No stdout_path/stderr_path → clean failure, no crash."""
        executor = ScriptExecutor(command="ignored")
        result = executor.finalize_surviving({"pid": 12345})

        assert result.type == "data"
        assert result.executor_state["failed"] is True
        assert "missing" in result.executor_state["error"].lower()


# ── LLMExecutor.finalize_surviving ───────────────────────────────────


class TestLLMExecutorFinalizeSurviving:
    def test_marks_run_failed_with_recoverable_error(self):
        """LLM API calls cannot reattach → finalize cleanly fails the run."""
        client = MockLLMClient(content='{"out": "ok"}')
        executor = LLMExecutor(
            client=client,
            model="test/model",
            prompt="Do the thing",
        )
        executor._output_fields = ["out"]

        result = executor.finalize_surviving({})

        assert result.type == "data"
        assert result.envelope.executor_meta["failed"] is True
        assert "interrupted" in result.envelope.executor_meta["error"].lower()
        assert result.envelope.executor_meta["model"] == "test/model"
        assert result.executor_state["failed"] is True
        assert result.executor_state["error_category"] == "infra_failure"
        # The LLM client must NOT have been called — finalize doesn't re-issue
        # the API request.
        assert len(client.calls) == 0

    def test_handles_none_executor_state(self):
        """finalize_surviving with empty state should still produce a clean failure."""
        client = MockLLMClient(content='{"out": "ok"}')
        executor = LLMExecutor(
            client=client,
            model="test/model",
            prompt="Do the thing",
        )

        result = executor.finalize_surviving(None)  # type: ignore[arg-type]

        assert result.type == "data"
        assert result.executor_state["failed"] is True
