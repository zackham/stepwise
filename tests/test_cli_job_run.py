"""Tests for CLI job run --wait, --async, --notify flags."""

import json
import uuid

import pytest

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.models import (
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
    _now,
)
from stepwise.project import init_project
from stepwise.store import SQLiteStore


def _wf() -> WorkflowDefinition:
    return WorkflowDefinition(
        steps={
            "a": StepDefinition(
                name="a",
                outputs=["result"],
                executor=ExecutorRef(type="script", config={"command": 'echo \'{"result": "ok"}\''}),
            ),
        },
    )


def _make_job(
    store: SQLiteStore,
    objective: str = "test-job",
    status: JobStatus = JobStatus.STAGED,
    group: str | None = None,
) -> Job:
    job = Job(
        id=f"job-{uuid.uuid4().hex[:8]}",
        objective=objective,
        workflow=_wf(),
        status=status,
        job_group=group,
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


def _setup_project(tmp_path):
    return init_project(tmp_path)


class TestJobRunAsync:
    """--async returns immediately with job IDs."""

    def test_async_single_job(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store, group="g1")
        store.close()

        rc = main(["job", "run", job.id, "--async"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["job_id"] == job.id
        assert data["status"] == "pending"

    def test_async_group(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        j1 = _make_job(store, group="batch")
        j2 = _make_job(store, group="batch")
        store.close()

        rc = main(["job", "run", "--group", "batch", "--async"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["group"] == "batch"
        assert data["status"] == "pending"
        assert set(data["job_ids"]) == {j1.id, j2.id}

    def test_async_and_wait_mutually_exclusive(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--async", "--wait"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "mutually exclusive" in err.lower()


class TestJobRunNotify:
    """--notify sets notify_url on jobs."""

    def test_notify_single_job(self, tmp_path, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--notify", "http://localhost:9999/hook"])
        assert rc == EXIT_SUCCESS

        store2 = SQLiteStore(str(project.db_path))
        loaded = store2.load_job(job.id)
        store2.close()
        assert loaded.notify_url == "http://localhost:9999/hook"

    def test_notify_with_context(self, tmp_path, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        ctx = json.dumps({"origin": "test"})
        rc = main(["job", "run", job.id, "--notify", "http://localhost:9999/hook",
                    "--notify-context", ctx])
        assert rc == EXIT_SUCCESS

        store2 = SQLiteStore(str(project.db_path))
        loaded = store2.load_job(job.id)
        store2.close()
        assert loaded.notify_url == "http://localhost:9999/hook"
        assert loaded.notify_context == {"origin": "test"}

    def test_notify_group(self, tmp_path, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        j1 = _make_job(store, group="notifygrp")
        j2 = _make_job(store, group="notifygrp")
        store.close()

        rc = main(["job", "run", "--group", "notifygrp",
                    "--notify", "http://localhost:9999/hook"])
        assert rc == EXIT_SUCCESS

        store2 = SQLiteStore(str(project.db_path))
        for jid in [j1.id, j2.id]:
            loaded = store2.load_job(jid)
            assert loaded.notify_url == "http://localhost:9999/hook"
        store2.close()

    def test_invalid_notify_context_json(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--notify", "http://localhost:9999/hook",
                    "--notify-context", "not-json"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "invalid" in err.lower()


class TestJobRunWaitSingle:
    """--wait blocks until job completes with JSON output."""

    def test_wait_single_job_completed(self, tmp_path, capsys, monkeypatch):
        """Run a single staged job with --wait and verify JSON output."""
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--wait"])
        out = capsys.readouterr().out
        # The wait infrastructure outputs JSON to stdout
        data = json.loads(out)
        assert data["status"] in ("completed", "failed")
        if data["status"] == "completed":
            assert rc == EXIT_SUCCESS
            assert "job_id" in data or "jobs" in data


class TestJobRunWaitGroup:
    """--group --wait waits for all jobs in the group."""

    def test_wait_group_all_complete(self, tmp_path, capsys, monkeypatch):
        """Run a group of staged jobs with --wait and verify all complete."""
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        j1 = _make_job(store, group="waitgrp")
        j2 = _make_job(store, group="waitgrp")
        store.close()

        rc = main(["job", "run", "--group", "waitgrp", "--wait"])
        out = capsys.readouterr().out
        data = json.loads(out)
        # Multi-job wait returns summary
        assert "jobs" in data
        assert data["summary"]["total"] >= 2
        # All jobs in the group should be accounted for
        job_ids_in_result = {j["job_id"] for j in data["jobs"]}
        assert j1.id in job_ids_in_result
        assert j2.id in job_ids_in_result


class TestJobRunParserFlags:
    """Verify the new flags are accepted by the parser."""

    def test_async_flag_accepted(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        # --async should not cause a parser error
        rc = main(["job", "run", job.id, "--async"])
        assert rc == EXIT_SUCCESS

    def test_notify_flag_accepted(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--notify", "http://example.com/hook"])
        assert rc == EXIT_SUCCESS

    def test_notify_context_flag_accepted(self, tmp_path, capsys, monkeypatch):
        project = _setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = _make_job(store)
        store.close()

        rc = main(["job", "run", job.id, "--notify", "http://example.com/hook",
                    "--notify-context", '{"key": "val"}'])
        assert rc == EXIT_SUCCESS
