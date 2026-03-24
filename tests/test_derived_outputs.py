"""Tests for derived outputs — computed fields from raw executor results."""

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from tests.conftest import register_step_fn, run_job_sync


def test_basic_derived_output(async_engine):
    """Derived output computes a boolean from a raw result field."""
    register_step_fn("producer", lambda inputs: {
        "result": "Everything looks good, no issues found.",
    })

    wf = WorkflowDefinition(steps={
        "produce": StepDefinition(
            name="produce",
            executor=ExecutorRef(type="callable", config={"fn_name": "producer"}),
            outputs=["result"],
            derived_outputs={
                "has_issues": "'issues' in str(result)",
            },
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    assert runs[0].result.artifact["has_issues"] is True


def test_derived_output_regex_extract(async_engine):
    """Derived output extracts structured data via regex_extract."""
    register_step_fn("agent", lambda inputs: {
        "result": "Analysis complete.\n>>>ESCALATE: need human review for section 3",
    })

    wf = WorkflowDefinition(steps={
        "analyze": StepDefinition(
            name="analyze",
            executor=ExecutorRef(type="callable", config={"fn_name": "agent"}),
            outputs=["result"],
            derived_outputs={
                "has_escalation": "'>>>ESCALATE:' in str(result)",
                "escalation_text": "regex_extract(r'>>>ESCALATE:\\s*(.+)', str(result))",
            },
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    artifact = runs[0].result.artifact
    assert artifact["has_escalation"] is True
    assert artifact["escalation_text"] == "need human review for section 3"


def test_derived_output_no_regex_match(async_engine):
    """regex_extract returns None when pattern doesn't match."""
    register_step_fn("agent", lambda inputs: {
        "result": "All good, no escalation needed.",
    })

    wf = WorkflowDefinition(steps={
        "analyze": StepDefinition(
            name="analyze",
            executor=ExecutorRef(type="callable", config={"fn_name": "agent"}),
            outputs=["result"],
            derived_outputs={
                "has_escalation": "'>>>ESCALATE:' in str(result)",
                "escalation_text": "regex_extract(r'>>>ESCALATE:\\s*(.+)', str(result))",
            },
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    artifact = runs[0].result.artifact
    assert artifact["has_escalation"] is False
    assert artifact["escalation_text"] is None


def test_downstream_step_uses_derived_output(async_engine):
    """A downstream step can consume a derived output via input binding."""
    register_step_fn("producer", lambda inputs: {
        "result": "Score: 0.95 — excellent quality",
    })
    register_step_fn("consumer", lambda inputs: {
        "summary": f"Score was {inputs['score']}",
    })

    wf = WorkflowDefinition(steps={
        "produce": StepDefinition(
            name="produce",
            executor=ExecutorRef(type="callable", config={"fn_name": "producer"}),
            outputs=["result"],
            derived_outputs={
                "score": "regex_extract(r'Score:\\s*([\\d.]+)', str(result))",
            },
        ),
        "consume": StepDefinition(
            name="consume",
            executor=ExecutorRef(type="callable", config={"fn_name": "consumer"}),
            inputs=[InputBinding("score", "produce", "score")],
            outputs=["summary"],
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    consume_run = [r for r in runs if r.step_name == "consume"][0]
    assert consume_run.result.artifact["summary"] == "Score was 0.95"


def test_derived_outputs_yaml_parsing():
    """derived_outputs are parsed correctly from YAML."""
    from stepwise.yaml_loader import load_workflow_yaml

    yaml_text = """\
name: test-derived
steps:
  analyze:
    run: |
      echo '{"result": "hello"}'
    outputs: [result]
    derived_outputs:
      has_greeting: "'hello' in str(result)"
      greeting_word: "regex_extract(r'(hello)', str(result))"
"""
    wf = load_workflow_yaml(yaml_text)
    step = wf.steps["analyze"]
    assert step.derived_outputs == {
        "has_greeting": "'hello' in str(result)",
        "greeting_word": "regex_extract(r'(hello)', str(result))",
    }


def test_derived_outputs_to_dict_from_dict():
    """derived_outputs round-trip through to_dict/from_dict."""
    step = StepDefinition(
        name="test",
        executor=ExecutorRef(type="script", config={"command": "echo"}),
        outputs=["result"],
        derived_outputs={"flag": "'x' in result"},
    )
    d = step.to_dict()
    assert d["derived_outputs"] == {"flag": "'x' in result"}
    restored = StepDefinition.from_dict(d)
    assert restored.derived_outputs == {"flag": "'x' in result"}


def test_derived_outputs_empty_not_serialized():
    """Empty derived_outputs should not appear in to_dict output."""
    step = StepDefinition(
        name="test",
        executor=ExecutorRef(type="script", config={"command": "echo"}),
        outputs=["result"],
    )
    d = step.to_dict()
    assert "derived_outputs" not in d
