"""Tests for AsyncEngine: event-driven execution, parallel dispatch, chain reactions."""

import asyncio
import time

import pytest

from tests.conftest import register_step_fn, run_job, run_job_sync
from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)


# ── Helpers ──────────────────────────────────────────────────────────


def single_step_wf(fn_name: str, outputs: list[str] | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=outputs or ["result"],
        ),
    })


def linear_chain_wf(fn_name: str, n: int) -> WorkflowDefinition:
    """A→B→C→... chain of n steps, each passing 'x' forward."""
    steps = {}
    for i in range(n):
        name = f"step-{i}"
        inputs = []
        if i > 0:
            prev = f"step-{i - 1}"
            inputs = [InputBinding("x", prev, "x")]
        steps[name] = StepDefinition(
            name=name,
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            inputs=inputs,
            outputs=["x"],
        )
    return WorkflowDefinition(steps=steps)


def parallel_wf(fn_name: str, n: int) -> WorkflowDefinition:
    """N independent steps, all running in parallel."""
    steps = {}
    for i in range(n):
        name = f"step-{i}"
        steps[name] = StepDefinition(
            name=name,
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["ok"],
        )
    return WorkflowDefinition(steps=steps)


def _cj(engine, wf, objective="test", **kwargs):
    """Shorthand for create_job with keyword args."""
    return engine.create_job(objective=objective, workflow=wf, **kwargs)


# ── Basic execution ──────────────────────────────────────────────────


class TestAsyncBasicExecution:
    def test_single_step_completes(self, async_engine):
        register_step_fn("pass", lambda inputs: {"result": "done"})
        job = _cj(async_engine, single_step_wf("pass"))
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert len(runs) == 1
        assert runs[0].status == StepRunStatus.COMPLETED
        assert runs[0].result.artifact["result"] == "done"

    def test_linear_chain_completes(self, async_engine):
        register_step_fn("inc", lambda inputs: {"x": (inputs.get("x") or 0) + 1})
        wf = linear_chain_wf("inc", 5)
        job = _cj(async_engine, wf)
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert len(runs) == 5
        last = async_engine.store.latest_completed_run(job.id, "step-4")
        assert last.result.artifact["x"] == 5

    def test_job_inputs_passed_through(self, async_engine):
        register_step_fn("echo", lambda inputs: {"result": inputs["name"]})
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "echo"}),
                inputs=[InputBinding("name", "$job", "name")],
                outputs=["result"],
            ),
        })
        job = _cj(async_engine, wf, inputs={"name": "world"})
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert runs[0].result.artifact["result"] == "world"

    def test_executor_failure_fails_job(self, async_engine):
        register_step_fn("boom", lambda inputs: (_ for _ in ()).throw(RuntimeError("kaboom")))
        job = _cj(async_engine, single_step_wf("boom"))
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_job(job.id)
        assert runs[0].status == StepRunStatus.FAILED
        assert "kaboom" in runs[0].error


# ── Parallel execution ───────────────────────────────────────────────


class TestAsyncParallelExecution:
    def test_independent_steps_run_concurrently(self, async_engine):
        """3 independent steps each sleeping 0.3s — should complete in ~0.3s, not ~0.9s."""
        register_step_fn("slow", lambda inputs: (time.sleep(0.3), {"ok": True})[1])
        wf = parallel_wf("slow", 3)
        job = _cj(async_engine, wf)
        start = time.time()
        job = run_job_sync(async_engine, job.id)
        elapsed = time.time() - start
        assert job.status == JobStatus.COMPLETED
        assert elapsed < 1.5  # generous; serial would be ~0.9s

    def test_multiple_jobs_concurrent(self, async_engine):
        """5 jobs each with one slow step — all complete concurrently."""
        register_step_fn("slow", lambda inputs: (time.sleep(0.3), {"result": "ok"})[1])

        async def _run():
            engine_task = asyncio.create_task(async_engine.run())
            jobs = []
            for i in range(5):
                j = _cj(async_engine, single_step_wf("slow"), objective=f"test-{i}")
                jobs.append(j)

            start = time.time()
            for j in jobs:
                async_engine.start_job(j.id)
            await asyncio.gather(*[async_engine.wait_for_job(j.id) for j in jobs])
            elapsed = time.time() - start

            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

            for j in jobs:
                loaded = async_engine.store.load_job(j.id)
                assert loaded.status == JobStatus.COMPLETED
            assert elapsed < 2.0  # 5 × 0.3s parallel ≈ 0.3s

        asyncio.run(_run())


# ── Chain reaction (no polling delay) ────────────────────────────────


class TestAsyncChainReaction:
    def test_chain_completes_fast(self, async_engine):
        """A→B→C→D→E with instant steps — completes quickly, no tick delays."""
        register_step_fn("pass", lambda inputs: {"x": 1})
        wf = linear_chain_wf("pass", 5)
        job = _cj(async_engine, wf)
        start = time.time()
        job = run_job_sync(async_engine, job.id)
        elapsed = time.time() - start
        assert job.status == JobStatus.COMPLETED
        assert elapsed < 2.0


# ── Watch / suspend / fulfill ────────────────────────────────────────


class TestAsyncWatchFulfill:
    def test_external_step_suspends_then_fulfills(self, async_engine):
        """External step suspends; fulfill triggers next steps."""
        register_step_fn("use", lambda inputs: {"result": inputs["answer"]})
        wf = WorkflowDefinition(steps={
            "ask": StepDefinition(
                name="ask",
                executor=ExecutorRef(type="external", config={"prompt": "What?"}),
                outputs=["answer"],
            ),
            "use": StepDefinition(
                name="use",
                executor=ExecutorRef(type="callable", config={"fn_name": "use"}),
                inputs=[InputBinding("answer", "ask", "answer")],
                outputs=["result"],
            ),
        })

        async def _run():
            engine_task = asyncio.create_task(async_engine.run())
            job = _cj(async_engine, wf)
            async_engine.start_job(job.id)

            # Wait for step to suspend
            await asyncio.sleep(0.1)
            suspended = async_engine.store.suspended_runs(job.id)
            assert len(suspended) == 1
            assert suspended[0].step_name == "ask"

            # Fulfill
            async_engine.fulfill_watch(suspended[0].id, {"answer": "42"})

            job = await asyncio.wait_for(async_engine.wait_for_job(job.id), 5)
            assert job.status == JobStatus.COMPLETED

            use_run = async_engine.store.latest_completed_run(job.id, "use")
            assert use_run.result.artifact["result"] == "42"

            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


# ── Cancellation ─────────────────────────────────────────────────────


class TestAsyncCancellation:
    def test_cancel_running_job(self, async_engine):
        """Cancel a job while a step is running in the thread pool."""
        cancel_event = __import__("threading").Event()

        def hang(inputs):
            cancel_event.wait(timeout=60)
            return {"ok": True}

        register_step_fn("hang", hang)
        wf = single_step_wf("hang")

        async def _run():
            engine_task = asyncio.create_task(async_engine.run())
            job = _cj(async_engine, wf)
            async_engine.start_job(job.id)

            await asyncio.sleep(0.1)  # let step dispatch
            async_engine.cancel_job(job.id)

            job = async_engine.store.load_job(job.id)
            assert job.status == JobStatus.CANCELLED

            # Unblock the thread so the task can finish
            cancel_event.set()
            await asyncio.sleep(0.1)

            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


# ── Exit rules ───────────────────────────────────────────────────────


class TestAsyncExitRules:
    def test_loop_exit_rule(self, async_engine):
        """Exit rule loop re-launches step."""
        call_count = 0

        def counting_fn(inputs):
            nonlocal call_count
            call_count += 1
            return {"quality": 0.5 if call_count < 3 else 0.9}

        register_step_fn("improve", counting_fn)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "improve"}),
                outputs=["quality"],
                exit_rules=[
                    ExitRule(
                        name="good",
                        type="expression",
                        config={"condition": "float(outputs.quality) >= 0.8", "action": "advance"},
                        priority=10,
                    ),
                    ExitRule(
                        name="retry",
                        type="expression",
                        config={"condition": "attempt < 5", "action": "loop", "target": "step-a"},
                        priority=1,
                    ),
                ],
            ),
        })

        job = _cj(async_engine, wf)
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.COMPLETED
        assert call_count == 3
        runs = async_engine.store.runs_for_job(job.id)
        completed = [r for r in runs if r.status == StepRunStatus.COMPLETED]
        assert len(completed) == 3

    def test_abandon_exit_rule(self, async_engine):
        """Exit rule abandon fails the job."""
        register_step_fn("bad", lambda inputs: {"status": "bad"})

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "bad"}),
                outputs=["status"],
                exit_rules=[
                    ExitRule(
                        name="abort",
                        type="field_match",
                        config={"field": "status", "value": "bad", "action": "abandon"},
                    ),
                ],
            ),
        })

        job = _cj(async_engine, wf)
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.FAILED


# ── Broadcast callback ───────────────────────────────────────────────


class TestAsyncBroadcast:
    def test_broadcast_callback_fires(self, async_engine):
        """Engine calls on_broadcast on job state changes."""
        register_step_fn("pass", lambda inputs: {"result": "ok"})

        events = []
        async_engine.on_broadcast = lambda e: events.append(e)

        job = _cj(async_engine, single_step_wf("pass"))
        job = run_job_sync(async_engine, job.id)
        assert job.status == JobStatus.COMPLETED
        assert any(e.get("type") == "job_changed" for e in events)
