"""Tests for engine: readiness, currentness, loops, completion, exit resolution."""

import asyncio

import pytest

from stepwise.engine import Engine
from stepwise.events import EventBus, EventType
from stepwise.executors import ExecutorResult, MockLLMExecutor, ScriptExecutor
from stepwise.models import (
    InputBinding,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepStatus,
    WorkflowDefinition,
)


def _make_engine(workflow, inputs=None, store=None, event_bus=None):
    job = Job.create(workflow, inputs=inputs)
    eb = event_bus or EventBus()
    engine = Engine(job, store=store, event_bus=eb)
    engine.register_executor("script", ScriptExecutor())
    engine.register_executor("mock_llm", MockLLMExecutor())
    return engine


# ── Readiness ────────────────────────────────────────────────────────


class TestReadiness:
    async def test_no_deps_ready_immediately(self):
        wf = WorkflowDefinition(
            name="simple",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )
        engine = _make_engine(wf)
        ready = engine._find_ready_steps()
        assert "a" in ready

    async def test_dep_not_met(self):
        wf = WorkflowDefinition(
            name="chain",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {}}),
                StepDefinition(name="b", executor="script", depends_on=["a"], config={"callable": lambda i: {}}),
            ],
        )
        engine = _make_engine(wf)
        ready = engine._find_ready_steps()
        assert "a" in ready
        assert "b" not in ready

    async def test_dep_met_after_completion(self):
        wf = WorkflowDefinition(
            name="chain",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {"v": 1}}),
                StepDefinition(name="b", executor="script", depends_on=["a"], config={"callable": lambda i: {}}),
            ],
        )
        engine = _make_engine(wf)
        # Manually complete step a
        engine.job.step_runs["a"].status = StepStatus.COMPLETED
        engine.job.step_runs["a"].outputs = {"v": 1}
        ready = engine._find_ready_steps()
        assert "b" in ready

    async def test_input_binding_dep(self):
        wf = WorkflowDefinition(
            name="binding",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {"x": 1}}),
                StepDefinition(
                    name="b",
                    executor="script",
                    inputs=[InputBinding("a", "x", "input_x")],
                    config={"callable": lambda i: {}},
                ),
            ],
        )
        engine = _make_engine(wf)
        ready = engine._find_ready_steps()
        assert "b" not in ready  # a hasn't completed yet

    async def test_multiple_deps(self):
        wf = WorkflowDefinition(
            name="multi",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {}}),
                StepDefinition(name="b", executor="script", config={"callable": lambda i: {}}),
                StepDefinition(name="c", executor="script", depends_on=["a", "b"], config={"callable": lambda i: {}}),
            ],
        )
        engine = _make_engine(wf)
        # Only a completed
        engine.job.step_runs["a"].status = StepStatus.COMPLETED
        engine.job.step_runs["a"].outputs = {}
        assert "c" not in engine._find_ready_steps()
        # Both completed
        engine.job.step_runs["b"].status = StepStatus.COMPLETED
        engine.job.step_runs["b"].outputs = {}
        assert "c" in engine._find_ready_steps()


# ── Currentness ──────────────────────────────────────────────────────


class TestCurrentness:
    async def test_completed_step_is_current(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script", config={"callable": lambda i: {"v": 1}})],
        )
        engine = _make_engine(wf, inputs={"x": 1})
        sr = engine.job.step_runs["a"]
        sr.status = StepStatus.COMPLETED
        sr.inputs = {"x": 1}
        sr.input_hash = sr.compute_input_hash()
        sr.outputs = {"v": 1}
        assert engine.is_current("a")

    async def test_stale_step_not_current(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script", config={"callable": lambda i: {}})],
        )
        engine = _make_engine(wf, inputs={"x": 1})
        sr = engine.job.step_runs["a"]
        sr.status = StepStatus.COMPLETED
        sr.inputs = {"x": 99}  # Different from job inputs
        sr.input_hash = sr.compute_input_hash()
        assert not engine.is_current("a")

    async def test_pending_not_current(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script", config={"callable": lambda i: {}})],
        )
        engine = _make_engine(wf)
        assert not engine.is_current("a")

    async def test_get_current_steps(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {}}),
                StepDefinition(name="b", executor="script", config={"callable": lambda i: {}}),
            ],
        )
        engine = _make_engine(wf)
        engine.job.step_runs["a"].status = StepStatus.RUNNING
        engine.job.step_runs["b"].status = StepStatus.READY
        current = engine.get_current_steps()
        names = {s.step_name for s in current}
        assert names == {"a", "b"}


# ── Loops ────────────────────────────────────────────────────────────


class TestLoops:
    async def test_loop_execution(self):
        wf = WorkflowDefinition(
            name="loop",
            steps=[
                StepDefinition(
                    name="gen",
                    executor="script",
                    config={"callable": lambda i: {"items": [10, 20, 30]}},
                ),
                StepDefinition(
                    name="process",
                    executor="script",
                    config={"callable": lambda i: {"doubled": i["item"] * 2}},
                    depends_on=["gen"],
                    loop_over="gen.items",
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        proc_sr = job.step_runs["process"]
        assert proc_sr.status == StepStatus.COMPLETED
        assert proc_sr.outputs["results"] == [
            {"doubled": 20},
            {"doubled": 40},
            {"doubled": 60},
        ]
        assert proc_sr.outputs["count"] == 3

    async def test_loop_creates_iteration_runs(self):
        wf = WorkflowDefinition(
            name="loop",
            steps=[
                StepDefinition(
                    name="gen",
                    executor="script",
                    config={"callable": lambda i: {"items": ["a", "b"]}},
                ),
                StepDefinition(
                    name="proc",
                    executor="script",
                    config={"callable": lambda i: {"val": i["item"]}},
                    depends_on=["gen"],
                    loop_over="gen.items",
                ),
            ],
        )
        engine = _make_engine(wf)
        await engine.run()
        assert "proc:0" in engine.job.step_runs
        assert "proc:1" in engine.job.step_runs
        assert engine.job.step_runs["proc:0"].iteration_value == "a"
        assert engine.job.step_runs["proc:1"].iteration_value == "b"

    async def test_empty_loop(self):
        wf = WorkflowDefinition(
            name="empty_loop",
            steps=[
                StepDefinition(
                    name="gen",
                    executor="script",
                    config={"callable": lambda i: {"items": []}},
                ),
                StepDefinition(
                    name="proc",
                    executor="script",
                    config={"callable": lambda i: {"val": i["item"]}},
                    depends_on=["gen"],
                    loop_over="gen.items",
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["proc"].outputs == {"results": [], "count": 0}

    async def test_loop_failure_propagates(self):
        def fail_on_b(inputs):
            if inputs["item"] == "b":
                raise ValueError("bad item")
            return {"val": inputs["item"]}

        wf = WorkflowDefinition(
            name="fail_loop",
            steps=[
                StepDefinition(
                    name="gen",
                    executor="script",
                    config={"callable": lambda i: {"items": ["a", "b", "c"]}},
                ),
                StepDefinition(
                    name="proc",
                    executor="script",
                    config={"callable": fail_on_b},
                    depends_on=["gen"],
                    loop_over="gen.items",
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.FAILED


# ── Completion & Exit Resolution ─────────────────────────────────────


class TestCompletion:
    async def test_single_step_completes(self):
        wf = WorkflowDefinition(
            name="single",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"result": 42}},
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.outputs["a"]["result"] == 42

    async def test_failure_propagates(self):
        wf = WorkflowDefinition(
            name="fail",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: (_ for _ in ()).throw(ValueError("boom"))},
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.FAILED

    async def test_partial_failure(self):
        wf = WorkflowDefinition(
            name="partial",
            steps=[
                StepDefinition(
                    name="ok",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
                StepDefinition(
                    name="fail",
                    executor="script",
                    config={"callable": lambda i: (_ for _ in ()).throw(ValueError("nope"))},
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.FAILED
        assert "fail" in job.outputs["error"]
        assert "ok" in job.outputs["step_outputs"]

    async def test_conditional_skip(self):
        wf = WorkflowDefinition(
            name="conditional",
            steps=[
                StepDefinition(
                    name="check",
                    executor="script",
                    config={"callable": lambda i: {"flag": False}},
                ),
                StepDefinition(
                    name="maybe",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                    depends_on=["check"],
                    condition="steps.get('check', {}).get('flag', False)",
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["maybe"].status == StepStatus.SKIPPED

    async def test_deadlock_detection(self):
        """A step waiting on a failed dependency should cause job failure."""
        wf = WorkflowDefinition(
            name="deadlock",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: (_ for _ in ()).throw(ValueError("fail"))},
                ),
                StepDefinition(
                    name="b",
                    executor="script",
                    depends_on=["a"],
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.FAILED


# ── Mark Stale & Re-run ──────────────────────────────────────────────


class TestMarkStale:
    async def test_mark_stale_resets_step(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {"v": 1}}),
            ],
        )
        engine = _make_engine(wf)
        job = await engine.run()
        assert job.step_runs["a"].status == StepStatus.COMPLETED

        engine.mark_stale("a")
        assert job.step_runs["a"].status == StepStatus.PENDING
        assert job.step_runs["a"].outputs is None

    async def test_mark_stale_cascades(self):
        wf = WorkflowDefinition(
            name="chain",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": lambda i: {"v": 1}}),
                StepDefinition(
                    name="b",
                    executor="script",
                    depends_on=["a"],
                    config={"callable": lambda i: {"v": 2}},
                ),
                StepDefinition(
                    name="c",
                    executor="script",
                    depends_on=["b"],
                    config={"callable": lambda i: {"v": 3}},
                ),
            ],
        )
        engine = _make_engine(wf)
        await engine.run()

        engine.mark_stale("a")
        assert engine.job.step_runs["a"].status == StepStatus.PENDING
        assert engine.job.step_runs["b"].status == StepStatus.PENDING
        assert engine.job.step_runs["c"].status == StepStatus.PENDING

    async def test_rerun_after_stale(self):
        counter = {"calls": 0}

        def counting_fn(inputs):
            counter["calls"] += 1
            return {"call_num": counter["calls"]}

        wf = WorkflowDefinition(
            name="rerun",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": counting_fn},
                ),
            ],
        )
        engine = _make_engine(wf)
        await engine.run()
        assert counter["calls"] == 1

        engine.mark_stale("a")
        await engine.run()
        assert counter["calls"] == 2
        assert engine.job.step_runs["a"].outputs["call_num"] == 2


# ── Tick-by-tick Control ─────────────────────────────────────────────


class TestTick:
    async def test_single_tick_launches(self):
        wf = WorkflowDefinition(
            name="tick",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )
        engine = _make_engine(wf)
        engine.job.status = JobStatus.RUNNING

        active = await engine.tick()
        # Step should be launched (task created)
        assert "a" in engine._tasks or engine.job.step_runs["a"].status in (
            StepStatus.RUNNING,
            StepStatus.COMPLETED,
        )

    async def test_tick_returns_false_when_done(self):
        wf = WorkflowDefinition(
            name="done",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )
        engine = _make_engine(wf)
        engine.job.status = JobStatus.COMPLETED
        assert not await engine.tick()


# ── Events ───────────────────────────────────────────────────────────


class TestEngineEvents:
    async def test_events_emitted(self):
        wf = WorkflowDefinition(
            name="events",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )
        eb = EventBus()
        engine = _make_engine(wf, event_bus=eb)
        await engine.run()

        types = [e.event_type for e in eb.history]
        assert EventType.JOB_STARTED in types
        assert EventType.STEP_STARTED in types
        assert EventType.STEP_COMPLETED in types
        assert EventType.JOB_COMPLETED in types

    async def test_failure_events(self):
        wf = WorkflowDefinition(
            name="fail_events",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: (_ for _ in ()).throw(ValueError("x"))},
                ),
            ],
        )
        eb = EventBus()
        engine = _make_engine(wf, event_bus=eb)
        await engine.run()

        types = [e.event_type for e in eb.history]
        assert EventType.STEP_FAILED in types
        assert EventType.JOB_FAILED in types
