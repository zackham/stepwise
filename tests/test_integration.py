"""End-to-end integration tests: linear, fan-out/fan-in, loops, sub-jobs, watches."""

import asyncio

import pytest

from stepwise.engine import Engine
from stepwise.events import EventBus, EventType
from stepwise.executors import (
    ExecutorResult,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
    SubJobExecutor,
    ExecutorRegistry,
)
from stepwise.models import (
    InputBinding,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepStatus,
    WorkflowDefinition,
)
from stepwise.store import StepwiseStore


def _run_engine(workflow, inputs=None, store=None, extra_executors=None):
    """Create an engine with standard executors."""
    job = Job.create(workflow, inputs=inputs)
    eb = EventBus()
    engine = Engine(job, store=store, event_bus=eb)
    engine.register_executor("script", ScriptExecutor())
    engine.register_executor("mock_llm", MockLLMExecutor())
    if extra_executors:
        for name, ex in extra_executors.items():
            engine.register_executor(name, ex)
    return engine


# ── Linear Workflow ──────────────────────────────────────────────────


class TestLinearWorkflow:
    async def test_three_step_chain(self, linear_workflow):
        engine = _run_engine(linear_workflow)
        job = await engine.run()

        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["step_a"].outputs == {"value": 1}
        assert job.step_runs["step_b"].outputs == {"value": 11}  # 1 + 10
        assert job.step_runs["step_c"].outputs == {"value": 22}  # 11 * 2

    async def test_linear_with_job_inputs(self):
        wf = WorkflowDefinition(
            name="with_inputs",
            steps=[
                StepDefinition(
                    name="echo",
                    executor="script",
                    config={
                        "callable": lambda inputs: {
                            "msg": f"Hello {inputs.get('name', 'world')}"
                        }
                    },
                ),
            ],
        )
        engine = _run_engine(wf, inputs={"name": "Alice"})
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["echo"].outputs["msg"] == "Hello Alice"

    async def test_all_steps_executed_in_order(self, linear_workflow):
        eb = EventBus()
        executed = []
        eb.subscribe(
            EventType.STEP_COMPLETED,
            lambda e: executed.append(e.step_name),
        )
        engine = _run_engine(linear_workflow)
        engine._event_bus = eb
        await engine.run()
        assert executed == ["step_a", "step_b", "step_c"]


# ── Fan-out / Fan-in ────────────────────────────────────────────────


class TestFanOutFanIn:
    async def test_parallel_branches(self, fanout_workflow):
        engine = _run_engine(fanout_workflow)
        job = await engine.run()

        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["branch_1"].outputs["result"] == "hello_b1"
        assert job.step_runs["branch_2"].outputs["result"] == "hello_b2"
        assert job.step_runs["merge"].outputs["combined"] == "hello_b1,hello_b2"

    async def test_merge_waits_for_all_branches(self, fanout_workflow):
        eb = EventBus()
        engine = _run_engine(fanout_workflow)
        engine._event_bus = eb
        await engine.run()

        completed_names = [
            e.step_name
            for e in eb.history
            if e.event_type == EventType.STEP_COMPLETED
        ]
        merge_idx = completed_names.index("merge")
        assert "branch_1" in completed_names[:merge_idx]
        assert "branch_2" in completed_names[:merge_idx]


# ── Loops ────────────────────────────────────────────────────────────


class TestLoopWorkflow:
    async def test_loop_with_collection(self, loop_workflow):
        engine = _run_engine(loop_workflow)
        job = await engine.run()

        assert job.status == JobStatus.COMPLETED
        # process produces results=[{doubled:2}, {doubled:4}, {doubled:6}]
        # collect sums them: 2+4+6 = 12
        assert job.step_runs["collect"].outputs["total"] == 12

    async def test_loop_iteration_outputs(self, loop_workflow):
        engine = _run_engine(loop_workflow)
        await engine.run()

        proc = engine.job.step_runs["process"]
        assert proc.outputs["count"] == 3
        assert len(proc.outputs["results"]) == 3


# ── Sub-jobs ─────────────────────────────────────────────────────────


class TestSubJobs:
    async def test_nested_workflow(self):
        inner_wf = WorkflowDefinition(
            name="inner",
            steps=[
                StepDefinition(
                    name="inner_step",
                    executor="script",
                    config={
                        "callable": lambda inputs: {
                            "result": inputs.get("x", 0) * 100
                        }
                    },
                ),
            ],
        )

        outer_wf = WorkflowDefinition(
            name="outer",
            steps=[
                StepDefinition(
                    name="prepare",
                    executor="script",
                    config={"callable": lambda inputs: {"x": 5}},
                ),
                StepDefinition(
                    name="sub",
                    executor="sub_job",
                    depends_on=["prepare"],
                    inputs=[InputBinding("prepare", "x", "x")],
                    config={"workflow": inner_wf.to_dict()},
                ),
                StepDefinition(
                    name="finish",
                    executor="script",
                    depends_on=["sub"],
                    inputs=[InputBinding("sub", "inner_step", "sub_output")],
                    config={
                        "callable": lambda inputs: {
                            "final": inputs.get("sub_output", {}).get("result", 0) + 1
                        }
                    },
                ),
            ],
        )

        registry = ExecutorRegistry()
        registry.register("script", ScriptExecutor())
        registry.register("sub_job", SubJobExecutor(registry))

        engine = _run_engine(
            outer_wf,
            extra_executors={
                "sub_job": SubJobExecutor(registry),
            },
        )
        # Also register script for the sub-job executor
        job = await engine.run()

        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["sub"].status == StepStatus.COMPLETED
        # inner_step output: {result: 500} -> sub outputs: {inner_step: {result: 500}}
        assert job.step_runs["finish"].outputs["final"] == 501

    async def test_sub_job_failure(self):
        inner_wf = WorkflowDefinition(
            name="failing_inner",
            steps=[
                StepDefinition(
                    name="fail_step",
                    executor="script",
                    config={
                        "callable": lambda i: (_ for _ in ()).throw(
                            ValueError("inner fail")
                        )
                    },
                ),
            ],
        )

        outer_wf = WorkflowDefinition(
            name="outer",
            steps=[
                StepDefinition(
                    name="sub",
                    executor="sub_job",
                    config={"workflow": inner_wf.to_dict()},
                ),
            ],
        )

        registry = ExecutorRegistry()
        registry.register("script", ScriptExecutor())
        registry.register("sub_job", SubJobExecutor(registry))

        engine = _run_engine(
            outer_wf,
            extra_executors={"sub_job": SubJobExecutor(registry)},
        )
        job = await engine.run()
        assert job.status == JobStatus.FAILED


# ── Watches (Stale & Re-run) ────────────────────────────────────────


class TestWatches:
    async def test_watch_triggers_rerun(self):
        """Simulate a watch by marking a step stale and re-running."""
        counter = {"val": 0}

        def read_val(inputs):
            counter["val"] += 1
            return {"reading": counter["val"]}

        wf = WorkflowDefinition(
            name="watch",
            steps=[
                StepDefinition(
                    name="sensor",
                    executor="script",
                    config={"callable": read_val},
                ),
                StepDefinition(
                    name="react",
                    executor="script",
                    depends_on=["sensor"],
                    inputs=[InputBinding("sensor", "reading", "reading")],
                    config={
                        "callable": lambda i: {
                            "processed": i["reading"] * 10
                        }
                    },
                ),
            ],
        )

        engine = _run_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["sensor"].outputs["reading"] == 1
        assert job.step_runs["react"].outputs["processed"] == 10

        # Simulate watch trigger: mark sensor as stale
        engine.mark_stale("sensor")
        assert job.step_runs["sensor"].status == StepStatus.PENDING
        assert job.step_runs["react"].status == StepStatus.PENDING

        # Re-run
        job = await engine.run()
        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["sensor"].outputs["reading"] == 2
        assert job.step_runs["react"].outputs["processed"] == 20

    async def test_watch_only_reruns_stale(self):
        """Only the stale step and its dependents re-run."""
        calls = {"a": 0, "b": 0, "c": 0}

        def track(name):
            def fn(inputs):
                calls[name] += 1
                return {"v": calls[name]}
            return fn

        wf = WorkflowDefinition(
            name="selective",
            steps=[
                StepDefinition(name="a", executor="script", config={"callable": track("a")}),
                StepDefinition(name="b", executor="script", config={"callable": track("b")}),
                StepDefinition(
                    name="c",
                    executor="script",
                    depends_on=["a"],
                    config={"callable": track("c")},
                ),
            ],
        )

        engine = _run_engine(wf)
        await engine.run()
        assert calls == {"a": 1, "b": 1, "c": 1}

        # Mark only 'a' stale -> 'c' should also re-run, but 'b' should not
        engine.mark_stale("a")
        await engine.run()
        assert calls == {"a": 2, "b": 1, "c": 2}


# ── Persistence Integration ─────────────────────────────────────────


class TestPersistenceIntegration:
    async def test_engine_persists_state(self):
        store = StepwiseStore(":memory:")
        wf = WorkflowDefinition(
            name="persist",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
                StepDefinition(
                    name="b",
                    executor="script",
                    depends_on=["a"],
                    inputs=[InputBinding("a", "v", "input_v")],
                    config={"callable": lambda i: {"v": i["input_v"] + 1}},
                ),
            ],
        )

        job = Job.create(wf)
        engine = Engine(job, store=store, event_bus=EventBus())
        engine.register_executor("script", ScriptExecutor())
        await engine.run()

        # Verify persisted
        loaded = store.load_job(job.id)
        assert loaded is not None
        assert loaded.status == JobStatus.COMPLETED
        assert loaded.step_runs["a"].status == StepStatus.COMPLETED
        assert loaded.step_runs["b"].status == StepStatus.COMPLETED

        # Events persisted
        events = store.load_events(job.id)
        assert len(events) > 0
        store.close()

    async def test_crash_recovery_and_resume(self):
        """Simulate crash by saving partial state, recovering, and resuming."""
        store = StepwiseStore(":memory:")
        wf = WorkflowDefinition(
            name="recover",
            steps=[
                StepDefinition(name="a", executor="script", config={"command": "echo v1"}),
                StepDefinition(
                    name="b",
                    executor="script",
                    depends_on=["a"],
                    config={"command": "echo v2"},
                ),
            ],
        )

        # Simulate: step a completed, step b was running when crash happened
        job = Job.create(wf)
        job.status = JobStatus.RUNNING
        job.step_runs["a"].status = StepStatus.COMPLETED
        job.step_runs["a"].outputs = {"v": 1}
        job.step_runs["b"].status = StepStatus.RUNNING
        store.save_job(job)

        # Recover
        recovered = store.recover_job(job.id)
        assert recovered.step_runs["a"].status == StepStatus.COMPLETED
        assert recovered.step_runs["b"].status == StepStatus.PENDING

        # Resume
        engine = Engine(recovered, store=store, event_bus=EventBus())
        engine.register_executor("script", ScriptExecutor())
        result = await engine.run()

        assert result.status == JobStatus.COMPLETED
        assert result.step_runs["b"].status == StepStatus.COMPLETED
        store.close()


# ── Complex Workflow ─────────────────────────────────────────────────


class TestComplexWorkflow:
    async def test_combined_patterns(self):
        """A workflow combining linear, fan-out, and loop patterns."""
        wf = WorkflowDefinition(
            name="complex",
            steps=[
                # Step 1: Generate data
                StepDefinition(
                    name="init",
                    executor="script",
                    config={"callable": lambda i: {"data": "hello", "items": [1, 2]}},
                ),
                # Step 2a: Process data (branch 1)
                StepDefinition(
                    name="upper",
                    executor="script",
                    depends_on=["init"],
                    inputs=[InputBinding("init", "data", "data")],
                    config={"callable": lambda i: {"result": i["data"].upper()}},
                ),
                # Step 2b: Loop over items (branch 2)
                StepDefinition(
                    name="double",
                    executor="script",
                    depends_on=["init"],
                    loop_over="init.items",
                    config={"callable": lambda i: {"doubled": i["item"] * 2}},
                ),
                # Step 3: Merge all
                StepDefinition(
                    name="merge",
                    executor="script",
                    depends_on=["upper", "double"],
                    inputs=[
                        InputBinding("upper", "result", "upper_result"),
                        InputBinding("double", "results", "doubled_results"),
                    ],
                    config={
                        "callable": lambda i: {
                            "summary": f"{i['upper_result']}: {i['doubled_results']}"
                        }
                    },
                ),
            ],
        )

        engine = _run_engine(wf)
        job = await engine.run()

        assert job.status == JobStatus.COMPLETED
        assert job.step_runs["upper"].outputs["result"] == "HELLO"
        assert job.step_runs["double"].outputs["results"] == [
            {"doubled": 2},
            {"doubled": 4},
        ]
        merge_output = job.step_runs["merge"].outputs["summary"]
        assert "HELLO" in merge_output

    async def test_error_in_branch_fails_job(self):
        wf = WorkflowDefinition(
            name="error_branch",
            steps=[
                StepDefinition(
                    name="ok",
                    executor="script",
                    config={"callable": lambda i: {"v": 1}},
                ),
                StepDefinition(
                    name="fail",
                    executor="script",
                    config={
                        "callable": lambda i: (_ for _ in ()).throw(
                            ValueError("branch error")
                        )
                    },
                ),
                StepDefinition(
                    name="merge",
                    executor="script",
                    depends_on=["ok", "fail"],
                    config={"callable": lambda i: {"v": 1}},
                ),
            ],
        )

        engine = _run_engine(wf)
        job = await engine.run()
        assert job.status == JobStatus.FAILED
        assert job.step_runs["merge"].status != StepStatus.COMPLETED
