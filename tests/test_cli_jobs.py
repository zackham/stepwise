"""Tests for CLI jobs, status, and cancel commands."""

import json
import pytest
from io import StringIO
from pathlib import Path

from stepwise.cli import EXIT_JOB_FAILED, EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.config import StepwiseConfig
from stepwise.engine import Engine
from stepwise.models import JobStatus, WorkflowDefinition, StepDefinition, ExecutorRef
from stepwise.project import DOT_DIR_NAME, init_project
from stepwise.runner import run_flow
from stepwise.store import SQLiteStore


SIMPLE_FLOW = """\
name: simple
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


def _setup_project_with_jobs(tmp_path, n_jobs=3):
    """Create a project and run some flows to populate the DB."""
    project = init_project(tmp_path)
    flow = tmp_path / "test.flow.yaml"
    flow.write_text(SIMPLE_FLOW)
    for _ in range(n_jobs):
        run_flow(flow, project, quiet=True, output_stream=StringIO(), config=StepwiseConfig())
    return project


class TestJobs:
    """jobs command lists jobs from project db."""

    def test_jobs_table_format(self, tmp_path, capsys, monkeypatch):
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)
        rc = main(["jobs"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "ID" in combined
        assert "STATUS" in combined
        assert "completed" in combined

    def test_jobs_json_output(self, tmp_path, capsys, monkeypatch):
        _setup_project_with_jobs(tmp_path, 2)
        monkeypatch.chdir(tmp_path)
        rc = main(["jobs", "--output", "json"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert "id" in data[0]
        assert "status" in data[0]

    def test_jobs_limit(self, tmp_path, capsys, monkeypatch):
        _setup_project_with_jobs(tmp_path, 5)
        monkeypatch.chdir(tmp_path)
        rc = main(["jobs", "--limit", "2"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Count job table rows (lines matching "job-" prefix, not step status lines)
        job_lines = [l for l in combined.split("\n") if "job-" in l and "completed" in l]
        assert len(job_lines) == 2

    def test_jobs_status_filter(self, tmp_path, capsys, monkeypatch):
        _setup_project_with_jobs(tmp_path, 3)
        monkeypatch.chdir(tmp_path)
        # All should be completed, filter for "running" should find none
        rc = main(["jobs", "--status", "running"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "No jobs found" in combined

    def test_jobs_empty_db(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["jobs"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "No jobs found" in combined

    def test_jobs_invalid_status(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["jobs", "--status", "bogus"])
        assert rc == EXIT_USAGE_ERROR


class TestStatus:
    """status <job-id> shows step-by-step detail."""

    def test_status_table(self, tmp_path, capsys, monkeypatch):
        project = _setup_project_with_jobs(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        # Get the job ID from the DB
        store = SQLiteStore(str(project.db_path))
        jobs = [store.load_job(r["id"]) for r in store._conn.execute("SELECT id FROM jobs").fetchall()]
        store.close()
        job_id = jobs[0].id

        rc = main(["status", job_id])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert job_id in combined
        assert "completed" in combined
        assert "hello" in combined

    def test_status_json(self, tmp_path, capsys, monkeypatch):
        project = _setup_project_with_jobs(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        store = SQLiteStore(str(project.db_path))
        jobs = [store.load_job(r["id"]) for r in store._conn.execute("SELECT id FROM jobs").fetchall()]
        store.close()
        job_id = jobs[0].id

        rc = main(["status", job_id, "--output", "json"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["job_id"] == job_id
        assert "steps" in data

    def test_status_nonexistent_job(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["status", "job-nonexistent"])
        assert rc == EXIT_JOB_FAILED
        err = capsys.readouterr().err
        assert "not found" in err.lower()


class TestCancel:
    """cancel <job-id> cancels running job."""

    def test_cancel_completed_job_errors(self, tmp_path, capsys, monkeypatch):
        project = _setup_project_with_jobs(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        store = SQLiteStore(str(project.db_path))
        jobs = [store.load_job(r["id"]) for r in store._conn.execute("SELECT id FROM jobs").fetchall()]
        store.close()
        job_id = jobs[0].id

        rc = main(["cancel", job_id])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already" in err.lower()

    def test_cancel_nonexistent_job(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["cancel", "job-nonexistent"])
        assert rc == EXIT_JOB_FAILED


class TestTemplates:
    """Template bundling."""

    def test_bundled_templates_dir_exists(self):
        from stepwise.project import get_bundled_templates_dir
        path = get_bundled_templates_dir()
        assert path.exists()
        templates = list(path.iterdir())
        assert len(templates) >= 1

    def test_templates_command_lists_bundled(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["templates"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "BUILT-IN:" in combined
        assert "streaming-demo" in combined
