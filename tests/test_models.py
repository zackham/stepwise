"""Tests for data model serialization, validation, and workflow construction."""

import json

from stepwise.models import (
    InputBinding,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepStatus,
    WorkflowDefinition,
)


# ── InputBinding ─────────────────────────────────────────────────────


class TestInputBinding:
    def test_roundtrip(self):
        binding = InputBinding("step_a", "output_key", "input_key")
        d = binding.to_dict()
        restored = InputBinding.from_dict(d)
        assert restored.source_step == "step_a"
        assert restored.source_key == "output_key"
        assert restored.target_key == "input_key"

    def test_to_dict_keys(self):
        b = InputBinding("src", "sk", "tk")
        d = b.to_dict()
        assert set(d.keys()) == {"source_step", "source_key", "target_key"}


# ── StepDefinition ───────────────────────────────────────────────────


class TestStepDefinition:
    def test_defaults(self):
        sd = StepDefinition(name="s1", executor="script")
        assert sd.config == {}
        assert sd.depends_on == []
        assert sd.inputs == []
        assert sd.max_retries == 0
        assert sd.timeout_seconds is None
        assert sd.condition is None
        assert sd.loop_over is None
        assert sd.is_sub_job is False

    def test_roundtrip(self):
        sd = StepDefinition(
            name="fetch",
            executor="script",
            config={"command": "curl http://example.com"},
            depends_on=["init"],
            inputs=[InputBinding("init", "url", "target_url")],
            max_retries=3,
            timeout_seconds=30.0,
            condition="steps['init']['ready']",
            loop_over="init.urls",
            is_sub_job=False,
        )
        d = sd.to_dict()
        restored = StepDefinition.from_dict(d)
        assert restored.name == "fetch"
        assert restored.executor == "script"
        assert restored.max_retries == 3
        assert restored.timeout_seconds == 30.0
        assert restored.condition == "steps['init']['ready']"
        assert restored.loop_over == "init.urls"
        assert len(restored.inputs) == 1
        assert restored.inputs[0].source_step == "init"

    def test_json_serializable(self):
        sd = StepDefinition(name="s", executor="e", config={"key": [1, 2, 3]})
        serialized = json.dumps(sd.to_dict())
        assert isinstance(serialized, str)


# ── StepRun ──────────────────────────────────────────────────────────


class TestStepRun:
    def test_create(self):
        sr = StepRun.create(job_id="j1", step_name="step_a")
        assert sr.job_id == "j1"
        assert sr.step_name == "step_a"
        assert sr.status == StepStatus.PENDING
        assert sr.attempt == 1
        assert sr.id  # UUID generated

    def test_roundtrip(self):
        sr = StepRun.create(job_id="j1", step_name="s1")
        sr.status = StepStatus.COMPLETED
        sr.inputs = {"x": 1}
        sr.outputs = {"y": 2}
        sr.input_hash = sr.compute_input_hash()

        d = sr.to_dict()
        restored = StepRun.from_dict(d)
        assert restored.status == StepStatus.COMPLETED
        assert restored.inputs == {"x": 1}
        assert restored.outputs == {"y": 2}
        assert restored.input_hash == sr.input_hash

    def test_input_hash_deterministic(self):
        sr1 = StepRun.create("j", "s", inputs={"a": 1, "b": 2})
        sr2 = StepRun.create("j", "s", inputs={"b": 2, "a": 1})
        assert sr1.compute_input_hash() == sr2.compute_input_hash()

    def test_input_hash_differs(self):
        sr1 = StepRun.create("j", "s", inputs={"a": 1})
        sr2 = StepRun.create("j", "s", inputs={"a": 2})
        assert sr1.compute_input_hash() != sr2.compute_input_hash()

    def test_iteration_fields(self):
        sr = StepRun.create(
            "j", "s", iteration_index=2, iteration_value="item_2"
        )
        d = sr.to_dict()
        restored = StepRun.from_dict(d)
        assert restored.iteration_index == 2
        assert restored.iteration_value == "item_2"


# ── WorkflowDefinition ──────────────────────────────────────────────


class TestWorkflowDefinition:
    def test_empty_workflow_valid(self):
        wf = WorkflowDefinition(name="empty")
        assert wf.validate() == []

    def test_linear_workflow(self):
        wf = WorkflowDefinition(
            name="linear",
            steps=[
                StepDefinition(name="a", executor="script"),
                StepDefinition(name="b", executor="script", depends_on=["a"]),
                StepDefinition(name="c", executor="script", depends_on=["b"]),
            ],
        )
        assert wf.validate() == []
        order = wf.topological_order()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_get_step(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="s1", executor="script")],
        )
        assert wf.get_step("s1") is not None
        assert wf.get_step("nonexistent") is None

    def test_validate_unknown_dep(self):
        wf = WorkflowDefinition(
            name="bad",
            steps=[
                StepDefinition(
                    name="a", executor="script", depends_on=["nonexistent"]
                ),
            ],
        )
        errors = wf.validate()
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_validate_unknown_input_source(self):
        wf = WorkflowDefinition(
            name="bad",
            steps=[
                StepDefinition(
                    name="a",
                    executor="script",
                    inputs=[InputBinding("ghost", "key", "key")],
                ),
            ],
        )
        errors = wf.validate()
        assert any("ghost" in e for e in errors)

    def test_validate_duplicate_names(self):
        wf = WorkflowDefinition(
            name="dup",
            steps=[
                StepDefinition(name="a", executor="script"),
                StepDefinition(name="a", executor="script"),
            ],
        )
        errors = wf.validate()
        assert any("Duplicate" in e for e in errors)

    def test_validate_cycle(self):
        wf = WorkflowDefinition(
            name="cycle",
            steps=[
                StepDefinition(name="a", executor="script", depends_on=["c"]),
                StepDefinition(name="b", executor="script", depends_on=["a"]),
                StepDefinition(name="c", executor="script", depends_on=["b"]),
            ],
        )
        errors = wf.validate()
        assert any("Cycle" in e for e in errors)

    def test_validate_loop_over_unknown(self):
        wf = WorkflowDefinition(
            name="bad_loop",
            steps=[
                StepDefinition(
                    name="a", executor="script", loop_over="ghost.items"
                ),
            ],
        )
        errors = wf.validate()
        assert any("ghost" in e for e in errors)

    def test_roundtrip(self):
        wf = WorkflowDefinition(
            name="test",
            description="A test workflow",
            steps=[
                StepDefinition(name="a", executor="script", config={"x": 1}),
                StepDefinition(name="b", executor="script", depends_on=["a"]),
            ],
        )
        d = wf.to_dict()
        restored = WorkflowDefinition.from_dict(d)
        assert restored.name == "test"
        assert restored.description == "A test workflow"
        assert len(restored.steps) == 2
        assert restored.steps[1].depends_on == ["a"]

    def test_topological_order_independent(self):
        wf = WorkflowDefinition(
            name="parallel",
            steps=[
                StepDefinition(name="a", executor="script"),
                StepDefinition(name="b", executor="script"),
                StepDefinition(name="c", executor="script"),
            ],
        )
        order = wf.topological_order()
        assert set(order) == {"a", "b", "c"}


# ── Job ──────────────────────────────────────────────────────────────


class TestJob:
    def test_create(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[
                StepDefinition(name="a", executor="script"),
                StepDefinition(name="b", executor="script", depends_on=["a"]),
            ],
        )
        job = Job.create(wf, inputs={"x": 1})
        assert job.status == JobStatus.PENDING
        assert job.inputs == {"x": 1}
        assert "a" in job.step_runs
        assert "b" in job.step_runs
        assert job.step_runs["a"].status == StepStatus.PENDING

    def test_create_with_parent(self):
        wf = WorkflowDefinition(name="child", steps=[])
        job = Job.create(wf, parent_job_id="parent_123")
        assert job.parent_job_id == "parent_123"

    def test_roundtrip(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script")],
        )
        job = Job.create(wf, inputs={"key": "value"})
        job.status = JobStatus.COMPLETED
        job.outputs = {"result": 42}

        d = job.to_dict()
        restored = Job.from_dict(d)
        assert restored.id == job.id
        assert restored.status == JobStatus.COMPLETED
        assert restored.inputs == {"key": "value"}
        assert restored.outputs == {"result": 42}
        assert "a" in restored.step_runs

    def test_get_step_run(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script")],
        )
        job = Job.create(wf)
        assert job.get_step_run("a") is not None
        assert job.get_step_run("nonexistent") is None

    def test_json_roundtrip(self):
        wf = WorkflowDefinition(
            name="test",
            steps=[StepDefinition(name="a", executor="script", config={"k": 1})],
        )
        job = Job.create(wf, inputs={"x": [1, 2, 3]})
        serialized = json.dumps(job.to_dict())
        restored = Job.from_dict(json.loads(serialized))
        assert restored.inputs == {"x": [1, 2, 3]}
