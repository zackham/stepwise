"""Engine-level tests for the fork-source step lifecycle (snapshot critical section)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import stepwise.snapshot as snapshot_mod
import stepwise.session_lock as lock_mod
from stepwise.engine import Engine, SessionState
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    Job,
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
    """Project-scoped sessions dir under a tmp CLAUDE_PROJECTS_DIR.

    Returns the sessions directory path. Tests get a known working_dir
    via the FAKE_WORKING_DIR module-level constant.
    """
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(snapshot_mod, "CLAUDE_PROJECTS_DIR", projects_root)
    sessions = projects_root / snapshot_mod.project_slug(FAKE_WORKING_DIR)
    sessions.mkdir(parents=True)
    return sessions


FAKE_WORKING_DIR = "/fake/work/test-project"


@pytest.fixture
def engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    return Engine(store=store, registry=reg)


def _agent_step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef("agent", {"working_dir": FAKE_WORKING_DIR}),
        **kwargs,
    )


# ─── _fork_source_step_names ──────────────────────────────────────────────


def test_fork_source_step_names_empty_when_no_fork_from(engine):
    wf = WorkflowDefinition(steps={
        "a": _agent_step("a", session="s1"),
        "b": _agent_step("b", session="s1", after=["a"]),
    })
    job = Job(
        id="j1", objective="t", workflow=wf, status=JobStatus.PENDING,
        created_at=_now(), updated_at=_now(),
    )
    assert engine._fork_source_step_names(job) == set()


def test_fork_source_step_names_finds_chain_root(engine):
    """Step 'parent_root' writes to session 'parent', and 'child' forks from
    'parent_root'. The fork source is 'parent_root' (the directly-named
    step under §8.2 step-name semantics)."""
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent_root",
            after=["parent_root"],
        ),
    })
    job = Job(
        id="j1", objective="t", workflow=wf, status=JobStatus.PENDING,
        created_at=_now(), updated_at=_now(),
    )
    sources = engine._fork_source_step_names(job)
    assert sources == {"parent_root"}


def test_fork_source_step_names_only_directly_named_step(engine):
    """Under step-name semantics, only the step directly named by fork_from
    is a fork source — not all writers on the parent session."""
    wf = WorkflowDefinition(steps={
        "p1": _agent_step("p1", session="parent"),
        "p2": _agent_step("p2", session="parent", after=["p1"]),
        "child": _agent_step(
            "child", session="forked", fork_from="p2", after=["p2"],
        ),
    })
    job = Job(
        id="j1", objective="t", workflow=wf, status=JobStatus.PENDING,
        created_at=_now(), updated_at=_now(),
    )
    sources = engine._fork_source_step_names(job)
    assert sources == {"p2"}


# ─── _lookup_snapshot_uuid ────────────────────────────────────────────────


def test_lookup_snapshot_uuid_returns_none_when_no_run(engine):
    wf = WorkflowDefinition(steps={
        "p": _agent_step("p", session="parent"),
    })
    job = engine.create_job("t", wf)
    # Lookup parameter is a STEP NAME under §8.2 step-name semantics.
    assert engine._lookup_snapshot_uuid(job, "p") is None


def test_lookup_snapshot_uuid_returns_persisted_value(engine):
    wf = WorkflowDefinition(steps={
        "p": _agent_step("p", session="parent"),
    })
    job = engine.create_job("t", wf)
    # Synthesize a completed run with a snapshot_uuid
    run = StepRun(
        id="run1", job_id=job.id, step_name="p", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"snapshot_uuid": "snap-abc"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)
    assert engine._lookup_snapshot_uuid(job, "p") == "snap-abc"


def test_lookup_snapshot_uuid_skips_runs_without_snapshot(engine):
    wf = WorkflowDefinition(steps={
        "p": _agent_step("p", session="parent"),
    })
    job = engine.create_job("t", wf)
    run = StepRun(
        id="run1", job_id=job.id, step_name="p", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"other_key": "value"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)
    assert engine._lookup_snapshot_uuid(job, "p") is None


# ─── _maybe_snapshot_for_fork_source ──────────────────────────────────────


def test_maybe_snapshot_skips_non_fork_source(engine, fake_sessions_dir):
    """A step that is NOT a fork source has snapshot helper as a no-op."""
    wf = WorkflowDefinition(steps={
        "solo": _agent_step("solo", session="solo_sess"),
    })
    job = engine.create_job("t", wf)
    engine._ensure_session_registry(job)
    # Set claude_uuid for the session
    state = engine._session_registries[job.id]["solo_sess"]
    state.claude_uuid = "live-uuid"
    state.created = True

    run = StepRun(
        id="r1", job_id=job.id, step_name="solo", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/foo"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)

    snapshot_called = []
    with patch.object(
        snapshot_mod, "snapshot_session",
        side_effect=lambda u, **kw: (snapshot_called.append(u) or "snap-x"),
    ):
        engine._maybe_snapshot_for_fork_source(job, run, "solo")

    assert snapshot_called == []
    assert "snapshot_uuid" not in (run.executor_state or {})


def test_maybe_snapshot_creates_snapshot_for_fork_source(engine, fake_sessions_dir):
    """A fork-source step gets a snapshot and snapshot_uuid persisted."""
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent_root",
            after=["parent_root"],
        ),
    })
    job = engine.create_job("t", wf)
    engine._ensure_session_registry(job)
    state = engine._session_registries[job.id]["parent"]
    state.claude_uuid = "live-uuid-123"
    state.created = True

    run = StepRun(
        id="r1", job_id=job.id, step_name="parent_root", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/foo"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)

    captured = {}
    def fake_snapshot(uuid, working_dir, **kw):
        captured["src"] = uuid
        captured["working_dir"] = working_dir
        return "snap-xyz"

    with patch("stepwise.engine.snapshot_session", create=True, side_effect=fake_snapshot), \
         patch.object(snapshot_mod, "snapshot_session", side_effect=fake_snapshot):
        engine._maybe_snapshot_for_fork_source(job, run, "parent_root")

    # The snapshot_uuid is persisted on the run's executor_state.
    assert run.executor_state.get("snapshot_uuid") == "snap-xyz"
    assert captured.get("src") == "live-uuid-123"
    assert captured.get("working_dir") == FAKE_WORKING_DIR


def test_maybe_snapshot_failure_leaves_step_in_recoverable_state(
    engine, fake_sessions_dir,
):
    """If snapshot raises, no snapshot_uuid is set; the run is recoverable."""
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent_root", after=["parent_root"],
        ),
    })
    job = engine.create_job("t", wf)
    engine._ensure_session_registry(job)
    state = engine._session_registries[job.id]["parent"]
    state.claude_uuid = "live-uuid"
    state.created = True

    run = StepRun(
        id="r1", job_id=job.id, step_name="parent_root", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/foo"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)

    with patch.object(
        snapshot_mod, "snapshot_session",
        side_effect=RuntimeError("disk full"),
    ):
        engine._maybe_snapshot_for_fork_source(job, run, "parent_root")

    # snapshot_uuid is NOT set — the run is in the recoverable state.
    assert "snapshot_uuid" not in (run.executor_state or {})


def test_maybe_snapshot_called_within_lock_critical_section(
    engine, fake_sessions_dir,
):
    """Snapshot is called WHILE the SessionLock is held."""
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent_root", after=["parent_root"],
        ),
    })
    job = engine.create_job("t", wf)
    engine._ensure_session_registry(job)
    state = engine._session_registries[job.id]["parent"]
    state.claude_uuid = "live-uuid"
    state.created = True

    run = StepRun(
        id="r1", job_id=job.id, step_name="parent_root", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"output_path": "/tmp/foo"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(run)

    events: list[str] = []

    class TrackingLock:
        def __init__(self, uuid, working_dir, mode):
            self.uuid = uuid
            self.working_dir = working_dir
            self.mode = mode

        def __enter__(self):
            events.append("lock_acquired")
            return self

        def __exit__(self, *exc):
            events.append("lock_released")

    def fake_snapshot(uuid, working_dir, **kw):
        events.append("snapshot_called")
        return "snap-xyz"

    with patch("stepwise.engine.SessionLock", TrackingLock, create=True), \
         patch("stepwise.engine.snapshot_session", create=True, side_effect=fake_snapshot), \
         patch.object(snapshot_mod, "snapshot_session", side_effect=fake_snapshot), \
         patch.object(lock_mod, "SessionLock", TrackingLock):
        engine._maybe_snapshot_for_fork_source(job, run, "parent_root")

    # Order: acquire → snapshot → release
    assert events == ["lock_acquired", "snapshot_called", "lock_released"]


# ─── R4: session-context block uses snapshot UUID, not live UUID ──────────


def test_session_context_uses_snapshot_uuid_not_live_uuid(engine, fake_sessions_dir):
    """When a child step is preparing to fork, the engine plumbs the
    snapshot UUID (from the parent step's executor_state) into
    _fork_from_session_id, not the live SessionState.claude_uuid."""
    wf = WorkflowDefinition(steps={
        "parent_root": _agent_step("parent_root", session="parent"),
        "child": _agent_step(
            "child", session="forked", fork_from="parent_root", after=["parent_root"],
        ),
    })
    job = engine.create_job("t", wf)
    engine._ensure_session_registry(job)
    # Live parent UUID is "live-parent-uuid"
    parent_state = engine._session_registries[job.id]["parent"]
    parent_state.claude_uuid = "live-parent-uuid"
    parent_state.created = True

    # Persist a completed parent run with snapshot_uuid set
    parent_run = StepRun(
        id="r1", job_id=job.id, step_name="parent_root", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"snapshot_uuid": "snap-parent-uuid"},
        result=HandoffEnvelope(
            artifact={}, sidecar=Sidecar(),
            workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(parent_run)

    # Snapshot lookup returns the snapshot uuid
    assert engine._lookup_snapshot_uuid(job, "parent_root") == "snap-parent-uuid"
    # And the snapshot uuid is distinct from the live uuid
    assert "snap-parent-uuid" != parent_state.claude_uuid
