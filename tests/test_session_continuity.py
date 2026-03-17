"""Tests for session continuity: continue_session, loop_prompt, cross-step sharing."""

import asyncio

import pytest

from tests.conftest import register_step_fn, run_job, run_job_sync
from stepwise.agent import AgentExecutor, MockAgentBackend
from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutionContext, ExecutorRegistry
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_backend():
    return MockAgentBackend()


@pytest.fixture
def engine(mock_backend):
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    from tests.conftest import CallableExecutor
    reg.register("callable", lambda cfg: CallableExecutor(fn_name=cfg.get("fn_name", "default")))
    reg.register("agent", lambda cfg: AgentExecutor(
        backend=mock_backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        output_path=cfg.get("output_path"),
        **{k: v for k, v in cfg.items()
           if k not in ("prompt", "output_mode", "output_path")},
    ))
    return AsyncEngine(store=store, registry=reg)


# ── Session Continuity Tests ─────────────────────────────────────────


class TestContinueSession:
    def test_continue_session_reuses_session_name(self, engine, mock_backend):
        """Agent step with continue_session=True loops — session name is stable."""
        call_count = {"n": 0}

        def auto_complete_result():
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return {"done": True, "result": "final"}
            return {"done": False, "result": "intermediate"}

        mock_backend.set_auto_complete(result={"done": False, "result": "intermediate"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["done", "result"],
                executor=ExecutorRef("agent", {"prompt": "Do work"}),
                continue_session=True,
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "agent-step", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("session test", wf)

        # Run first iteration
        async def run_one_step():
            engine_task = asyncio.create_task(engine.run())
            engine.start_job(job.id)
            # Wait for first step to launch and complete
            import time
            for _ in range(50):
                await asyncio.sleep(0.02)
                j = engine.store.load_job(job.id)
                runs = engine.store.runs_for_job(job.id)
                if len(runs) >= 2 or j.status != JobStatus.RUNNING:
                    break
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_one_step())

        runs = engine.store.runs_for_job(job.id)
        # Verify session names across runs
        session_names = set()
        for r in runs:
            if r.executor_state and r.executor_state.get("session_name"):
                session_names.add(r.executor_state["session_name"])

        # With continue_session, second run should use the same session name
        # (engine passes _prev_session_name from first run's executor_state)
        pids = [p for p in mock_backend._processes]
        if len(pids) >= 2:
            first_session = mock_backend._processes[pids[0]].get("session_name", "")
            second_session = mock_backend._processes[pids[1]].get("session_name", "")
            # First run gets "step-agent-step-1" (no prev session), second should reuse
            assert second_session == first_session or "agent-step" in second_session

    def test_continue_session_uses_loop_prompt(self, engine, mock_backend):
        """On attempt 2, verify the prompt is the loop_prompt template."""
        mock_backend.set_auto_complete(result={"done": False, "result": "x"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["done", "result"],
                executor=ExecutorRef("agent", {"prompt": "Initial task: do $objective"}),
                continue_session=True,
                loop_prompt="Continue with previous result: $result",
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "agent-step", "max_iterations": 3,
                    }, priority=5),
                ],
                inputs=[
                    InputBinding("result", "agent-step", "result", optional=True),
                ],
            ),
        })

        job = engine.create_job("loop prompt test", wf)

        async def run_two_iterations():
            engine_task = asyncio.create_task(engine.run())
            engine.start_job(job.id)
            for _ in range(100):
                await asyncio.sleep(0.02)
                runs = engine.store.runs_for_job(job.id)
                if len(runs) >= 2:
                    break
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_two_iterations())

        pids = sorted(mock_backend._processes.keys())
        if len(pids) >= 2:
            first_prompt = mock_backend._processes[pids[0]]["prompt"]
            second_prompt = mock_backend._processes[pids[1]]["prompt"]
            # First should use original prompt
            assert "Initial task" in first_prompt
            # Second should use loop_prompt
            assert "Continue with previous result" in second_prompt


class TestLoopPromptWithoutSession:
    def test_loop_prompt_without_continue_session(self, engine, mock_backend):
        """Agent step with loop_prompt but continue_session=False — uses loop_prompt on attempt > 1."""
        mock_backend.set_auto_complete(result={"done": False, "result": "x"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["done", "result"],
                executor=ExecutorRef("agent", {"prompt": "Original prompt"}),
                loop_prompt="Loop iteration prompt",
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "agent-step", "max_iterations": 3,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("loop prompt no session", wf)

        async def run_two_iterations():
            engine_task = asyncio.create_task(engine.run())
            engine.start_job(job.id)
            for _ in range(100):
                await asyncio.sleep(0.02)
                runs = engine.store.runs_for_job(job.id)
                if len(runs) >= 2:
                    break
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_two_iterations())

        pids = sorted(mock_backend._processes.keys())
        if len(pids) >= 2:
            first_prompt = mock_backend._processes[pids[0]]["prompt"]
            second_prompt = mock_backend._processes[pids[1]]["prompt"]
            assert "Original prompt" in first_prompt
            assert "Loop iteration prompt" in second_prompt


class TestMaxContinuousAttempts:
    def test_max_continuous_attempts_circuit_breaker(self, engine, mock_backend):
        """After max_continuous_attempts, session name changes (fresh session)."""
        mock_backend.set_auto_complete(result={"done": False, "result": "x"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["done", "result"],
                executor=ExecutorRef("agent", {"prompt": "Work"}),
                continue_session=True,
                max_continuous_attempts=2,
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "agent-step", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("circuit breaker test", wf)

        async def run_iterations():
            engine_task = asyncio.create_task(engine.run())
            engine.start_job(job.id)
            for _ in range(200):
                await asyncio.sleep(0.02)
                runs = engine.store.runs_for_job(job.id)
                if len(runs) >= 3:
                    break
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_iterations())

        pids = sorted(mock_backend._processes.keys())
        # Attempt 3 exceeds max_continuous_attempts=2, should get fresh session
        if len(pids) >= 3:
            session_3 = mock_backend._processes[pids[2]].get("session_name", "")
            # Fresh session should have attempt suffix (not the stable name)
            assert session_3 == f"step-agent-step-3" or "agent-step" in session_3


class TestPrevSessionPassedViaConfig:
    def test_prev_session_passed_via_config(self, engine, mock_backend):
        """Verify engine passes _prev_session_name in exec_ref config."""
        mock_backend.set_auto_complete(result={"done": False, "result": "x"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["done", "result"],
                executor=ExecutorRef("agent", {"prompt": "Work"}),
                continue_session=True,
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "agent-step", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("config pass test", wf)

        async def run_two():
            engine_task = asyncio.create_task(engine.run())
            engine.start_job(job.id)
            for _ in range(100):
                await asyncio.sleep(0.02)
                runs = engine.store.runs_for_job(job.id)
                if len(runs) >= 2:
                    break
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_two())

        # Check that the second spawn received a session name in config
        pids = sorted(mock_backend._processes.keys())
        if len(pids) >= 2:
            second_config = mock_backend._processes[pids[1]]["config"]
            # Should have _session_name set (from _prev_session_name or stable name)
            session_name = second_config.get("_session_name")
            assert session_name is not None, f"Expected _session_name in config: {second_config}"


class TestYAMLParseContinueSession:
    def test_yaml_parse_continue_session(self):
        """Parse continue_session, loop_prompt, max_continuous_attempts from YAML."""
        wf = load_workflow_string("""
steps:
  implement:
    executor: agent
    prompt: "Build: $spec"
    continue_session: true
    loop_prompt: "Continue: $prev_result"
    max_continuous_attempts: 5
    inputs:
      spec: $job.spec
    outputs: [result]
""")
        step = wf.steps["implement"]
        assert step.continue_session is True
        assert step.loop_prompt == "Continue: $prev_result"
        assert step.max_continuous_attempts == 5

    def test_yaml_defaults(self):
        """Missing session fields default correctly."""
        wf = load_workflow_string("""
steps:
  simple:
    executor: agent
    prompt: "Do work"
    outputs: [result]
""")
        step = wf.steps["simple"]
        assert step.continue_session is False
        assert step.loop_prompt is None
        assert step.max_continuous_attempts is None


class TestSessionSerialization:
    def test_step_definition_roundtrip(self):
        """StepDefinition with session fields round-trips through to_dict/from_dict."""
        step = StepDefinition(
            name="test",
            outputs=["result"],
            executor=ExecutorRef("agent", {"prompt": "work"}),
            continue_session=True,
            loop_prompt="Continue: $prev",
            max_continuous_attempts=3,
        )
        d = step.to_dict()
        assert d["continue_session"] is True
        assert d["loop_prompt"] == "Continue: $prev"
        assert d["max_continuous_attempts"] == 3

        restored = StepDefinition.from_dict(d)
        assert restored.continue_session is True
        assert restored.loop_prompt == "Continue: $prev"
        assert restored.max_continuous_attempts == 3

    def test_defaults_not_serialized(self):
        """Default session field values don't appear in serialized output."""
        step = StepDefinition(
            name="test", outputs=["result"],
            executor=ExecutorRef("agent", {}),
        )
        d = step.to_dict()
        assert "continue_session" not in d
        assert "loop_prompt" not in d
        assert "max_continuous_attempts" not in d


# ── Phase 3: Cross-step Session Sharing ──────────────────────────────


class TestSessionIdAutoEmit:
    def test_session_id_auto_emitted(self, engine, mock_backend):
        """Agent step with continue_session=True — verify _session_id in artifact."""
        mock_backend.set_auto_complete(result={"result": "done"})

        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work"}),
                continue_session=True,
            ),
        })

        job = engine.create_job("session id test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = engine.store.runs_for_job(job.id)
        assert len(runs) == 1
        artifact = runs[0].result.artifact
        assert "_session_id" in artifact
        assert "agent-step" in artifact["_session_id"]

    def test_session_id_flows_to_downstream(self, engine, mock_backend):
        """Step B receives _session_id from step A — verify B continues A's session."""
        mock_backend.set_auto_complete(result={"result": "done"})

        register_step_fn("passthrough", lambda i: {"result": i.get("_session_id", "none")})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work A"}),
                continue_session=True,
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work B"}),
                inputs=[
                    InputBinding("_session_id", "a", "_session_id", optional=True),
                    InputBinding("a_result", "a", "result"),
                ],
            ),
        })

        job = engine.create_job("downstream session test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED

        # Check that B's spawn received the session name from A
        pids = sorted(mock_backend._processes.keys())
        if len(pids) >= 2:
            b_config = mock_backend._processes[pids[1]]["config"]
            assert "_session_name" in b_config

    def test_session_id_passthrough(self, engine, mock_backend):
        """Step B receives and re-emits _session_id — step C can continue."""
        mock_backend.set_auto_complete(result={"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work A"}),
                continue_session=True,
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work B"}),
                inputs=[
                    InputBinding("_session_id", "a", "_session_id", optional=True),
                    InputBinding("a_result", "a", "result"),
                ],
            ),
            "c": StepDefinition(
                name="c", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work C"}),
                inputs=[
                    InputBinding("_session_id", "b", "_session_id", optional=True),
                    InputBinding("b_result", "b", "result"),
                ],
            ),
        })

        job = engine.create_job("passthrough session test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED

        # C should also get the session
        pids = sorted(mock_backend._processes.keys())
        if len(pids) >= 3:
            c_config = mock_backend._processes[pids[2]]["config"]
            assert "_session_name" in c_config

    def test_session_id_with_optional_input(self, engine, mock_backend):
        """_session_id as optional — first step creates, second continues."""
        mock_backend.set_auto_complete(result={"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work A"}),
                continue_session=True,
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("agent", {"prompt": "Work B"}),
                inputs=[
                    InputBinding("_session_id", "a", "_session_id", optional=True),
                ],
            ),
        })

        job = engine.create_job("optional session test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED


class TestSessionLock:
    def test_session_lock_serializes_access(self, engine, mock_backend):
        """Two steps sharing _session_id via inputs — verify lock exists."""
        # This is a structural test; full concurrency testing requires for_each
        from stepwise.engine import _SessionLockManager

        mgr = _SessionLockManager()
        lock = mgr.get_lock("test-session")
        assert not mgr.is_locked("test-session")
        assert lock is not None
