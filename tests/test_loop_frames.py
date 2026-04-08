"""Step 7 (§11.5): unit tests for the LoopFrame stack on Job.

Covers (R23a-g):
  - LoopFrame dataclass round-trip (to_dict / from_dict)
  - Engine._get_or_create_loop_frame idempotency + parent_frame_id semantics
  - Engine._invalidate_child_frames recursion
  - Engine._rebuild_loop_frames crash recovery
  - Job.loop_frames SQLite persistence
  - Schema migration idempotency
  - Backwards compat: pre-step-7 jobs deserialize cleanly
"""

from __future__ import annotations

import pytest

from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    Job,
    JobStatus,
    LoopFrame,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


def make_engine() -> Engine:
    store = SQLiteStore(":memory:")
    return Engine(store=store, registry=ExecutorRegistry())


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=kwargs.pop("executor", ExecutorRef("script", {})),
        **kwargs,
    )


def _wf(*steps: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(steps={s.name: s for s in steps})


def _loop_rule(target: str, name: str = "back") -> ExitRule:
    return ExitRule(name=name, type="always", config={"action": "loop", "target": target})


# ─── R23c: LoopFrame round-trip ───────────────────────────────────────────


def test_frame_serialization_roundtrip():
    f = LoopFrame(
        frame_id="analyze",
        iteration_index=3,
        parent_frame_id="outer",
        presence={"x": True, "y": False},
    )
    d = f.to_dict()
    assert d == {
        "frame_id": "analyze",
        "iteration_index": 3,
        "parent_frame_id": "outer",
        "presence": {"x": True, "y": False},
    }
    f2 = LoopFrame.from_dict(d)
    assert f2 == f


def test_frame_serialization_empty_presence():
    f = LoopFrame(frame_id="x")
    f2 = LoopFrame.from_dict(f.to_dict())
    assert f2.iteration_index == 0
    assert f2.presence == {}
    assert f2.parent_frame_id is None


# ─── R23a: _get_or_create_loop_frame idempotent ───────────────────────────


def test_frame_create_idempotent():
    engine = make_engine()
    wf = _wf(_step("a", exit_rules=[_loop_rule("a")]))
    job = engine.create_job("t", wf)
    f1 = engine._get_or_create_loop_frame(job, "a", parent_frame_id=None)
    f2 = engine._get_or_create_loop_frame(job, "a", parent_frame_id=None)
    assert f1 is f2
    assert "a" in job.loop_frames


def test_frame_parent_set_only_on_create():
    engine = make_engine()
    wf = _wf(_step("a"))
    job = engine.create_job("t", wf)
    f1 = engine._get_or_create_loop_frame(job, "inner", parent_frame_id="outer")
    assert f1.parent_frame_id == "outer"
    # Calling with mismatched parent must NOT mutate the existing frame.
    f2 = engine._get_or_create_loop_frame(job, "inner", parent_frame_id="other")
    assert f2.parent_frame_id == "outer"


# ─── R23b: frame increment ────────────────────────────────────────────────


def test_frame_increment_via_helper():
    engine = make_engine()
    wf = _wf(_step("a"))
    job = engine.create_job("t", wf)
    frame = engine._get_or_create_loop_frame(job, "a")
    assert frame.iteration_index == 0
    frame.iteration_index += 1
    frame.iteration_index += 1
    assert frame.iteration_index == 2


# ─── _invalidate_child_frames ─────────────────────────────────────────────


def test_invalidate_child_frames_removes_direct_children():
    engine = make_engine()
    wf = _wf(_step("a"))
    job = engine.create_job("t", wf)
    engine._get_or_create_loop_frame(job, "outer", parent_frame_id=None)
    engine._get_or_create_loop_frame(job, "inner", parent_frame_id="outer")
    assert "inner" in job.loop_frames

    engine._invalidate_child_frames(job, parent_frame_id="outer")

    assert "inner" not in job.loop_frames
    assert "outer" in job.loop_frames  # parent untouched


def test_invalidate_child_frames_recursive():
    engine = make_engine()
    wf = _wf(_step("a"))
    job = engine.create_job("t", wf)
    engine._get_or_create_loop_frame(job, "outer", parent_frame_id=None)
    engine._get_or_create_loop_frame(job, "middle", parent_frame_id="outer")
    engine._get_or_create_loop_frame(job, "inner", parent_frame_id="middle")

    engine._invalidate_child_frames(job, parent_frame_id="outer")

    # All descendants of outer are removed.
    assert "middle" not in job.loop_frames
    assert "inner" not in job.loop_frames
    assert "outer" in job.loop_frames


# ─── R23d: SQLite persistence ─────────────────────────────────────────────


def test_job_loop_frames_persist_to_sqlite():
    store = SQLiteStore(":memory:")
    wf = WorkflowDefinition(steps={
        "a": _step("a"),
    })
    j = Job(
        id="j-loop",
        objective="t",
        workflow=wf,
        loop_frames={
            "a": LoopFrame(
                frame_id="a",
                iteration_index=4,
                parent_frame_id=None,
                presence={"prev": True},
            ),
        },
    )
    store.save_job(j)

    j2 = store.load_job("j-loop")
    assert "a" in j2.loop_frames
    assert j2.loop_frames["a"].iteration_index == 4
    assert j2.loop_frames["a"].presence == {"prev": True}


def test_save_job_preserves_loop_frames_across_updates():
    store = SQLiteStore(":memory:")
    wf = WorkflowDefinition(steps={"a": _step("a")})
    j = Job(id="j1", objective="t", workflow=wf)
    store.save_job(j)

    # Mutate + save
    j.loop_frames["a"] = LoopFrame(frame_id="a", iteration_index=2)
    store.save_job(j)

    j2 = store.load_job("j1")
    assert j2.loop_frames["a"].iteration_index == 2


# ─── R23e: crash recovery via _rebuild_loop_frames ───────────────────────


def test_rebuild_loop_frames_from_step_runs():
    engine = make_engine()
    # Two-step loop: a → b, b loops back to a (3 completed runs of a means 2 relaunches)
    wf = _wf(
        _step("a"),
        _step("b", inputs=[
            __import__("stepwise.models", fromlist=["InputBinding"]).InputBinding(
                local_name="prev", source_step="a", source_field="result",
            )
        ], exit_rules=[_loop_rule("a")]),
    )
    job = engine.create_job("t", wf)

    # Synthesize 3 completed runs of step "a"
    for i in range(3):
        run = StepRun(
            id=f"run-a-{i}",
            job_id=job.id,
            step_name="a",
            attempt=i + 1,
            status=StepRunStatus.COMPLETED,
            started_at=_now(),
            completed_at=_now(),
            result=HandoffEnvelope(
                artifact={}, sidecar=Sidecar(),
                workspace=None, timestamp=_now(),
            ),
        )
        engine.store.save_run(run)

    # Wipe in-memory frames to simulate crash
    job.loop_frames = {}

    engine._rebuild_loop_frames(job)

    # The loop initiator is "a" (it's the target of b's loop). After 3
    # completed runs, the rebuilt frame's iteration_index reflects the count.
    assert "a" in job.loop_frames
    assert job.loop_frames["a"].iteration_index == 3


def test_rebuild_loop_frames_no_loops_is_noop():
    engine = make_engine()
    wf = _wf(_step("a"))
    job = engine.create_job("t", wf)
    engine._rebuild_loop_frames(job)
    assert job.loop_frames == {}


# ─── R23f: schema migration idempotent ────────────────────────────────────


def test_schema_migration_idempotent(tmp_path):
    db_path = tmp_path / "stepwise.db"
    store1 = SQLiteStore(str(db_path))
    # Save a job with frames
    wf = WorkflowDefinition(steps={"a": _step("a")})
    j = Job(id="j1", objective="t", workflow=wf,
            loop_frames={"a": LoopFrame(frame_id="a", iteration_index=2)})
    store1.save_job(j)
    store1.close()

    # Open again — _migrate runs at __init__ and should be a no-op
    store2 = SQLiteStore(str(db_path))
    j2 = store2.load_job("j1")
    assert j2.loop_frames["a"].iteration_index == 2


# ─── R23g: backwards compat for pre-step-7 jobs ───────────────────────────


def test_old_jobs_without_loop_frames_load_clean(tmp_path):
    """A job row written before the loop_frames column existed should
    deserialize with an empty dict (no crash)."""
    db_path = tmp_path / "stepwise.db"
    store = SQLiteStore(str(db_path))
    # Manually drop the loop_frames column to simulate a pre-step-7 schema.
    # SQLite doesn't support DROP COLUMN trivially, so instead we update
    # the row to NULL and verify the load path handles it.
    wf = WorkflowDefinition(steps={"a": _step("a")})
    j = Job(id="j-old", objective="t", workflow=wf)
    store.save_job(j)
    # Force the column to NULL (legacy state)
    store._conn.execute("UPDATE jobs SET loop_frames = NULL WHERE id = ?", (j.id,))
    store._conn.commit()

    j2 = store.load_job("j-old")
    assert j2.loop_frames == {}


def test_job_to_dict_omits_empty_loop_frames():
    wf = WorkflowDefinition(steps={"a": _step("a")})
    j = Job(id="j1", objective="t", workflow=wf)
    d = j.to_dict()
    assert "loop_frames" not in d


def test_job_to_dict_includes_non_empty_loop_frames():
    wf = WorkflowDefinition(steps={"a": _step("a")})
    j = Job(id="j1", objective="t", workflow=wf,
            loop_frames={"x": LoopFrame(frame_id="x", iteration_index=1)})
    d = j.to_dict()
    assert "loop_frames" in d
    assert d["loop_frames"]["x"]["iteration_index"] == 1


# ─── Integration: increment + invalidate cascade ──────────────────────────


def test_increment_outer_invalidates_inner_via_helper_chain():
    engine = make_engine()
    wf = _wf(_step("outer"), _step("inner"))
    job = engine.create_job("t", wf)
    # Set up an outer + inner frame stack
    outer = engine._get_or_create_loop_frame(job, "outer", parent_frame_id=None)
    inner = engine._get_or_create_loop_frame(job, "inner", parent_frame_id="outer")
    inner.iteration_index = 3
    inner.presence = {"foo": True}
    outer.iteration_index = 2

    # Simulate outer firing: bump iteration, clear presence, invalidate children
    outer.iteration_index += 1
    outer.presence.clear()
    engine._invalidate_child_frames(job, parent_frame_id="outer")

    assert outer.iteration_index == 3
    assert "inner" not in job.loop_frames
