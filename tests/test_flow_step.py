"""Tests for direct flow step composition (flow: on a step).

Covers: YAML parsing, mutual exclusivity, output contract validation,
engine sub-flow delegation, input passing, nested flow steps, cycle
detection, file refs, bare name refs, and integration.
"""

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
    reg.register("sub_flow", lambda config: CallableExecutor(fn_name="__noop__"))
    return store, Engine(store=store, registry=reg)


def tick_until_done(engine, job_id, max_ticks=50):
    for _ in range(max_ticks):
        job = engine.get_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job
        engine.tick()
    return engine.get_job(job_id)


def json_echo(fields: dict) -> str:
    """Build an echo command that outputs JSON. For use in Python-constructed workflows."""
    import json
    return f"echo '{json.dumps(fields)}'"


# ── YAML Parsing ─────────────────────────────────────────────────────


class TestFlowStepParsing:
    def test_parse_inline_flow(self):
        """flow: with inline steps dict."""
        wf = load_workflow_string(
            "steps:\n"
            "  delegate:\n"
            "    flow:\n"
            "      steps:\n"
            "        inner:\n"
            "          run: echo ok\n"
            "          outputs: [result]\n"
            "    outputs: [result]\n"
        )
        step = wf.steps["delegate"]
        assert step.executor.type == "sub_flow"
        assert step.sub_flow is not None
        assert "inner" in step.sub_flow.steps

    def test_parse_file_ref(self):
        """flow: with a .yaml file path."""
        with tempfile.TemporaryDirectory() as d:
            sub_path = Path(d) / "sub.flow.yaml"
            sub_path.write_text(
                "steps:\n"
                "  work:\n"
                "    run: echo answer\n"
                "    outputs: [answer]\n"
            )
            main_path = Path(d) / "main.flow.yaml"
            main_path.write_text(
                "steps:\n"
                "  delegate:\n"
                "    flow: sub.flow.yaml\n"
                "    outputs: [answer]\n"
            )
            wf = load_workflow_yaml(str(main_path))
            step = wf.steps["delegate"]
            assert step.executor.type == "sub_flow"
            assert step.sub_flow is not None
            assert "work" in step.sub_flow.steps
            assert step.executor.config.get("flow_ref") == "sub.flow.yaml"

    def test_parse_bare_name(self):
        """flow: with a bare flow name resolved via project discovery."""
        with tempfile.TemporaryDirectory() as d:
            flows_dir = Path(d) / "flows"
            flows_dir.mkdir()
            sub_dir = flows_dir / "helper"
            sub_dir.mkdir()
            (sub_dir / "FLOW.yaml").write_text(
                "steps:\n"
                "  work:\n"
                "    run: echo data\n"
                "    outputs: [data]\n"
            )
            main_path = flows_dir / "main.flow.yaml"
            main_path.write_text(
                "steps:\n"
                "  delegate:\n"
                "    flow: helper\n"
                "    outputs: [data]\n"
            )
            wf = load_workflow_yaml(str(main_path), project_dir=Path(d))
            step = wf.steps["delegate"]
            assert step.executor.type == "sub_flow"
            assert step.sub_flow is not None
            assert step.executor.config.get("flow_ref") == "helper"

    def test_mutual_exclusivity_flow_and_routes(self):
        with pytest.raises(YAMLLoadError, match="cannot combine flow and routes"):
            load_workflow_string(
                "steps:\n"
                "  bad:\n"
                "    flow:\n"
                "      steps:\n"
                "        x:\n"
                "          run: echo x\n"
                "          outputs: [a]\n"
                "    routes:\n"
                "      default:\n"
                "        flow:\n"
                "          steps:\n"
                "            y:\n"
                "              run: echo y\n"
                "              outputs: [a]\n"
                "    outputs: [a]\n"
            )

    def test_mutual_exclusivity_flow_and_run(self):
        with pytest.raises(YAMLLoadError, match="cannot combine flow with run/executor"):
            load_workflow_string(
                "steps:\n"
                "  bad:\n"
                "    flow:\n"
                "      steps:\n"
                "        x:\n"
                "          run: echo x\n"
                "          outputs: [a]\n"
                "    run: echo hi\n"
                "    outputs: [a]\n"
            )

    def test_mutual_exclusivity_flow_and_executor(self):
        with pytest.raises(YAMLLoadError, match="cannot combine flow with run/executor"):
            load_workflow_string(
                "steps:\n"
                "  bad:\n"
                "    flow:\n"
                "      steps:\n"
                "        x:\n"
                "          run: echo x\n"
                "          outputs: [a]\n"
                "    executor: human\n"
                "    outputs: [a]\n"
            )

    def test_output_contract_missing_outputs(self):
        """Sub-flow terminal step must cover declared outputs."""
        with pytest.raises(YAMLLoadError, match="missing outputs"):
            load_workflow_string(
                "steps:\n"
                "  delegate:\n"
                "    flow:\n"
                "      steps:\n"
                "        inner:\n"
                "          run: echo a\n"
                "          outputs: [a]\n"
                "    outputs: [a, b]\n"
            )

    def test_output_contract_no_outputs_declared(self):
        """Flow steps must declare outputs."""
        with pytest.raises(YAMLLoadError, match="flow steps must declare outputs"):
            load_workflow_string(
                "steps:\n"
                "  delegate:\n"
                "    flow:\n"
                "      steps:\n"
                "        inner:\n"
                "          run: echo ok\n"
            )

    def test_cycle_detection_file_ref(self):
        """A -> B -> A should raise cycle error."""
        with tempfile.TemporaryDirectory() as d:
            a_path = Path(d) / "a.flow.yaml"
            b_path = Path(d) / "b.flow.yaml"

            a_path.write_text(
                "steps:\n"
                "  delegate:\n"
                "    flow: b.flow.yaml\n"
                "    outputs: [x]\n"
            )
            b_path.write_text(
                "steps:\n"
                "  delegate:\n"
                "    flow: a.flow.yaml\n"
                "    outputs: [x]\n"
            )
            with pytest.raises(YAMLLoadError, match="circular flow reference"):
                load_workflow_yaml(str(a_path))

    def test_serialization_round_trip(self):
        """StepDefinition with sub_flow survives to_dict/from_dict."""
        wf = load_workflow_string(
            "steps:\n"
            "  delegate:\n"
            "    flow:\n"
            "      steps:\n"
            "        inner:\n"
            "          run: echo ok\n"
            "          outputs: [result]\n"
            "    outputs: [result]\n"
        )
        d = wf.to_dict()
        wf2 = WorkflowDefinition.from_dict(d)
        step = wf2.steps["delegate"]
        assert step.executor.type == "sub_flow"
        assert step.sub_flow is not None
        assert "inner" in step.sub_flow.steps

    def test_inputs_parsed(self):
        """Flow step with input bindings from upstream."""
        wf = load_workflow_string(
            "steps:\n"
            "  research:\n"
            "    run: echo findings\n"
            "    outputs: [findings]\n"
            "  council:\n"
            "    flow:\n"
            "      steps:\n"
            "        analyze:\n"
            "          run: echo consensus\n"
            "          outputs: [consensus]\n"
            "    inputs:\n"
            "      question: research.findings\n"
            "    outputs: [consensus]\n"
        )
        step = wf.steps["council"]
        assert len(step.inputs) == 1
        assert step.inputs[0].local_name == "question"
        assert step.inputs[0].source_step == "research"
        assert step.inputs[0].source_field == "findings"


# ── Engine Execution ─────────────────────────────────────────────────


class TestFlowStepExecution:
    def test_basic_sub_flow_execution(self):
        """Sub-flow step runs and parent completes with outputs."""
        store, engine = make_engine()

        inner_wf = WorkflowDefinition(steps={
            "work": StepDefinition(
                name="work",
                executor=ExecutorRef("script", {"command": json_echo({"result": "ok"})}),
                outputs=["result"],
            ),
        })

        outer_wf = WorkflowDefinition(steps={
            "delegate": StepDefinition(
                name="delegate",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["result"],
                sub_flow=inner_wf,
            ),
        })

        job = engine.create_job("test sub-flow", outer_wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        run = store.latest_completed_run(job.id, "delegate")
        assert run is not None
        assert run.result.artifact["result"] == "ok"

    def test_input_passing_to_sub_flow(self):
        """Parent inputs flow through to sub-flow job."""
        store, engine = make_engine()

        inner_wf = WorkflowDefinition(steps={
            "echo_it": StepDefinition(
                name="echo_it",
                executor=ExecutorRef("script", {"command": json_echo({"output": "received"})}),
                outputs=["output"],
                inputs=[InputBinding("data", "$job", "data")],
            ),
        })

        outer_wf = WorkflowDefinition(steps={
            "produce": StepDefinition(
                name="produce",
                executor=ExecutorRef("script", {"command": json_echo({"data": "hello"})}),
                outputs=["data"],
            ),
            "delegate": StepDefinition(
                name="delegate",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["output"],
                sub_flow=inner_wf,
                inputs=[InputBinding("data", "produce", "data")],
            ),
        })

        job = engine.create_job("test input passing", outer_wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        run = store.latest_completed_run(job.id, "delegate")
        assert run is not None
        assert run.result.artifact["output"] == "received"

    def test_nested_sub_flows(self):
        """Flow step inside a flow step (2 levels deep)."""
        store, engine = make_engine()

        level2_wf = WorkflowDefinition(steps={
            "deep": StepDefinition(
                name="deep",
                executor=ExecutorRef("script", {"command": json_echo({"value": "deep"})}),
                outputs=["value"],
            ),
        })

        level1_wf = WorkflowDefinition(steps={
            "mid": StepDefinition(
                name="mid",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["value"],
                sub_flow=level2_wf,
            ),
        })

        outer_wf = WorkflowDefinition(steps={
            "top": StepDefinition(
                name="top",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["value"],
                sub_flow=level1_wf,
            ),
        })

        job = engine.create_job("nested test", outer_wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        run = store.latest_completed_run(job.id, "top")
        assert run is not None
        assert run.result.artifact["value"] == "deep"

    def test_sub_flow_failure_propagates(self):
        """If sub-flow fails, parent step and job fail."""
        store, engine = make_engine()

        inner_wf = WorkflowDefinition(steps={
            "fail": StepDefinition(
                name="fail",
                executor=ExecutorRef("script", {"command": "exit 1"}),
                outputs=["x"],
            ),
        })

        outer_wf = WorkflowDefinition(steps={
            "delegate": StepDefinition(
                name="delegate",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["x"],
                sub_flow=inner_wf,
            ),
        })

        job = engine.create_job("failure test", outer_wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.FAILED


# ── Integration ──────────────────────────────────────────────────────


class TestFlowStepIntegration:
    def test_yaml_to_execution_pipeline(self):
        """Parse YAML with flow step, run it through engine (programmatic construction)."""
        store, engine = make_engine()

        # Build via Python (YAML parsing tested in TestFlowStepParsing)
        inner_wf = WorkflowDefinition(steps={
            "analyze": StepDefinition(
                name="analyze",
                executor=ExecutorRef("script", {"command": json_echo({"consensus": "agreed"})}),
                outputs=["consensus"],
            ),
        })

        outer_wf = WorkflowDefinition(steps={
            "research": StepDefinition(
                name="research",
                executor=ExecutorRef("script", {"command": json_echo({"findings": "data"})}),
                outputs=["findings"],
            ),
            "council": StepDefinition(
                name="council",
                executor=ExecutorRef("sub_flow", {}),
                outputs=["consensus"],
                sub_flow=inner_wf,
                inputs=[InputBinding("question", "research", "findings")],
            ),
        })

        job = engine.create_job("integration test", outer_wf)
        engine.start_job(job.id)
        job = tick_until_done(engine, job.id)
        assert job.status == JobStatus.COMPLETED

        run = store.latest_completed_run(job.id, "council")
        assert run is not None
        assert run.result.artifact["consensus"] == "agreed"

    def test_flow_step_with_file_ref_execution(self):
        """Flow step referencing a file runs correctly."""
        with tempfile.TemporaryDirectory() as d:
            sub_path = Path(d) / "sub.flow.yaml"
            sub_path.write_text(
                "steps:\n"
                "  work:\n"
                '    run: "echo \'{\\"answer\\": \\"42\\"}\'"\n'
                "    outputs: [answer]\n"
            )
            main_path = Path(d) / "main.flow.yaml"
            main_path.write_text(
                "steps:\n"
                "  delegate:\n"
                "    flow: sub.flow.yaml\n"
                "    outputs: [answer]\n"
            )
            wf = load_workflow_yaml(str(main_path))
            store, engine = make_engine()
            job = engine.create_job("file ref test", wf)
            engine.start_job(job.id)
            job = tick_until_done(engine, job.id)
            assert job.status == JobStatus.COMPLETED

            run = store.latest_completed_run(job.id, "delegate")
            assert run is not None
            assert run.result.artifact["answer"] == "42"
