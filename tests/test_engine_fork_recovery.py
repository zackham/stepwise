"""Crash recovery tests for fork-source steps and orphan tmp cleanup."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import stepwise.snapshot as snapshot_mod
import stepwise.session_lock as lock_mod
from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


@pytest.fixture
def fake_sessions_dir(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(snapshot_mod, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(lock_mod, "SESSIONS_DIR", sessions)
    return sessions


@pytest.fixture
def engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    return AsyncEngine(store=store, registry=reg)


def _agent_step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef("agent", {}),
        **kwargs,
    )


# ─── orphan tmp cleanup ───────────────────────────────────────────────────


def test_recover_jobs_cleans_up_orphaned_tmps(engine, fake_sessions_dir):
    """recover_jobs() removes leftover .tmp files at startup."""
    (fake_sessions_dir / ".orphan1.tmp").write_bytes(b"x")
    (fake_sessions_dir / ".orphan2.tmp").write_bytes(b"y")
    (fake_sessions_dir / "real.json").write_bytes(b"z")

    # store has no jobs, so the per-job recovery path is a no-op.
    engine.recover_jobs()

    assert not (fake_sessions_dir / ".orphan1.tmp").exists()
    assert not (fake_sessions_dir / ".orphan2.tmp").exists()
    assert (fake_sessions_dir / "real.json").exists()


# ─── _recover_fork_source_steps_without_snapshot ─────────────────────────


def _make_fork_flow_job(engine):
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent", after=["parent_root"],
        ),
    })
    job = engine.create_job("t", wf)
    return engine.get_job(job.id)


def _make_running_run(job, step_name: str, executor_state: dict | None = None) -> StepRun:
    return StepRun(
        id=f"run-{step_name}",
        job_id=job.id,
        step_name=step_name,
        attempt=1,
        status=StepRunStatus.RUNNING,
        started_at=_now(),
        completed_at=None,
        executor_state=executor_state,
        result=None,
    )


def test_crash_recovery_reexec_fork_source_without_snapshot_uuid(engine, fake_sessions_dir):
    """A fork-source step in RUNNING state with no snapshot_uuid is failed
    so it can be re-executed on the next dispatch tick."""
    job = _make_fork_flow_job(engine)
    # Synthesize a RUNNING run for the fork-source step with no snapshot_uuid.
    run = _make_running_run(job, "parent_root", executor_state={"output_path": "/tmp/x"})
    engine.store.save_run(run)

    engine._recover_fork_source_steps_without_snapshot(job)

    # The run should now be FAILED with the recovery error category.
    refreshed = engine.store.latest_run(job.id, "parent_root")
    assert refreshed is not None
    assert refreshed.status == StepRunStatus.FAILED
    assert refreshed.error_category == "fork_source_crash_recovery"


def test_crash_recovery_skips_fork_source_with_snapshot_uuid(engine, fake_sessions_dir):
    """A fork-source step that DID persist a snapshot_uuid is left alone."""
    job = _make_fork_flow_job(engine)
    run = _make_running_run(
        job, "parent_root",
        executor_state={"output_path": "/tmp/x", "snapshot_uuid": "snap-xyz"},
    )
    engine.store.save_run(run)

    engine._recover_fork_source_steps_without_snapshot(job)

    refreshed = engine.store.latest_run(job.id, "parent_root")
    assert refreshed is not None
    assert refreshed.status == StepRunStatus.RUNNING  # untouched


def test_crash_recovery_skips_non_fork_source(engine, fake_sessions_dir):
    """Steps that aren't fork sources are left alone by this recovery phase."""
    wf = WorkflowDefinition(steps={
        "solo": _agent_step("solo", session="solo_sess"),
    })
    job = engine.create_job("t", wf)
    job = engine.get_job(job.id)
    run = _make_running_run(job, "solo")
    engine.store.save_run(run)

    engine._recover_fork_source_steps_without_snapshot(job)

    refreshed = engine.store.latest_run(job.id, "solo")
    assert refreshed is not None
    assert refreshed.status == StepRunStatus.RUNNING  # untouched


def test_crash_recovery_no_fork_sources_is_noop(engine, fake_sessions_dir):
    """A flow with no fork_from has no work to do in fork-source recovery."""
    wf = WorkflowDefinition(steps={
        "a": _agent_step("a"),
    })
    job = engine.create_job("t", wf)
    job = engine.get_job(job.id)
    # Should not raise.
    engine._recover_fork_source_steps_without_snapshot(job)
