"""Regression tests for the 1.0 RC hardening audit findings (engine).

Covers:
- F1:  sub_flow sub-jobs no longer stranded PENDING under max_concurrent_jobs
- F2:  sibling step results preserved when the job is paused by escalate
- F3:  poll watch checks run in the thread pool, not on the event loop
- F4:  --rerun cache bypass works under AsyncEngine
- F5:  for_each fail_fast cancels capacity-queued PENDING siblings
- F6:  escalated (PAUSED) for_each sub-jobs wait for human resolution
- F7:  concurrent _launch calls create a single run
- F8:  session lock held until executor thread finishes after cancellation
- F15: AsyncEngine.run() survives exceptions from _poll_external_changes
- F62: readiness scans memoize currency checks (query count stays linear)
- F63: _emit reuses a pre-loaded Job instead of reloading per event
"""

import asyncio
import threading
import time

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutionContext
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    ForEachSpec,
    InputBinding,
    JobConfig,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)

from tests.conftest import register_step_fn, run_job_sync


def _simple_step(name, fn_name, outputs, inputs=None, exit_rules=None, **kw):
    return StepDefinition(
        name=name,
        outputs=outputs,
        executor=ExecutorRef("callable", {"fn_name": fn_name}),
        inputs=inputs or [],
        exit_rules=exit_rules or [],
        **kw,
    )


# ── F1: sub_flow sub-job not stranded when max_concurrent_jobs saturated ──


def test_sub_flow_sub_job_starts_when_capacity_saturated(store, registry):
    """A direct sub_flow sub-job bypasses the capacity gate: the parent
    already holds a slot in DELEGATED state, so queue-gating the sub-job
    deadlocked the parent permanently."""
    register_step_fn("inner_fn", lambda inputs: {"out": "inner-done"})

    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=1)

    inner = WorkflowDefinition(steps={
        "inner": _simple_step("inner", "inner_fn", ["out"]),
    })
    wf = WorkflowDefinition(steps={
        "delegate": StepDefinition(
            name="delegate",
            outputs=["out"],
            executor=ExecutorRef("sub_flow", {}),
            sub_flow=inner,
        ),
    })

    job = engine.create_job(objective="t", workflow=wf)
    result = run_job_sync(engine, job.id, timeout=10)
    assert result.status == JobStatus.COMPLETED
    runs = engine.store.runs_for_job(job.id)
    assert runs[0].status == StepRunStatus.COMPLETED
    assert runs[0].result.artifact["out"] == "inner-done"


# ── F2: sibling result preserved when job paused by escalate ─────────────


def test_sibling_result_preserved_when_job_escalates(async_engine):
    """When a parallel step escalates (job → PAUSED) while a sibling's
    executor is still running, the sibling's result must be persisted —
    not silently discarded leaving the run stuck in RUNNING."""
    register_step_fn("gate", lambda inputs: {"status": "halt"})

    def slow(inputs):
        time.sleep(0.5)
        return {"out": 42}

    register_step_fn("slow", slow)

    wf = WorkflowDefinition(steps={
        "gate": _simple_step(
            "gate", "gate", ["status"],
            exit_rules=[ExitRule("stuck", "always", {"action": "escalate"})],
        ),
        "slow": _simple_step("slow", "slow", ["out"]),
    })

    async def scenario():
        engine_task = asyncio.create_task(async_engine.run())
        try:
            job = async_engine.create_job(objective="t", workflow=wf)
            async_engine.start_job(job.id)

            slow_run = None
            for _ in range(100):
                await asyncio.sleep(0.05)
                j = async_engine.store.load_job(job.id)
                runs = async_engine.store.runs_for_job(job.id)
                slow_run = next((r for r in runs if r.step_name == "slow"), None)
                if (j.status == JobStatus.PAUSED and slow_run is not None
                        and slow_run.status != StepRunStatus.RUNNING):
                    break

            j = async_engine.store.load_job(job.id)
            assert j.status == JobStatus.PAUSED  # escalated by gate
            assert slow_run is not None
            # The fix: result persisted, run completed, exit deferred
            assert slow_run.status == StepRunStatus.COMPLETED
            assert slow_run.result is not None
            assert slow_run.result.artifact["out"] == 42
            assert (slow_run.executor_state or {}).get("_deferred_exit") is True

            # Resume → deferred exit processed → job completes
            async_engine.resume_job(job.id)
            async_engine._check_job_terminal(job.id)
            j = async_engine.store.load_job(job.id)
            assert j.status == JobStatus.COMPLETED
            slow_run = async_engine.store.latest_run(job.id, "slow")
            assert not (slow_run.executor_state or {}).get("_deferred_exit")
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_sibling_error_persisted_when_job_escalates(async_engine):
    """A sibling executor *crash* arriving after the job left RUNNING must
    mark the run FAILED (with the error), not leave it stuck RUNNING."""
    register_step_fn("gate", lambda inputs: {"status": "halt"})

    def crasher(inputs):
        time.sleep(0.5)
        raise RuntimeError("boom from thread")

    register_step_fn("crasher", crasher)

    wf = WorkflowDefinition(steps={
        "gate": _simple_step(
            "gate", "gate", ["status"],
            exit_rules=[ExitRule("stuck", "always", {"action": "escalate"})],
        ),
        "crash": _simple_step("crash", "crasher", ["out"]),
    })

    async def scenario():
        engine_task = asyncio.create_task(async_engine.run())
        try:
            job = async_engine.create_job(objective="t", workflow=wf)
            async_engine.start_job(job.id)
            crash_run = None
            for _ in range(100):
                await asyncio.sleep(0.05)
                j = async_engine.store.load_job(job.id)
                runs = async_engine.store.runs_for_job(job.id)
                crash_run = next((r for r in runs if r.step_name == "crash"), None)
                if (j.status == JobStatus.PAUSED and crash_run is not None
                        and crash_run.status != StepRunStatus.RUNNING):
                    break
            assert crash_run is not None
            # CallableExecutor catches the exception and returns a failed
            # data result — either way the run must be terminal with error.
            assert crash_run.status == StepRunStatus.FAILED
            assert "boom" in (crash_run.error or "")
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ── F3: poll checks run off the event loop ───────────────────────────────


def test_poll_check_runs_in_thread_pool(async_engine, monkeypatch):
    """The blocking check_command (up to 300s) must run in the executor
    thread pool — never inline on the engine's event loop."""
    from stepwise import poll_eval

    seen_threads: list[str] = []

    def fake_eval(command, cwd=None, env=None, timeout_seconds=300):
        seen_threads.append(threading.current_thread().name)
        return poll_eval.PollResult(ready=True, output={"done": True})

    monkeypatch.setattr(poll_eval, "evaluate_poll_command_sync", fake_eval)

    wf = WorkflowDefinition(steps={
        "wait": StepDefinition(
            name="wait",
            outputs=["done"],
            executor=ExecutorRef("poll", {
                "check_command": "true",
                "interval_seconds": 0.1,
            }),
        ),
    })

    job = async_engine.create_job(objective="t", workflow=wf)
    result = run_job_sync(async_engine, job.id, timeout=10)
    assert result.status == JobStatus.COMPLETED
    run = async_engine.store.latest_run(job.id, "wait")
    assert run.status == StepRunStatus.COMPLETED
    assert run.result.artifact["done"] is True
    # The check ran in the engine's executor pool, off the event loop
    assert seen_threads, "poll check never executed"
    assert all(name.startswith("stepwise-exec") for name in seen_threads)
    assert not async_engine._poll_checks_inflight


# ── F4: --rerun cache bypass under AsyncEngine ───────────────────────────


def test_rerun_bypasses_cache_async_engine(store, registry):
    from stepwise.cache import StepResultCache
    from stepwise.models import CacheConfig

    call_count = 0

    def fn(inputs):
        nonlocal call_count
        call_count += 1
        return {"result": f"run-{call_count}"}

    register_step_fn("rerun_fn_async", fn)

    cache = StepResultCache(":memory:")
    engine = AsyncEngine(store=store, registry=registry, cache=cache)

    wf = WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            outputs=["result"],
            executor=ExecutorRef("callable", {"fn_name": "rerun_fn_async"}),
            inputs=[InputBinding("x", "$job", "x")],
            cache=CacheConfig(),
        ),
    })

    async def scenario():
        engine_task = asyncio.create_task(engine.run())
        try:
            job1 = engine.create_job(objective="t1", workflow=wf, inputs={"x": "a"})
            engine.start_job(job1.id)
            assert (await asyncio.wait_for(engine.wait_for_job(job1.id), 10)
                    ).status == JobStatus.COMPLETED
            assert call_count == 1

            # Same inputs — cache hit
            job2 = engine.create_job(objective="t2", workflow=wf, inputs={"x": "a"})
            engine.start_job(job2.id)
            assert (await asyncio.wait_for(engine.wait_for_job(job2.id), 10)
                    ).status == JobStatus.COMPLETED
            assert call_count == 1

            # --rerun must bypass the cache
            job3 = engine.create_job(
                objective="t3", workflow=wf, inputs={"x": "a"},
                config=JobConfig(metadata={"rerun_steps": ["step-a"]}),
            )
            engine.start_job(job3.id)
            assert (await asyncio.wait_for(engine.wait_for_job(job3.id), 10)
                    ).status == JobStatus.COMPLETED
            assert call_count == 2
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ── F5: fail_fast cancels capacity-queued PENDING siblings ───────────────


def test_fail_fast_cancels_pending_capacity_queued_siblings(store, registry):
    ok_calls = []

    def worker(inputs):
        item = inputs["item"]
        if item == "bad":
            raise RuntimeError("item failed")
        ok_calls.append(item)
        return {"flag": item}

    register_step_fn("fanout_worker", worker)
    register_step_fn("fanout_source", lambda i: {"items": ["bad", "ok1", "ok2"]})

    # parent (1 slot) + first sub-job (1 slot) = at limit → ok1/ok2 queue PENDING
    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=2)

    sub_flow = WorkflowDefinition(steps={
        "work": _simple_step(
            "work", "fanout_worker", ["flag"],
            inputs=[InputBinding("item", "$job", "item")],
        ),
    })
    wf = WorkflowDefinition(steps={
        "source": _simple_step("source", "fanout_source", ["items"]),
        "fanout": StepDefinition(
            name="fanout",
            outputs=["results"],
            executor=ExecutorRef("for_each", {}),
            for_each=ForEachSpec(source_step="source", source_field="items"),
            sub_flow=sub_flow,
        ),
    })

    async def scenario():
        engine_task = asyncio.create_task(engine.run())
        try:
            job = engine.create_job(objective="t", workflow=wf)
            engine.start_job(job.id)
            for _ in range(100):
                await asyncio.sleep(0.05)
                j = engine.store.load_job(job.id)
                if j.status not in (JobStatus.RUNNING, JobStatus.PENDING):
                    break
            j = engine.store.load_job(job.id)
            assert j.status == JobStatus.FAILED

            # Give any (buggy) deferred queue-start a chance to fire
            await asyncio.sleep(0.3)

            fe_run = engine.store.latest_run(job.id, "fanout")
            sub_ids = (fe_run.executor_state or {}).get("sub_job_ids", [])
            assert len(sub_ids) == 3
            statuses = sorted(
                engine.store.load_job(sid).status.value for sid in sub_ids
            )
            assert statuses == ["cancelled", "cancelled", "failed"]
            # The queued siblings never executed their (billable) work
            assert ok_calls == []
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ── F6: escalated for_each sub-jobs wait for human resolution ────────────


def test_paused_for_each_sub_job_waits_then_completes(async_engine):
    register_step_fn("fe_worker", lambda i: {"flag": i["item"]})
    register_step_fn("fe_source", lambda i: {"items": ["ok", "stuck"]})

    sub_flow = WorkflowDefinition(steps={
        "work": _simple_step(
            "work", "fe_worker", ["flag"],
            inputs=[InputBinding("item", "$job", "item")],
            exit_rules=[ExitRule("needs-human", "field_match", {
                "field": "flag", "value": "stuck", "action": "escalate",
            })],
        ),
    })
    wf = WorkflowDefinition(steps={
        "source": _simple_step("source", "fe_source", ["items"]),
        "fanout": StepDefinition(
            name="fanout",
            outputs=["results"],
            executor=ExecutorRef("for_each", {}),
            for_each=ForEachSpec(source_step="source", source_field="items"),
            sub_flow=sub_flow,
        ),
    })

    async def scenario():
        engine_task = asyncio.create_task(async_engine.run())
        try:
            job = async_engine.create_job(objective="t", workflow=wf)
            async_engine.start_job(job.id)

            paused_id = None
            for _ in range(100):
                await asyncio.sleep(0.05)
                fe_run = async_engine.store.latest_run(job.id, "fanout")
                if not fe_run or not fe_run.executor_state:
                    continue
                sub_ids = fe_run.executor_state.get("sub_job_ids", [])
                if len(sub_ids) < 2:
                    continue
                subs = [async_engine.store.load_job(s) for s in sub_ids]
                by_status = {s.status for s in subs}
                if by_status == {JobStatus.COMPLETED, JobStatus.PAUSED}:
                    paused_id = next(
                        s.id for s in subs if s.status == JobStatus.PAUSED
                    )
                    break

            assert paused_id is not None, "expected one paused, one completed sub-job"

            # F6: the parent must still be waiting — NOT completed/failed
            # with the escalated item recorded as an error.
            parent = async_engine.store.load_job(job.id)
            assert parent.status == JobStatus.RUNNING
            fe_run = async_engine.store.latest_run(job.id, "fanout")
            assert fe_run.status == StepRunStatus.DELEGATED

            # Human resolves the escalation → parent completes with real data
            async_engine.resume_job(paused_id)
            async_engine._check_job_terminal(paused_id)

            for _ in range(100):
                await asyncio.sleep(0.05)
                parent = async_engine.store.load_job(job.id)
                if parent.status not in (JobStatus.RUNNING, JobStatus.PENDING):
                    break
            assert parent.status == JobStatus.COMPLETED
            fe_run = async_engine.store.latest_run(job.id, "fanout")
            results = fe_run.result.artifact["results"]
            assert results == [{"flag": "ok"}, {"flag": "stuck"}]
            assert not any("_error" in (r or {}) for r in results)
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ── F7: concurrent _launch creates a single run ──────────────────────────


def test_concurrent_launch_creates_single_run(async_engine):
    def slowstep(inputs):
        time.sleep(0.4)
        return {"v": 1}

    register_step_fn("slowstep", slowstep)

    wf = WorkflowDefinition(steps={
        "s": _simple_step("s", "slowstep", ["v"]),
    })

    async def scenario():
        engine_task = asyncio.create_task(async_engine.run())
        try:
            await asyncio.sleep(0.05)  # let run() record the loop
            job = async_engine.create_job(objective="t", workflow=wf)
            job.status = JobStatus.RUNNING
            async_engine.store.save_job(job)

            barrier = threading.Barrier(2)
            errors = []

            def launch():
                try:
                    j = async_engine.store.load_job(job.id)
                    barrier.wait(timeout=5)
                    async_engine._launch(j, "s")
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            t1 = threading.Thread(target=launch)
            t2 = threading.Thread(target=launch)
            t1.start()
            t2.start()
            await asyncio.to_thread(t1.join)
            await asyncio.to_thread(t2.join)
            assert errors == []

            runs = async_engine.store.runs_for_job(job.id)
            assert len(runs) == 1, (
                f"expected exactly one run, got {len(runs)} "
                "(duplicate-launch TOCTOU)"
            )
            await asyncio.wait_for(async_engine.wait_for_job(job.id), 5)
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ── F8: session lock held until executor thread finishes ─────────────────


def test_session_lock_held_after_task_cancellation(async_engine):
    started = threading.Event()
    release = threading.Event()

    def blocker(inputs):
        started.set()
        release.wait(timeout=5)
        return {"v": 1}

    register_step_fn("blocker", blocker)
    ref = ExecutorRef("callable", {"fn_name": "blocker", "_session_name": "sess1"})
    ctx = ExecutionContext(
        job_id="j", step_name="s", attempt=1,
        workspace_path=".", idempotency="idempotent",
    )

    async def scenario():
        task = asyncio.create_task(
            async_engine._run_executor("j", "s", "r", ref, {}, ctx)
        )
        ok = await asyncio.to_thread(started.wait, 2)
        assert ok, "executor never started"

        task.cancel()
        await asyncio.sleep(0.1)

        lock = async_engine._session_locks.get_lock("sess1")
        # The pool thread is still inside executor.start() — the session
        # lock must NOT have been released by the cancellation.
        assert lock.locked(), "session lock released while executor thread active"

        release.set()
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        assert not lock.locked(), "session lock never released"
        # _run_executor swallows CancelledError by design ("don't push
        # event") — just ensure the task terminated without error.
        assert task.done()
        assert task.cancelled() or task.exception() is None

    asyncio.run(scenario())


# ── F15: engine loop survives _poll_external_changes exceptions ──────────


def test_engine_loop_survives_poll_exception(async_engine, monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("poll exploded")

    monkeypatch.setattr(async_engine, "_poll_external_changes", boom)

    class FastTimeoutQueue:
        async def get(self):
            if len(calls) < 3:
                raise asyncio.TimeoutError  # force the idle/poll path
            await asyncio.Event().wait()  # then block forever

        async def put(self, item):  # pragma: no cover
            pass

    async_engine._queue = FastTimeoutQueue()

    async def scenario():
        task = asyncio.create_task(async_engine.run())
        await asyncio.sleep(0.3)
        assert len(calls) >= 3, "loop died after the first poll exception"
        assert not task.done(), "engine loop exited — jobs would strand"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


# ── F62: readiness scans memoize currency checks ─────────────────────────


def test_find_ready_query_count_is_linear(async_engine):
    register_step_fn("emit_v", lambda inputs: {"v": (inputs.get("v") or 0) + 1})

    n = 12
    steps = {}
    prev = None
    for i in range(n):
        name = f"s{i:02d}"
        inputs = [InputBinding("v", prev, "v")] if prev else []
        steps[name] = _simple_step(name, "emit_v", ["v"], inputs=inputs)
        prev = name
    wf = WorkflowDefinition(steps=steps)

    job = async_engine.create_job(objective="chain", workflow=wf)
    result = run_job_sync(async_engine, job.id, timeout=20)
    assert result.status == JobStatus.COMPLETED

    store = async_engine.store
    counts = {"n": 0}
    orig_latest, orig_load = store.latest_run, store.load_run

    def counting_latest(*a, **kw):
        counts["n"] += 1
        return orig_latest(*a, **kw)

    def counting_load(*a, **kw):
        counts["n"] += 1
        return orig_load(*a, **kw)

    store.latest_run = counting_latest
    store.load_run = counting_load
    try:
        job = store.load_job(job.id)
        ready = async_engine._find_ready(job)
    finally:
        store.latest_run = orig_latest
        store.load_run = orig_load

    assert ready == []
    # Memoized scan is O(n); the unmemoized recursion re-walked the chain
    # per step → ≥ n²/2 (~72+ for n=12) run queries.
    assert counts["n"] <= 5 * n, (
        f"readiness scan made {counts['n']} run queries for a {n}-step chain"
    )


# ── F63: _emit reuses a pre-loaded Job ───────────────────────────────────


def test_emit_skips_job_reload_when_job_passed(async_engine):
    register_step_fn("noop", lambda inputs: {"v": 1})
    wf = WorkflowDefinition(steps={"s": _simple_step("s", "noop", ["v"])})
    job = async_engine.create_job(objective="t", workflow=wf)

    store = async_engine.store
    loads = {"n": 0}
    orig_load_job = store.load_job

    def counting(job_id):
        loads["n"] += 1
        return orig_load_job(job_id)

    store.load_job = counting
    try:
        async_engine._emit(job.id, "test.event", {"k": 1}, job=job)
    finally:
        store.load_job = orig_load_job

    assert loads["n"] == 0, "_emit reloaded the job despite being passed one"
    events = async_engine.get_events(job.id)
    assert any(e.type == "test.event" and e.data == {"k": 1} for e in events)

    # Fallback path still works (no job passed)
    async_engine._emit(job.id, "test.event2")
    events = async_engine.get_events(job.id)
    assert any(e.type == "test.event2" for e in events)
