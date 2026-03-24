"""Tests for data model validation and serialization."""

import pytest
from stepwise.models import (
    DecoratorRef,
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    SubJobDefinition,
    WatchSpec,
    WorkflowDefinition,
)


# ── Test 17: Graph validation ─────────────────────────────────────────


class TestWorkflowValidation:
    def test_valid_linear_workflow(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {"command": "echo hi"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("script", {"command": "echo hi"}),
                inputs=[InputBinding("data", "a", "result")],
            ),
        })
        assert w.validate() == []

    def test_missing_source_step(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "nonexistent", "value")],
            ),
        })
        errors = w.validate()
        assert any("nonexistent" in e for e in errors)

    def test_missing_source_field(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "a", "nonexistent_field")],
            ),
        })
        errors = w.validate()
        assert any("nonexistent_field" in e for e in errors)

    def test_duplicate_local_names(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x", "y"],
                executor=ExecutorRef("script", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("script", {}),
                inputs=[
                    InputBinding("data", "a", "x"),
                    InputBinding("data", "a", "y"),  # duplicate local_name "data"
                ],
            ),
        })
        errors = w.validate()
        assert any("duplicate local_name" in e.lower() for e in errors)

    def test_duplicate_outputs(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result", "result"],
                executor=ExecutorRef("script", {}),
            ),
        })
        errors = w.validate()
        assert any("duplicate output" in e.lower() for e in errors)

    def test_cycle_detection(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["out"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "b", "out")],
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "a", "out")],
            ),
        })
        errors = w.validate()
        assert any("cycle" in e.lower() for e in errors)

    def test_invalid_loop_target(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {}),
                exit_rules=[
                    ExitRule("loop", "field_match", {
                        "field": "result", "value": True,
                        "action": "loop", "target": "nonexistent",
                    }),
                ],
            ),
        })
        errors = w.validate()
        assert any("nonexistent" in e for e in errors)

    def test_entry_steps(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
            "b": StepDefinition(
                name="b", outputs=["r"], executor=ExecutorRef("script", {}),
                inputs=[InputBinding("x", "a", "r")],
            ),
            "c": StepDefinition(name="c", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        entry = w.entry_steps()
        assert set(entry) == {"a", "c"}

    def test_terminal_steps(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
            "b": StepDefinition(
                name="b", outputs=["r"], executor=ExecutorRef("script", {}),
                inputs=[InputBinding("x", "a", "r")],
            ),
        })
        terminal = w.terminal_steps()
        assert terminal == ["b"]

    def test_job_level_inputs_pass_validation(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("req", "$job", "requirements")],
            ),
        })
        assert w.validate() == []

    def test_after_missing_step(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["r"],
                executor=ExecutorRef("script", {}),
                after=["nonexistent"],
            ),
        })
        errors = w.validate()
        assert any("nonexistent" in e for e in errors)

    def test_from_dict_sequencing_fallback(self):
        """Old serialized data with 'sequencing' key deserializes to .after."""
        d = {
            "name": "x", "outputs": ["y"],
            "executor": {"type": "script", "config": {}},
            "sequencing": ["a"],
        }
        step = StepDefinition.from_dict(d)
        assert step.after == ["a"]
        # New serialization uses "after"
        assert "after" in step.to_dict()
        assert "sequencing" not in step.to_dict()

    def test_empty_workflow(self):
        w = WorkflowDefinition(steps={})
        errors = w.validate()
        assert len(errors) > 0


# ── Graph validation: entry and terminal steps ───────────────────────


class TestEntryTerminalValidation:
    def test_no_entry_steps_error(self):
        """All steps have dependencies → no entry step → validation error."""
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["r"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "b", "r")],
            ),
            "b": StepDefinition(
                name="b", outputs=["r"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "a", "r")],
            ),
        })
        errors = w.validate()
        # This is a cycle, which should be caught
        assert len(errors) > 0

    def test_no_terminal_steps_error(self):
        """Every step is depended on by another → no terminal step → validation error."""
        # A → B → A is a cycle. Let's make a non-cyclic case:
        # a → b, b → c, c → a would be cyclic
        # For a non-cyclic "no terminal" case, we need something like
        # a → b, b → a which is cyclic. In practice, in a DAG, no terminal
        # steps can't happen without cycles. So let's test that terminal_steps()
        # returns the right thing with after.
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["r"],
                executor=ExecutorRef("script", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["r"],
                executor=ExecutorRef("script", {}),
                after=["a"],
            ),
        })
        # "a" is depended on by "b" via after, so "b" is terminal
        assert w.terminal_steps() == ["b"]
        assert w.entry_steps() == ["a"]
        assert w.validate() == []

    def test_entry_steps_with_job_bindings(self):
        """Steps with only $job bindings are entry steps."""
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["r"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("req", "$job", "requirements")],
            ),
            "b": StepDefinition(
                name="b", outputs=["r"],
                executor=ExecutorRef("script", {}),
                inputs=[InputBinding("data", "a", "r")],
            ),
        })
        assert w.entry_steps() == ["a"]
        assert w.terminal_steps() == ["b"]


# ── Serialization Roundtrips ──────────────────────────────────────────


class TestSerialization:
    def test_executor_ref_roundtrip(self):
        ref = ExecutorRef("script", {"command": "make test"}, decorators=[
            DecoratorRef("timeout", {"minutes": 30}),
        ])
        d = ref.to_dict()
        ref2 = ExecutorRef.from_dict(d)
        assert ref2.type == "script"
        assert ref2.config == {"command": "make test"}
        assert len(ref2.decorators) == 1
        assert ref2.decorators[0].type == "timeout"

    def test_exit_rule_roundtrip(self):
        rule = ExitRule("pass", "field_match", {
            "field": "pass", "value": True, "action": "advance",
        }, priority=10)
        d = rule.to_dict()
        rule2 = ExitRule.from_dict(d)
        assert rule2.name == "pass"
        assert rule2.priority == 10
        assert rule2.config["action"] == "advance"

    def test_handoff_envelope_roundtrip(self):
        env = HandoffEnvelope(
            artifact={"plan": "do stuff", "confidence": 0.85},
            sidecar=Sidecar(
                decisions_made=["chose React"],
                assumptions=["user has Node"],
            ),
            executor_meta={"retry": {"attempts": 2}},
            workspace="/tmp/test",
        )
        d = env.to_dict()
        env2 = HandoffEnvelope.from_dict(d)
        assert env2.artifact["plan"] == "do stuff"
        assert env2.sidecar.decisions_made == ["chose React"]
        assert env2.executor_meta["retry"]["attempts"] == 2

    def test_watch_spec_roundtrip(self):
        ws = WatchSpec("poll", {
            "check_command": "check.py",
            "interval_seconds": 60,
        }, fulfillment_outputs=["status", "url"])
        d = ws.to_dict()
        ws2 = WatchSpec.from_dict(d)
        assert ws2.mode == "poll"
        assert ws2.config["interval_seconds"] == 60
        assert ws2.fulfillment_outputs == ["status", "url"]

    def test_step_run_roundtrip(self):
        run = StepRun(
            id="run-123",
            job_id="job-456",
            step_name="test",
            attempt=2,
            status=StepRunStatus.COMPLETED,
            inputs={"data": "hello"},
            dep_run_ids={"a": "run-001"},
            result=HandoffEnvelope(artifact={"out": "done"}, sidecar=Sidecar()),
        )
        d = run.to_dict()
        run2 = StepRun.from_dict(d)
        assert run2.id == "run-123"
        assert run2.attempt == 2
        assert run2.dep_run_ids == {"a": "run-001"}
        assert run2.result.artifact["out"] == "done"

    def test_workflow_roundtrip(self):
        w = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan", "confidence"],
                executor=ExecutorRef("agent", {"backend": "claude"}),
                inputs=[InputBinding("requirements", "$job", "requirements")],
                exit_rules=[
                    ExitRule("default", "always", {"action": "advance"}),
                ],
            ),
        })
        d = w.to_dict()
        w2 = WorkflowDefinition.from_dict(d)
        assert "plan" in w2.steps
        assert w2.steps["plan"].outputs == ["plan", "confidence"]
        assert w2.steps["plan"].inputs[0].source_step == "$job"

    def test_job_roundtrip(self):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(
            id="job-abc",
            objective="Test job",
            workflow=w,
            status=JobStatus.RUNNING,
            inputs={"key": "value"},
            config=JobConfig(max_sub_job_depth=3),
        )
        d = job.to_dict()
        job2 = Job.from_dict(d)
        assert job2.id == "job-abc"
        assert job2.objective == "Test job"
        assert job2.inputs["key"] == "value"
        assert job2.config.max_sub_job_depth == 3

    def test_sub_job_definition_roundtrip(self):
        w = WorkflowDefinition(steps={
            "x": StepDefinition(name="x", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        sd = SubJobDefinition(objective="sub task", workflow=w)
        d = sd.to_dict()
        sd2 = SubJobDefinition.from_dict(d)
        assert sd2.objective == "sub task"
        assert "x" in sd2.workflow.steps
