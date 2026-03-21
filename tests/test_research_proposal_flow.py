"""Tests for the research-proposal flow.

Covers: YAML parsing, validation, council for_each + synthesis pipeline,
external checkpoint exit rules, any_of input resolution, partial failure
handling, and feedback loop cycling.
"""

import pytest
from pathlib import Path

from tests.conftest import register_step_fn, CallableExecutor, run_job_sync
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutorRegistry,
    ScriptExecutor,
    ExternalExecutor,
    MockLLMExecutor,
)
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    ForEachSpec,
    InputBinding,
    JobStatus,
    OutputFieldSpec,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_yaml


FLOW_PATH = Path("flows/research-proposal/FLOW.yaml")


def make_engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("script", lambda config: ScriptExecutor(command=config.get("command", "echo '{}'")))
    reg.register("external", lambda config: ExternalExecutor(prompt=config.get("prompt", "")))
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        responses=config.get("responses"),
    ))
    reg.register("for_each", lambda config: CallableExecutor(fn_name="__noop__"))
    return store, Engine(store=store, registry=reg)


def tick_until_done(engine, job_id, max_ticks=50):
    for _ in range(max_ticks):
        job = engine.get_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job
        engine.tick()
    return engine.get_job(job_id)


# ── T1: YAML parses without errors ──────────────────────────────────


class TestFlowParsing:
    def test_yaml_parses_successfully(self):
        """FLOW.yaml loads without parse errors."""
        wf = load_workflow_yaml(FLOW_PATH)
        assert len(wf.steps) == 10

    def test_step_names_match(self):
        wf = load_workflow_yaml(FLOW_PATH)
        expected = {
            "init", "setup-models",
            "council-1", "synthesize-1", "revise-1",
            "council-2", "synthesize-2", "revise-2",
            "external-checkpoint", "finalize",
        }
        assert set(wf.steps.keys()) == expected

    def test_terminal_step_is_finalize(self):
        wf = load_workflow_yaml(FLOW_PATH)
        assert wf.terminal_steps() == ["finalize"]

    def test_for_each_steps_have_sub_flows(self):
        wf = load_workflow_yaml(FLOW_PATH)
        assert wf.steps["council-1"].for_each is not None
        assert wf.steps["council-1"].sub_flow is not None
        assert wf.steps["council-2"].for_each is not None
        assert wf.steps["council-2"].sub_flow is not None

    def test_external_checkpoint_has_typed_schema(self):
        wf = load_workflow_yaml(FLOW_PATH)
        schema = wf.steps["external-checkpoint"].output_schema
        assert "choice" in schema
        assert schema["choice"].type == "choice"
        assert set(schema["choice"].options) == {"approved", "feedback", "done"}


# ── T2: Validation ──────────────────────────────────────────────────


class TestFlowValidation:
    def test_validation_no_errors(self):
        """validate() returns no errors."""
        wf = load_workflow_yaml(FLOW_PATH)
        errors = wf.validate()
        assert errors == [], f"Unexpected errors: {errors}"

    def test_validation_no_warnings(self):
        """warnings() returns empty list."""
        wf = load_workflow_yaml(FLOW_PATH)
        warns = wf.warnings()
        assert warns == [], f"Unexpected warnings: {warns}"


# ── T3: Council for_each + synthesis pipeline ───────────────────────


class TestCouncilPipeline:
    def test_for_each_fans_out_to_models_and_synthesizes(self):
        """Simulates setup-models → council-1 → synthesize-1."""
        register_step_fn("init_stub", lambda inputs: {
            "draft_content": "Draft about X",
            "title": "X", "slug": "x", "report_path": "/tmp/x.md",
            "url": "/tmp/x.md", "notes": "n",
        })
        register_step_fn("synth_stub", lambda inputs: {
            "synthesis": f"Synthesis of {len(inputs.get('results', []))} reviews",
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review", outputs=["response"],
                executor=ExecutorRef("callable", {"fn_name": "review_fn"}),
                inputs=[
                    InputBinding("model_id", "$job", "model_id"),
                    InputBinding("draft_content", "$job", "draft_content"),
                ],
            ),
        })

        register_step_fn("review_fn", lambda inputs: {
            "response": f"Review by {inputs['model_id']}",
        })

        wf = WorkflowDefinition(steps={
            "init": StepDefinition(
                name="init",
                outputs=["title", "slug", "report_path", "url", "notes", "draft_content"],
                executor=ExecutorRef("callable", {"fn_name": "init_stub"}),
            ),
            "setup-models": StepDefinition(
                name="setup-models", outputs=["models"],
                executor=ExecutorRef("script", {
                    "command": "printf '{\"models\": [\"m1\", \"m2\"]}'"
                }),
                sequencing=["init"],
            ),
            "council-1": StepDefinition(
                name="council-1", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="setup-models",
                    source_field="models",
                    item_var="model_id",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
                inputs=[
                    InputBinding("draft_content", "init", "draft_content"),
                ],
            ),
            "synthesize-1": StepDefinition(
                name="synthesize-1", outputs=["synthesis"],
                executor=ExecutorRef("callable", {"fn_name": "synth_stub"}),
                inputs=[
                    InputBinding("results", "council-1", "results"),
                ],
            ),
        })

        job = engine.create_job("council test", wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        runs = engine.get_runs(job.id)
        synth_run = [r for r in runs if r.step_name == "synthesize-1"][0]
        assert "synthesis" in synth_run.result.artifact
        assert "2 reviews" in synth_run.result.artifact["synthesis"]


# ── T4: External checkpoint exit rules ─────────────────────────────────


class TestExternalCheckpoint:
    def _build_checkpoint_workflow(self):
        return WorkflowDefinition(steps={
            "upstream": StepDefinition(
                name="upstream", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "stub"}),
            ),
            "checkpoint": StepDefinition(
                name="checkpoint",
                outputs=["choice", "feedback"],
                output_schema={
                    "choice": OutputFieldSpec(
                        type="choice",
                        options=["approved", "feedback", "done"],
                    ),
                    "feedback": OutputFieldSpec(
                        type="text", required=False,
                    ),
                },
                executor=ExecutorRef("external", {"prompt": "Review"}),
                inputs=[
                    InputBinding("result", "upstream", "result"),
                ],
                exit_rules=[
                    ExitRule("approved", "expression", {
                        "condition": "outputs.choice in ('approved', 'done')",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("feedback", "expression", {
                        "condition": "True",
                        "action": "loop",
                        "target": "upstream",
                    }, priority=5),
                ],
            ),
        })

    def test_approved_choice_advances(self):
        """choice='approved' triggers advance exit rule."""
        register_step_fn("stub", lambda inputs: {"result": "ok"})
        _, engine = make_engine()

        wf = self._build_checkpoint_workflow()
        job = engine.create_job("test", wf)
        engine.start_job(job.id)
        engine.tick()  # upstream runs

        engine.tick()  # checkpoint suspends
        runs = engine.get_runs(job.id, "checkpoint")
        run = runs[0]
        assert run.status == StepRunStatus.SUSPENDED

        engine.fulfill_watch(run.id, {"choice": "approved"})
        engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_done_choice_advances(self):
        """choice='done' also triggers advance exit rule."""
        register_step_fn("stub", lambda inputs: {"result": "ok"})
        _, engine = make_engine()

        wf = self._build_checkpoint_workflow()
        job = engine.create_job("test", wf)
        engine.start_job(job.id)
        engine.tick()  # upstream runs
        engine.tick()  # checkpoint suspends

        runs = engine.get_runs(job.id, "checkpoint")
        engine.fulfill_watch(runs[0].id, {"choice": "done"})
        engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_feedback_choice_loops(self):
        """choice='feedback' triggers loop back to upstream."""
        call_count = {"n": 0}

        def stub(inputs):
            call_count["n"] += 1
            return {"result": f"v{call_count['n']}"}

        register_step_fn("stub", stub)
        _, engine = make_engine()

        wf = self._build_checkpoint_workflow()
        job = engine.create_job("test", wf)
        engine.start_job(job.id)
        engine.tick()  # upstream runs
        engine.tick()  # checkpoint suspends

        runs = engine.get_runs(job.id, "checkpoint")
        engine.fulfill_watch(runs[0].id, {
            "choice": "feedback",
            "feedback": "needs more detail",
        })
        engine.tick()
        engine.tick()  # upstream re-runs

        upstream_runs = engine.get_runs(job.id, "upstream")
        assert len(upstream_runs) == 2

    def test_invalid_choice_rejected(self):
        """Fulfilling with invalid choice value is rejected."""
        register_step_fn("stub", lambda inputs: {"result": "ok"})
        _, engine = make_engine()

        wf = self._build_checkpoint_workflow()
        job = engine.create_job("test", wf)
        engine.start_job(job.id)
        engine.tick()
        engine.tick()

        runs = engine.get_runs(job.id, "checkpoint")
        with pytest.raises(ValueError, match="invalid choice"):
            engine.fulfill_watch(runs[0].id, {"choice": "maybe"})


# ── T5: any_of input resolution ─────────────────────────────────────


class TestAnyOfInputs:
    def test_any_of_resolves_first_available(self, async_engine):
        """When both sources exist, first in list wins."""
        register_step_fn("a", lambda inputs: {"val": "from_a"})
        register_step_fn("b", lambda inputs: {"val": "from_b"})
        register_step_fn("c", lambda inputs: {"got": inputs["x"]})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["val"],
                executor=ExecutorRef("callable", {"fn_name": "a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["val"],
                executor=ExecutorRef("callable", {"fn_name": "b"}),
            ),
            "c": StepDefinition(
                name="c", outputs=["got"],
                executor=ExecutorRef("callable", {"fn_name": "c"}),
                inputs=[InputBinding(
                    "x", "", "",
                    any_of_sources=[("a", "val"), ("b", "val")],
                )],
            ),
        })

        job = async_engine.create_job("test", wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        c_run = [r for r in runs if r.step_name == "c"][0]
        assert c_run.result.artifact["got"] == "from_a"


# ── T6: for_each with on_error: continue ────────────────────────────


class TestCouncilErrorHandling:
    def test_partial_model_failure_still_completes(self):
        """If 1 of 3 models fails, for_each still completes with _error entry."""
        def review(inputs):
            if inputs["item"] == "bad-model":
                raise RuntimeError("API error")
            return {"response": f"OK from {inputs['item']}"}

        register_step_fn("review_or_fail", review)
        register_step_fn("produce_models", lambda inputs: {
            "models": ["good-1", "bad-model", "good-2"]
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review", outputs=["response"],
                executor=ExecutorRef("callable", {"fn_name": "review_or_fail"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        wf = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["models"],
                executor=ExecutorRef("callable", {"fn_name": "produce_models"}),
            ),
            "council": StepDefinition(
                name="council", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source",
                    source_field="models",
                    item_var="item",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("error handling test", wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "council")[0].result.artifact["results"]
        assert len(results) == 3
        assert results[0]["response"] == "OK from good-1"
        assert "_error" in results[1]
        assert results[2]["response"] == "OK from good-2"


# ── T7: Feedback loop cycles correctly ──────────────────────────────


class TestFeedbackLoop:
    def test_external_feedback_loop_revise_rerun(self):
        """External 'feedback' → revise reruns → external reruns → 'approved' → done."""
        call_count = {"revise": 0}

        def revise(inputs):
            call_count["revise"] += 1
            hf = inputs.get("external_feedback")
            return {
                "report_content": f"v{call_count['revise']}",
                "result": f"revised with: {hf}",
            }

        register_step_fn("revise", revise)
        register_step_fn("upstream_stub", lambda inputs: {
            "report_content": "initial",
            "result": "initial draft",
        })

        _, engine = make_engine()

        wf = WorkflowDefinition(steps={
            "upstream": StepDefinition(
                name="upstream", outputs=["report_content", "result"],
                executor=ExecutorRef("callable", {"fn_name": "upstream_stub"}),
            ),
            "revise-2": StepDefinition(
                name="revise-2", outputs=["report_content", "result"],
                executor=ExecutorRef("callable", {"fn_name": "revise"}),
                inputs=[
                    InputBinding("report_content", "upstream", "report_content"),
                    InputBinding("external_feedback", "external-checkpoint", "feedback",
                                 optional=True),
                ],
            ),
            "external-checkpoint": StepDefinition(
                name="external-checkpoint",
                outputs=["choice", "feedback"],
                output_schema={
                    "choice": OutputFieldSpec(
                        type="choice",
                        options=["approved", "feedback", "done"],
                    ),
                    "feedback": OutputFieldSpec(type="text", required=False),
                },
                executor=ExecutorRef("external", {"prompt": "Review"}),
                inputs=[InputBinding(
                    "result", "", "",
                    any_of_sources=[
                        ("revise-2", "result"),
                        ("upstream", "result"),
                    ],
                )],
                exit_rules=[
                    ExitRule("approved", "expression", {
                        "condition": "outputs.choice in ('approved', 'done')",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("feedback", "expression", {
                        "condition": "True",
                        "action": "loop",
                        "target": "revise-2",
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("loop test", wf)
        engine.start_job(job.id)
        tick_until_done(engine, job.id, max_ticks=5)

        # upstream and revise-2 should have run, external-checkpoint should be suspended
        runs = engine.get_runs(job.id, "external-checkpoint")
        assert len(runs) == 1
        assert runs[0].status == StepRunStatus.SUSPENDED

        # First fulfill: feedback → loop to revise-2
        engine.fulfill_watch(runs[0].id, {
            "choice": "feedback",
            "feedback": "add more evidence",
        })
        tick_until_done(engine, job.id, max_ticks=10)

        # revise-2 should have re-run, external-checkpoint suspended again
        assert call_count["revise"] == 2
        runs = engine.get_runs(job.id, "external-checkpoint")
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        assert len(suspended) == 1

        # Second fulfill: approved → job completes
        engine.fulfill_watch(suspended[0].id, {"choice": "approved"})
        tick_until_done(engine, job.id, max_ticks=5)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED
        assert call_count["revise"] == 2
