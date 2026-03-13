"""Tests for M8 route step execution.

Covers: YAML parsing, validation, cycle detection, route matching,
sub-job delegation, output propagation, serialization round-trip,
file refs, loop/attempt interaction, and error handling.
"""

import os
import pytest
import tempfile
from pathlib import Path

from tests.conftest import register_step_fn, CallableExecutor
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ScriptExecutor, HumanExecutor, MockLLMExecutor
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    RouteDefinition,
    RouteSpec,
    StepDefinition,
    StepRunStatus,
    SubJobDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string, load_workflow_yaml, YAMLLoadError


def make_engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("script", lambda config: ScriptExecutor(command=config.get("command", "echo '{}'")))
    reg.register("human", lambda config: HumanExecutor(prompt=config.get("prompt", ""), notify=config.get("notify")))
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        responses=config.get("responses"),
    ))
    reg.register("for_each", lambda config: CallableExecutor(fn_name="__noop__"))
    reg.register("route", lambda config: CallableExecutor(fn_name="__noop__"))
    return store, Engine(store=store, registry=reg)


def tick_until_done(engine, job_id, max_ticks=50):
    for _ in range(max_ticks):
        job = engine.get_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job
        engine.tick()
    return engine.get_job(job_id)


# ── YAML Parsing ─────────────────────────────────────────────────────


class TestRouteYAMLParsing:
    def test_parse_inline_flow(self):
        """Inline flow block parses correctly."""
        w = load_workflow_string("""
steps:
  triage:
    executor: mock_llm
    prompt: classify
    outputs: [category]
  run_pipeline:
    inputs: { category: triage.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow:
          steps:
            do_fast:
              executor: mock_llm
              prompt: fast path
              outputs: [result]
      default:
        flow:
          steps:
            do_default:
              executor: mock_llm
              prompt: default path
              outputs: [result]
    outputs: [result]
""")
        step = w.steps["run_pipeline"]
        assert step.route_def is not None
        assert len(step.route_def.routes) == 2
        assert step.route_def.routes[0].name == "fast"
        assert step.route_def.routes[0].when == "category == 'fast'"
        assert step.route_def.routes[0].flow is not None
        assert step.route_def.routes[1].name == "default"
        assert step.route_def.routes[1].when is None

    def test_parse_registry_ref(self, monkeypatch):
        """@author:name resolved at parse time, flow_ref preserved for provenance."""
        fast_yaml = "name: fast-pipeline\nauthor: alice\nsteps:\n  go:\n    run: echo fast\n    outputs: [result]\n"
        default_yaml = "name: default-pipeline\nauthor: bob\nsteps:\n  go:\n    run: echo default\n    outputs: [result]\n"

        def mock_fetch(slug, *, use_cache=True):
            if slug == "fast-pipeline":
                return fast_yaml
            if slug == "default-pipeline":
                return default_yaml
            raise Exception(f"Not found: {slug}")

        monkeypatch.setattr("stepwise.registry_client.fetch_flow_yaml", mock_fetch)

        w = load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: "@alice:fast-pipeline"
      default:
        flow: "@bob:default-pipeline"
    outputs: [result]
""")
        route_def = w.steps["route"].route_def
        assert route_def.routes[0].flow is not None
        assert route_def.routes[0].flow_ref == "@alice:fast-pipeline"
        assert "go" in route_def.routes[0].flow.steps
        assert route_def.routes[1].flow is not None
        assert route_def.routes[1].flow_ref == "@bob:default-pipeline"
        assert "go" in route_def.routes[1].flow.steps

    def test_parse_default_declared_first(self):
        """Default declared first in YAML still evaluates last."""
        w = load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      default:
        flow:
          steps:
            fallback:
              executor: mock_llm
              prompt: fallback
              outputs: [result]
      specific:
        when: "category == 'specific'"
        flow:
          steps:
            specific_step:
              executor: mock_llm
              prompt: specific
              outputs: [result]
    outputs: [result]
""")
        route_def = w.steps["route"].route_def
        # specific should come first, default last
        assert route_def.routes[0].name == "specific"
        assert route_def.routes[1].name == "default"

    def test_parse_non_default_missing_when_errors(self):
        """Non-default route without when: → parse error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        flow:
          steps:
            do_fast:
              executor: mock_llm
              prompt: fast
              outputs: [result]
    outputs: [result]
""")
        assert "must have a 'when' expression" in str(exc_info.value)

    def test_parse_empty_when_string_errors(self):
        """when: "" → parse error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: ""
        flow:
          steps:
            do_fast:
              executor: mock_llm
              prompt: fast
              outputs: [result]
    outputs: [result]
""")
        assert "must have a 'when' expression" in str(exc_info.value)

    def test_parse_missing_flow(self):
        """Route entry lacking flow: → error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
    outputs: [result]
""")
        assert "missing 'flow'" in str(exc_info.value)

    def test_parse_missing_outputs(self):
        """Route step with no outputs: → error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow:
          steps:
            do_fast:
              executor: mock_llm
              prompt: fast
              outputs: [result]
""")
        assert "route steps must declare outputs" in str(exc_info.value)

    def test_parse_empty_routes(self):
        """Empty routes: → error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category]
  route:
    inputs: { category: classify.category }
    routes: {}
    outputs: [result]
""")
        assert "at least one entry" in str(exc_info.value)

    def test_parse_with_for_each_errors(self):
        """Both for_each and routes → error."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items, category]
  route:
    for_each: produce.items
    inputs: { category: produce.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow:
          steps:
            do_fast:
              executor: mock_llm
              prompt: fast
              outputs: [result]
    outputs: [result]
    flow:
      steps:
        sub:
          executor: mock_llm
          prompt: sub
          outputs: [result]
""")
        assert "cannot combine for_each and routes" in str(exc_info.value)

    def test_default_with_when_errors(self):
        """Default route must not have a when expression."""
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      default:
        when: "True"
        flow:
          steps:
            do_default:
              executor: mock_llm
              prompt: default
              outputs: [result]
    outputs: [result]
""")
        assert "must not have a 'when' expression" in str(exc_info.value)


# ── File Ref Cycle Detection ────────────────────────────────────────


class TestRouteFileRefCycleDetection:
    def test_circular_file_ref_detected(self):
        """A→B→A raises ValueError, not RecursionError."""
        with tempfile.TemporaryDirectory() as tmp:
            flow_a = Path(tmp) / "a.yaml"
            flow_b = Path(tmp) / "b.yaml"

            flow_a.write_text(f"""
steps:
  step1:
    executor: mock_llm
    prompt: step1
    outputs: [result]
    routes:
      r1:
        when: "True"
        flow: {flow_b.name}
    outputs: [result]
""")
            flow_b.write_text(f"""
steps:
  step1:
    executor: mock_llm
    prompt: step1
    outputs: [result]
    routes:
      r1:
        when: "True"
        flow: {flow_a.name}
    outputs: [result]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(flow_a))
            assert "circular flow reference" in str(exc_info.value).lower()

    def test_deep_nesting_allowed(self):
        """A→B→C (no cycle) loads successfully."""
        with tempfile.TemporaryDirectory() as tmp:
            flow_c = Path(tmp) / "c.yaml"
            flow_c.write_text("""
steps:
  leaf:
    executor: mock_llm
    prompt: leaf
    outputs: [result]
""")
            flow_b = Path(tmp) / "b.yaml"
            flow_b.write_text(f"""
steps:
  mid:
    routes:
      r1:
        when: "True"
        flow: {flow_c.name}
    outputs: [result]
""")
            flow_a = Path(tmp) / "a.yaml"
            flow_a.write_text(f"""
steps:
  top:
    routes:
      r1:
        when: "True"
        flow: {flow_b.name}
    outputs: [result]
""")
            w = load_workflow_yaml(str(flow_a))
            assert "top" in w.steps

    def test_sibling_routes_same_file(self):
        """Two routes referencing same file loads without false cycle error."""
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "shared.yaml"
            shared.write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main = Path(tmp) / "main.yaml"
            main.write_text(f"""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: {{ category: classify.category }}
    routes:
      fast:
        when: "category == 'fast'"
        flow: {shared.name}
      slow:
        when: "category == 'slow'"
        flow: {shared.name}
    outputs: [result]
""")
            w = load_workflow_yaml(str(main))
            assert w.steps["route"].route_def is not None
            assert len(w.steps["route"].route_def.routes) == 2


# ── Validation ───────────────────────────────────────────────────────


class TestRouteValidation:
    def test_validates_inline_subflows(self):
        """Sub-flow validation errors bubble up."""
        rd = RouteDefinition(routes=[
            RouteSpec("r1", "True", WorkflowDefinition(steps={}), None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        w = WorkflowDefinition(steps={"route_step": step})
        errors = w.validate()
        assert any("has no steps" in e for e in errors)

    def test_output_contract_every_terminal_step(self):
        """Each terminal step must independently cover outputs."""
        # Sub-flow with one terminal step missing 'result'
        sub = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("mock_llm", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["other"],  # missing 'result'
                executor=ExecutorRef("mock_llm", {}),
            ),
        })
        # Both a and b are terminal steps
        rd = RouteDefinition(routes=[
            RouteSpec("r1", "True", sub, None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        w = WorkflowDefinition(steps={"route_step": step})
        errors = w.validate()
        assert any("missing outputs" in e for e in errors)

    def test_empty_subflow_with_outputs_fails(self):
        """Sub-flow with no terminal steps but route declares outputs → error."""
        # Cycle: a→b→a (no terminal steps)
        sub = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x"],
                executor=ExecutorRef("mock_llm", {}),
                inputs=[InputBinding("y", "b", "y")],
            ),
            "b": StepDefinition(
                name="b", outputs=["y"],
                executor=ExecutorRef("mock_llm", {}),
                inputs=[InputBinding("x", "a", "x")],
            ),
        })
        rd = RouteDefinition(routes=[
            RouteSpec("r1", "True", sub, None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        w = WorkflowDefinition(steps={"route_step": step})
        errors = w.validate()
        # Should catch either cycle or no terminal steps
        assert len(errors) > 0

    def test_rejects_combined_for_each(self):
        """Mutual exclusivity enforced in validation."""
        from stepwise.models import ForEachSpec
        rd = RouteDefinition(routes=[
            RouteSpec("r1", "True", WorkflowDefinition(steps={
                "a": StepDefinition(name="a", outputs=["result"],
                                    executor=ExecutorRef("mock_llm", {})),
            }), None),
        ])
        fe = ForEachSpec(source_step="x", source_field="items")
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
            for_each=fe,
        )
        w = WorkflowDefinition(steps={
            "x": StepDefinition(name="x", outputs=["items"],
                                executor=ExecutorRef("mock_llm", {})),
            "route_step": step,
        })
        errors = w.validate()
        assert any("cannot combine" in e for e in errors)

    def test_multiple_defaults_rejected(self):
        """Only one default allowed."""
        rd = RouteDefinition(routes=[
            RouteSpec("default", None, WorkflowDefinition(steps={
                "a": StepDefinition(name="a", outputs=["result"],
                                    executor=ExecutorRef("mock_llm", {})),
            }), None),
            RouteSpec("default", None, WorkflowDefinition(steps={
                "b": StepDefinition(name="b", outputs=["result"],
                                    executor=ExecutorRef("mock_llm", {})),
            }), None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        w = WorkflowDefinition(steps={"route_step": step})
        errors = w.validate()
        assert any("multiple default" in e for e in errors)

    def test_attempt_reserved_name(self):
        """Input binding named 'attempt' → validation error."""
        rd = RouteDefinition(routes=[
            RouteSpec("r1", "True", WorkflowDefinition(steps={
                "a": StepDefinition(name="a", outputs=["result"],
                                    executor=ExecutorRef("mock_llm", {})),
            }), None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            inputs=[InputBinding("attempt", "upstream", "val")],
            route_def=rd,
        )
        w = WorkflowDefinition(steps={
            "upstream": StepDefinition(name="upstream", outputs=["val"],
                                       executor=ExecutorRef("mock_llm", {})),
            "route_step": step,
        })
        errors = w.validate()
        assert any("'attempt' is a reserved name" in e for e in errors)

    def test_non_default_missing_when_in_validation(self):
        """Route named 'special' without when → validation error."""
        rd = RouteDefinition(routes=[
            RouteSpec("special", None, WorkflowDefinition(steps={
                "a": StepDefinition(name="a", outputs=["result"],
                                    executor=ExecutorRef("mock_llm", {})),
            }), None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        w = WorkflowDefinition(steps={"route_step": step})
        errors = w.validate()
        assert any("must have a 'when' expression" in e for e in errors)


# ── Execution ────────────────────────────────────────────────────────


def _make_basic_route_workflow(route_results: dict[str, dict]):
    """Helper: triage step → route step with callable sub-flows.

    route_results maps route name → {fn_name: str, condition: str | None}
    """
    register_step_fn("triage", lambda inputs: {"category": inputs.get("category", "default")})

    routes = []
    for rname, rconfig in route_results.items():
        fn_name = rconfig["fn_name"]
        register_step_fn(fn_name, rconfig.get("fn", lambda inputs: {"result": f"from_{fn_name}"}))
        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": fn_name}),
            ),
        })
        routes.append(RouteSpec(
            name=rname,
            when=rconfig.get("condition"),
            flow=sub_flow,
            flow_ref=None,
        ))

    return WorkflowDefinition(steps={
        "triage": StepDefinition(
            name="triage", outputs=["category"],
            executor=ExecutorRef("callable", {"fn_name": "triage"}),
        ),
        "run_pipeline": StepDefinition(
            name="run_pipeline", outputs=["result"],
            executor=ExecutorRef("route", {}),
            inputs=[InputBinding("category", "triage", "category")],
            route_def=RouteDefinition(routes=routes),
        ),
    })


class TestRouteExecution:
    def test_matches_first_condition(self):
        """Basic dispatch works — first matching condition wins."""
        register_step_fn("fast_fn", lambda inputs: {"result": "fast_result"})

        w = _make_basic_route_workflow({
            "fast": {"fn_name": "fast_fn", "condition": "category == 'default'"},
            "slow": {"fn_name": "slow_fn", "condition": "category == 'slow'"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "default"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

    def test_second_condition(self):
        """First false, second matches."""
        register_step_fn("slow_fn", lambda inputs: {"result": "slow_result"})

        w = _make_basic_route_workflow({
            "fast": {"fn_name": "fast_fn", "condition": "category == 'fast'"},
            "slow": {"fn_name": "slow_fn", "condition": "category == 'default'"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "default"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        # Verify the slow route was matched
        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.executor_state["matched_route"] == "slow"

    def test_multiple_true_uses_first(self):
        """Both routes would match, first-match wins."""
        register_step_fn("first_fn", lambda inputs: {"result": "first"})
        register_step_fn("second_fn", lambda inputs: {"result": "second"})

        w = _make_basic_route_workflow({
            "first": {"fn_name": "first_fn", "condition": "True"},
            "second": {"fn_name": "second_fn", "condition": "True"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "any"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.executor_state["matched_route"] == "first"

    def test_default_route_fires(self):
        """No when: matches, default catches."""
        register_step_fn("default_fn", lambda inputs: {"result": "default"})

        w = _make_basic_route_workflow({
            "fast": {"fn_name": "fast_fn", "condition": "category == 'fast'"},
            "default": {"fn_name": "default_fn", "condition": None},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "other"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.executor_state["matched_route"] == "default"

    def test_no_match_no_default_fails(self):
        """Step fails with clear error and route_no_match category."""
        w = _make_basic_route_workflow({
            "fast": {"fn_name": "fast_fn", "condition": "category == 'fast'"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "other"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.status == StepRunStatus.FAILED
        assert route_run.error_category == "route_no_match"
        assert "No route matched" in route_run.error

    def test_when_expression_error_fails_step(self):
        """Bad expression → step fails, doesn't skip to next."""
        w = _make_basic_route_workflow({
            "bad": {"fn_name": "fast_fn", "condition": "1 / 0"},
            "good": {"fn_name": "good_fn", "condition": "True"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "any"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.error_category == "route_eval_error"

    def test_when_undefined_variable_fails(self):
        """when: references variable not in inputs → NameError → step fails."""
        w = _make_basic_route_workflow({
            "bad": {"fn_name": "fast_fn", "condition": "nonexistent == 'x'"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "any"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "run_pipeline"][0]
        assert route_run.error_category == "route_eval_error"

    def test_passes_inputs_to_sub_job(self):
        """Parent inputs available in sub-flow via $job."""
        captured = {}

        def capture_fn(inputs):
            captured.update(inputs)
            return {"result": "captured"}

        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("capture_fn", capture_fn)

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "capture_fn"}),
                inputs=[InputBinding("cat", "$job", "category")],
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED
        assert captured.get("cat") == "fast"

    def test_sub_job_outputs_propagate(self):
        """Sub-flow terminal outputs become route step result."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "the_answer"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "route"][0]
        assert route_run.result is not None
        assert route_run.result.artifact.get("result") == "the_answer"

    def test_sub_job_failure_fails_step(self):
        """Sub-flow failure → route step failure."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})

        def fail_fn(inputs):
            raise RuntimeError("sub-flow crash")

        register_step_fn("fail_fn", fail_fn)

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED

    def test_downstream_steps_wait(self):
        """Steps depending on route step wait for sub-job completion."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "route_result"})

        downstream_inputs = {}

        def downstream_fn(inputs):
            downstream_inputs.update(inputs)
            return {"final": "done"}

        register_step_fn("downstream_fn", downstream_fn)

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
            "consumer": StepDefinition(
                name="consumer", outputs=["final"],
                executor=ExecutorRef("callable", {"fn_name": "downstream_fn"}),
                inputs=[InputBinding("data", "route", "result")],
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED
        assert downstream_inputs.get("data") == "route_result"

    def test_sub_job_depth_guard_fails_gracefully(self):
        """Depth limit hit → run marked FAILED (not orphaned DELEGATED)."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "done"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        from stepwise.models import JobConfig
        store, engine = make_engine()
        # Set max_sub_job_depth to 0 to force depth guard
        job = engine.create_job("test", w, config=JobConfig(max_sub_job_depth=0))
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "route"][0]
        assert route_run.status == StepRunStatus.FAILED
        assert "sub-job" in route_run.error.lower() or "depth" in route_run.error.lower()

    def test_in_job_tree(self):
        """get_job_tree includes route sub-jobs."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "done"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        tree = engine.get_job_tree(job.id)
        assert len(tree["sub_jobs"]) == 1  # one route sub-job


# ── Attempt and Loops ─────────────────────────────────────────────────


class TestRouteAttemptAndLoops:
    def test_attempt_starts_at_one(self):
        """First launch has attempt=1 in namespace."""
        observed = {}

        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "done"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        # Use attempt in condition to verify it's 1
        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("first_try", "attempt == 1", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        runs = store.runs_for_job(job.id)
        route_run = [r for r in runs if r.step_name == "route"][0]
        assert route_run.executor_state["matched_route"] == "first_try"

    def test_when_uses_attempt(self):
        """when: "attempt > 1" expression works correctly."""
        register_step_fn("triage", lambda inputs: {"category": "any"})
        register_step_fn("worker", lambda inputs: {"result": "done"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        # attempt > 1 should NOT match on first try
        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("retry_only", "attempt > 1", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        # Should fail because attempt=1 and "attempt > 1" is False, no default
        assert job.status == JobStatus.FAILED


# ── Serialization Round-Trip ──────────────────────────────────────────


class TestRouteSerializationRoundTrip:
    def test_inline_flow_round_trip(self):
        """to_dict() → from_dict() preserves inline flow."""
        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("mock_llm", {}),
            ),
        })
        rd = RouteDefinition(routes=[
            RouteSpec("fast", "category == 'fast'", sub_flow, None),
            RouteSpec("default", None, sub_flow, None),
        ])
        step = StepDefinition(
            name="route_step", outputs=["result"],
            executor=ExecutorRef("route", {}),
            route_def=rd,
        )
        d = step.to_dict()
        restored = StepDefinition.from_dict(d)
        assert restored.route_def is not None
        assert len(restored.route_def.routes) == 2
        assert restored.route_def.routes[0].name == "fast"
        assert restored.route_def.routes[0].when == "category == 'fast'"
        assert restored.route_def.routes[0].flow is not None
        assert "worker" in restored.route_def.routes[0].flow.steps
        assert restored.route_def.routes[1].name == "default"
        assert restored.route_def.routes[1].when is None

    def test_file_ref_round_trip(self):
        """to_dict() bakes resolved flow inline, preserves flow_ref."""
        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("mock_llm", {}),
            ),
        })
        rd = RouteDefinition(routes=[
            RouteSpec("fast", "True", sub_flow, "flows/fast.yaml"),
        ])
        d = rd.to_dict()
        # Should have both flow (baked) and flow_ref (metadata)
        route_d = d["routes"][0]
        assert "flow" in route_d
        assert route_d["flow_ref"] == "flows/fast.yaml"

        # Restore without needing file access
        restored = RouteDefinition.from_dict(d)
        assert restored.routes[0].flow is not None
        assert "worker" in restored.routes[0].flow.steps
        assert restored.routes[0].flow_ref == "flows/fast.yaml"

    def test_registry_ref_round_trip(self):
        """to_dict() emits flow_ref (@author:name), no flow."""
        rd = RouteDefinition(routes=[
            RouteSpec("fast", "True", None, "@alice:fast-pipeline"),
        ])
        d = rd.to_dict()
        route_d = d["routes"][0]
        assert "flow" not in route_d or route_d.get("flow") is None
        assert route_d["flow_ref"] == "@alice:fast-pipeline"

        restored = RouteDefinition.from_dict(d)
        assert restored.routes[0].flow is None
        assert restored.routes[0].flow_ref == "@alice:fast-pipeline"


# ── File Ref Loading ──────────────────────────────────────────────────


class TestRouteWithFileRef:
    def test_loads_external_flow_file(self):
        """File path loads and runs."""
        with tempfile.TemporaryDirectory() as tmp:
            sub_path = Path(tmp) / "sub.yaml"
            sub_path.write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text(f"""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: {{ category: classify.category }}
    routes:
      fast:
        when: "category == 'fast'"
        flow: sub.yaml
    outputs: [result]
""")
            w = load_workflow_yaml(str(main_path))
            route_def = w.steps["route"].route_def
            assert route_def.routes[0].flow is not None
            assert "worker" in route_def.routes[0].flow.steps
            assert route_def.routes[0].flow_ref == "sub.yaml"

    def test_relative_path_resolution(self):
        """Resolves relative to parent flow directory."""
        with tempfile.TemporaryDirectory() as tmp:
            subdir = Path(tmp) / "flows"
            subdir.mkdir()
            sub_path = subdir / "sub.yaml"
            sub_path.write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: flows/sub.yaml
    outputs: [result]
""")
            w = load_workflow_yaml(str(main_path))
            assert w.steps["route"].route_def.routes[0].flow is not None

    def test_file_not_found_errors(self):
        """Missing file → clear error at parse time."""
        with tempfile.TemporaryDirectory() as tmp:
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: nonexistent.yaml
    outputs: [result]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(main_path))
            assert "not found" in str(exc_info.value).lower()


# ── Name-Based Route Flow Resolution ─────────────────────────────────


class TestRouteWithFlowName:
    def test_bare_name_resolves_from_flows_dir(self):
        """flow: my-sub resolves via flow name discovery."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create flows/my-sub/FLOW.yaml (directory flow)
            sub_dir = Path(tmp) / "flows" / "my-sub"
            sub_dir.mkdir(parents=True)
            (sub_dir / "FLOW.yaml").write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: my-sub
    outputs: [result]
""")
            w = load_workflow_yaml(str(main_path))
            route_def = w.steps["route"].route_def
            assert route_def.routes[0].flow is not None
            assert "worker" in route_def.routes[0].flow.steps
            assert route_def.routes[0].flow_ref == "my-sub"

    def test_bare_name_single_file_flow(self):
        """flow: my-sub resolves to my-sub.flow.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            flows_dir = Path(tmp) / "flows"
            flows_dir.mkdir()
            (flows_dir / "my-sub.flow.yaml").write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: my-sub
    outputs: [result]
""")
            w = load_workflow_yaml(str(main_path))
            assert w.steps["route"].route_def.routes[0].flow is not None
            assert "worker" in w.steps["route"].route_def.routes[0].flow.steps

    def test_bare_name_not_found_errors(self):
        """Non-existent bare name → clear error."""
        with tempfile.TemporaryDirectory() as tmp:
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  classify:
    executor: mock_llm
    prompt: classify
    outputs: [category, result]
  route:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'fast'"
        flow: nonexistent-flow
    outputs: [result]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(main_path))
            assert "not found" in str(exc_info.value).lower()

    def test_bare_name_circular_ref_detected(self):
        """Circular bare name ref raises error."""
        with tempfile.TemporaryDirectory() as tmp:
            flows_dir = Path(tmp) / "flows"
            flows_dir.mkdir()
            # main references sub-flow by name, sub-flow references main file
            # (which is what's currently being loaded → cycle)
            sub_dir = flows_dir / "my-sub"
            sub_dir.mkdir()
            main_path = Path(tmp) / "main.yaml"
            (sub_dir / "FLOW.yaml").write_text(f"""
steps:
  step1:
    executor: mock_llm
    prompt: step1
    outputs: [result]
    routes:
      r1:
        when: "True"
        flow: {main_path}
    outputs: [result]
""")
            main_path.write_text("""
steps:
  step1:
    executor: mock_llm
    prompt: classify
    outputs: [result]
    routes:
      r1:
        when: "True"
        flow: my-sub
    outputs: [result]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(main_path))
            assert "circular flow reference" in str(exc_info.value).lower()


# ── For-Each File Ref ─────────────────────────────────────────────────


class TestForEachFileRef:
    def test_for_each_with_file_ref(self):
        """for_each flow: accepts file path."""
        with tempfile.TemporaryDirectory() as tmp:
            sub_path = Path(tmp) / "sub.yaml"
            sub_path.write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text(f"""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items]
  each_item:
    for_each: produce.items
    flow: sub.yaml
    outputs: [results]
""")
            w = load_workflow_yaml(str(main_path))
            assert w.steps["each_item"].sub_flow is not None
            assert "worker" in w.steps["each_item"].sub_flow.steps

    def test_for_each_file_not_found(self):
        """Missing file → clear error."""
        with tempfile.TemporaryDirectory() as tmp:
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items]
  each_item:
    for_each: produce.items
    flow: nonexistent.yaml
    outputs: [results]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(main_path))
            assert "not found" in str(exc_info.value).lower()


# ── For-Each Name Resolution ─────────────────────────────────────────


class TestForEachWithFlowName:
    def test_bare_name_resolves_from_flows_dir(self):
        """for_each flow: my-sub resolves via flow name discovery."""
        with tempfile.TemporaryDirectory() as tmp:
            sub_dir = Path(tmp) / "flows" / "my-sub"
            sub_dir.mkdir(parents=True)
            (sub_dir / "FLOW.yaml").write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items]
  each_item:
    for_each: produce.items
    flow: my-sub
    outputs: [results]
""")
            w = load_workflow_yaml(str(main_path))
            assert w.steps["each_item"].sub_flow is not None
            assert "worker" in w.steps["each_item"].sub_flow.steps

    def test_bare_name_single_file_flow(self):
        """for_each flow: my-sub resolves to my-sub.flow.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            flows_dir = Path(tmp) / "flows"
            flows_dir.mkdir()
            (flows_dir / "my-sub.flow.yaml").write_text("""
steps:
  worker:
    executor: mock_llm
    prompt: work
    outputs: [result]
""")
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items]
  each_item:
    for_each: produce.items
    flow: my-sub
    outputs: [results]
""")
            w = load_workflow_yaml(str(main_path))
            assert w.steps["each_item"].sub_flow is not None
            assert "worker" in w.steps["each_item"].sub_flow.steps

    def test_bare_name_not_found_errors(self):
        """Non-existent bare name → clear error."""
        with tempfile.TemporaryDirectory() as tmp:
            main_path = Path(tmp) / "main.yaml"
            main_path.write_text("""
steps:
  produce:
    executor: mock_llm
    prompt: produce
    outputs: [items]
  each_item:
    for_each: produce.items
    flow: nonexistent-flow
    outputs: [results]
""")
            with pytest.raises(YAMLLoadError) as exc_info:
                load_workflow_yaml(str(main_path))
            assert "not found" in str(exc_info.value).lower()


# ── Events ────────────────────────────────────────────────────────────


class TestRouteEvents:
    def test_route_matched_event(self):
        """Successful route match emits ROUTE_MATCHED event."""
        register_step_fn("triage", lambda inputs: {"category": "fast"})
        register_step_fn("worker", lambda inputs: {"result": "done"})

        sub_flow = WorkflowDefinition(steps={
            "worker": StepDefinition(
                name="worker", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "worker"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "triage": StepDefinition(
                name="triage", outputs=["category"],
                executor=ExecutorRef("callable", {"fn_name": "triage"}),
            ),
            "route": StepDefinition(
                name="route", outputs=["result"],
                executor=ExecutorRef("route", {}),
                inputs=[InputBinding("category", "triage", "category")],
                route_def=RouteDefinition(routes=[
                    RouteSpec("fast", "True", sub_flow, None),
                ]),
            ),
        })

        store, engine = make_engine()
        job = engine.create_job("test", w)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        events = store.load_events(job.id)
        route_events = [e for e in events if e.type == "route.matched"]
        assert len(route_events) == 1
        assert route_events[0].data["route"] == "fast"

    def test_route_no_match_event(self):
        """Failed match emits ROUTE_NO_MATCH event."""
        register_step_fn("triage", lambda inputs: {"category": "unknown"})

        w = _make_basic_route_workflow({
            "fast": {"fn_name": "fast_fn", "condition": "category == 'fast'"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "unknown"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        events = store.load_events(job.id)
        no_match_events = [e for e in events if e.type == "route.no_match"]
        assert len(no_match_events) == 1

    def test_route_eval_error_event(self):
        """Expression error emits ROUTE_EVAL_ERROR event."""
        register_step_fn("triage", lambda inputs: {"category": "any"})

        w = _make_basic_route_workflow({
            "bad": {"fn_name": "fast_fn", "condition": "undefined_var"},
        })

        store, engine = make_engine()
        job = engine.create_job("test", w, inputs={"category": "any"})
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)

        events = store.load_events(job.id)
        eval_errors = [e for e in events if e.type == "route.eval_error"]
        assert len(eval_errors) == 1
