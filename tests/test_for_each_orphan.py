"""Tests for the for_each sub-job orphan-spawn race + watchdog recovery.

Covers:
- ForEachSpec.stale_pending_timeout_seconds round-trips.
- YAML loader parses `stale_pending_timeout` on for_each blocks.
- _is_for_each_sub_job correctly identifies for_each sub-jobs.
- _start_queued_jobs auto-starts for_each sub-jobs when slots free up
  (the primary fix — previously sub-jobs were skipped and orphaned).
- _recover_orphaned_for_each_sub_jobs watchdog re-dispatches PENDING
  sub-jobs whose parent step has been delegated longer than the timeout.
- A for_each batch that exceeds max_concurrent_jobs still completes
  (regression test for the orphan race).
- Normal small for_each runs (no capacity pressure) are unaffected.
"""

from datetime import timedelta

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    ForEachSpec,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string

from tests.conftest import CallableExecutor, register_step_fn, run_job_sync


# ── Spec & YAML round-trip ────────────────────────────────────────────


def test_for_each_spec_default_timeout():
    spec = ForEachSpec(source_step="produce", source_field="items")
    assert spec.stale_pending_timeout_seconds == 60


def test_for_each_spec_serialization_roundtrip():
    spec = ForEachSpec(
        source_step="produce",
        source_field="items",
        item_var="x",
        on_error="continue",
        stale_pending_timeout_seconds=120,
    )
    d = spec.to_dict()
    assert d["stale_pending_timeout_seconds"] == 120
    restored = ForEachSpec.from_dict(d)
    assert restored.stale_pending_timeout_seconds == 120
    assert restored.on_error == "continue"


def test_for_each_spec_from_dict_default_when_missing():
    spec = ForEachSpec.from_dict({
        "source_step": "produce",
        "source_field": "items",
    })
    assert spec.stale_pending_timeout_seconds == 60


def test_yaml_loader_parses_stale_pending_timeout():
    yaml_text = """\
name: test-flow
steps:
  produce:
    run: 'echo {"items":[1,2]}'
    outputs: [items]
  fan-out:
    for_each: produce.items
    as: x
    stale_pending_timeout: 90
    flow:
      steps:
        echo-it:
          run: 'echo {"out":1}'
          outputs: [out]
"""
    wf = load_workflow_string(yaml_text)
    step = wf.steps["fan-out"]
    assert step.for_each is not None
    assert step.for_each.stale_pending_timeout_seconds == 90


def test_yaml_loader_default_stale_pending_timeout():
    yaml_text = """\
name: test-flow
steps:
  produce:
    run: 'echo {"items":[1]}'
    outputs: [items]
  fan-out:
    for_each: produce.items
    as: x
    flow:
      steps:
        echo-it:
          run: 'echo {"out":1}'
          outputs: [out]
"""
    wf = load_workflow_string(yaml_text)
    assert wf.steps["fan-out"].for_each.stale_pending_timeout_seconds == 60


def test_yaml_loader_rejects_invalid_stale_pending_timeout():
    yaml_text = """\
name: test-flow
steps:
  produce:
    run: 'echo {"items":[1]}'
    outputs: [items]
  fan-out:
    for_each: produce.items
    as: x
    stale_pending_timeout: -5
    flow:
      steps:
        echo-it:
          run: 'echo {"out":1}'
          outputs: [out]
"""
    with pytest.raises(Exception, match="stale_pending_timeout"):
        load_workflow_string(yaml_text)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_for_each_workflow(num_items: int) -> WorkflowDefinition:
    """Workflow: produce N items → fan out via for_each → each sub-job processes one."""
    sub_flow = WorkflowDefinition(steps={
        "process": StepDefinition(
            name="process", outputs=["result"],
            executor=ExecutorRef("callable", {"fn_name": "process_item"}),
            inputs=[InputBinding("item", "$job", "item")],
        ),
    })
    return WorkflowDefinition(steps={
        "produce": StepDefinition(
            name="produce", outputs=["items"],
            executor=ExecutorRef("callable", {"fn_name": "produce_list"}),
        ),
        "fan_out": StepDefinition(
            name="fan_out", outputs=["results"],
            executor=ExecutorRef("for_each", {}),
            for_each=ForEachSpec(
                source_step="produce",
                source_field="items",
                item_var="item",
            ),
            sub_flow=sub_flow,
        ),
    })


# ── _is_for_each_sub_job ──────────────────────────────────────────────


def test_is_for_each_sub_job_identifies_correctly(async_engine):
    """The helper inspects parent_step_run.executor_state for the for_each marker."""
    register_step_fn("produce_list", lambda inputs: {"items": ["a", "b"]})
    register_step_fn("process_item", lambda inputs: {"result": f"r-{inputs['item']}"})

    wf = _make_for_each_workflow(2)
    job = async_engine.create_job(objective="t", workflow=wf)
    run_job_sync(async_engine, job.id, timeout=10)

    # All sub-jobs of fan_out should be classified as for_each sub-jobs.
    sub_jobs = async_engine.store.child_jobs(job.id)
    assert len(sub_jobs) == 2
    for sj in sub_jobs:
        assert async_engine._is_for_each_sub_job(sj) is True

    # The parent job itself is not a sub-job at all.
    assert async_engine._is_for_each_sub_job(job) is False


# ── Orphan race regression: max_concurrent_jobs ───────────────────────


def test_for_each_completes_under_max_concurrent_pressure(store, registry):
    """Regression: a for_each batch that exceeds max_concurrent_jobs must
    complete — previously the queued sub-jobs were never restarted because
    _start_queued_jobs skipped any job with parent_job_id set.
    """
    register_step_fn("produce_list", lambda inputs: {
        "items": list(range(8)),  # 8 items — much larger than the slot count
    })
    register_step_fn("process_item", lambda inputs: {"result": inputs["item"] * 10})

    # Tight gate: parent + 1 sub-job at most. 7 sub-jobs must queue and
    # then drain through _start_queued_jobs as slots free.
    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=2)
    wf = _make_for_each_workflow(8)
    job = engine.create_job(objective="orphan-regression", workflow=wf)

    result = run_job_sync(engine, job.id, timeout=20)
    assert result.status == JobStatus.COMPLETED, (
        f"Expected COMPLETED, got {result.status}. "
        f"This means the orphan-spawn race re-emerged."
    )

    # All 8 sub-jobs should have completed
    sub_jobs = engine.store.child_jobs(job.id)
    assert len(sub_jobs) == 8
    assert all(sj.status == JobStatus.COMPLETED for sj in sub_jobs), (
        f"Some sub-jobs orphaned: "
        f"{[(sj.id, sj.status.value) for sj in sub_jobs if sj.status != JobStatus.COMPLETED]}"
    )

    # Parent for_each step should have collected all results in order
    runs = engine.store.runs_for_job(job.id)
    fe_run = [r for r in runs if r.step_name == "fan_out"][0]
    assert fe_run.status == StepRunStatus.COMPLETED
    results = fe_run.result.artifact["results"]
    assert len(results) == 8
    assert [r["result"] for r in results] == [i * 10 for i in range(8)]


# ── Watchdog: _recover_orphaned_for_each_sub_jobs ─────────────────────


def test_watchdog_recovers_pending_sub_job(store, registry):
    """If a for_each sub-job is stuck PENDING beyond the timeout, the
    watchdog re-dispatches it via start_job."""
    register_step_fn("produce_list", lambda inputs: {"items": ["x", "y"]})
    register_step_fn("process_item", lambda inputs: {"result": f"r-{inputs['item']}"})

    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=0)

    # Use a very short timeout so we don't have to wait
    sub_flow = WorkflowDefinition(steps={
        "process": StepDefinition(
            name="process", outputs=["result"],
            executor=ExecutorRef("callable", {"fn_name": "process_item"}),
            inputs=[InputBinding("item", "$job", "item")],
        ),
    })
    wf = WorkflowDefinition(steps={
        "produce": StepDefinition(
            name="produce", outputs=["items"],
            executor=ExecutorRef("callable", {"fn_name": "produce_list"}),
        ),
        "fan_out": StepDefinition(
            name="fan_out", outputs=["results"],
            executor=ExecutorRef("for_each", {}),
            for_each=ForEachSpec(
                source_step="produce",
                source_field="items",
                item_var="item",
                stale_pending_timeout_seconds=1,
            ),
            sub_flow=sub_flow,
        ),
    })

    job = engine.create_job(objective="watchdog-test", workflow=wf)

    # Manually set up an orphaned-state scenario:
    # 1. Mark parent job RUNNING (without going through start_job, so we can
    #    construct a half-dispatched for_each by hand)
    job.status = JobStatus.RUNNING
    engine.store.save_job(job)

    # 2. Run the produce step so its result exists
    engine._launch(job, "produce")
    # produce is a callable that completes synchronously inside _launch's
    # to_thread call, but we're not running the event loop here, so we
    # need to drive the produce step manually.
    # Simpler: just stash a fake completed StepRun for produce.
    from stepwise.models import HandoffEnvelope, Sidecar, StepRun

    fake_produce = StepRun(
        id="run_produce_fake",
        job_id=job.id,
        step_name="produce",
        attempt=1,
        status=StepRunStatus.COMPLETED,
        started_at=_now(),
        completed_at=_now(),
        result=HandoffEnvelope(
            artifact={"items": ["x", "y"]},
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        ),
    )
    # Cancel any in-flight runs from the _launch above
    for r in engine.store.runs_for_job(job.id):
        if r.step_name == "produce":
            r.status = StepRunStatus.CANCELLED
            r.completed_at = _now()
            engine.store.save_run(r)
    engine.store.save_run(fake_produce)

    # 3. Launch the for_each step (creates 2 sub-jobs and tries to start them).
    #    Since we're not in an asyncio loop, _launch's create_task path will
    #    fall through to run_coroutine_threadsafe → which fails because
    #    self._loop is None. We sidestep this by calling _launch_for_each
    #    directly on the base class — no asyncio task creation needed.
    fe_run = engine._launch_for_each(job, wf.steps["fan_out"])
    assert fe_run.status == StepRunStatus.DELEGATED
    sub_job_ids = fe_run.executor_state["sub_job_ids"]
    assert len(sub_job_ids) == 2

    # 4. Force the sub-jobs back to PENDING and reset their state to simulate
    #    the orphan: they were created but start_job did nothing useful.
    for sid in sub_job_ids:
        sj = engine.store.load_job(sid)
        sj.status = JobStatus.PENDING
        engine.store.save_job(sj)
        # Cancel any runs they may have started
        for r in engine.store.runs_for_job(sid):
            r.status = StepRunStatus.CANCELLED
            r.completed_at = _now()
            engine.store.save_run(r)

    # 5. Backdate the parent for_each run so it looks "old" relative to the
    #    1-second timeout.
    fe_run.started_at = _now() - timedelta(seconds=10)
    engine.store.save_run(fe_run)

    # 6. Run the watchdog.
    engine._recover_orphaned_for_each_sub_jobs()

    # 7. The watchdog should have called start_job on each PENDING sub-job,
    #    transitioning them to RUNNING (no max_concurrent_jobs gate here).
    for sid in sub_job_ids:
        sj = engine.store.load_job(sid)
        assert sj.status == JobStatus.RUNNING, (
            f"Watchdog did not recover sub-job {sid}: status={sj.status.value}"
        )


def test_watchdog_skips_recent_for_each(store, registry):
    """The watchdog must not touch a for_each whose parent run age is below
    the configured timeout — recovery should only kick in for stale ones."""
    register_step_fn("produce_list", lambda inputs: {"items": ["x"]})
    register_step_fn("process_item", lambda inputs: {"result": "ok"})

    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=0)
    wf = WorkflowDefinition(steps={
        "produce": StepDefinition(
            name="produce", outputs=["items"],
            executor=ExecutorRef("callable", {"fn_name": "produce_list"}),
        ),
        "fan_out": StepDefinition(
            name="fan_out", outputs=["results"],
            executor=ExecutorRef("for_each", {}),
            for_each=ForEachSpec(
                source_step="produce",
                source_field="items",
                item_var="item",
                stale_pending_timeout_seconds=300,  # high — recovery should not fire
            ),
            sub_flow=WorkflowDefinition(steps={
                "process": StepDefinition(
                    name="process", outputs=["result"],
                    executor=ExecutorRef("callable", {"fn_name": "process_item"}),
                    inputs=[InputBinding("item", "$job", "item")],
                ),
            }),
        ),
    })

    job = engine.create_job(objective="watchdog-skip", workflow=wf)
    job.status = JobStatus.RUNNING
    engine.store.save_job(job)

    from stepwise.models import HandoffEnvelope, Sidecar, StepRun

    engine.store.save_run(StepRun(
        id="run_produce_fake",
        job_id=job.id,
        step_name="produce",
        attempt=1,
        status=StepRunStatus.COMPLETED,
        started_at=_now(),
        completed_at=_now(),
        result=HandoffEnvelope(
            artifact={"items": ["x"]},
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        ),
    ))
    fe_run = engine._launch_for_each(job, wf.steps["fan_out"])
    sub_id = fe_run.executor_state["sub_job_ids"][0]

    # Force sub-job back to PENDING and verify watchdog leaves it alone
    sj = engine.store.load_job(sub_id)
    sj.status = JobStatus.PENDING
    engine.store.save_job(sj)
    for r in engine.store.runs_for_job(sub_id):
        r.status = StepRunStatus.CANCELLED
        r.completed_at = _now()
        engine.store.save_run(r)

    # Parent run is fresh (just started) — well under the 300s timeout
    engine._recover_orphaned_for_each_sub_jobs()
    sj = engine.store.load_job(sub_id)
    assert sj.status == JobStatus.PENDING, "Watchdog should not fire for recent for_each runs"


# ── Normal-flow regression ────────────────────────────────────────────


def test_normal_for_each_unaffected(async_engine):
    """A small for_each with plenty of capacity should still work normally."""
    register_step_fn("produce_list", lambda inputs: {"items": [1, 2, 3]})
    register_step_fn("process_item", lambda inputs: {"result": inputs["item"] + 100})

    wf = _make_for_each_workflow(3)
    job = async_engine.create_job(objective="normal", workflow=wf)
    result = run_job_sync(async_engine, job.id, timeout=10)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    fe_run = [r for r in runs if r.step_name == "fan_out"][0]
    results = fe_run.result.artifact["results"]
    assert [r["result"] for r in results] == [101, 102, 103]
