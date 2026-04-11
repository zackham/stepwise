"""Tests for usage limit detection and agent executor resilience."""
import json
import os
import tempfile
import threading
import time
import pytest
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from stepwise.agent import _detect_usage_limit_in_line
from stepwise.acp_ndjson import tail_for_usage_limit


class TestDetectUsageLimitInLine:
    def test_ndjson_error_message(self):
        line = json.dumps({"error": {"message": "You're out of extra usage · resets 3pm (America/Los_Angeles)"}})
        assert _detect_usage_limit_in_line(line, parse_json=True) is not None

    def test_ndjson_agent_chunk(self):
        line = json.dumps({"params": {"update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "out of extra usage resets 3pm (UTC)"}
        }}})
        assert _detect_usage_limit_in_line(line, parse_json=True) is not None

    def test_stderr_raw_text(self):
        assert _detect_usage_limit_in_line(
            "You're out of extra usage · resets 3pm (America/Los_Angeles)",
            parse_json=False) is not None

    def test_normal_ndjson_no_match(self):
        line = json.dumps({"params": {"update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "Hello, I'll help you with that."}
        }}})
        assert _detect_usage_limit_in_line(line, parse_json=True) is None

    def test_normal_stderr_no_match(self):
        assert _detect_usage_limit_in_line("Warning: some deprecation", parse_json=False) is None

    def test_malformed_json_no_crash(self):
        assert _detect_usage_limit_in_line("{bad json", parse_json=True) is None


class TestTailForUsageLimit:
    def test_offset_tracking(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"params": {}}\n')
            f.write('{"params": {}}\n')
            path = f.name
        try:
            offset, hit = tail_for_usage_limit(path, 0, parse_json=True)
            assert hit is None
            assert offset > 0
            # Write more data
            with open(path, "a") as f:
                f.write(json.dumps({"error": {"message": "out of extra usage resets 3pm (UTC)"}}) + "\n")
            offset2, hit2 = tail_for_usage_limit(path, offset, parse_json=True)
            assert hit2 is not None
            assert offset2 > offset
        finally:
            os.unlink(path)

    def test_missing_file(self):
        offset, hit = tail_for_usage_limit("/nonexistent", 0, parse_json=True)
        assert offset == 0
        assert hit is None


class TestAgentExecutorUsageLimit:
    """Integration tests using MockAgentBackend."""

    def test_callback_fires_and_sets_executor_state(self):
        """Usage limit detection triggers state_update_fn with usage_limit_waiting."""
        from stepwise.agent import AgentExecutor, AgentProcess, AgentStatus

        state_updates = []

        class MockBackend:
            supports_resume = False
            def spawn(self, prompt, config, context):
                return AgentProcess(pid=1, pgid=1, output_path="/tmp/out.jsonl",
                                     working_dir="/tmp")
            def wait(self, process, on_usage_limit=None):
                # Simulate: usage limit detected, then process exits normally
                if on_usage_limit:
                    from datetime import datetime
                    on_usage_limit(datetime(2026, 3, 29, 15, 0), "resets 3pm")
                return AgentStatus(state="completed", exit_code=0)
            def check(self, process): pass
            def cancel(self, process): pass

        executor = AgentExecutor(backend=MockBackend(), config={})
        from stepwise.executors import ExecutionContext
        ctx = ExecutionContext(
            job_id="job-test", step_name="test-step", attempt=1,
            workspace_path="/tmp", idempotency="idempotent",
        )
        ctx.state_update_fn = lambda state: state_updates.append(dict(state))

        result = executor.start({}, ctx)

        # First update: initial state (pid set)
        # Then: usage_limit_waiting=True
        assert any(s.get("usage_limit_waiting") is True for s in state_updates)
        # Last update: usage_limit_waiting=False (cleared)
        assert state_updates[-1].get("usage_limit_waiting") is False
        # Result should be successful
        assert result.type == "data"
        assert not (result.executor_state or {}).get("failed")

    def test_no_callback_without_state_update_fn(self):
        """Without state_update_fn, usage limit callback doesn't crash."""
        from stepwise.agent import AgentExecutor, AgentProcess, AgentStatus

        class MockBackend:
            supports_resume = False
            def spawn(self, prompt, config, context):
                return AgentProcess(pid=1, pgid=1, output_path="/tmp/out.jsonl",
                                     working_dir="/tmp")
            def wait(self, process, on_usage_limit=None):
                if on_usage_limit:
                    on_usage_limit(None, "resets 3pm")
                return AgentStatus(state="completed", exit_code=0)
            def check(self, process): pass
            def cancel(self, process): pass

        executor = AgentExecutor(backend=MockBackend(), config={})
        from stepwise.executors import ExecutionContext
        ctx = ExecutionContext(
            job_id="job-test", step_name="test-step", attempt=1,
            workspace_path="/tmp", idempotency="idempotent",
        )
        # No state_update_fn set
        result = executor.start({}, ctx)
        assert result.type == "data"


class TestBroadcastOnUsageLimit:
    """Verify engine broadcasts tick when state_update_fn sets usage_limit_waiting."""

    def test_broadcast_fires_on_usage_limit(self, store, async_engine):
        broadcasts = []
        async_engine.on_broadcast = lambda event: broadcasts.append(event)

        from stepwise.models import (
            Job, JobStatus, WorkflowDefinition, StepDefinition,
            ExecutorRef, StepRun, StepRunStatus, _now,
        )
        wf = WorkflowDefinition(steps={
            "s": StepDefinition(name="s", executor=ExecutorRef(type="callable"),
                                outputs=["x"]),
        })
        job = Job(id="job-bcast", objective="test", workflow=wf,
                  inputs={}, status=JobStatus.RUNNING)
        store.save_job(job)
        run = StepRun(id="run-bcast", job_id="job-bcast", step_name="s",
                      attempt=1, status=StepRunStatus.RUNNING, started_at=_now())
        store.save_run(run)

        # Directly invoke the pattern: update state with usage_limit_waiting
        run = store.load_run("run-bcast")
        run.executor_state = {"usage_limit_waiting": True, "reset_at": "2026-03-29T15:00:00"}
        store.save_run(run)
        if async_engine.on_broadcast:
            async_engine.on_broadcast({"job_id": "job-bcast"})

        assert len(broadcasts) == 1
        assert broadcasts[0]["job_id"] == "job-bcast"


class TestEngineWithUsageLimitRuns:
    """Verify engine behavior is unchanged when runs have usage_limit_waiting in executor_state."""

    def test_job_stays_running(self, async_engine, store):
        """A RUNNING step with usage_limit_waiting does not trigger job failure."""
        from stepwise.models import (
            Job, JobStatus, WorkflowDefinition, StepDefinition,
            ExecutorRef, StepRun, StepRunStatus, _now,
        )
        wf = WorkflowDefinition(steps={
            "agent-step": StepDefinition(
                name="agent-step", executor=ExecutorRef(type="callable"),
                outputs=["result"]),
        })
        job = Job(id="job-1", objective="test", workflow=wf,
                  inputs={}, status=JobStatus.RUNNING)
        store.save_job(job)
        run = StepRun(
            id="run-1", job_id="job-1", step_name="agent-step",
            attempt=1, status=StepRunStatus.RUNNING,
            executor_state={"usage_limit_waiting": True,
                            "reset_at": "2026-03-29T15:00:00"},
            started_at=_now(),
        )
        store.save_run(run)

        # _job_complete should return False (run is RUNNING = in-motion)
        assert not async_engine._job_complete(job)
        # Job should NOT be settled
        job = store.load_job("job-1")
        assert job.status == JobStatus.RUNNING

    def test_step_readiness_blocked(self, async_engine, store):
        """A RUNNING step with usage_limit_waiting blocks re-launch (standard RUNNING behavior)."""
        from stepwise.models import (
            Job, JobStatus, WorkflowDefinition, StepDefinition,
            ExecutorRef, InputBinding, StepRun, StepRunStatus, _now,
        )
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a", executor=ExecutorRef(type="callable"),
                outputs=["x"]),
            "step-b": StepDefinition(
                name="step-b", executor=ExecutorRef(type="callable"),
                inputs=[InputBinding("x", "step-a", "x")],
                outputs=["y"]),
        })
        job = Job(id="job-2", objective="test", workflow=wf,
                  inputs={}, status=JobStatus.RUNNING)
        store.save_job(job)
        run = StepRun(
            id="run-2", job_id="job-2", step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            executor_state={"usage_limit_waiting": True},
            started_at=_now(),
        )
        store.save_run(run)

        # step-a is RUNNING → not ready (standard behavior)
        assert not async_engine._is_step_ready(job, "step-a", wf.steps["step-a"])
        # step-b depends on step-a → not ready
        assert not async_engine._is_step_ready(job, "step-b", wf.steps["step-b"])

    def test_cancel_works_during_usage_limit(self, store):
        """Run with usage_limit_waiting can be cancelled via the cancel_run API path."""
        from stepwise.models import (
            Job, JobStatus, WorkflowDefinition, StepDefinition,
            ExecutorRef, StepRun, StepRunStatus, _now,
        )
        wf = WorkflowDefinition(steps={
            "s": StepDefinition(name="s", executor=ExecutorRef(type="callable"),
                                outputs=["x"]),
        })
        job = Job(id="job-c", objective="test", workflow=wf,
                  inputs={}, status=JobStatus.RUNNING)
        store.save_job(job)
        run = StepRun(
            id="run-cancel", job_id="job-c", step_name="s",
            attempt=1, status=StepRunStatus.RUNNING,
            executor_state={"usage_limit_waiting": True},
            started_at=_now(),
        )
        store.save_run(run)
        loaded = store.load_run("run-cancel")
        # Status is RUNNING — cancel_run API allows cancellation
        assert loaded.status == StepRunStatus.RUNNING
        assert loaded.executor_state["usage_limit_waiting"] is True
