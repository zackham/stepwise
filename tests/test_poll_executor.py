"""Tests for the PollExecutor and poll watch engine integration."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    PollExecutor,
)
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)


# ── Unit tests: PollExecutor.start() ─────────────────────────────────


class TestPollExecutorStart:
    def _make_context(self, **kwargs):
        defaults = dict(
            job_id="j1",
            step_name="wait",
            attempt=1,
            workspace_path="/tmp",
            idempotency="test",
        )
        defaults.update(kwargs)
        return ExecutionContext(**defaults)

    def test_returns_watch_with_poll_mode(self):
        executor = PollExecutor(
            check_command="gh pr view --json state",
            interval_seconds=30,
        )
        result = executor.start({}, self._make_context())
        assert result.type == "watch"
        assert result.watch.mode == "poll"
        assert result.watch.config["check_command"] == "gh pr view --json state"
        assert result.watch.config["interval_seconds"] == 30

    def test_passes_through_preinterpolated_command(self):
        """PollExecutor.start() passes command through as-is (engine interpolates upstream)."""
        executor = PollExecutor(
            check_command="gh pr view 42 --json state",
            interval_seconds=10,
        )
        result = executor.start({"pr_number": "42"}, self._make_context())
        assert result.watch.config["check_command"] == "gh pr view 42 --json state"

    def test_passes_through_preinterpolated_prompt(self):
        """PollExecutor.start() passes prompt through as-is (engine interpolates upstream)."""
        executor = PollExecutor(
            check_command="echo",
            prompt="Waiting for PR #7",
        )
        result = executor.start({"pr_number": "7"}, self._make_context())
        assert result.watch.config["prompt"] == "Waiting for PR #7"

    def test_no_prompt_if_empty(self):
        executor = PollExecutor(check_command="echo", prompt="")
        result = executor.start({}, self._make_context())
        assert "prompt" not in result.watch.config


# ── Integration tests: poll watch in AsyncEngine ─────────────────────


class TestPollWatchEngine:
    @pytest.mark.asyncio
    async def test_poll_watch_fulfills_on_json_output(self, async_engine):
        """Poll executor suspends, engine polls check_command, completes on JSON."""
        # Create a script that outputs JSON on the second call
        tmpdir = tempfile.mkdtemp()
        marker = Path(tmpdir) / "called"
        check_script = Path(tmpdir) / "check.sh"
        check_script.write_text(f"""#!/bin/bash
if [ -f "{marker}" ]; then
    echo '{{"status": "approved"}}'
else
    touch "{marker}"
    echo ""
fi
""")
        check_script.chmod(0o755)

        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef("poll", {
                    "check_command": str(check_script),
                    "interval_seconds": 1,
                }),
                outputs=["status"],
            ),
        })
        job = async_engine.create_job(objective="test poll", workflow=wf)

        engine_task = asyncio.create_task(async_engine.run())
        try:
            async_engine.start_job(job.id)
            result = await asyncio.wait_for(
                async_engine.wait_for_job(job.id), timeout=10
            )
            assert result.status == JobStatus.COMPLETED
            runs = async_engine.store.runs_for_job(job.id)
            assert len(runs) == 1
            assert runs[0].status == StepRunStatus.COMPLETED
            assert runs[0].result.artifact["status"] == "approved"
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_poll_watch_stays_suspended_until_json(self, async_engine):
        """Poll watch remains suspended when check_command returns empty."""
        tmpdir = tempfile.mkdtemp()
        check_script = Path(tmpdir) / "check.sh"
        # Always returns empty — never fulfilled
        check_script.write_text("#!/bin/bash\necho ''")
        check_script.chmod(0o755)

        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef("poll", {
                    "check_command": str(check_script),
                    "interval_seconds": 1,
                }),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test poll", workflow=wf)

        engine_task = asyncio.create_task(async_engine.run())
        try:
            async_engine.start_job(job.id)
            # Wait long enough for at least one poll cycle
            await asyncio.sleep(2.5)
            runs = async_engine.store.runs_for_job(job.id)
            assert len(runs) == 1
            assert runs[0].status == StepRunStatus.SUSPENDED
            assert runs[0].watch.mode == "poll"
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_poll_watch_cancelled_on_job_cancel(self, async_engine):
        """Poll timer is cleaned up when job is cancelled."""
        tmpdir = tempfile.mkdtemp()
        check_script = Path(tmpdir) / "check.sh"
        check_script.write_text("#!/bin/bash\necho ''")
        check_script.chmod(0o755)

        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef("poll", {
                    "check_command": str(check_script),
                    "interval_seconds": 1,
                }),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test poll", workflow=wf)

        engine_task = asyncio.create_task(async_engine.run())
        try:
            async_engine.start_job(job.id)
            await asyncio.sleep(1.5)  # Let poll start
            async_engine.cancel_job(job.id)
            # Poll task should be cleaned up
            assert len(async_engine._poll_tasks) == 0
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_poll_with_upstream_input(self, async_engine):
        """Poll step receives inputs from upstream step."""
        from tests.conftest import register_step_fn

        register_step_fn("make_pr", lambda inputs: {"pr_number": "99"})

        tmpdir = tempfile.mkdtemp()
        check_script = Path(tmpdir) / "check.sh"
        # Immediately return JSON — we're testing input wiring, not polling
        check_script.write_text('#!/bin/bash\necho \'{"decision": "approved"}\'')
        check_script.chmod(0o755)

        wf = WorkflowDefinition(steps={
            "create-pr": StepDefinition(
                name="create-pr",
                executor=ExecutorRef("callable", {"fn_name": "make_pr"}),
                outputs=["pr_number"],
            ),
            "wait-review": StepDefinition(
                name="wait-review",
                executor=ExecutorRef("poll", {
                    "check_command": str(check_script),
                    "interval_seconds": 1,
                }),
                inputs=[InputBinding("pr_number", "create-pr", "pr_number")],
                outputs=["decision"],
                after=["create-pr"],
            ),
        })
        job = async_engine.create_job(objective="test poll", workflow=wf)

        engine_task = asyncio.create_task(async_engine.run())
        try:
            async_engine.start_job(job.id)
            result = await asyncio.wait_for(
                async_engine.wait_for_job(job.id), timeout=10
            )
            assert result.status == JobStatus.COMPLETED
            runs = async_engine.store.runs_for_job(job.id)
            wait_run = [r for r in runs if r.step_name == "wait-review"][0]
            assert wait_run.result.artifact["decision"] == "approved"
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass


# ── YAML parsing tests ───────────────────────────────────────────────


class TestPollYamlParsing:
    def test_parse_poll_step(self):
        from stepwise.yaml_loader import load_workflow_yaml
        yaml_str = """
name: test-poll
steps:
  wait-approval:
    executor: poll
    check_command: |
      gh pr view 42 --json reviewDecision
    interval_seconds: 30
    outputs: [decision]
"""
        tmpdir = tempfile.mkdtemp()
        flow_file = Path(tmpdir) / "test.flow.yaml"
        flow_file.write_text(yaml_str)
        wf = load_workflow_yaml(str(flow_file))
        step = wf.steps["wait-approval"]
        assert step.executor.type == "poll"
        assert "gh pr view 42" in step.executor.config["check_command"]
        assert step.executor.config["interval_seconds"] == 30

    def test_parse_poll_step_with_config_block(self):
        from stepwise.yaml_loader import load_workflow_yaml
        yaml_str = """
name: test-poll
steps:
  wait-deploy:
    executor: poll
    config:
      check_command: "curl -s https://example.com/health"
      interval_seconds: 10
    outputs: [status]
"""
        tmpdir = tempfile.mkdtemp()
        flow_file = Path(tmpdir) / "test.flow.yaml"
        flow_file.write_text(yaml_str)
        wf = load_workflow_yaml(str(flow_file))
        step = wf.steps["wait-deploy"]
        assert step.executor.type == "poll"
        assert step.executor.config["check_command"] == "curl -s https://example.com/health"
        assert step.executor.config["interval_seconds"] == 10

    def test_parse_poll_step_requires_check_command(self):
        from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
        yaml_str = """
name: test-poll
steps:
  broken:
    executor: poll
    outputs: [result]
"""
        tmpdir = tempfile.mkdtemp()
        flow_file = Path(tmpdir) / "test.flow.yaml"
        flow_file.write_text(yaml_str)
        with pytest.raises(YAMLLoadError, match="check_command"):
            load_workflow_yaml(str(flow_file))

    def test_parse_poll_step_with_prompt(self):
        from stepwise.yaml_loader import load_workflow_yaml
        yaml_str = """
name: test-poll
steps:
  wait-review:
    executor: poll
    check_command: "gh pr view --json state"
    prompt: "Waiting for PR review"
    outputs: [state]
"""
        tmpdir = tempfile.mkdtemp()
        flow_file = Path(tmpdir) / "test.flow.yaml"
        flow_file.write_text(yaml_str)
        wf = load_workflow_yaml(str(flow_file))
        step = wf.steps["wait-review"]
        assert step.executor.config["prompt"] == "Waiting for PR review"
