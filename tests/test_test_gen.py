"""Tests for stepwise test-fixture generator."""

from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.test_gen import generate_test_fixture
from stepwise.yaml_loader import load_workflow_yaml
from tests.conftest import register_step_fn, run_job_sync


YAML_LINEAR = """\
name: linear-test
steps:
  fetch:
    run: |
      echo '{"data": "hello"}'
    outputs: [data]

  process:
    run: |
      echo '{"result": "done"}'
    inputs:
      data: fetch.data
    outputs: [result]
"""

YAML_WITH_LOOPS = """\
name: loop-test
steps:
  generate:
    run: |
      echo '{"text": "hello"}'
    outputs: [text]
    exits:
      - name: good
        when: "outputs.text == 'done'"
        action: advance
      - name: retry
        when: "True"
        action: loop
        target: generate
        max_iterations: 3
"""

YAML_WITH_JOB_INPUTS = """\
name: job-input-test
steps:
  fetch:
    run: |
      echo '{"data": "hello"}'
    inputs:
      url: $job.target_url
      token: $job.api_token
    outputs: [data]
"""

YAML_WITH_BRANCHES = """\
name: branch-test
steps:
  check:
    run: |
      echo '{"status": "pass"}'
    outputs: [status]

  deploy:
    run: |
      echo '{"url": "done"}'
    inputs:
      status: check.status
    when: "status == 'pass'"
    outputs: [url]

  fix:
    run: |
      echo '{"fixes": "done"}'
    inputs:
      status: check.status
    when: "status == 'fail'"
    outputs: [fixes]
"""

YAML_WITH_EXTERNAL = """\
name: external-test
steps:
  review:
    executor: external
    prompt: "Please review"
    outputs: [approved]

  deploy:
    run: |
      echo '{"url": "done"}'
    inputs:
      approved: review.approved
    outputs: [url]
"""

YAML_WITH_ADVANCE_CONDITION = """\
name: advance-cond-test
steps:
  analyze:
    run: |
      echo '{"quality_score": "0.9", "summary": "good"}'
    outputs: [quality_score, summary]
    exits:
      - name: good-enough
        when: "float(outputs.quality_score) >= 0.8"
        action: advance
      - name: retry
        when: "True"
        action: loop
        target: analyze
        max_iterations: 3
"""


def test_generated_code_compiles_linear():
    """Generated test for a linear flow is syntactically valid."""
    wf = load_workflow_yaml(YAML_LINEAR)
    code = generate_test_fixture(wf, "linear-test")
    compile(code, "<test>", "exec")


def test_generated_code_compiles_loops():
    """Generated test for a flow with loops is syntactically valid."""
    wf = load_workflow_yaml(YAML_WITH_LOOPS)
    code = generate_test_fixture(wf, "loop-test")
    compile(code, "<test>", "exec")


def test_generated_code_compiles_branches():
    """Generated test for a flow with when conditions is syntactically valid."""
    wf = load_workflow_yaml(YAML_WITH_BRANCHES)
    code = generate_test_fixture(wf, "branch-test")
    compile(code, "<test>", "exec")


def test_generated_code_compiles_external():
    """Generated test for a flow with external steps is syntactically valid."""
    wf = load_workflow_yaml(YAML_WITH_EXTERNAL)
    code = generate_test_fixture(wf, "external-test")
    compile(code, "<test>", "exec")


def test_generated_code_compiles_job_inputs():
    """Generated test for a flow with $job inputs is syntactically valid."""
    wf = load_workflow_yaml(YAML_WITH_JOB_INPUTS)
    code = generate_test_fixture(wf, "job-input-test")
    compile(code, "<test>", "exec")
    assert "sample_target_url" in code
    assert "sample_api_token" in code


def test_generated_code_compiles_advance_condition():
    """Generated test with exit rule advance condition infers stub values."""
    wf = load_workflow_yaml(YAML_WITH_ADVANCE_CONDITION)
    code = generate_test_fixture(wf, "advance-cond-test")
    compile(code, "<test>", "exec")
    # Should have inferred quality_score >= 0.8
    assert "0.8" in code


def test_linear_flow_runs(async_engine):
    """Generated test for a simple linear flow actually runs."""
    wf = load_workflow_yaml(YAML_LINEAR)
    code = generate_test_fixture(wf, "linear-test")

    # Execute the generated code to register stubs and build workflow
    ns = {
        "register_step_fn": register_step_fn,
        "run_job_sync": run_job_sync,
        "ExitRule": ExitRule,
        "ExecutorRef": ExecutorRef,
        "InputBinding": InputBinding,
        "JobStatus": JobStatus,
        "StepDefinition": StepDefinition,
        "WorkflowDefinition": WorkflowDefinition,
    }

    exec(code, ns)

    # Instantiate the test class and call the method
    test_class = None
    for v in ns.values():
        if isinstance(v, type) and v.__name__.startswith("Test"):
            test_class = v
            break

    assert test_class is not None
    instance = test_class()
    instance.test_happy_path(async_engine)


def test_loop_flow_runs(async_engine):
    """Generated test for a flow with loops runs to completion."""
    wf = load_workflow_yaml(YAML_WITH_LOOPS)
    code = generate_test_fixture(wf, "loop-test")

    ns = {
        "register_step_fn": register_step_fn,
        "run_job_sync": run_job_sync,
        "ExitRule": ExitRule,
        "ExecutorRef": ExecutorRef,
        "InputBinding": InputBinding,
        "JobStatus": JobStatus,
        "StepDefinition": StepDefinition,
        "WorkflowDefinition": WorkflowDefinition,
    }

    exec(code, ns)

    test_class = None
    for v in ns.values():
        if isinstance(v, type) and v.__name__.startswith("Test"):
            test_class = v
            break

    assert test_class is not None
    instance = test_class()
    instance.test_happy_path(async_engine)


def test_job_inputs_flow_runs(async_engine):
    """Generated test for a flow with $job inputs runs to completion."""
    wf = load_workflow_yaml(YAML_WITH_JOB_INPUTS)
    code = generate_test_fixture(wf, "job-input-test")

    ns = {
        "register_step_fn": register_step_fn,
        "run_job_sync": run_job_sync,
        "ExitRule": ExitRule,
        "ExecutorRef": ExecutorRef,
        "InputBinding": InputBinding,
        "JobStatus": JobStatus,
        "StepDefinition": StepDefinition,
        "WorkflowDefinition": WorkflowDefinition,
    }

    exec(code, ns)

    test_class = None
    for v in ns.values():
        if isinstance(v, type) and v.__name__.startswith("Test"):
            test_class = v
            break

    assert test_class is not None
    instance = test_class()
    instance.test_happy_path(async_engine)


def test_output_file_mode(tmp_path):
    """The generator produces valid code that can be written to a file."""
    wf = load_workflow_yaml(YAML_LINEAR)
    code = generate_test_fixture(wf, "linear-test")
    out_path = tmp_path / "test_generated.py"
    out_path.write_text(code)
    assert out_path.exists()
    compile(out_path.read_text(), str(out_path), "exec")
