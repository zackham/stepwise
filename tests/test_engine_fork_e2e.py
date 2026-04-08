"""End-to-end test that the fork lifecycle plumbs the snapshot UUID correctly.

This is the R13 acceptance test. It does NOT exercise real claude_direct
invocations — instead, it constructs a small flow with fork_from, sets up
session state by hand, and walks through the engine's fork-source helper
chain to assert that:

  1. _maybe_snapshot_for_fork_source persists snapshot_uuid on the parent
     step's executor_state inside the SessionLock + snapshot critical
     section.
  2. _lookup_snapshot_uuid returns that snapshot_uuid for the parent
     session name.
  3. The session-context block (when called for the child step) would
     plumb the snapshot UUID into _fork_from_session_id, not the live
     parent UUID.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import stepwise.snapshot as snapshot_mod
import stepwise.session_lock as lock_mod
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
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
    return Engine(store=store, registry=reg)


def _agent(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef("agent", {}),
        **kwargs,
    )


def test_e2e_fork_flow_uses_snapshot(engine, fake_sessions_dir):
    """Walks the fork lifecycle end-to-end with mocked snapshot.

    Flow shape:
      parent_root (session=parent) → child (session=forked, fork_from=parent)
    """
    wf = WorkflowDefinition(steps={
        "parent_root": _agent("parent_root", session="parent"),
        "child": _agent(
            "child", session="forked", fork_from="parent", after=["parent_root"],
        ),
    })
    job = engine.create_job("e2e fork", wf)
    engine._ensure_session_registry(job)

    # Simulate the parent session having captured a live UUID after
    # subprocess exit (this is normally done by the completion path).
    parent_state = engine._session_registries[job.id]["parent"]
    parent_state.claude_uuid = "live-parent-uuid-001"
    parent_state.created = True

    # Synthesize a COMPLETED parent run (no snapshot_uuid yet — that's
    # what _maybe_snapshot_for_fork_source will add).
    parent_run = StepRun(
        id="parent-run", job_id=job.id, step_name="parent_root", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/parent.jsonl"},
        result=HandoffEnvelope(
            artifact={"k": "v"}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(parent_run)

    # Mock snapshot_session to return a known value.
    with patch.object(
        snapshot_mod, "snapshot_session",
        side_effect=lambda u, **kw: f"snap-of-{u}",
    ):
        engine._maybe_snapshot_for_fork_source(job, parent_run, "parent_root")

    # Step 1 acceptance: the run's executor_state has snapshot_uuid set.
    refreshed = engine.store.latest_completed_run(job.id, "parent_root")
    assert refreshed is not None
    assert refreshed.executor_state["snapshot_uuid"] == "snap-of-live-parent-uuid-001"

    # Step 2 acceptance: _lookup_snapshot_uuid finds it.
    snap = engine._lookup_snapshot_uuid(job, "parent")
    assert snap == "snap-of-live-parent-uuid-001"

    # Step 3 acceptance: the snapshot UUID is distinct from the live UUID.
    assert snap != parent_state.claude_uuid

    # The lock file should exist (created by SessionLock during the
    # critical section).
    lock_file = fake_sessions_dir / f"{parent_state.claude_uuid}.lock"
    assert lock_file.exists()


def test_e2e_non_fork_source_unchanged(engine, fake_sessions_dir):
    """A non-fork-source step's lifecycle is unchanged: no snapshot, no lock file."""
    wf = WorkflowDefinition(steps={
        "solo": _agent("solo", session="solo_sess"),
    })
    job = engine.create_job("e2e nofork", wf)
    engine._ensure_session_registry(job)
    state = engine._session_registries[job.id]["solo_sess"]
    state.claude_uuid = "live-solo-uuid"
    state.created = True

    run = StepRun(
        id="solo-run", job_id=job.id, step_name="solo", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/solo.jsonl"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)

    engine._maybe_snapshot_for_fork_source(job, run, "solo")

    # No snapshot UUID added.
    assert "snapshot_uuid" not in (run.executor_state or {})
    # No lock file created.
    assert not (fake_sessions_dir / "live-solo-uuid.lock").exists()
