"""Tests for agent-emitted flows (Level 1 + Level 1.5)."""

import os
import tempfile
from pathlib import Path

from stepwise.agent import (
    EMIT_FLOW_DIR,
    EMIT_FLOW_FILENAME,
    AgentExecutor,
    AgentProcess,
    AgentStatus,
    MockAgentBackend,
)
from stepwise.executors import ExecutionContext, ExecutorResult
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    ExitRule,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    SubJobDefinition,
    WorkflowDefinition,
    _now,
)

from tests.conftest import register_step_fn, run_job_sync


# ── Test Helper ──────────────────────────────────────────────────────


class FileWritingMockBackend(MockAgentBackend):
    """Mock backend that writes files to working_dir on spawn."""

    def __init__(self):
        super().__init__()
        self._files_to_write: dict[str, str] = {}

    def set_emit_file(self, content: str):
        self._files_to_write[os.path.join(EMIT_FLOW_DIR, EMIT_FLOW_FILENAME)] = content

    def clear_emit_file(self):
        self._files_to_write.clear()

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        process = super().spawn(prompt, config, context)
        for rel_path, content in self._files_to_write.items():
            full_path = Path(process.working_dir) / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
        return process


SIMPLE_EMIT_FLOW = """\
name: emitted-flow
steps:
  do-work:
    run: |
      echo '{"result": "done"}'
    outputs: [result]
"""

MULTI_STEP_EMIT_FLOW = """\
name: emitted-multi
steps:
  step-a:
    run: |
      echo '{"value": "hello"}'
    outputs: [value]

  step-b:
    run: |
      echo '{"result": "processed"}'
    inputs:
      value: step-a.value
    outputs: [result]
"""


# ── Level 1 Tests ────────────────────────────────────────────────────


def test_agent_emits_flow_creates_sub_job(async_engine):
    """Happy path: agent emits flow → DELEGATED → sub-job → outputs propagate back."""
    workspace = tempfile.mkdtemp()
    backend = FileWritingMockBackend()
    backend.set_emit_file(SIMPLE_EMIT_FLOW)
    backend.set_auto_complete(result={})

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    # The sub-flow uses script executor (echo), which is already registered
    wf = WorkflowDefinition(steps={
        "agent-step": StepDefinition(
            name="agent-step",
            executor=ExecutorRef("agent", {
                "prompt": "Do the work",
                "emit_flow": True,
                "output_fields": ["result"],
            }),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(
        objective="test emit flow",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    # Check the parent step's run
    runs = async_engine.store.runs_for_job(job.id)
    agent_run = [r for r in runs if r.step_name == "agent-step"][0]
    assert agent_run.status == StepRunStatus.COMPLETED
    assert agent_run.sub_job_id is not None
    assert agent_run.result is not None
    assert agent_run.result.artifact["result"] == "done"


def test_agent_no_emit_completes_normally(async_engine):
    """emit_flow=true but no file written → normal type='data' completion."""
    workspace = tempfile.mkdtemp()
    backend = MockAgentBackend()
    backend.set_auto_complete(result={"result": "direct"})

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    wf = WorkflowDefinition(steps={
        "agent-step": StepDefinition(
            name="agent-step",
            executor=ExecutorRef("agent", {
                "prompt": "Simple task",
                "emit_flow": True,
            }),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(
        objective="test no emit",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    agent_run = [r for r in runs if r.step_name == "agent-step"][0]
    assert agent_run.status == StepRunStatus.COMPLETED
    assert agent_run.sub_job_id is None
    assert agent_run.result.artifact["result"] == "direct"


def test_agent_emits_invalid_yaml_fails(async_engine):
    """Bad/truncated YAML → step fails."""
    workspace = tempfile.mkdtemp()
    backend = FileWritingMockBackend()
    backend.set_emit_file("steps:\n  bad:\n    - not a mapping\n")
    backend.set_auto_complete(result={})

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    wf = WorkflowDefinition(steps={
        "agent-step": StepDefinition(
            name="agent-step",
            executor=ExecutorRef("agent", {
                "prompt": "Do work",
                "emit_flow": True,
            }),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(
        objective="test invalid yaml",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.FAILED


def test_agent_emit_output_mismatch_fails(async_engine):
    """Terminal outputs don't match parent step → step fails."""
    workspace = tempfile.mkdtemp()
    # Emit a flow whose terminal step outputs ["value"] but parent expects ["result"]
    mismatch_flow = """\
name: mismatch
steps:
  do-work:
    run: |
      echo '{"value": "hello"}'
    outputs: [value]
"""
    backend = FileWritingMockBackend()
    backend.set_emit_file(mismatch_flow)
    backend.set_auto_complete(result={})

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    wf = WorkflowDefinition(steps={
        "agent-step": StepDefinition(
            name="agent-step",
            executor=ExecutorRef("agent", {
                "prompt": "Do work",
                "emit_flow": True,
                "output_fields": ["result"],
            }),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(
        objective="test output mismatch",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.FAILED


def test_emit_flow_prompt_instructions():
    """emit_flow=true → prompt contains emission docs."""
    backend = MockAgentBackend()
    executor = AgentExecutor(
        backend=backend,
        prompt="Do work: $spec",
        emit_flow=True,
    )
    context = ExecutionContext(
        job_id="j1", step_name="s1", attempt=1,
        workspace_path="/tmp/test", idempotency="test",
    )
    prompt = executor._render_prompt({"spec": "build it"}, context)
    assert "Flow Emission" in prompt
    assert "emit.flow.yaml" in prompt


def test_no_emit_flow_instructions_by_default():
    """No emit_flow → no emission docs in prompt."""
    backend = MockAgentBackend()
    executor = AgentExecutor(
        backend=backend,
        prompt="Do work: $spec",
    )
    context = ExecutionContext(
        job_id="j1", step_name="s1", attempt=1,
        workspace_path="/tmp/test", idempotency="test",
    )
    prompt = executor._render_prompt({"spec": "build it"}, context)
    assert "Flow Emission" not in prompt


def test_engine_delegate_result(async_engine):
    """Engine-level: CallableExecutor returns type='delegate' → sub-job cascade works."""
    register_step_fn("inner", lambda inputs: {"result": "from-sub"})

    inner_wf = WorkflowDefinition(steps={
        "inner-step": StepDefinition(
            name="inner-step",
            executor=ExecutorRef("callable", {"fn_name": "inner"}),
            outputs=["result"],
        ),
    })

    def delegate_fn(inputs):
        return ExecutorResult(
            type="delegate",
            sub_job_def=SubJobDefinition(
                objective="delegated work",
                workflow=inner_wf,
            ),
            executor_state={"emitted_flow": True},
        )

    register_step_fn("delegator", delegate_fn)

    wf = WorkflowDefinition(steps={
        "outer-step": StepDefinition(
            name="outer-step",
            executor=ExecutorRef("callable", {"fn_name": "delegator"}),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(objective="test delegate", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    outer_run = [r for r in runs if r.step_name == "outer-step"][0]
    assert outer_run.sub_job_id is not None
    assert outer_run.result.artifact["result"] == "from-sub"


def test_yaml_loader_passes_emit_flow():
    """YAML emit_flow: true → reaches ExecutorRef.config."""
    from stepwise.yaml_loader import load_workflow_string

    yaml_str = """\
steps:
  agent-step:
    executor: agent
    prompt: "Do work"
    emit_flow: true
    outputs: [result]
"""
    wf = load_workflow_string(yaml_str)
    step = wf.steps["agent-step"]
    assert step.executor.config.get("emit_flow") is True


def test_delegate_depth_limit(async_engine):
    """Emitted flow that would exceed max_sub_job_depth → fails."""
    inner_wf = WorkflowDefinition(steps={
        "inner-step": StepDefinition(
            name="inner-step",
            executor=ExecutorRef("callable", {"fn_name": "noop"}),
            outputs=["result"],
        ),
    })
    register_step_fn("noop", lambda inputs: {"result": "ok"})

    def delegate_fn(inputs):
        return ExecutorResult(
            type="delegate",
            sub_job_def=SubJobDefinition(
                objective="too deep",
                workflow=inner_wf,
            ),
            executor_state={"emitted_flow": True},
        )

    register_step_fn("deep-delegator", delegate_fn)

    wf = WorkflowDefinition(steps={
        "outer": StepDefinition(
            name="outer",
            executor=ExecutorRef("callable", {"fn_name": "deep-delegator"}),
            outputs=["result"],
        ),
    })

    # Set max_sub_job_depth to 0 so any sub-job creation fails
    job = async_engine.create_job(
        objective="test depth limit",
        workflow=wf,
        config=JobConfig(max_sub_job_depth=0),
    )
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.FAILED


# ── Level 1.5 Tests ──────────────────────────────────────────────────


def test_delegated_marker_injected(async_engine):
    """_delegated: True in artifact after delegate step completes via sub-job."""
    register_step_fn("inner", lambda inputs: {"result": "sub-output"})

    inner_wf = WorkflowDefinition(steps={
        "inner-step": StepDefinition(
            name="inner-step",
            executor=ExecutorRef("callable", {"fn_name": "inner"}),
            outputs=["result"],
        ),
    })

    def delegate_fn(inputs):
        return ExecutorResult(
            type="delegate",
            sub_job_def=SubJobDefinition(
                objective="delegated work",
                workflow=inner_wf,
            ),
            executor_state={"emitted_flow": True},
        )

    register_step_fn("delegator", delegate_fn)

    wf = WorkflowDefinition(steps={
        "outer": StepDefinition(
            name="outer",
            executor=ExecutorRef("callable", {"fn_name": "delegator"}),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(objective="test marker", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    outer_run = [r for r in runs if r.step_name == "outer"][0]
    assert outer_run.result.artifact.get("_delegated") is True


def test_no_delegated_marker_on_direct(async_engine):
    """No _delegated in artifact when agent completes directly (no emit)."""
    register_step_fn("direct", lambda inputs: {"result": "direct-output"})

    wf = WorkflowDefinition(steps={
        "step": StepDefinition(
            name="step",
            executor=ExecutorRef("callable", {"fn_name": "direct"}),
            outputs=["result"],
        ),
    })

    job = async_engine.create_job(objective="test no marker", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    step_run = runs[0]
    assert "_delegated" not in step_run.result.artifact


def test_iterative_delegation_loop(async_engine):
    """Agent emits flow → completes → exit rule loops → agent runs again →
    emits another flow → loops → agent returns direct → advances."""
    workspace = tempfile.mkdtemp()

    # Track spawn count to change behavior between iterations
    backend = FileWritingMockBackend()
    backend.set_auto_complete(result={"result": "done"})

    iteration_count = [0]
    original_spawn = backend.spawn

    def tracking_spawn(prompt, config, context):
        iteration_count[0] += 1
        if iteration_count[0] <= 2:
            # First two iterations: emit a flow
            backend.set_emit_file(SIMPLE_EMIT_FLOW)
        else:
            # Third iteration: complete directly (no emit file)
            backend.clear_emit_file()
        return original_spawn(prompt, config, context)

    backend.spawn = tracking_spawn

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    wf = WorkflowDefinition(steps={
        "agent-phase": StepDefinition(
            name="agent-phase",
            executor=ExecutorRef("agent", {
                "prompt": "Implement feature",
                "emit_flow": True,
            }),
            outputs=["result"],
            exit_rules=[
                ExitRule(
                    name="continue",
                    type="expression",
                    config={
                        "condition": "outputs.get('_delegated', False)",
                        "action": "loop",
                        "target": "agent-phase",
                    },
                    priority=2,
                ),
                ExitRule(
                    name="done",
                    type="expression",
                    config={
                        "condition": "True",
                        "action": "advance",
                    },
                    priority=1,
                ),
            ],
        ),
    })

    job = async_engine.create_job(
        objective="test iterative delegation",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id, timeout=15)
    assert result.status == JobStatus.COMPLETED

    # Should have had 3 iterations of agent-phase
    assert iteration_count[0] == 3

    # The final run should NOT have _delegated
    runs = async_engine.store.runs_for_job(job.id)
    agent_runs = sorted(
        [r for r in runs if r.step_name == "agent-phase"],
        key=lambda r: r.attempt,
    )
    # Verify we had multiple attempts
    assert len(agent_runs) >= 3
    # Last run was direct completion (no sub_job_id)
    last_run = agent_runs[-1]
    assert last_run.sub_job_id is None


def test_iterative_delegation_max_iterations(async_engine):
    """Loop respects max_iterations, escalates to PAUSED."""
    workspace = tempfile.mkdtemp()

    backend = FileWritingMockBackend()
    backend.set_emit_file(SIMPLE_EMIT_FLOW)
    backend.set_auto_complete(result={})

    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        **{k: v for k, v in cfg.items() if k not in ("prompt", "output_mode", "output_path")},
    ))

    wf = WorkflowDefinition(steps={
        "agent-phase": StepDefinition(
            name="agent-phase",
            executor=ExecutorRef("agent", {
                "prompt": "Implement feature",
                "emit_flow": True,
            }),
            outputs=["result"],
            exit_rules=[
                ExitRule(
                    name="continue",
                    type="expression",
                    config={
                        "condition": "outputs.get('_delegated', False)",
                        "action": "loop",
                        "target": "agent-phase",
                        "max_iterations": 2,
                    },
                    priority=2,
                ),
                ExitRule(
                    name="done",
                    type="expression",
                    config={
                        "condition": "True",
                        "action": "advance",
                    },
                    priority=1,
                ),
            ],
        ),
    })

    job = async_engine.create_job(
        objective="test max iterations",
        workflow=wf,
        workspace_path=workspace,
    )
    result = run_job_sync(async_engine, job.id, timeout=15)
    # Should be PAUSED because max_iterations was exceeded
    assert result.status == JobStatus.PAUSED
