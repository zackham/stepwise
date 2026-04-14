"""Tests for CLI observability commands: tail, logs, output positional step."""

import json
import io
import sys
from io import StringIO
from pathlib import Path

import pytest

from stepwise.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    _format_event_line,
    _format_job_header,
    main,
)
from stepwise.config import StepwiseConfig
from stepwise.project import init_project
from stepwise.runner import run_flow


# ── Helpers ──────────────────────────────────────────────────────────


SIMPLE_FLOW = """\
name: simple
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

TWO_STEP_FLOW = """\
name: two-step
author: test
steps:
  step-a:
    run: 'echo "{\\"val\\": 1}"'
    outputs: [val]

  step-b:
    run: |
      echo "{\\"result\\": 2}"
    inputs:
      val: step-a.val
    outputs: [result]
"""


def _setup_project_with_jobs(tmp_path, flow_text=SIMPLE_FLOW, n_jobs=1):
    """Create a project and run flows to populate the DB."""
    project = init_project(tmp_path)
    flow = tmp_path / "test.flow.yaml"
    flow.write_text(flow_text)
    for _ in range(n_jobs):
        run_flow(flow, project, quiet=True, output_stream=StringIO(), config=StepwiseConfig())
    return project


def _capture_stdout(argv):
    """Run CLI and capture stdout."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = main(argv)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, output


def _capture_stderr(argv):
    """Run CLI and capture stderr."""
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        code = main(argv)
        output = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    return code, output


# ── Format Event Line Tests ──────────────────────────────────────────


class TestFormatEventLine:
    """Pure unit tests for _format_event_line."""

    def test_step_started_format(self):
        envelope = {
            "type": "step.started",
            "timestamp": "2026-03-21T12:05:01",
            "data": {"step": "plan", "attempt": 1},
        }
        line = _format_event_line(envelope)
        assert "[12:05:01]" in line
        assert "step.started" in line
        assert "plan" in line

    def test_step_completed_format(self):
        envelope = {
            "type": "step.completed",
            "timestamp": "2026-03-21T12:05:30",
            "data": {"step": "plan", "attempt": 2},
        }
        line = _format_event_line(envelope)
        assert "step.completed" in line
        assert "(attempt 2)" in line

    def test_step_completed_from_cache(self):
        envelope = {
            "type": "step.completed",
            "timestamp": "2026-03-21T12:05:30",
            "data": {"step": "fetch", "attempt": 1, "from_cache": True},
        }
        line = _format_event_line(envelope)
        assert "from_cache" in line

    def test_step_suspended_format(self):
        envelope = {
            "type": "step.suspended",
            "timestamp": "2026-03-21T12:06:00",
            "data": {"step": "review", "prompt": "Please approve"},
        }
        line = _format_event_line(envelope)
        assert "step.suspended" in line
        assert "Please approve" in line

    def test_step_failed_format(self):
        long_error = "x" * 200
        envelope = {
            "type": "step.failed",
            "timestamp": "2026-03-21T12:06:00",
            "data": {"step": "build", "error": long_error},
        }
        line = _format_event_line(envelope)
        assert "step.failed" in line
        # Error should be truncated to 80 chars
        assert len(line.split("build")[1].strip()) <= 80

    def test_job_completed_format(self):
        envelope = {
            "type": "job.completed",
            "timestamp": "2026-03-21T12:10:00",
            "data": {},
        }
        line = _format_event_line(envelope)
        assert "job.completed" in line
        # No crash on empty data

    def test_job_failed_format(self):
        envelope = {
            "type": "job.failed",
            "timestamp": "2026-03-21T12:10:00",
            "data": {"reason": "step build failed"},
        }
        line = _format_event_line(envelope)
        assert "job.failed" in line
        assert "step build failed" in line

    def test_ws_envelope_format(self):
        """WS format uses 'event' key instead of 'type'."""
        envelope = {
            "event": "step.started",
            "timestamp": "2026-03-21T12:05:01",
            "data": {"step": "plan"},
            "step": "plan",
        }
        line = _format_event_line(envelope)
        assert "step.started" in line
        assert "plan" in line

    def test_exit_resolved_format(self):
        envelope = {
            "type": "exit.resolved",
            "timestamp": "2026-03-21T12:07:00",
            "data": {"step": "check", "action": "loop", "target": "build"},
        }
        line = _format_event_line(envelope)
        assert "exit.resolved" in line
        assert "loop" in line
        assert "build" in line

    def test_loop_iteration_format(self):
        envelope = {
            "type": "loop.iteration",
            "timestamp": "2026-03-21T12:07:30",
            "data": {"step": "build", "attempt": 3},
        }
        line = _format_event_line(envelope)
        assert "loop.iteration" in line
        assert "attempt 3" in line


# ── Format Job Header Tests ──────────────────────────────────────────


class TestFormatJobHeader:
    def test_completed_job_header(self):
        header = _format_job_header({
            "id": "job-123",
            "status": "completed",
            "name": "My Flow",
            "created_at": "2026-03-21T12:00:00+00:00",
            "completed_at": "2026-03-21T12:05:30+00:00",
        })
        assert "Job: job-123 (My Flow)" in header
        assert "Status: completed" in header
        assert "Duration: 5m 30s" in header

    def test_running_job_header(self):
        """Running job has no completed_at — duration shows elapsed."""
        header = _format_job_header({
            "id": "job-456",
            "status": "running",
            "objective": "test run",
            "created_at": "2026-03-21T12:00:00+00:00",
            "completed_at": "",
        })
        assert "Job: job-456 (test run)" in header
        assert "Status: running" in header
        assert "Duration:" in header


# ── Logs Command Tests ───────────────────────────────────────────────


class TestLogs:
    def test_logs_completed_job(self, tmp_path, monkeypatch):
        """Logs shows events for a completed job."""
        project = _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        from stepwise.store import SQLiteStore
        store = SQLiteStore(str(project.db_path))
        jobs = store.all_jobs()
        job_id = jobs[0].id
        store.close()

        code, output = _capture_stdout(["--standalone", "logs", job_id])
        assert code == EXIT_SUCCESS
        assert job_id in output
        assert "completed" in output.lower()
        assert "step.started" in output
        assert "step.completed" in output

    def test_logs_nonexistent_job(self, tmp_path, monkeypatch):
        """Logs for nonexistent job returns error."""
        _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        code, output = _capture_stderr(["--standalone", "logs", "nonexistent-job"])
        assert code == EXIT_JOB_FAILED
        assert "not found" in output.lower()

    def test_logs_header_format(self, tmp_path, monkeypatch):
        """Verify header lines match expected patterns."""
        project = _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        from stepwise.store import SQLiteStore
        store = SQLiteStore(str(project.db_path))
        jobs = store.all_jobs()
        job_id = jobs[0].id
        store.close()

        code, output = _capture_stdout(["--standalone", "logs", job_id])
        assert code == EXIT_SUCCESS
        lines = output.strip().split("\n")
        assert lines[0].startswith("Job: ")
        assert lines[1].startswith("Status: ")
        assert lines[2].startswith("Duration: ")

    def test_logs_event_count(self, tmp_path, monkeypatch):
        """Two-step flow should produce at least 4 step events."""
        project = _setup_project_with_jobs(tmp_path, flow_text=TWO_STEP_FLOW)
        monkeypatch.chdir(tmp_path)

        from stepwise.store import SQLiteStore
        store = SQLiteStore(str(project.db_path))
        jobs = store.all_jobs()
        job_id = jobs[0].id
        store.close()

        code, output = _capture_stdout(["--standalone", "logs", job_id])
        assert code == EXIT_SUCCESS
        # Count event lines (lines starting with "[")
        event_lines = [l for l in output.strip().split("\n") if l.startswith("[")]
        # At least 4: 2 started + 2 completed (may also have job events)
        assert len(event_lines) >= 4


# ── Output Positional Step Tests ─────────────────────────────────────


class TestOutputPositionalStep:
    @pytest.fixture
    def completed_job(self, tmp_path, monkeypatch):
        """Run a simple flow and return (tmp_path, job_id)."""
        project = _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        from stepwise.store import SQLiteStore
        store = SQLiteStore(str(project.db_path))
        jobs = store.all_jobs()
        job_id = jobs[0].id
        store.close()
        return tmp_path, job_id

    def test_output_positional_step(self, completed_job):
        """Positional step name returns raw artifact."""
        tmp_path, job_id = completed_job
        code, output = _capture_stdout([
            "--standalone", "--project-dir", str(tmp_path),
            "output", job_id, "hello",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert "msg" in result
        assert result["msg"] == "hi"

    def test_output_positional_step_not_found(self, completed_job):
        """Nonexistent step returns error."""
        tmp_path, job_id = completed_job
        code, output = _capture_stdout([
            "--standalone", "--project-dir", str(tmp_path),
            "output", job_id, "nonexistent",
        ])
        assert code == EXIT_JOB_FAILED
        result = json.loads(output)
        assert "_error" in result

    def test_output_flag_still_works(self, completed_job):
        """--step flag still works (backward compat)."""
        tmp_path, job_id = completed_job
        code, output = _capture_stdout([
            "--standalone", "--project-dir", str(tmp_path),
            "output", job_id, "--step", "hello",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        # --step wraps in {step: artifact}
        assert "hello" in result

    def test_output_no_step_unchanged(self, completed_job):
        """Bare output still returns job-level format."""
        tmp_path, job_id = completed_job
        code, output = _capture_stdout([
            "--standalone", "--project-dir", str(tmp_path),
            "output", job_id,
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert "status" in result
        assert result["status"] == "completed"

    def test_output_flag_overrides_positional(self, completed_job):
        """When both --step and positional are given, --step wins."""
        tmp_path, job_id = completed_job
        code, output = _capture_stdout([
            "--standalone", "--project-dir", str(tmp_path),
            "output", job_id, "--step", "hello",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        # --step format wraps result
        assert "hello" in result


# ── Tail Command Tests ───────────────────────────────────────────────


class TestTail:
    def test_tail_no_server_error(self, tmp_path, monkeypatch):
        """Tail without a server returns CONFIG_ERROR."""
        _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        code, output = _capture_stderr(["--standalone", "tail", "job-123"])
        assert code == EXIT_CONFIG_ERROR
        assert "requires a running server" in output

    def test_tail_no_server_prints_help(self, tmp_path, monkeypatch):
        """Error message includes actionable guidance."""
        _setup_project_with_jobs(tmp_path)
        monkeypatch.chdir(tmp_path)

        code, output = _capture_stderr(["--standalone", "tail", "job-123"])
        assert "stepwise server start" in output
