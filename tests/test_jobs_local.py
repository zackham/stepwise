"""Tests for `stepwise jobs` and `stepwise status` local (no-server) mode.

N35: When no server is running, both commands fall back to querying
     `.stepwise/stepwise.db` directly.  The same output format is used
     as in the server-backed path.

Coverage:
  - Table format always uses local DB (no server attempted)
  - JSON format tries server first, falls back silently when unreachable
  - Stale server.pid (process dead) is cleaned up and falls back to local
  - Active server.pid pointing at an unreachable port falls back silently
  - Output is structurally identical to server-backed JSON format
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path

import pytest

from stepwise.cli import EXIT_JOB_FAILED, EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.config import StepwiseConfig
from stepwise.project import init_project
from stepwise.runner import run_flow
from stepwise.store import SQLiteStore


# ── Helpers ─────────────────────────────────────────────────────────────


SIMPLE_FLOW = """\
name: simple
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


def _setup_project_with_jobs(tmp_path: Path, n_jobs: int = 2):
    """Create a project and run n_jobs flows to populate the DB."""
    project = init_project(tmp_path)
    flow_file = tmp_path / "test.flow.yaml"
    flow_file.write_text(SIMPLE_FLOW)
    for _ in range(n_jobs):
        run_flow(flow_file, project, quiet=True, output_stream=StringIO(), config=StepwiseConfig())
    return project


def _get_first_job_id(project) -> str:
    store = SQLiteStore(str(project.db_path))
    try:
        jobs = store.all_jobs()
        assert jobs, "No jobs found in DB — setup failed"
        return jobs[0].id
    finally:
        store.close()


def _write_stale_pid(project, port: int = 19999) -> None:
    """Write a server.pid whose PID does not exist (stale/dead process)."""
    pid_file = project.dot_dir / "server.pid"
    # Use a PID that is almost certainly not running: max pid value
    pid_file.write_text(json.dumps({
        "pid": 9_999_999,
        "port": port,
        "url": f"http://localhost:{port}",
    }))


def _write_live_pid_unreachable_port(project, port: int = 19998) -> None:
    """Write a server.pid with the current process PID (alive) but on an
    unreachable port — simulates a server that is registered but not listening."""
    pid_file = project.dot_dir / "server.pid"
    pid_file.write_text(json.dumps({
        "pid": os.getpid(),
        "port": port,
        "url": f"http://localhost:{port}",
    }))


# ── stepwise jobs — table format ─────────────────────────────────────────


class TestJobsTableNoServer:
    """Table-format jobs command never contacts the server."""

    def test_jobs_no_server_shows_table(self, tmp_path, capsys, monkeypatch):
        """jobs (table) works with no server running at all."""
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs"])

        assert rc == EXIT_SUCCESS

    def test_jobs_no_server_has_expected_columns(self, tmp_path, capsys, monkeypatch):
        """Table output includes standard columns."""
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs"])

        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "ID" in combined
        assert "STATUS" in combined
        assert "completed" in combined

    def test_jobs_stale_pidfile_still_reads_local(self, tmp_path, capsys, monkeypatch):
        """Stale server.pid (dead PID) is cleaned up; jobs still reads local DB."""
        project = _setup_project_with_jobs(tmp_path, 1)
        _write_stale_pid(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs"])

        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "completed" in combined

    def test_jobs_empty_no_server(self, tmp_path, capsys, monkeypatch):
        """jobs with no jobs in DB and no server shows 'No jobs found'."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs"])

        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "No jobs found" in combined


# ── stepwise jobs — JSON format fallback ─────────────────────────────────


class TestJobsJsonLocalFallback:
    """JSON-mode jobs falls back to local DB when server is unreachable."""

    def test_jobs_json_no_server(self, tmp_path, capsys, monkeypatch):
        """`jobs --output json` works with no server.pid at all."""
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_jobs_json_has_expected_fields(self, tmp_path, capsys, monkeypatch):
        """Local JSON output includes id, status, objective, steps fields."""
        _setup_project_with_jobs(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        entry = data[0]
        assert "id" in entry
        assert "status" in entry
        assert entry["status"] == "completed"
        assert "objective" in entry
        assert "steps" in entry

    def test_jobs_json_stale_pidfile_fallback(self, tmp_path, capsys, monkeypatch):
        """Stale server.pid → falls back silently to local DB in JSON mode."""
        project = _setup_project_with_jobs(tmp_path, 2)
        _write_stale_pid(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_jobs_json_unreachable_server_fallback(self, tmp_path, capsys, monkeypatch):
        """Live PID but unreachable port → fallback warning + local DB result."""
        project = _setup_project_with_jobs(tmp_path, 1)
        _write_live_pid_unreachable_port(project, port=19998)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_jobs_json_status_filter_local(self, tmp_path, capsys, monkeypatch):
        """--status filter works in local fallback JSON mode."""
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json", "--status", "completed"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert all(j["status"] == "completed" for j in data)

    def test_jobs_json_running_filter_empty_local(self, tmp_path, capsys, monkeypatch):
        """--status running returns empty list when all jobs are completed."""
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json", "--status", "running"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data == []

    def test_jobs_json_limit_local(self, tmp_path, capsys, monkeypatch):
        """--limit works in local fallback JSON mode."""
        _setup_project_with_jobs(tmp_path, 5)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json", "--limit", "2"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 2


# ── stepwise status — table format ──────────────────────────────────────


class TestStatusTableNoServer:
    """Table-format status command never contacts the server."""

    def test_status_no_server(self, tmp_path, capsys, monkeypatch):
        """`status <job-id>` works with no server running."""
        project = _setup_project_with_jobs(tmp_path, 1)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id])

        assert rc == EXIT_SUCCESS

    def test_status_shows_job_id_and_steps(self, tmp_path, capsys, monkeypatch):
        """Status table shows job ID, step names, and completion state."""
        project = _setup_project_with_jobs(tmp_path, 1)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id])

        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert job_id in combined
        assert "completed" in combined
        assert "hello" in combined  # step name from SIMPLE_FLOW

    def test_status_stale_pidfile_still_reads_local(self, tmp_path, capsys, monkeypatch):
        """Stale server.pid does not prevent local status lookup."""
        project = _setup_project_with_jobs(tmp_path, 1)
        _write_stale_pid(project)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id])

        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert job_id in combined

    def test_status_nonexistent_job_no_server(self, tmp_path, capsys, monkeypatch):
        """status with unknown job-id returns error even without server."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", "job-doesnotexist"])

        assert rc == EXIT_JOB_FAILED
        err = capsys.readouterr().err
        assert "not found" in err.lower()


# ── stepwise status — JSON format fallback ───────────────────────────────


class TestStatusJsonLocalFallback:
    """JSON-mode status falls back to local DB when server is unreachable."""

    def test_status_json_no_server(self, tmp_path, capsys, monkeypatch):
        """`status <job-id> --output json` works with no server."""
        project = _setup_project_with_jobs(tmp_path, 1)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id, "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["job_id"] == job_id
        assert "steps" in data

    def test_status_json_stale_pidfile_fallback(self, tmp_path, capsys, monkeypatch):
        """Stale server.pid → fallback to local DB in JSON mode."""
        project = _setup_project_with_jobs(tmp_path, 1)
        _write_stale_pid(project)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id, "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["job_id"] == job_id
        assert "steps" in data

    def test_status_json_unreachable_server_fallback(self, tmp_path, capsys, monkeypatch):
        """Live PID but unreachable port → fallback to local DB in JSON mode."""
        project = _setup_project_with_jobs(tmp_path, 1)
        _write_live_pid_unreachable_port(project, port=19997)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id, "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["job_id"] == job_id

    def test_status_json_nonexistent_job_no_server(self, tmp_path, capsys, monkeypatch):
        """status JSON with unknown job-id returns error even without server."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", "job-doesnotexist", "--output", "json"])

        assert rc == EXIT_JOB_FAILED

    def test_status_json_contains_step_statuses(self, tmp_path, capsys, monkeypatch):
        """Local JSON status response contains per-step status info."""
        project = _setup_project_with_jobs(tmp_path, 1)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id, "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        # steps should contain our 'hello' step
        assert any(
            "hello" in str(step)
            for step in (data.get("steps") or [])
        )


# ── Output format consistency ────────────────────────────────────────────


class TestLocalOutputFormatConsistency:
    """Local-mode output uses the same schema as the server-backed format."""

    def test_jobs_json_schema_matches_server_format(self, tmp_path, capsys, monkeypatch):
        """Local jobs JSON uses the same fields as the server-backed _job_summary."""
        _setup_project_with_jobs(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(["jobs", "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        # Fields from _job_summary()
        assert set(data[0].keys()) >= {"id", "name", "status", "objective", "steps", "created_at"}

    def test_status_json_schema_matches_server_format(self, tmp_path, capsys, monkeypatch):
        """Local status JSON has job_id and steps fields (server-compatible)."""
        project = _setup_project_with_jobs(tmp_path, 1)
        job_id = _get_first_job_id(project)
        monkeypatch.chdir(tmp_path)

        rc = main(["status", job_id, "--output", "json"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "job_id" in data
        assert "steps" in data
        assert data["job_id"] == job_id
