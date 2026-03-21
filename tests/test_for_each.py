"""Tests for for_each step execution.

Covers: basic for_each, empty list, error handling (fail_fast + continue),
nested field access, input passthrough, YAML parsing, and cancellation.
"""

import pytest

from tests.conftest import register_step_fn
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ScriptExecutor, ExternalExecutor, MockLLMExecutor
from tests.conftest import CallableExecutor
from stepwise.models import (
    ExecutorRef,
    ForEachSpec,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string, YAMLLoadError


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
    reg.register("for_each", lambda config: CallableExecutor(fn_name="__noop__"))  # placeholder
    return store, Engine(store=store, registry=reg)


def tick_until_done(engine, job_id, max_ticks=50):
    for _ in range(max_ticks):
        job = engine.get_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job
        engine.tick()
    return engine.get_job(job_id)


# ── Basic for_each ────────────────────────────────────────────────────


class TestForEachBasic:
    def test_for_each_iterates_over_list(self):
        """Step A produces a list, for_each step B processes each item."""
        register_step_fn("produce_list", lambda inputs: {
            "items": ["apple", "banana", "cherry"]
        })
        register_step_fn("process_item", lambda inputs: {
            "result": f"processed_{inputs['item']}"
        })

        _, engine = make_engine()

        # Sub-flow: single step that processes each item
        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "process_item"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "produce": StepDefinition(
                name="produce", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "produce_list"}),
            ),
            "each_item": StepDefinition(
                name="each_item", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="produce",
                    source_field="items",
                    item_var="item",
                ),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("For-each basic", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "each_item")
        assert len(runs) == 1
        fe_run = runs[0]
        assert fe_run.status == StepRunStatus.COMPLETED
        results = fe_run.result.artifact["results"]
        assert len(results) == 3
        assert results[0]["result"] == "processed_apple"
        assert results[1]["result"] == "processed_banana"
        assert results[2]["result"] == "processed_cherry"

    def test_for_each_preserves_order(self):
        """Results are collected in the same order as the source list."""
        register_step_fn("numbers", lambda inputs: {"nums": [10, 20, 30, 40, 50]})
        register_step_fn("double", lambda inputs: {"doubled": inputs["n"] * 2})

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "double_it": StepDefinition(
                name="double_it", outputs=["doubled"],
                executor=ExecutorRef("callable", {"fn_name": "double"}),
                inputs=[InputBinding("n", "$job", "n")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["nums"],
                executor=ExecutorRef("callable", {"fn_name": "numbers"}),
            ),
            "double_all": StepDefinition(
                name="double_all", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="source", source_field="nums", item_var="n"),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("For-each order", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "double_all")[0].result.artifact["results"]
        assert [r["doubled"] for r in results] == [20, 40, 60, 80, 100]


# ── Empty list ────────────────────────────────────────────────────────


class TestForEachEmptyList:
    def test_empty_list_completes_immediately(self):
        """For-each with empty source list completes with empty results."""
        register_step_fn("empty", lambda inputs: {"items": []})

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "empty"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="source", source_field="items"),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("For-each empty", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "each")[0].result.artifact["results"]
        assert results == []


# ── Error handling ────────────────────────────────────────────────────


class TestForEachErrors:
    def test_fail_fast_cancels_remaining(self):
        """With on_error=fail_fast, first failure fails the for_each step."""
        call_count = {"n": 0}

        def sometimes_fail(inputs):
            call_count["n"] += 1
            if inputs["item"] == "bad":
                raise RuntimeError("intentional failure")
            return {"result": f"ok_{inputs['item']}"}

        register_step_fn("maybe_fail", sometimes_fail)

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "maybe_fail"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "produce_bad_list"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source", source_field="items",
                    on_error="fail_fast",
                ),
                sub_flow=sub_flow,
            ),
        })

        register_step_fn("produce_bad_list", lambda inputs: {
            "items": ["good", "bad", "also_good"]
        })

        job = engine.create_job("For-each fail_fast", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.FAILED

        fe_run = engine.get_runs(job.id, "each")[0]
        assert fe_run.status == StepRunStatus.FAILED
        assert "failed" in fe_run.error.lower()

    def test_continue_collects_errors(self):
        """With on_error=continue, failed items get error markers, rest succeed."""
        def maybe_fail(inputs):
            if inputs["item"] == "bad":
                raise RuntimeError("intentional failure")
            return {"result": f"ok_{inputs['item']}"}

        register_step_fn("maybe_fail_cont", maybe_fail)

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "maybe_fail_cont"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "bad_list_cont"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source", source_field="items",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
            ),
        })

        register_step_fn("bad_list_cont", lambda inputs: {
            "items": ["good", "bad", "also_good"]
        })

        job = engine.create_job("For-each continue", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED

        results = engine.get_runs(job.id, "each")[0].result.artifact["results"]
        assert len(results) == 3
        assert results[0]["result"] == "ok_good"
        assert "_error" in results[1]  # the failed item
        assert results[2]["result"] == "ok_also_good"


# ── Nested field access ──────────────────────────────────────────────


class TestForEachNestedField:
    def test_nested_source_field(self):
        """for_each can access nested fields like 'design.sections'."""
        register_step_fn("nested_src", lambda inputs: {
            "design": {"sections": ["intro", "body", "conclusion"]}
        })
        register_step_fn("process_section", lambda inputs: {
            "content": f"content_for_{inputs['section']}"
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "write": StepDefinition(
                name="write", outputs=["content"],
                executor=ExecutorRef("callable", {"fn_name": "process_section"}),
                inputs=[InputBinding("section", "$job", "section")],
            ),
        })

        w = WorkflowDefinition(steps={
            "design": StepDefinition(
                name="design", outputs=["design"],
                executor=ExecutorRef("callable", {"fn_name": "nested_src"}),
            ),
            "write_sections": StepDefinition(
                name="write_sections", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="design", source_field="design.sections",
                    item_var="section",
                ),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Nested field", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "write_sections")[0].result.artifact["results"]
        assert len(results) == 3
        assert results[0]["content"] == "content_for_intro"
        assert results[2]["content"] == "content_for_conclusion"


# ── Input passthrough ────────────────────────────────────────────────


class TestForEachInputPassthrough:
    def test_parent_inputs_passed_to_sub_flow(self):
        """Inputs from the parent step are available in sub-flow via $job."""
        register_step_fn("make_list", lambda inputs: {"items": ["x", "y"]})
        register_step_fn("use_context", lambda inputs: {
            "result": f"{inputs['item']}_in_{inputs['context']}"
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "use": StepDefinition(
                name="use", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "use_context"}),
                inputs=[
                    InputBinding("item", "$job", "item"),
                    InputBinding("context", "$job", "context"),
                ],
            ),
        })

        w = WorkflowDefinition(steps={
            "gen": StepDefinition(
                name="gen", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "make_list"}),
            ),
            "process": StepDefinition(
                name="process", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="gen", source_field="items"),
                sub_flow=sub_flow,
                inputs=[InputBinding("context", "$job", "context_val")],
            ),
        })

        job = engine.create_job(
            "Input passthrough", w,
            inputs={"context_val": "test_env"},
        )
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "process")[0].result.artifact["results"]
        assert results[0]["result"] == "x_in_test_env"
        assert results[1]["result"] == "y_in_test_env"


# ── Multi-step sub-flow ──────────────────────────────────────────────


class TestForEachMultiStepSubFlow:
    def test_sub_flow_with_multiple_steps(self):
        """Sub-flow can have its own DAG: gen → review pipeline per item."""
        register_step_fn("topics", lambda inputs: {"items": ["AI", "quantum"]})
        register_step_fn("draft", lambda inputs: {
            "text": f"draft about {inputs['topic']}"
        })
        register_step_fn("review", lambda inputs: {
            "reviewed": f"reviewed: {inputs['draft_text']}"
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "write_draft": StepDefinition(
                name="write_draft", outputs=["text"],
                executor=ExecutorRef("callable", {"fn_name": "draft"}),
                inputs=[InputBinding("topic", "$job", "topic")],
            ),
            "review_draft": StepDefinition(
                name="review_draft", outputs=["reviewed"],
                executor=ExecutorRef("callable", {"fn_name": "review"}),
                inputs=[InputBinding("draft_text", "write_draft", "text")],
            ),
        })

        w = WorkflowDefinition(steps={
            "get_topics": StepDefinition(
                name="get_topics", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "topics"}),
            ),
            "process_topics": StepDefinition(
                name="process_topics", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="get_topics", source_field="items",
                    item_var="topic",
                ),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Multi-step sub-flow", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        results = engine.get_runs(job.id, "process_topics")[0].result.artifact["results"]
        assert len(results) == 2
        assert results[0]["reviewed"] == "reviewed: draft about AI"
        assert results[1]["reviewed"] == "reviewed: draft about quantum"


# ── Downstream step ──────────────────────────────────────────────────


class TestForEachDownstream:
    def test_step_after_for_each_gets_results(self):
        """A step depending on for_each output receives the collected results."""
        register_step_fn("gen_items", lambda inputs: {"items": [1, 2, 3]})
        register_step_fn("square", lambda inputs: {"squared": inputs["n"] ** 2})
        register_step_fn("summarize", lambda inputs: {
            "total": sum(r["squared"] for r in inputs["all_results"])
        })

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "sq": StepDefinition(
                name="sq", outputs=["squared"],
                executor=ExecutorRef("callable", {"fn_name": "square"}),
                inputs=[InputBinding("n", "$job", "n")],
            ),
        })

        w = WorkflowDefinition(steps={
            "numbers": StepDefinition(
                name="numbers", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "gen_items"}),
            ),
            "square_all": StepDefinition(
                name="square_all", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="numbers", source_field="items", item_var="n"),
                sub_flow=sub_flow,
            ),
            "total": StepDefinition(
                name="total", outputs=["total"],
                executor=ExecutorRef("callable", {"fn_name": "summarize"}),
                inputs=[InputBinding("all_results", "square_all", "results")],
            ),
        })

        job = engine.create_job("Downstream", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED
        total_run = engine.get_runs(job.id, "total")[0]
        assert total_run.result.artifact["total"] == 14  # 1 + 4 + 9


# ── Cancellation ─────────────────────────────────────────────────────


class TestForEachCancellation:
    def test_cancel_job_cancels_for_each_sub_jobs(self):
        """Cancelling a job also cancels for_each sub-jobs."""
        register_step_fn("slow_list", lambda inputs: {"items": ["a", "b", "c"]})

        _, engine = make_engine()

        # Use external executor in sub-flow so sub-jobs stay running
        sub_flow = WorkflowDefinition(steps={
            "wait": StepDefinition(
                name="wait", outputs=["result"],
                executor=ExecutorRef("external", {"prompt": "provide result"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "slow_list"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="source", source_field="items"),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Cancel test", w)
        engine.start_job(job.id)

        # Tick to get for_each launched
        engine.tick()

        # Get the for_each run and its sub-job IDs
        fe_run = engine.get_runs(job.id, "each")[0]
        assert fe_run.status == StepRunStatus.DELEGATED
        sub_job_ids = fe_run.executor_state["sub_job_ids"]
        assert len(sub_job_ids) == 3

        # Cancel the parent job
        engine.cancel_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.CANCELLED

        # Check sub-jobs are cancelled
        for sid in sub_job_ids:
            sub = engine.get_job(sid)
            assert sub.status == JobStatus.CANCELLED


# ── YAML Parsing ─────────────────────────────────────────────────────


class TestForEachYAML:
    def test_parse_for_each_step(self):
        """YAML for_each syntax is parsed correctly."""
        yaml_str = """
steps:
  produce:
    executor: callable
    fn_name: produce_list
    outputs: [items]

  process_all:
    for_each: produce.items
    as: thing
    on_error: continue
    flow:
      steps:
        handle:
          executor: callable
          fn_name: handle_thing
          outputs: [result]
"""
        wf = load_workflow_string(yaml_str)
        step = wf.steps["process_all"]

        assert step.for_each is not None
        assert step.for_each.source_step == "produce"
        assert step.for_each.source_field == "items"
        assert step.for_each.item_var == "thing"
        assert step.for_each.on_error == "continue"
        assert step.sub_flow is not None
        assert "handle" in step.sub_flow.steps
        assert step.executor.type == "for_each"
        assert step.outputs == ["results"]  # defaults

    def test_parse_for_each_default_as(self):
        """Default item variable is 'item'."""
        yaml_str = """
steps:
  source:
    executor: callable
    fn_name: src
    outputs: [items]

  each:
    for_each: source.items
    flow:
      steps:
        do:
          executor: callable
          fn_name: handler
          outputs: [done]
"""
        wf = load_workflow_string(yaml_str)
        assert wf.steps["each"].for_each.item_var == "item"
        assert wf.steps["each"].for_each.on_error == "fail_fast"

    def test_parse_for_each_missing_flow_errors(self):
        """for_each without flow block raises an error."""
        yaml_str = """
steps:
  source:
    executor: callable
    fn_name: src
    outputs: [items]

  each:
    for_each: source.items
"""
        with pytest.raises(YAMLLoadError):
            load_workflow_string(yaml_str)

    def test_parse_for_each_invalid_source(self):
        """for_each with invalid source (no dot) raises an error."""
        yaml_str = """
steps:
  each:
    for_each: nodot
    flow:
      steps:
        do:
          executor: callable
          fn_name: handler
          outputs: [done]
"""
        with pytest.raises(YAMLLoadError):
            load_workflow_string(yaml_str)


# ── Validation ───────────────────────────────────────────────────────


class TestForEachValidation:
    def test_for_each_source_must_exist(self):
        """Workflow validation catches for_each referencing non-existent step."""
        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="nonexistent", source_field="items"),
                sub_flow=sub_flow,
            ),
        })

        errors = w.validate()
        assert any("nonexistent" in e for e in errors)

    def test_for_each_not_list_at_runtime(self):
        """If source field is not a list at runtime, launch raises ValueError."""
        register_step_fn("not_list", lambda inputs: {"items": "not a list"})

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "not_list"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="source", source_field="items"),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Not a list", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        # Should fail because "items" is a string, not a list
        assert job.status == JobStatus.FAILED


# ── Job tree ─────────────────────────────────────────────────────────


class TestForEachJobTree:
    def test_get_job_tree_includes_for_each_sub_jobs(self):
        """get_job_tree should include for_each sub-jobs."""
        register_step_fn("tree_list", lambda inputs: {"items": ["a", "b"]})
        register_step_fn("tree_proc", lambda inputs: {"result": inputs["item"]})

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "tree_proc"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "tree_list"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(source_step="source", source_field="items"),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Tree test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        tree = engine.get_job_tree(job.id)
        assert len(tree["sub_jobs"]) == 2


# ── All-fail with on_error: continue ────────────────────────────────


class TestForEachAllFail:
    def test_all_items_fail_with_continue_fails_step(self):
        """When ALL for-each items fail, the step should fail even with on_error=continue."""
        def always_fail(inputs):
            raise RuntimeError("every item fails")

        register_step_fn("always_fail_fe", always_fail)

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "always_fail_fe"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "all_fail_source"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source", source_field="items",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
            ),
        })

        register_step_fn("all_fail_source", lambda inputs: {
            "items": ["a", "b", "c"]
        })

        job = engine.create_job("For-each all-fail", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.FAILED

        fe_run = engine.get_runs(job.id, "each")[0]
        assert fe_run.status == StepRunStatus.FAILED
        assert "All 3 sub-jobs failed" in fe_run.error

        # Results should still be available for debugging
        assert fe_run.result is not None
        results = fe_run.result.artifact["results"]
        assert len(results) == 3
        assert all("_error" in r for r in results)

    def test_partial_failure_with_continue_still_succeeds(self):
        """When some (but not all) items fail with on_error=continue, the step succeeds."""
        def partial_fail(inputs):
            if inputs["item"] == "bad":
                raise RuntimeError("this one fails")
            return {"result": f"ok_{inputs['item']}"}

        register_step_fn("partial_fail_fe", partial_fail)

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "partial_fail_fe"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "partial_fail_source"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source", source_field="items",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
            ),
        })

        register_step_fn("partial_fail_source", lambda inputs: {
            "items": ["good", "bad", "also_good"]
        })

        job = engine.create_job("For-each partial-fail", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        # Partial failure with continue should still succeed
        assert job.status == JobStatus.COMPLETED

    def test_single_item_fails_with_continue_fails_step(self):
        """A single-item for-each where that one item fails should fail."""
        register_step_fn("single_fail_fe", lambda inputs: (_ for _ in ()).throw(RuntimeError("fail")))

        _, engine = make_engine()

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "single_fail_fe"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        w = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "single_fail_src"}),
            ),
            "each": StepDefinition(
                name="each", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="source", source_field="items",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
            ),
        })

        register_step_fn("single_fail_src", lambda inputs: {"items": ["only"]})

        job = engine.create_job("For-each single-fail", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        assert job.status == JobStatus.FAILED
        fe_run = engine.get_runs(job.id, "each")[0]
        assert fe_run.status == StepRunStatus.FAILED
        assert "All 1 sub-jobs failed" in fe_run.error
