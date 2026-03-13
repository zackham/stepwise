"""Tests for engine: readiness, currentness, loops, completion, exit resolution.

Covers required test cases 1-9, 19-24.
"""

import pytest

from tests.conftest import CallableExecutor, register_step_fn
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutionContext,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
)
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    SubJobDefinition,
    WatchSpec,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


def make_engine():
    """Create a fresh store + registry + engine for tests."""
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("script", lambda config: ScriptExecutor(command=config.get("command", "echo '{}'")))
    reg.register("human", lambda config: HumanExecutor(prompt=config.get("prompt", "")))
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        responses=config.get("responses"),
    ))
    return Engine(store=store, registry=reg)


# ── Test 1: Linear workflow A → B → C ────────────────────────────────


class TestLinearWorkflow:
    def test_linear_a_b_c(self):
        register_step_fn("a_fn", lambda inputs: {"value": 1})
        register_step_fn("b_fn", lambda inputs: {"value": inputs["a_value"] + 10})
        register_step_fn("c_fn", lambda inputs: {"value": inputs["b_value"] * 2})

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "a_fn"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "b_fn"}),
                inputs=[InputBinding("a_value", "a", "value")],
            ),
            "c": StepDefinition(
                name="c", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "c_fn"}),
                inputs=[InputBinding("b_value", "b", "value")],
            ),
        })

        job = engine.create_job("Linear test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        c_run = [r for r in runs if r.step_name == "c"][0]
        assert c_run.result.artifact["value"] == 22  # (1 + 10) * 2


# ── Test 2: Fan-out/fan-in ───────────────────────────────────────────


class TestFanOutFanIn:
    def test_fan_out_fan_in(self):
        register_step_fn("topic_fn", lambda inputs: {"topic": "workflow engines"})
        register_step_fn("hist_fn", lambda inputs: {"findings": f"history of {inputs['topic']}"})
        register_step_fn("prior_fn", lambda inputs: {"findings": f"prior art in {inputs['topic']}"})
        register_step_fn("best_fn", lambda inputs: {"findings": f"best practices for {inputs['topic']}"})
        register_step_fn("synth_fn", lambda inputs: {
            "synthesis": f"{inputs['history']} | {inputs['prior_art']} | {inputs['best']}"
        })

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["topic"],
                executor=ExecutorRef("callable", {"fn_name": "topic_fn"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["findings"],
                executor=ExecutorRef("callable", {"fn_name": "hist_fn"}),
                inputs=[InputBinding("topic", "a", "topic")],
            ),
            "c": StepDefinition(
                name="c", outputs=["findings"],
                executor=ExecutorRef("callable", {"fn_name": "prior_fn"}),
                inputs=[InputBinding("topic", "a", "topic")],
            ),
            "d": StepDefinition(
                name="d", outputs=["findings"],
                executor=ExecutorRef("callable", {"fn_name": "best_fn"}),
                inputs=[InputBinding("topic", "a", "topic")],
            ),
            "e": StepDefinition(
                name="e", outputs=["synthesis"],
                executor=ExecutorRef("callable", {"fn_name": "synth_fn"}),
                inputs=[
                    InputBinding("history", "b", "findings"),
                    InputBinding("prior_art", "c", "findings"),
                    InputBinding("best", "d", "findings"),
                ],
            ),
        })

        job = engine.create_job("Fan-out test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        e_run = [r for r in engine.get_runs(job.id) if r.step_name == "e"][0]
        assert "history of workflow engines" in e_run.result.artifact["synthesis"]
        assert "prior art in workflow engines" in e_run.result.artifact["synthesis"]
        assert "best practices for workflow engines" in e_run.result.artifact["synthesis"]


# ── Test 3: Job-level inputs ─────────────────────────────────────────


class TestJobLevelInputs:
    def test_entry_step_binds_from_job(self):
        register_step_fn("analyze_fn", lambda inputs: {
            "plan": f"plan for {inputs['requirements']}"
        })

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "analyze": StepDefinition(
                name="analyze", outputs=["plan"],
                executor=ExecutorRef("callable", {"fn_name": "analyze_fn"}),
                inputs=[
                    InputBinding("requirements", "$job", "requirements"),
                    InputBinding("repo", "$job", "repo_path"),
                ],
            ),
        })

        job = engine.create_job(
            "Job inputs test", w,
            inputs={"requirements": "add avatars", "repo_path": "/code"},
        )
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        assert runs[0].inputs["requirements"] == "add avatars"
        assert runs[0].inputs["repo"] == "/code"
        assert runs[0].result.artifact["plan"] == "plan for add avatars"


# ── Test 4: Loop A → B → (loop back to A, max 3) ────────────────────


class TestLoopBasic:
    def test_loop_with_max_iterations(self):
        call_count = {"a": 0, "b": 0}

        def a_fn(inputs):
            call_count["a"] += 1
            return {"value": call_count["a"]}

        def b_fn(inputs):
            call_count["b"] += 1
            return {"pass": call_count["b"] >= 3, "result": inputs["data"]}

        register_step_fn("loop_a", a_fn)
        register_step_fn("loop_b", b_fn)

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "loop_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["pass", "result"],
                executor=ExecutorRef("callable", {"fn_name": "loop_b"}),
                inputs=[InputBinding("data", "a", "value")],
                exit_rules=[
                    ExitRule("pass", "field_match", {
                        "field": "pass", "value": True, "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "field_match", {
                        "field": "pass", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("Loop test", w)
        engine.start_job(job.id)

        for _ in range(20):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED
        assert call_count["a"] == 3
        assert call_count["b"] == 3


# ── Test 5: Transitive currentness ───────────────────────────────────


class TestTransitiveCurrentness:
    def test_a_loops_b_c_must_rerun_before_d(self):
        """A → B, A → C, B+C → D. A loops. D must wait for both B and C."""
        a_count = {"n": 0}
        d_inputs_log = []

        def a_fn(inputs):
            a_count["n"] += 1
            return {"value": a_count["n"]}

        def b_fn(inputs):
            return {"result": f"b_got_{inputs['a_val']}"}

        def c_fn(inputs):
            return {"result": f"c_got_{inputs['a_val']}"}

        def d_fn(inputs):
            d_inputs_log.append(dict(inputs))
            return {"done": a_count["n"] >= 2, "combined": f"{inputs['b_val']}+{inputs['c_val']}"}

        register_step_fn("tc_a", a_fn)
        register_step_fn("tc_b", b_fn)
        register_step_fn("tc_c", c_fn)
        register_step_fn("tc_d", d_fn)

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "tc_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "tc_b"}),
                inputs=[InputBinding("a_val", "a", "value")],
            ),
            "c": StepDefinition(
                name="c", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "tc_c"}),
                inputs=[InputBinding("a_val", "a", "value")],
            ),
            "d": StepDefinition(
                name="d", outputs=["done", "combined"],
                executor=ExecutorRef("callable", {"fn_name": "tc_d"}),
                inputs=[
                    InputBinding("b_val", "b", "result"),
                    InputBinding("c_val", "c", "result"),
                ],
                exit_rules=[
                    ExitRule("done", "field_match", {
                        "field": "done", "value": True, "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "field_match", {
                        "field": "done", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("Transitive test", w)
        engine.start_job(job.id)

        for _ in range(30):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # D should have run twice
        assert len(d_inputs_log) >= 2
        last_d = d_inputs_log[-1]
        assert "b_got_2" in last_d["b_val"]
        assert "c_got_2" in last_d["c_val"]


# ── Test 6: Loop does not prematurely complete job ───────────────────


class TestLoopNoPrematureCompletion:
    def test_terminal_step_loops_job_stays_running(self):
        count = {"n": 0}

        def a_fn(inputs):
            count["n"] += 1
            return {"value": count["n"]}

        def b_fn(inputs):
            return {"pass": inputs["data"] >= 3, "result": inputs["data"]}

        register_step_fn("npc_a", a_fn)
        register_step_fn("npc_b", b_fn)

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "npc_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["pass", "result"],
                executor=ExecutorRef("callable", {"fn_name": "npc_b"}),
                inputs=[InputBinding("data", "a", "value")],
                exit_rules=[
                    ExitRule("pass", "field_match", {
                        "field": "pass", "value": True, "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "field_match", {
                        "field": "pass", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 10,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("No premature completion", w)
        engine.start_job(job.id)

        for _ in range(30):
            job = engine.get_job(job.id)
            if job.status == JobStatus.COMPLETED:
                break
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        assert job.status == JobStatus.COMPLETED
        assert count["n"] == 3


# ── Test 7: In-flight supersession ───────────────────────────────────


class TestInflightSupersession:
    def test_running_run_supersedes_completed(self):
        """A2 running means A1 is not current."""
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "provide value"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "echo_fn"}),
                inputs=[InputBinding("data", "a", "value")],
            ),
        })

        register_step_fn("echo_fn", lambda inputs: {"result": inputs.get("data", "")})

        job = engine.create_job("Supersession test", w)
        engine.start_job(job.id)

        # A is suspended (human watch)
        runs = engine.get_runs(job.id, "a")
        assert len(runs) == 1
        a1 = runs[0]
        assert a1.status == StepRunStatus.SUSPENDED

        # Fulfill A1
        engine.fulfill_watch(a1.id, {"value": "first"})

        # Tick — B should run
        engine.tick()
        job = engine.get_job(job.id)

        # Rerun A
        a2 = engine.rerun_step(job.id, "a")
        assert a2.status == StepRunStatus.SUSPENDED

        # Check B's old run is not current
        b_runs = engine.get_runs(job.id, "b")
        if b_runs:
            job = engine.get_job(job.id)
            assert not engine._is_current(job, b_runs[-1])


# ── Test 8: Sequencing freshness ─────────────────────────────────────


class TestSequencingFreshness:
    def test_sequencing_dep_must_rerun(self):
        impl_count = {"n": 0}
        test_count = {"n": 0}

        def impl_fn(inputs):
            impl_count["n"] += 1
            return {"result": f"impl_v{impl_count['n']}"}

        def test_fn(inputs):
            test_count["n"] += 1
            return {"report": f"test_run_{test_count['n']}", "passed": True}

        register_step_fn("seq_impl", impl_fn)
        register_step_fn("seq_test", test_fn)

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "implement": StepDefinition(
                name="implement", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "seq_impl"}),
            ),
            "test": StepDefinition(
                name="test", outputs=["report", "passed"],
                executor=ExecutorRef("callable", {"fn_name": "seq_test"}),
                sequencing=["implement"],
            ),
        })

        job = engine.create_job("Sequencing test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED
        assert impl_count["n"] == 1
        assert test_count["n"] == 1

        # Rerun implement
        engine.rerun_step(job.id, "implement")
        for _ in range(10):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        assert impl_count["n"] == 2
        assert test_count["n"] == 2


# ── Test 9: Manual rerun during completion ───────────────────────────


class TestManualRerunDuringCompletion:
    def test_rerun_a_b_becomes_non_current(self):
        register_step_fn("mr_a", lambda inputs: {"value": "hello"})
        register_step_fn("mr_b", lambda inputs: {"result": inputs.get("data", "")})

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "mr_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "mr_b"}),
                inputs=[InputBinding("data", "a", "value")],
            ),
        })

        job = engine.create_job("Rerun test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # Rerun A
        engine.rerun_step(job.id, "a")

        for _ in range(10):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        b_runs = engine.get_runs(job.id, "b")
        assert len(b_runs) == 2


# ── Artifact field validation ────────────────────────────────────────


class TestArtifactValidation:
    def test_missing_declared_outputs_fails_step(self):
        """When artifact doesn't contain declared outputs, step fails."""
        # Return {"wrong_field": 1} but step declares outputs=["expected"]
        register_step_fn("bad_output", lambda i: {"wrong_field": 1})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["expected"],
                executor=ExecutorRef("callable", {"fn_name": "bad_output"}),
            ),
        })

        job = engine.create_job("Validation test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.FAILED

        runs = engine.get_runs(job.id, "a")
        assert runs[0].status == StepRunStatus.FAILED
        assert "missing declared outputs" in runs[0].error

    def test_correct_outputs_pass_validation(self):
        """When artifact contains all declared outputs, step succeeds."""
        register_step_fn("good_output", lambda i: {"expected": "yes", "extra": "ok"})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["expected"],
                executor=ExecutorRef("callable", {"fn_name": "good_output"}),
            ),
        })

        job = engine.create_job("Validation pass", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_empty_outputs_no_validation(self):
        """Steps with no declared outputs skip validation."""
        register_step_fn("no_outputs", lambda i: {"anything": "goes"})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=[],
                executor=ExecutorRef("callable", {"fn_name": "no_outputs"}),
            ),
        })

        job = engine.create_job("No outputs", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED


# ── Test 19: Rerun safety ────────────────────────────────────────────


class TestRerunSafety:
    def test_reject_rerun_when_suspended(self):
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "provide"}),
            ),
        })

        job = engine.create_job("Rerun safety", w)
        engine.start_job(job.id)

        with pytest.raises(ValueError, match="Cannot rerun"):
            engine.rerun_step(job.id, "a")


# ── Test 20: Rerun fail then succeed ─────────────────────────────────


class TestRerunFailSucceed:
    def test_fail_rerun_succeed(self):
        count = {"n": 0}

        def a_fn(inputs):
            count["n"] += 1
            if count["n"] == 1:
                raise RuntimeError("first attempt fails")
            return {"value": "success"}

        register_step_fn("frs_a", a_fn)

        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "frs_a"}),
            ),
        })

        job = engine.create_job("Fail then succeed", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.FAILED

        engine.rerun_step(job.id, "a")
        engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "a")
        assert len(runs) == 2
        assert runs[0].status == StepRunStatus.FAILED
        assert runs[1].status == StepRunStatus.COMPLETED


# ── Test 21: Exit rules ──────────────────────────────────────────────


class TestExitRules:
    def test_advance_action(self):
        register_step_fn("adv", lambda i: {"pass": True})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["pass"],
                executor=ExecutorRef("callable", {"fn_name": "adv"}),
                exit_rules=[
                    ExitRule("ok", "field_match", {
                        "field": "pass", "value": True, "action": "advance",
                    }, priority=10),
                ],
            ),
        })

        job = engine.create_job("Advance test", w)
        engine.start_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_escalate_action(self):
        register_step_fn("esc", lambda i: {"severity": "critical"})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["severity"],
                executor=ExecutorRef("callable", {"fn_name": "esc"}),
                exit_rules=[
                    ExitRule("critical", "field_match", {
                        "field": "severity", "value": "critical",
                        "action": "escalate",
                    }, priority=10),
                ],
            ),
        })

        job = engine.create_job("Escalate test", w)
        engine.start_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.PAUSED

    def test_abandon_action(self):
        register_step_fn("abn", lambda i: {"fatal": True})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["fatal"],
                executor=ExecutorRef("callable", {"fn_name": "abn"}),
                exit_rules=[
                    ExitRule("abandon", "field_match", {
                        "field": "fatal", "value": True,
                        "action": "abandon",
                    }, priority=10),
                ],
            ),
        })

        job = engine.create_job("Abandon test", w)
        engine.start_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.FAILED

    def test_loop_action(self):
        count = {"n": 0}

        def a_fn(inputs):
            count["n"] += 1
            return {"done": count["n"] >= 2}

        register_step_fn("loop_act", a_fn)
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["done"],
                executor=ExecutorRef("callable", {"fn_name": "loop_act"}),
                exit_rules=[
                    ExitRule("done", "field_match", {
                        "field": "done", "value": True, "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "field_match", {
                        "field": "done", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("Loop action test", w)
        engine.start_job(job.id)

        for _ in range(20):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        assert job.status == JobStatus.COMPLETED
        assert count["n"] == 2


# ── Test 22: Exit rule defaults ──────────────────────────────────────


class TestExitRuleDefaults:
    def test_empty_rules_advance(self):
        register_step_fn("empty_rules", lambda i: {"value": 42})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "empty_rules"}),
                exit_rules=[],
            ),
        })

        job = engine.create_job("Empty rules", w)
        engine.start_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_no_match_advances(self):
        register_step_fn("no_match", lambda i: {"value": 42})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "no_match"}),
                exit_rules=[
                    ExitRule("specific", "field_match", {
                        "field": "value", "value": 999, "action": "escalate",
                    }, priority=10),
                ],
            ),
        })

        job = engine.create_job("No match", w)
        engine.start_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_max_iterations_escalates(self):
        register_step_fn("max_iter", lambda i: {"done": False})
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["done"],
                executor=ExecutorRef("callable", {"fn_name": "max_iter"}),
                exit_rules=[
                    ExitRule("loop", "field_match", {
                        "field": "done", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 2,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("Max iterations", w)
        engine.start_job(job.id)

        for _ in range(30):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.PAUSED


# ── Test 23: Loop counting ───────────────────────────────────────────


class TestLoopCounting:
    def test_counts_completions_not_attempts(self):
        call_count = {"n": 0}

        def a_fn(inputs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("fail on second call")
            return {"done": False}

        register_step_fn("lc_a", a_fn)
        engine = make_engine()

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["done"],
                executor=ExecutorRef("callable", {"fn_name": "lc_a"}),
                exit_rules=[
                    ExitRule("loop", "field_match", {
                        "field": "done", "value": False,
                        "action": "loop", "target": "a", "max_iterations": 3,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("Loop counting", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.FAILED

        # Rerun — the failed run shouldn't count
        call_count["n"] = 0

        engine.rerun_step(job.id, "a")

        for _ in range(20):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        completed_count = engine.store.completed_run_count(job.id, "a")
        assert completed_count <= 3
