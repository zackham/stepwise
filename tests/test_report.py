"""Tests for HTML report generation."""

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from stepwise.models import (
    ExitRule,
    ExecutorRef,
    FlowMetadata,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.report import (
    StepReport,
    _compute_layers,
    _format_duration,
    _format_cost,
    default_report_path,
    generate_report,
    save_report,
)
from stepwise.store import SQLiteStore


# ── Helpers ──────────────────────────────────────────────────────────


def _make_workflow(steps_config: dict) -> WorkflowDefinition:
    """Create a workflow from a simplified config."""
    steps = {}
    for name, cfg in steps_config.items():
        inputs = []
        for local_name, source in cfg.get("inputs", {}).items():
            src_step, src_field = source.split(".")
            inputs.append(InputBinding(local_name=local_name, source_step=src_step, source_field=src_field))
        steps[name] = StepDefinition(
            name=name,
            outputs=cfg.get("outputs", []),
            executor=ExecutorRef(type=cfg.get("executor", "script")),
            inputs=inputs,
            exit_rules=[ExitRule.from_dict(r) for r in cfg.get("exit_rules", [])],
        )
    return WorkflowDefinition(steps=steps)


def _make_job(workflow: WorkflowDefinition, status: JobStatus = JobStatus.COMPLETED) -> Job:
    now = datetime.now(timezone.utc)
    return Job(
        id="job-test1234",
        objective="test-flow",
        workflow=workflow,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_run(
    job_id: str,
    step_name: str,
    attempt: int = 1,
    status: StepRunStatus = StepRunStatus.COMPLETED,
    artifact: dict | None = None,
    error: str | None = None,
) -> StepRun:
    started = datetime.now(timezone.utc) - timedelta(seconds=2)
    completed = datetime.now(timezone.utc)
    return StepRun(
        id=f"run-{step_name}-{attempt}",
        job_id=job_id,
        step_name=step_name,
        attempt=attempt,
        status=status,
        started_at=started,
        completed_at=completed if status in (StepRunStatus.COMPLETED, StepRunStatus.FAILED) else None,
        result=HandoffEnvelope(artifact=artifact or {"output": "test"}) if status == StepRunStatus.COMPLETED else None,
        error=error if status == StepRunStatus.FAILED else None,
    )


# ── Layer Computation ────────────────────────────────────────────────


class TestComputeLayers:
    def test_linear_three_steps(self):
        wf = _make_workflow({
            "fetch": {"outputs": ["data"]},
            "transform": {"outputs": ["cleaned"], "inputs": {"data": "fetch.data"}},
            "summarize": {"outputs": ["summary"], "inputs": {"cleaned": "transform.cleaned"}},
        })
        layers = _compute_layers(wf)
        assert len(layers) == 3
        assert layers[0] == ["fetch"]
        assert layers[1] == ["transform"]
        assert layers[2] == ["summarize"]

    def test_parallel_fan_out(self):
        wf = _make_workflow({
            "a": {"outputs": ["x"]},
            "b": {"outputs": ["y"]},
            "c": {"outputs": ["z"]},
            "merge": {"outputs": ["result"], "inputs": {"x": "a.x", "y": "b.y", "z": "c.z"}},
        })
        layers = _compute_layers(wf)
        assert len(layers) == 2
        # a, b, c should be in layer 0
        assert set(layers[0]) == {"a", "b", "c"}
        assert layers[1] == ["merge"]

    def test_single_step(self):
        wf = _make_workflow({"only": {"outputs": ["x"]}})
        layers = _compute_layers(wf)
        assert len(layers) == 1
        assert layers[0] == ["only"]

    def test_diamond(self):
        wf = _make_workflow({
            "start": {"outputs": ["x"]},
            "left": {"outputs": ["y"], "inputs": {"x": "start.x"}},
            "right": {"outputs": ["z"], "inputs": {"x": "start.x"}},
            "end": {"outputs": ["r"], "inputs": {"y": "left.y", "z": "right.z"}},
        })
        layers = _compute_layers(wf)
        assert len(layers) == 3
        assert layers[0] == ["start"]
        assert set(layers[1]) == {"left", "right"}
        assert layers[2] == ["end"]


# ── Format Helpers ───────────────────────────────────────────────────


class TestFormatHelpers:
    def test_duration_ms(self):
        assert _format_duration(0.005) == "5ms"

    def test_duration_seconds(self):
        assert _format_duration(3.2) == "3.2s"

    def test_duration_minutes(self):
        assert _format_duration(120) == "2.0m"

    def test_duration_hours(self):
        assert _format_duration(7200) == "2.0h"

    def test_cost_zero(self):
        assert _format_cost(0) == ""

    def test_cost_small(self):
        assert _format_cost(0.005) == "$0.0050"

    def test_cost_normal(self):
        assert _format_cost(1.234) == "$1.234"


# ── Default Report Path ─────────────────────────────────────────────


class TestDefaultReportPath:
    def test_flow_yaml(self):
        p = default_report_path(Path("/tmp/my-flow.flow.yaml"))
        assert p == Path("/tmp/my-flow-report.html")

    def test_plain_yaml(self):
        p = default_report_path(Path("/tmp/pipeline.yaml"))
        assert p == Path("/tmp/pipeline-report.html")


# ── Full Report Generation ───────────────────────────────────────────


class TestGenerateReport:
    def test_linear_completed(self):
        wf = _make_workflow({
            "fetch": {"outputs": ["data"]},
            "transform": {"outputs": ["cleaned"], "inputs": {"data": "fetch.data"}},
        })
        wf.metadata = FlowMetadata(name="test-pipeline", description="A test", author="tester")
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        for step in ["fetch", "transform"]:
            store.save_run(_make_run(job.id, step))

        html = generate_report(job, store, Path("test.flow.yaml"))

        assert "test-pipeline" in html
        assert "A test" in html
        assert "tester" in html
        assert "COMPLETED" in html.upper()
        assert "fetch" in html
        assert "transform" in html
        assert "svg" in html.lower()
        assert "<!DOCTYPE html>" in html
        store.close()

    def test_failed_job(self):
        wf = _make_workflow({
            "setup": {"outputs": ["ready"]},
            "deploy": {"outputs": ["url"], "inputs": {"ready": "setup.ready"}},
        })
        job = _make_job(wf, status=JobStatus.FAILED)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "setup"))
        store.save_run(_make_run(job.id, "deploy", status=StepRunStatus.FAILED, error="Connection refused"))

        html = generate_report(job, store, Path("fail.flow.yaml"))

        assert "FAILED" in html.upper()
        assert "Connection refused" in html
        assert "error-box" in html
        store.close()

    def test_loop_with_attempts(self):
        wf = _make_workflow({
            "generate": {"outputs": ["content", "score"]},
            "publish": {"outputs": ["url"], "inputs": {"content": "generate.content"}},
        })
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        for attempt in range(1, 4):
            store.save_run(_make_run(job.id, "generate", attempt=attempt, artifact={"score": 0.5 + attempt * 0.1}))
        store.save_run(_make_run(job.id, "publish"))

        html = generate_report(job, store)

        assert "Attempt 1" in html
        assert "Attempt 2" in html
        assert "Attempt 3" in html
        assert "3 runs" in html  # shown in step detail header
        store.close()

    def test_parallel_steps(self):
        wf = _make_workflow({
            "a": {"outputs": ["x"]},
            "b": {"outputs": ["y"]},
            "merge": {"outputs": ["r"], "inputs": {"x": "a.x", "y": "b.y"}},
        })
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        for step in ["a", "b", "merge"]:
            store.save_run(_make_run(job.id, step))

        html = generate_report(job, store)

        assert "merge" in html
        assert "<path d=" in html  # SVG edges
        store.close()

    def test_save_report(self, tmp_path):
        html = "<html><body>test</body></html>"
        out = save_report(html, tmp_path / "report.html")
        assert out.exists()
        assert out.read_text() == html

    def test_sidecar_rendering(self):
        wf = _make_workflow({"step1": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        run = _make_run(job.id, "step1")
        run.result = HandoffEnvelope(
            artifact={"answer": 42},
            sidecar=Sidecar(
                decisions_made=["Used algorithm A"],
                assumptions=["Data is sorted"],
            ),
        )
        store.save_run(run)

        html = generate_report(job, store)
        assert "Used algorithm A" in html
        assert "Data is sorted" in html
        store.close()

    def test_executor_meta_rendering(self):
        wf = _make_workflow({"llm_step": {"outputs": ["text"], "executor": "llm"}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        run = _make_run(job.id, "llm_step")
        run.result = HandoffEnvelope(
            artifact={"text": "hello"},
            executor_meta={"model": "gpt-4", "tokens": 1500, "cost_usd": 0.05},
        )
        store.save_run(run)

        html = generate_report(job, store)
        assert "gpt-4" in html
        assert "1500" in html
        store.close()

    def test_no_metadata(self):
        """Report works even without flow metadata."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert "<!DOCTYPE html>" in html
        assert "test-flow" in html  # uses objective as fallback
        store.close()

    def test_cost_tracking(self):
        wf = _make_workflow({"expensive": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        run = _make_run(job.id, "expensive")
        store.save_run(run)
        store.save_step_event(run.id, "cost", {"cost_usd": 0.15})

        html = generate_report(job, store)
        assert "$0.150" in html
        store.close()

    def test_details_summary_element(self):
        """Step details use native <details>/<summary> (zero JS)."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert "<details" in html
        assert "<summary>" in html
        # Should NOT have onclick toggling
        assert "onclick" not in html
        store.close()

    def test_anchor_links_in_dag(self):
        """DAG nodes link to step detail sections."""
        wf = _make_workflow({
            "fetch": {"outputs": ["data"]},
            "process": {"outputs": ["result"], "inputs": {"data": "fetch.data"}},
        })
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        for step in ["fetch", "process"]:
            store.save_run(_make_run(job.id, step))

        html = generate_report(job, store)
        assert 'href="#step-fetch"' in html
        assert 'href="#step-process"' in html
        assert 'id="step-fetch"' in html
        assert 'id="step-process"' in html
        store.close()

    def test_light_mode_css(self):
        """Report includes prefers-color-scheme light mode."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert "prefers-color-scheme: light" in html
        store.close()

    def test_print_stylesheet(self):
        """Report includes print media query."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert "@media print" in html
        store.close()

    def test_yaml_appendix(self, tmp_path):
        """Report includes YAML source when flow_path exists."""
        yaml_content = "name: test\nsteps:\n  step:\n    run: echo hello\n"
        flow_file = tmp_path / "test.flow.yaml"
        flow_file.write_text(yaml_content)

        wf = _make_workflow({"step": {"outputs": ["x"]}})
        wf.metadata = FlowMetadata(name="test")
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store, flow_file)
        assert "Flow Source" in html
        assert "echo hello" in html
        assert "yaml-appendix" in html
        store.close()

    def test_multi_step_loopback(self):
        """Report handles loop-back to earlier step (review → research)."""
        wf = _make_workflow({
            "research": {"outputs": ["findings"]},
            "draft": {"outputs": ["content"], "inputs": {"findings": "research.findings"}},
            "review": {"outputs": ["verdict"], "inputs": {"content": "draft.content"}},
            "publish": {"outputs": ["url"], "inputs": {"content": "draft.content"}},
        })
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        # Simulate: research→draft→review (loop back) → research→draft→review (advance) → publish
        for attempt in range(1, 3):
            store.save_run(_make_run(job.id, "research", attempt=attempt))
            store.save_run(_make_run(job.id, "draft", attempt=attempt))
            store.save_run(_make_run(job.id, "review", attempt=attempt))
        store.save_run(_make_run(job.id, "publish"))

        html = generate_report(job, store)

        # All steps present
        assert "research" in html
        assert "draft" in html
        assert "review" in html
        assert "publish" in html
        # Multiple runs tracked
        assert "7" in html  # 7 total runs in stats
        assert "2 runs" in html  # shown on multi-attempt step details
        assert "Attempt 1" in html
        assert "Attempt 2" in html
        # Attempt badges on DAG nodes
        assert ">2<" in html  # badge text "2" in SVG
        store.close()

    def test_no_yaml_without_path(self):
        """No YAML appendix section when flow_path is None."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert '<details class="yaml-appendix"' not in html
        assert "Flow Source" not in html
        store.close()

    # Chain metadata rendering test removed — chains feature was replaced by named sessions.


# ── Copy Button Tests ───────────────────────────────────────────────


class TestCopyButtons:
    def test_copy_button_in_output(self):
        """Copy buttons appear in generated HTML."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert '<button class="copy-btn">Copy</button>' in html
        assert 'class="copyable"' in html
        store.close()

    def test_copy_button_javascript(self):
        """Report includes clipboard JS."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert "navigator.clipboard.writeText" in html
        assert "document.execCommand" in html  # fallback
        assert "<script>" in html
        store.close()

    def test_copy_button_in_yaml_appendix(self, tmp_path):
        """Copy button appears in YAML source section."""
        flow_file = tmp_path / "test.flow.yaml"
        flow_file.write_text("name: test\nsteps:\n  s:\n    run: echo hi\n")

        wf = _make_workflow({"s": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "s"))

        html = generate_report(job, store, flow_file)
        # YAML appendix should have a copy button
        assert html.count('<button class="copy-btn">Copy</button>') >= 2  # at least step output + yaml
        store.close()

    def test_copy_button_hidden_in_print(self):
        """Print CSS hides copy buttons."""
        wf = _make_workflow({"step": {"outputs": ["x"]}})
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step"))

        html = generate_report(job, store)
        assert ".copy-btn" in html
        assert "@media print" in html
        store.close()


# ── Sub-Job Tests ───────────────────────────────────────────────────


class TestSubJobVisualization:
    def test_for_each_children_rendered(self):
        """For-each sub-jobs appear in the report."""
        wf = _make_workflow({
            "fan-out": {"outputs": ["results"]},
        })
        parent_job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(parent_job)

        # Create parent run with sub_job_ids in executor_state
        child_ids = []
        for i in range(3):
            child_wf = _make_workflow({"process": {"outputs": ["result"]}})
            child_job = Job(
                id=f"child-{i}",
                objective=f"item-{i}",
                workflow=child_wf,
                status=JobStatus.COMPLETED,
                created_at=parent_job.created_at,
                updated_at=parent_job.updated_at,
                parent_job_id=parent_job.id,
            )
            store.save_job(child_job)
            store.save_run(_make_run(child_job.id, "process", artifact={"result": f"done-{i}"}))
            child_ids.append(child_job.id)

        parent_run = _make_run(parent_job.id, "fan-out")
        parent_run.executor_state = {"sub_job_ids": child_ids}
        store.save_run(parent_run)

        html = generate_report(parent_job, store)

        assert "Sub-Jobs (3)" in html
        assert "sub-job-detail" in html
        assert "sub-job-step-table" in html
        assert "process" in html
        for i in range(3):
            assert f"item-{i}" in html
        store.close()

    def test_delegation_child_rendered(self):
        """Delegation sub-job appears in the report."""
        wf = _make_workflow({
            "implement": {"outputs": ["result"], "executor": "agent"},
        })
        parent_job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(parent_job)

        # Create child job
        child_wf = _make_workflow({
            "build": {"outputs": ["artifact"]},
            "test": {"outputs": ["passed"], "inputs": {"artifact": "build.artifact"}},
        })
        child_job = Job(
            id="child-delegated",
            objective="sub-flow",
            workflow=child_wf,
            status=JobStatus.COMPLETED,
            created_at=parent_job.created_at,
            updated_at=parent_job.updated_at,
            parent_job_id=parent_job.id,
        )
        store.save_job(child_job)
        store.save_run(_make_run(child_job.id, "build", artifact={"artifact": "built"}))
        store.save_run(_make_run(child_job.id, "test", artifact={"passed": True}))

        parent_run = _make_run(parent_job.id, "implement")
        parent_run.sub_job_id = "child-delegated"
        store.save_run(parent_run)

        html = generate_report(parent_job, store)

        assert "Sub-Jobs (1)" in html
        assert "sub-flow" in html
        assert "build" in html
        assert "test" in html
        store.close()

    def test_nested_sub_jobs(self):
        """Nested sub-jobs (parent → child → grandchild) rendered recursively."""
        wf = _make_workflow({"outer": {"outputs": ["x"]}})
        parent_job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(parent_job)

        # Child
        child_wf = _make_workflow({"inner": {"outputs": ["y"]}})
        child_job = Job(
            id="child-nested",
            objective="child-flow",
            workflow=child_wf,
            status=JobStatus.COMPLETED,
            created_at=parent_job.created_at,
            updated_at=parent_job.updated_at,
            parent_job_id=parent_job.id,
        )
        store.save_job(child_job)

        # Grandchild
        gc_wf = _make_workflow({"deep": {"outputs": ["z"]}})
        gc_job = Job(
            id="grandchild",
            objective="grandchild-flow",
            workflow=gc_wf,
            status=JobStatus.COMPLETED,
            created_at=parent_job.created_at,
            updated_at=parent_job.updated_at,
            parent_job_id=child_job.id,
        )
        store.save_job(gc_job)
        store.save_run(_make_run(gc_job.id, "deep", artifact={"z": "deepval"}))

        child_run = _make_run(child_job.id, "inner")
        child_run.sub_job_id = "grandchild"
        store.save_run(child_run)

        parent_run = _make_run(parent_job.id, "outer")
        parent_run.sub_job_id = "child-nested"
        store.save_run(parent_run)

        html = generate_report(parent_job, store)

        assert "child-flow" in html
        assert "grandchild-flow" in html
        assert "deep" in html
        store.close()

    def test_no_sub_jobs_no_regression(self):
        """Simple job without sub-jobs renders identically (no sub-job section)."""
        wf = _make_workflow({
            "step-a": {"outputs": ["x"]},
            "step-b": {"outputs": ["y"], "inputs": {"x": "step-a.x"}},
        })
        job = _make_job(wf)
        store = SQLiteStore()
        store.save_job(job)
        store.save_run(_make_run(job.id, "step-a"))
        store.save_run(_make_run(job.id, "step-b"))

        html = generate_report(job, store)

        assert "Sub-Jobs" not in html
        assert '<details class="sub-job-detail">' not in html
        assert "step-a" in html
        assert "step-b" in html
        store.close()
