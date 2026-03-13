"""Tests for typed output fields (OutputFieldSpec, YAML parsing, engine validation)."""

import pytest

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    OutputFieldSpec,
    StepDefinition,
    StepRun,
    StepRunStatus,
    HandoffEnvelope,
    Sidecar,
    WatchSpec,
    WorkflowDefinition,
    _now,
)
from stepwise.yaml_loader import load_workflow_string, YAMLLoadError
from stepwise.engine import Engine
from tests.conftest import register_step_fn, run_job_sync


# ── OutputFieldSpec serialization ────────────────────────────────────


class TestOutputFieldSpec:
    def test_default_to_dict_is_empty(self):
        spec = OutputFieldSpec()
        assert spec.to_dict() == {}

    def test_roundtrip_full(self):
        spec = OutputFieldSpec(
            type="choice",
            required=False,
            default="a",
            description="Pick one",
            options=["a", "b", "c"],
            multiple=True,
            min=None,
            max=None,
        )
        d = spec.to_dict()
        restored = OutputFieldSpec.from_dict(d)
        assert restored.type == "choice"
        assert restored.required is False
        assert restored.default == "a"
        assert restored.description == "Pick one"
        assert restored.options == ["a", "b", "c"]
        assert restored.multiple is True

    def test_roundtrip_number(self):
        spec = OutputFieldSpec(type="number", min=0.0, max=10.0)
        d = spec.to_dict()
        assert d == {"type": "number", "min": 0.0, "max": 10.0}
        restored = OutputFieldSpec.from_dict(d)
        assert restored.min == 0.0
        assert restored.max == 10.0

    def test_sparse_serialization(self):
        """Only non-default values should appear in to_dict()."""
        spec = OutputFieldSpec(type="bool", description="Approve?")
        d = spec.to_dict()
        assert d == {"type": "bool", "description": "Approve?"}
        assert "required" not in d
        assert "default" not in d

    def test_from_dict_defaults(self):
        spec = OutputFieldSpec.from_dict({})
        assert spec.type == "str"
        assert spec.required is True
        assert spec.default is None
        assert spec.options is None


# ── StepDefinition output_schema serialization ──────────────────────


class TestStepDefinitionSchema:
    def test_roundtrip_with_schema(self):
        step = StepDefinition(
            name="review",
            outputs=["decision", "notes"],
            output_schema={
                "decision": OutputFieldSpec(type="choice", options=["approve", "reject"]),
                "notes": OutputFieldSpec(type="text", required=False),
            },
            executor=ExecutorRef(type="human"),
        )
        d = step.to_dict()
        assert "output_schema" in d
        assert "decision" in d["output_schema"]
        assert d["output_schema"]["decision"]["type"] == "choice"

        restored = StepDefinition.from_dict(d)
        assert restored.output_schema["decision"].type == "choice"
        assert restored.output_schema["decision"].options == ["approve", "reject"]
        assert restored.output_schema["notes"].required is False

    def test_empty_schema_not_serialized(self):
        step = StepDefinition(
            name="test",
            outputs=["result"],
            executor=ExecutorRef(type="script", config={"command": "echo '{}'"}),
        )
        d = step.to_dict()
        assert "output_schema" not in d


# ── WatchSpec output_schema serialization ────────────────────────────


class TestWatchSpecSchema:
    def test_roundtrip_with_schema(self):
        ws = WatchSpec(
            mode="human",
            fulfillment_outputs=["decision"],
            output_schema={"decision": {"type": "choice", "options": ["yes", "no"]}},
        )
        d = ws.to_dict()
        assert d["output_schema"] == {"decision": {"type": "choice", "options": ["yes", "no"]}}

        restored = WatchSpec.from_dict(d)
        assert restored.output_schema["decision"]["type"] == "choice"

    def test_empty_schema_not_serialized(self):
        ws = WatchSpec(mode="human", fulfillment_outputs=["x"])
        d = ws.to_dict()
        assert "output_schema" not in d


# ── YAML parsing ────────────────────────────────────────────────────


class TestYAMLOutputParsing:
    def test_list_format_backward_compat(self):
        wf = load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "What?"
    outputs: [answer]
""")
        assert wf.steps["ask"].outputs == ["answer"]
        assert wf.steps["ask"].output_schema == {}

    def test_dict_format_basic(self):
        wf = load_workflow_string("""
steps:
  review:
    executor: human
    prompt: "Review this"
    outputs:
      decision:
        type: choice
        options: [approve, reject]
      notes:
        type: text
        required: false
""")
        step = wf.steps["review"]
        assert step.outputs == ["decision", "notes"]
        assert step.output_schema["decision"].type == "choice"
        assert step.output_schema["decision"].options == ["approve", "reject"]
        assert step.output_schema["notes"].type == "text"
        assert step.output_schema["notes"].required is False

    def test_dict_format_bare_null(self):
        wf = load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "Enter value"
    outputs:
      response:
""")
        step = wf.steps["ask"]
        assert step.outputs == ["response"]
        assert step.output_schema["response"].type == "str"

    def test_dict_format_number(self):
        wf = load_workflow_string("""
steps:
  rate:
    executor: human
    prompt: "Rate 1-10"
    outputs:
      score:
        type: number
        min: 1
        max: 10
        description: "Rating score"
""")
        spec = wf.steps["rate"].output_schema["score"]
        assert spec.type == "number"
        assert spec.min == 1
        assert spec.max == 10
        assert spec.description == "Rating score"

    def test_dict_format_bool(self):
        wf = load_workflow_string("""
steps:
  confirm:
    executor: human
    prompt: "Confirm?"
    outputs:
      approved:
        type: bool
        default: false
""")
        spec = wf.steps["confirm"].output_schema["approved"]
        assert spec.type == "bool"
        assert spec.default is False

    def test_invalid_type_rejected(self):
        with pytest.raises(YAMLLoadError, match="invalid type 'date'"):
            load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "When?"
    outputs:
      date:
        type: date
""")

    def test_choice_without_options_rejected(self):
        with pytest.raises(YAMLLoadError, match="requires non-empty 'options'"):
            load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "Pick"
    outputs:
      pick:
        type: choice
""")

    def test_options_on_non_choice_rejected(self):
        with pytest.raises(YAMLLoadError, match="cannot have 'options'"):
            load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "Name?"
    outputs:
      name:
        type: str
        options: [a, b]
""")

    def test_min_max_on_non_number_rejected(self):
        with pytest.raises(YAMLLoadError, match="cannot have 'min'/'max'"):
            load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "Name?"
    outputs:
      name:
        type: str
        min: 0
""")

    def test_multiple_on_non_choice_rejected(self):
        with pytest.raises(YAMLLoadError, match="cannot have 'multiple'"):
            load_workflow_string("""
steps:
  ask:
    executor: human
    prompt: "Name?"
    outputs:
      name:
        type: str
        multiple: true
""")


# ── Engine fulfill validation ────────────────────────────────────────


class TestFulfillValidation:
    def test_number_coercion(self, async_engine):
        """Number strings should be coerced to float."""
        coerced, errors = Engine._validate_fulfill_payload(
            {"score": "7.5"},
            {"score": {"type": "number"}},
        )
        assert errors == []
        assert coerced["score"] == 7.5

    def test_number_min_max_rejection(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"score": "15"},
            {"score": {"type": "number", "min": 0, "max": 10}},
        )
        assert len(errors) == 1
        assert "above maximum" in errors[0]

    def test_number_invalid_string(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"score": "not-a-number"},
            {"score": {"type": "number"}},
        )
        assert len(errors) == 1
        assert "expected a number" in errors[0]

    def test_bool_coercion(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"approved": "yes"},
            {"approved": {"type": "bool"}},
        )
        assert errors == []
        assert coerced["approved"] is True

    def test_bool_false_coercion(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"approved": "no"},
            {"approved": {"type": "bool"}},
        )
        assert errors == []
        assert coerced["approved"] is False

    def test_bool_native(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"approved": True},
            {"approved": {"type": "bool"}},
        )
        assert errors == []
        assert coerced["approved"] is True

    def test_bool_invalid(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"approved": "maybe"},
            {"approved": {"type": "bool"}},
        )
        assert len(errors) == 1

    def test_choice_valid(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"pick": "b"},
            {"pick": {"type": "choice", "options": ["a", "b", "c"]}},
        )
        assert errors == []
        assert coerced["pick"] == "b"

    def test_choice_invalid(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"pick": "x"},
            {"pick": {"type": "choice", "options": ["a", "b", "c"]}},
        )
        assert len(errors) == 1
        assert "invalid choice" in errors[0]

    def test_choice_multiple_valid(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"picks": ["a", "c"]},
            {"picks": {"type": "choice", "options": ["a", "b", "c"], "multiple": True}},
        )
        assert errors == []
        assert coerced["picks"] == ["a", "c"]

    def test_choice_multiple_invalid(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"picks": ["a", "x"]},
            {"picks": {"type": "choice", "options": ["a", "b"], "multiple": True}},
        )
        assert len(errors) == 1

    def test_choice_multiple_not_list(self, async_engine):
        _, errors = Engine._validate_fulfill_payload(
            {"picks": "a"},
            {"picks": {"type": "choice", "options": ["a", "b"], "multiple": True}},
        )
        assert len(errors) == 1
        assert "expected a list" in errors[0]

    def test_optional_field_omission(self, async_engine):
        """Optional fields with blank values should be removed."""
        coerced, errors = Engine._validate_fulfill_payload(
            {"notes": ""},
            {"notes": {"type": "text", "required": False}},
        )
        assert errors == []
        assert "notes" not in coerced

    def test_optional_field_with_default(self, async_engine):
        coerced, errors = Engine._validate_fulfill_payload(
            {"notes": ""},
            {"notes": {"type": "str", "required": False, "default": "N/A"}},
        )
        assert errors == []
        assert coerced["notes"] == "N/A"


# ── Engine fulfill integration ──────────────────────────────────────


class TestFulfillWatchIntegration:
    def test_schema_propagated_to_watch(self, engine):
        """output_schema on StepDefinition should propagate to WatchSpec."""
        wf = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review",
                outputs=["decision"],
                output_schema={
                    "decision": OutputFieldSpec(type="choice", options=["yes", "no"]),
                },
                executor=ExecutorRef(type="human", config={"prompt": "Approve?"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf, inputs={})
        engine.start_job(job.id)
        engine.tick()

        runs = engine.store.runs_for_job(job.id)
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        assert len(suspended) == 1
        run = suspended[0]
        assert run.watch is not None
        assert run.watch.output_schema == {"decision": {"type": "choice", "options": ["yes", "no"]}}

    def test_fulfill_with_valid_choice(self, engine):
        wf = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review",
                outputs=["decision"],
                output_schema={
                    "decision": OutputFieldSpec(type="choice", options=["yes", "no"]),
                },
                executor=ExecutorRef(type="human", config={"prompt": "Approve?"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf, inputs={})
        engine.start_job(job.id)
        engine.tick()

        runs = engine.store.runs_for_job(job.id)
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        run = suspended[0]

        result = engine.fulfill_watch(run.id, {"decision": "yes"})
        assert result is None  # success

        updated = engine.store.load_run(run.id)
        assert updated.status == StepRunStatus.COMPLETED
        assert updated.result.artifact["decision"] == "yes"

    def test_fulfill_with_invalid_choice_rejected(self, engine):
        wf = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review",
                outputs=["decision"],
                output_schema={
                    "decision": OutputFieldSpec(type="choice", options=["yes", "no"]),
                },
                executor=ExecutorRef(type="human", config={"prompt": "Approve?"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf, inputs={})
        engine.start_job(job.id)
        engine.tick()

        runs = engine.store.runs_for_job(job.id)
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        run = suspended[0]

        with pytest.raises(ValueError, match="invalid choice"):
            engine.fulfill_watch(run.id, {"decision": "maybe"})

    def test_optional_field_can_be_omitted(self, engine):
        wf = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review",
                outputs=["decision", "notes"],
                output_schema={
                    "decision": OutputFieldSpec(type="choice", options=["yes", "no"]),
                    "notes": OutputFieldSpec(type="text", required=False),
                },
                executor=ExecutorRef(type="human", config={"prompt": "Approve?"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf, inputs={})
        engine.start_job(job.id)
        engine.tick()

        runs = engine.store.runs_for_job(job.id)
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        run = suspended[0]

        # Only provide required field, omit optional
        result = engine.fulfill_watch(run.id, {"decision": "yes"})
        assert result is None  # success


# ── _validate_artifact respects required: false ──────────────────────


class TestValidateArtifact:
    def test_optional_field_not_in_artifact(self, async_engine):
        step_def = StepDefinition(
            name="test",
            outputs=["required_field", "optional_field"],
            output_schema={
                "optional_field": OutputFieldSpec(required=False),
            },
            executor=ExecutorRef(type="script", config={"command": "echo"}),
        )
        envelope = HandoffEnvelope(
            artifact={"required_field": "value"},
            sidecar=Sidecar(),
            workspace="",
            timestamp=_now(),
        )
        error = async_engine._validate_artifact(step_def, envelope)
        assert error is None

    def test_required_field_missing_still_errors(self, async_engine):
        step_def = StepDefinition(
            name="test",
            outputs=["required_field", "optional_field"],
            output_schema={
                "optional_field": OutputFieldSpec(required=False),
            },
            executor=ExecutorRef(type="script", config={"command": "echo"}),
        )
        envelope = HandoffEnvelope(
            artifact={"optional_field": "value"},
            sidecar=Sidecar(),
            workspace="",
            timestamp=_now(),
        )
        error = async_engine._validate_artifact(step_def, envelope)
        assert error is not None
        assert "required_field" in error

    def test_all_optional_empty_artifact_ok(self, async_engine):
        step_def = StepDefinition(
            name="test",
            outputs=["a", "b"],
            output_schema={
                "a": OutputFieldSpec(required=False),
                "b": OutputFieldSpec(required=False),
            },
            executor=ExecutorRef(type="human"),
        )
        error = async_engine._validate_artifact(step_def, None)
        assert error is None


# ── suspended_step_details includes schema ───────────────────────────


class TestSuspendedStepDetails:
    def test_includes_output_schema(self, engine):
        wf = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review",
                outputs=["decision"],
                output_schema={
                    "decision": OutputFieldSpec(type="bool", description="Approve?"),
                },
                executor=ExecutorRef(type="human", config={"prompt": "Approve?"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf, inputs={})
        engine.start_job(job.id)
        engine.tick()

        details = engine.suspended_step_details(job.id)
        assert len(details) == 1
        assert "output_schema" in details[0]
        assert details[0]["output_schema"]["decision"]["type"] == "bool"
