"""Tests for agent ergonomics features: resolved_flow_status, idempotent fulfill, CLI enhancements."""

import io
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stepwise.cli import main, EXIT_SUCCESS, EXIT_JOB_FAILED, EXIT_USAGE_ERROR, EXIT_SUSPENDED
from stepwise.engine import Engine
from stepwise.executors import ExecutorResult, ExecutorStatus
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn


def _capture_stdout(argv: list[str]) -> tuple[int, str]:
    """Run CLI and capture stdout."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = main(argv)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, output


# ── Helpers ────────────────────────────────────────────────────────────

def _run_to_completion(engine, job_id, max_ticks=20):
    """Tick engine until job reaches terminal state."""
    for _ in range(max_ticks):
        engine.tick()
        job = engine.get_job(job_id)
        if job.status.value in ("completed", "failed", "cancelled"):
            return job
    return engine.get_job(job_id)


def _run_to_suspension(engine, job_id, max_ticks=20):
    """Tick engine until a step suspends."""
    for _ in range(max_ticks):
        engine.tick()
        runs = engine.get_runs(job_id)
        suspended = [r for r in runs if r.status == StepRunStatus.SUSPENDED]
        if suspended:
            return suspended
    return []


# ── Phase 1a: resolved_flow_status ────────────────────────────────────


class TestResolvedFlowStatus:
    """Tests for engine.resolved_flow_status()."""

    def test_flat_flow_all_completed(self, engine, store):
        """Flat flow with all steps completed."""
        register_step_fn("step_a", lambda inputs: {"x": 1})
        register_step_fn("step_b", lambda inputs: {"y": 2})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x"],
                executor=ExecutorRef("callable", {"fn_name": "step_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["y"],
                executor=ExecutorRef("callable", {"fn_name": "step_b"}),
                inputs=[InputBinding("x", "a", "x")],
            ),
        })
        job = engine.create_job(objective="test-flow", workflow=wf)
        engine.start_job(job.id)
        job = _run_to_completion(engine, job.id)
        assert job.status.value == "completed"

        status = engine.resolved_flow_status(job.id)

        assert status["job_id"] == job.id
        assert status["status"] == "completed"
        assert status["flow"] == "test-flow"
        assert len(status["steps"]) == 2

        step_a = status["steps"][0]
        assert step_a["name"] == "a"
        assert step_a["type"] == "callable"
        assert step_a["status"] == "completed"
        assert step_a["attempt"] == 1
        assert "x" in step_a["outputs"]

        step_b = status["steps"][1]
        assert step_b["name"] == "b"
        assert step_b["status"] == "completed"
        assert "y" in step_b["outputs"]

        assert status["sub_jobs"] == []

    def test_flow_with_suspended_step(self, engine, store):
        """Flow with a suspended external step shows prompt, run_id, expected_outputs."""
        register_step_fn("prep", lambda inputs: {"data": "ready"})

        wf = WorkflowDefinition(steps={
            "prep": StepDefinition(
                name="prep", outputs=["data"],
                executor=ExecutorRef("callable", {"fn_name": "prep"}),
            ),
            "review": StepDefinition(
                name="review", outputs=["approved"],
                executor=ExecutorRef("external", {"prompt": "Review the data"}),
                inputs=[InputBinding("data", "prep", "data")],
            ),
        })
        job = engine.create_job(objective="review-flow", workflow=wf)
        engine.start_job(job.id)
        suspended = _run_to_suspension(engine, job.id)
        assert len(suspended) > 0

        status = engine.resolved_flow_status(job.id)
        assert status["status"] == "running"

        review = next(s for s in status["steps"] if s["name"] == "review")
        assert review["status"] == "suspended"
        assert review["prompt"] == "Review the data"
        assert review["expected_outputs"] == ["approved"]
        assert "run_id" in review
        assert "suspended_at" in review

    def test_flow_with_pending_steps(self, engine, store):
        """Pending steps show depends_on."""
        wf = WorkflowDefinition(steps={
            "first": StepDefinition(
                name="first", outputs=["x"],
                executor=ExecutorRef("callable", {"fn_name": "first"}),
            ),
            "second": StepDefinition(
                name="second", outputs=["y"],
                executor=ExecutorRef("callable", {"fn_name": "second"}),
                inputs=[InputBinding("x", "first", "x")],
            ),
        })
        job = engine.create_job(objective="dep-flow", workflow=wf)
        # Don't start — check pending state
        status = engine.resolved_flow_status(job.id)

        for step in status["steps"]:
            assert step["status"] == "pending"

        second = next(s for s in status["steps"] if s["name"] == "second")
        assert second["depends_on"] == ["first"]

    def test_cost_included(self, engine, store):
        """Per-step and total cost are included."""
        register_step_fn("costly", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "costly": StepDefinition(
                name="costly", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "costly"}),
            ),
        })
        job = engine.create_job(objective="cost-flow", workflow=wf)
        engine.start_job(job.id)
        job = _run_to_completion(engine, job.id)
        assert job.status.value == "completed"

        # Add a cost event to the run
        runs = engine.get_runs(job.id)
        store.save_step_event(runs[0].id, "cost", {"cost_usd": 0.05})

        status = engine.resolved_flow_status(job.id)
        assert status["cost_usd"] == 0.05
        assert status["steps"][0]["cost_usd"] == 0.05

    def test_failed_step_shows_error(self, engine, store):
        """Failed step includes error message."""
        def fail_fn(inputs):
            raise RuntimeError("oops")

        register_step_fn("failing", fail_fn)

        wf = WorkflowDefinition(steps={
            "failing": StepDefinition(
                name="failing", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "failing"}),
            ),
        })
        job = engine.create_job(objective="fail-flow", workflow=wf)
        engine.start_job(job.id)
        job = _run_to_completion(engine, job.id)

        status = engine.resolved_flow_status(job.id)
        step = status["steps"][0]
        assert step["status"] == "failed"
        assert "error" in step


# ── Phase 1b: Idempotent fulfill_watch ────────────────────────────────


class TestIdempotentFulfill:
    """Tests for idempotent fulfill_watch behavior."""

    def test_double_fulfill_returns_error(self, engine, store):
        """Fulfilling an already-fulfilled step returns error dict, not exception."""
        register_step_fn("prep", lambda inputs: {"data": "ready"})

        wf = WorkflowDefinition(steps={
            "prep": StepDefinition(
                name="prep", outputs=["data"],
                executor=ExecutorRef("callable", {"fn_name": "prep"}),
            ),
            "review": StepDefinition(
                name="review", outputs=["approved"],
                executor=ExecutorRef("external", {"prompt": "Approve"}),
                inputs=[InputBinding("data", "prep", "data")],
            ),
        })
        job = engine.create_job(objective="idem-test", workflow=wf)
        engine.start_job(job.id)
        suspended = _run_to_suspension(engine, job.id)
        assert len(suspended) > 0

        run_id = suspended[0].id

        # First fulfill — should succeed
        result = engine.fulfill_watch(run_id, {"approved": "yes"})
        assert result is None

        # Second fulfill — should return error dict, not raise
        result = engine.fulfill_watch(run_id, {"approved": "yes"})
        assert result is not None
        assert result["error"] == "already_fulfilled"
        assert result["run_id"] == run_id
        assert result["job_id"] == job.id
        assert "fulfilled_at" in result
        assert "job_status" in result

    def test_fulfill_completed_returns_error(self, engine, store):
        """Fulfilling a completed step returns error dict."""
        register_step_fn("step", lambda inputs: {"x": 1})

        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step", outputs=["x"],
                executor=ExecutorRef("callable", {"fn_name": "step"}),
            ),
        })
        job = engine.create_job(objective="test", workflow=wf)
        engine.start_job(job.id)
        _run_to_completion(engine, job.id)

        runs = engine.get_runs(job.id)
        assert runs[0].status == StepRunStatus.COMPLETED

        result = engine.fulfill_watch(runs[0].id, {"x": 1})
        assert result is not None
        assert result["error"] == "already_fulfilled"


# ── Phase 2: CLI Enhancement Tests ────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal stepwise project in a temp dir."""
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir()
    (dot_dir / "templates").mkdir()
    (dot_dir / "jobs").mkdir()
    return tmp_path


@pytest.fixture
def external_flow(tmp_project):
    """A flow with an external step for CLI tests."""
    flow = tmp_project / "external.flow.yaml"
    flow.write_text("""\
name: external-test
description: Flow with external approval

steps:
  prepare:
    run: |
      python3 -c "import json; print(json.dumps({'data': 'ready', 'count': 42}))"
    outputs: [data, count]

  approve:
    executor: external
    prompt: "Review and approve"
    outputs: [approved, reason]
    inputs:
      data: prepare.data

  deploy:
    run: |
      python3 -c "import json, os; print(json.dumps({'url': 'https://example.com'}))"
    outputs: [url]
    inputs:
      approved: approve.approved
""")
    return flow


class TestStatusJsonCLI:
    """Tests for `status --output json` using resolved_flow_status."""

    def test_status_json_uses_resolved_format(self, external_flow, tmp_project):
        """status --output json returns the resolved flow status format."""
        # Run to get a job
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
            "--var", "repo=test",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        # Now get status
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "status", job_id, "--output", "json",
        ])
        assert code == EXIT_SUCCESS
        status = json.loads(output)

        assert status["job_id"] == job_id
        assert "steps" in status
        assert "cost_usd" in status
        assert "sub_jobs" in status

        # Verify step structure
        step_names = [s["name"] for s in status["steps"]]
        assert "prepare" in step_names
        assert "approve" in step_names


class TestOutputStepCLI:
    """Tests for `output --step` and `output --run`."""

    def test_output_step_completed(self, external_flow, tmp_project):
        """output --step retrieves specific step outputs."""
        # Run until suspended (prepare completes, approve suspends)
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        # Get prepare step output
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id, "--step", "prepare",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["prepare"]["data"] == "ready"
        assert result["prepare"]["count"] == 42

    def test_output_step_not_completed(self, external_flow, tmp_project):
        """output --step for incomplete step returns null + status."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        # deploy hasn't run yet
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id, "--step", "deploy",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["deploy"] is None

    def test_output_step_nonexistent(self, external_flow, tmp_project):
        """output --step for nonexistent step shows error."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id, "--step", "nonexistent",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert "_error" in result["nonexistent"]

    def test_output_step_inputs(self, external_flow, tmp_project):
        """output --step --inputs retrieves step inputs."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", job_id, "--step", "approve", "--inputs",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        # approve's input is data from prepare
        assert "approve" in result
        assert result["approve"]["data"] == "ready"

    def test_output_run_id(self, external_flow, tmp_project):
        """output --run retrieves by run ID."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        # Get the prepare run_id from status
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "status", job_id, "--output", "json",
        ])
        status = json.loads(output)
        # Find the completed prepare step's run_id by querying runs directly
        from stepwise.store import SQLiteStore
        from stepwise.project import find_project
        project = find_project(tmp_project)
        store = SQLiteStore(str(project.db_path))
        try:
            runs = store.runs_for_job(job_id)
            prep_run = [r for r in runs if r.step_name == "prepare"][0]
        finally:
            store.close()

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "output", "--run", prep_run.id,
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["data"] == "ready"


class TestCancelJsonCLI:
    """Tests for `cancel --output json`."""

    def test_cancel_json_output(self, external_flow, tmp_project):
        """cancel --output json returns completed/cancelled/remaining steps."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "cancel", job_id, "--output", "json",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)

        assert result["job_id"] == job_id
        assert result["status"] == "cancelled"
        assert "prepare" in result["completed_steps"]
        assert "approve" in result["cancelled_steps"]
        assert any(s["name"] == "deploy" for s in result["remaining_steps"])


class TestListSuspendedCLI:
    """Tests for `list --suspended`."""

    def test_list_suspended_json(self, external_flow, tmp_project):
        """list --suspended --output json shows pending external steps."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        assert data["status"] == "suspended"

        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "list", "--suspended", "--output", "json",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)

        assert result["count"] >= 1
        item = result["suspended_steps"][0]
        assert "job_id" in item
        assert "run_id" in item
        assert "step_name" in item
        assert "prompt" in item
        assert "expected_outputs" in item
        assert "suspended_at" in item
        assert "age_seconds" in item
        assert "fulfill_command" in item

    def test_list_suspended_empty(self, tmp_project):
        """list --suspended with no jobs returns empty."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "list", "--suspended", "--output", "json",
        ])
        assert code == EXIT_SUCCESS
        result = json.loads(output)
        assert result["count"] == 0
        assert result["suspended_steps"] == []


# ── Phase 3: Wait/Fulfill Flow Tests ─────────────────────────────────


class TestWaitSuspension:
    """Tests for --wait returning on suspension."""

    def test_wait_returns_on_suspension(self, external_flow, tmp_project):
        """--wait returns EXIT_SUSPENDED with suspended step details."""
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])

        assert code == EXIT_SUSPENDED
        data = json.loads(output)
        assert data["status"] == "suspended"
        assert "suspended_steps" in data
        assert len(data["suspended_steps"]) >= 1
        assert "completed_steps" in data
        assert "prepare" in data["completed_steps"]
        assert "cost_usd" in data
        assert "duration_seconds" in data

        step = data["suspended_steps"][0]
        assert step["step"] == "approve"
        assert "run_id" in step
        assert "prompt" in step
        assert "fields" in step
        assert "inputs" in step
        assert "suspended_at" in step


class TestFulfillWait:
    """Tests for fulfill --wait chaining."""

    def test_fulfill_and_wait_to_completion(self, external_flow, tmp_project):
        """fulfill --wait fulfills step then blocks until completion."""
        # Start and get suspended
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        assert code == EXIT_SUSPENDED
        data = json.loads(output)
        run_id = data["suspended_steps"][0]["run_id"]

        # Fulfill with --wait → should complete the job
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", run_id, '{"approved": "yes", "reason": "looks good"}', "--wait",
        ])
        assert code == EXIT_SUCCESS
        data = json.loads(output)
        assert data["status"] == "completed"
        assert "outputs" in data


class TestWaitCommand:
    """Tests for `stepwise wait <job-id>`."""

    def test_wait_on_suspended_job(self, external_flow, tmp_project):
        """wait on a suspended job returns immediately with suspended status."""
        # Start and get suspended
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        assert code == EXIT_SUSPENDED
        data = json.loads(output)
        job_id = data["job_id"]

        # Wait on the still-suspended job
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", job_id,
        ])
        assert code == EXIT_SUSPENDED
        data = json.loads(output)
        assert data["status"] == "suspended"

    def test_wait_on_completed_job(self, external_flow, tmp_project):
        """wait on a completed job returns immediately."""
        # Run the full flow
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "run", str(external_flow), "--wait",
        ])
        data = json.loads(output)
        job_id = data["job_id"]
        run_id = data["suspended_steps"][0]["run_id"]

        # Fulfill
        _capture_stdout([
            "--project-dir", str(tmp_project),
            "fulfill", run_id, '{"approved": "yes", "reason": "ok"}',
        ])

        # Wait on completed job — should return immediately
        code, output = _capture_stdout([
            "--project-dir", str(tmp_project),
            "wait", job_id,
        ])
        assert code == EXIT_SUCCESS
        data = json.loads(output)
        assert data["status"] == "completed"


# ── Phase 4: Project Hooks Tests ─────────────────────────────────────


class TestHooks:
    """Tests for the hooks module."""

    def test_fire_hook_runs_script(self, tmp_path):
        """fire_hook executes a hook script and passes JSON payload on stdin."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        # Create a hook that writes stdin to a file
        output_file = tmp_path / "hook_output.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f"#!/bin/sh\ncat > {output_file}\n")
        hook.chmod(0o755)

        from stepwise.hooks import fire_hook
        payload = {"job_id": "job-123", "step": "review"}
        result = fire_hook("suspend", payload, dot_dir)

        assert result is True
        assert output_file.exists()
        written = json.loads(output_file.read_text())
        assert written["job_id"] == "job-123"
        assert written["step"] == "review"

    def test_fire_hook_no_script(self, tmp_path):
        """fire_hook returns False when no hook script exists."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        from stepwise.hooks import fire_hook
        result = fire_hook("suspend", {}, dot_dir)
        assert result is False

    def test_fire_hook_logs_failure(self, tmp_path):
        """Failed hooks log to .stepwise/logs/hooks.log."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        hook = hooks_dir / "on-fail"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

        from stepwise.hooks import fire_hook
        fire_hook("fail", {"job_id": "job-bad"}, dot_dir)

        log_file = dot_dir / "logs" / "hooks.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "on-fail" in content
        assert "exit=1" in content

    def test_fire_hook_for_event_maps_correctly(self, tmp_path):
        """fire_hook_for_event maps engine events to hook names."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        output_file = tmp_path / "event_payload.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f"#!/bin/sh\ncat > {output_file}\n")
        hook.chmod(0o755)

        from stepwise.hooks import fire_hook_for_event
        result = fire_hook_for_event(
            "step.suspended",
            {"step": "approve", "run_id": "run-abc", "watch_mode": "external"},
            "job-456",
            dot_dir,
        )
        assert result is True
        written = json.loads(output_file.read_text())
        assert written["event"] == "step.suspended"
        assert written["hook"] == "suspend"
        assert written["job_id"] == "job-456"
        assert written["step"] == "approve"
        assert "fulfill_command" in written

    def test_fire_hook_for_event_unmapped_event(self, tmp_path):
        """Unmapped events don't fire hooks."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        from stepwise.hooks import fire_hook_for_event
        result = fire_hook_for_event("step.started", {}, "job-1", dot_dir)
        assert result is False

    def test_fire_hook_for_event_no_project_dir(self):
        """None project_dir is a no-op."""
        from stepwise.hooks import fire_hook_for_event
        result = fire_hook_for_event("step.suspended", {}, "job-1", None)
        assert result is False

    def test_hook_timeout(self, tmp_path):
        """Hooks that exceed timeout are killed and failure logged."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        # Create a hook that sleeps forever — but we'll patch the timeout
        hook = hooks_dir / "on-complete"
        hook.write_text("#!/bin/sh\nsleep 999\n")
        hook.chmod(0o755)

        from unittest.mock import patch
        from stepwise.hooks import fire_hook

        # Patch the timeout to 0.1s for test speed
        with patch("stepwise.hooks.subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.side_effect = [
                __import__("subprocess").TimeoutExpired(cmd="", timeout=0.1)
            ]
            mock_proc.kill.return_value = None
            # Second communicate after kill
            mock_proc.communicate.side_effect = [
                __import__("subprocess").TimeoutExpired(cmd="", timeout=0.1),
                (b"", b""),
            ]

            result = fire_hook("complete", {"job_id": "j1"}, dot_dir)
            assert result is True
            mock_proc.kill.assert_called_once()


class TestHookScaffolding:
    """Tests for hook scaffolding in init."""

    def test_scaffold_hooks_creates_files(self, tmp_path):
        """scaffold_hooks creates executable hook scripts."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        from stepwise.hooks import scaffold_hooks
        created = scaffold_hooks(dot_dir)

        assert len(created) == 4
        hooks_dir = dot_dir / "hooks"
        assert (hooks_dir / "on-suspend").exists()
        assert (hooks_dir / "on-step-complete").exists()
        assert (hooks_dir / "on-complete").exists()
        assert (hooks_dir / "on-fail").exists()

        # Check executable
        import stat
        mode = (hooks_dir / "on-suspend").stat().st_mode
        assert mode & stat.S_IXUSR

    def test_scaffold_hooks_idempotent(self, tmp_path):
        """scaffold_hooks doesn't overwrite existing hooks."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        from stepwise.hooks import scaffold_hooks
        scaffold_hooks(dot_dir)

        # Modify one
        hook = dot_dir / "hooks" / "on-suspend"
        hook.write_text("#!/bin/sh\ncustom stuff\n")

        # Re-scaffold — should not overwrite
        created = scaffold_hooks(dot_dir)
        assert len(created) == 0  # nothing new created
        assert "custom stuff" in hook.read_text()

    def test_init_project_creates_hooks(self, tmp_path):
        """init_project creates hooks dir and scaffolding."""
        from stepwise.project import init_project
        project = init_project(tmp_path)

        hooks_dir = project.dot_dir / "hooks"
        assert hooks_dir.is_dir()
        assert (hooks_dir / "on-suspend").exists()
        assert (hooks_dir / "on-complete").exists()
        assert (hooks_dir / "on-fail").exists()

        logs_dir = project.dot_dir / "logs"
        assert logs_dir.is_dir()


class TestHookEngineIntegration:
    """Tests that the engine fires hooks at the right events."""

    def test_engine_fires_suspend_hook(self, store, engine, tmp_path):
        """Engine fires on-suspend hook when step suspends."""
        # Set up project hooks dir
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        output_file = tmp_path / "suspend_fired.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f"#!/bin/sh\ncat > {output_file}\n")
        hook.chmod(0o755)

        # Point the existing engine at our hooks dir
        engine.project_dir = dot_dir

        register_step_fn("prep_fn", lambda inputs: {"x": 1})

        wf = WorkflowDefinition(steps={
            "prep": StepDefinition(
                name="prep", outputs=["x"],
                executor=ExecutorRef("callable", {"fn_name": "prep_fn"}),
            ),
            "review": StepDefinition(
                name="review", outputs=["approved"],
                executor=ExecutorRef("external", {"prompt": "Review please"}),
                inputs=[InputBinding("x", "prep", "x")],
            ),
        })

        job = engine.create_job(objective="test", workflow=wf)
        engine.start_job(job.id)
        _run_to_suspension(engine, job.id)

        assert output_file.exists()
        payload = json.loads(output_file.read_text())
        assert payload["event"] == "step.suspended"
        assert payload["step"] == "review"
        assert payload["job_id"] == job.id
        assert "fulfill_command" in payload

    def test_engine_fires_complete_hook(self, store, engine, tmp_path):
        """Engine fires on-complete hook when job completes."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()

        output_file = tmp_path / "complete_fired.json"
        hook = hooks_dir / "on-complete"
        hook.write_text(f"#!/bin/sh\ncat > {output_file}\n")
        hook.chmod(0o755)

        engine.project_dir = dot_dir

        register_step_fn("done_fn", lambda inputs: {"y": 2})

        wf = WorkflowDefinition(steps={
            "only": StepDefinition(
                name="only", outputs=["y"],
                executor=ExecutorRef("callable", {"fn_name": "done_fn"}),
            ),
        })

        job = engine.create_job(objective="simple", workflow=wf)
        engine.start_job(job.id)
        _run_to_completion(engine, job.id)

        assert output_file.exists()
        payload = json.loads(output_file.read_text())
        assert payload["event"] == "job.completed"
        assert payload["job_id"] == job.id


# ── Phase 5: Server-Aware CLI Tests ──────────────────────────────────


class TestServerDetect:
    """Tests for server_detect module."""

    def test_no_pidfile_returns_none(self, tmp_path):
        """No server.pid → None."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        from stepwise.server_detect import detect_server
        assert detect_server(dot_dir) is None

    def test_stale_pidfile_cleaned_up(self, tmp_path):
        """Pidfile with dead PID is removed."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        pid_file = dot_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 999999999, "port": 8340}))

        from stepwise.server_detect import detect_server
        result = detect_server(dot_dir)
        assert result is None
        assert not pid_file.exists()

    def test_write_and_remove_pidfile(self, tmp_path):
        """write_pidfile creates file, remove_pidfile deletes it."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        from stepwise.server_detect import write_pidfile, remove_pidfile
        path = write_pidfile(dot_dir, 8340)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["port"] == 8340
        assert "pid" in data

        remove_pidfile(dot_dir)
        assert not path.exists()

    def test_detect_server_none_project_dir(self):
        """None project_dir → None."""
        from stepwise.server_detect import detect_server
        assert detect_server(None) is None


class TestAPIClient:
    """Tests for the API client module."""

    def test_client_init(self):
        """Client stores base URL."""
        from stepwise.api_client import StepwiseClient
        client = StepwiseClient("http://localhost:8340")
        assert client.base_url == "http://localhost:8340"

    def test_client_strips_trailing_slash(self):
        """Client strips trailing slash from URL."""
        from stepwise.api_client import StepwiseClient
        client = StepwiseClient("http://localhost:8340/")
        assert client.base_url == "http://localhost:8340"

    def test_client_connection_error(self):
        """Client raises StepwiseAPIError on connection failure."""
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient("http://localhost:1")  # nothing listening
        with pytest.raises(StepwiseAPIError) as exc_info:
            client.health()
        assert exc_info.value.status == 0
        assert "Connection failed" in exc_info.value.detail


class TestServerEndpoints:
    """Tests for new server API endpoints (using test client)."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a FastAPI test client."""
        import os
        os.environ["STEPWISE_DB"] = str(tmp_path / "test.db")
        os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "templates")
        os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "jobs")
        os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
        (tmp_path / "templates").mkdir(exist_ok=True)
        (tmp_path / "jobs").mkdir(exist_ok=True)

        from fastapi.testclient import TestClient
        from stepwise.server import app
        with TestClient(app) as c:
            yield c

    def test_health_endpoint(self, client):
        """GET /api/health returns status ok."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "active_jobs" in data

    def test_suspended_endpoint_empty(self, client):
        """GET /api/jobs/suspended with no jobs returns empty list."""
        resp = client.get("/api/jobs/suspended")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["suspended_steps"] == []

    def test_status_endpoint_not_found(self, client):
        """GET /api/jobs/{id}/status returns 404 for unknown job."""
        resp = client.get("/api/jobs/job-nonexistent/status")
        assert resp.status_code == 404

    def test_output_endpoint_not_found(self, client):
        """GET /api/jobs/{id}/output returns 404 for unknown job."""
        resp = client.get("/api/jobs/job-nonexistent/output")
        assert resp.status_code == 404

    def test_create_and_status(self, client):
        """Create a job, start it, get resolved status."""
        # Create a simple job using full serialized format (what to_dict produces)
        workflow = {
            "steps": {
                "greet": {
                    "name": "greet",
                    "outputs": ["msg"],
                    "executor": {"type": "script", "config": {
                        "command": "echo '{\"msg\": \"hello\"}'",
                    }},
                }
            }
        }
        resp = client.post("/api/jobs", json={
            "objective": "test-job",
            "workflow": workflow,
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        # Start and wait for async engine to process
        client.post(f"/api/jobs/{job_id}/start")
        import time
        for _ in range(100):
            time.sleep(0.1)
            resp = client.get(f"/api/jobs/{job_id}/status")
            if resp.status_code == 200 and resp.json()["status"] in ("running", "completed"):
                break

        # Get resolved status
        resp = client.get(f"/api/jobs/{job_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] in ("running", "completed")
        assert len(data["steps"]) == 1

    def test_fulfill_returns_job_id(self, client):
        """POST /api/runs/{run_id}/fulfill returns job_id in response."""
        import time
        # Create a job with an external step
        workflow = {
            "steps": {
                "approve": {
                    "name": "approve",
                    "outputs": ["ok"],
                    "executor": {"type": "external", "config": {"prompt": "Approve?"}},
                }
            }
        }
        resp = client.post("/api/jobs", json={"objective": "test", "workflow": workflow})
        job_id = resp.json()["id"]
        client.post(f"/api/jobs/{job_id}/start")

        # Wait for async engine to suspend the external step
        suspended = []
        for _ in range(100):
            time.sleep(0.1)
            resp = client.get("/api/jobs/suspended")
            suspended = resp.json()["suspended_steps"]
            if suspended:
                break
        assert len(suspended) == 1, "External step never reached SUSPENDED status"
        run_id = suspended[0]["run_id"]

        # Fulfill
        resp = client.post(f"/api/runs/{run_id}/fulfill", json={"payload": {"ok": True}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "fulfilled"
        assert data["run_id"] == run_id
        assert data["job_id"] == job_id


# ── Phase 5b: CLI Server Routing Tests ─────────────────────────────────


class TestCLIServerRouting:
    """Tests for CLI server routing via _try_server."""

    def test_try_server_no_server(self):
        """_try_server returns (None, None) when no server available."""
        import argparse
        from stepwise.cli import _try_server

        args = argparse.Namespace(standalone=True)
        data, code = _try_server(args, lambda c: c.health())
        assert data is None
        assert code is None

    def test_try_server_connection_failure_fallback(self):
        """_try_server falls back with warning on connection failure."""
        import argparse
        from stepwise.cli import _try_server

        args = argparse.Namespace(standalone=False, server="http://localhost:1")
        captured_stderr = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_stderr
        try:
            data, code = _try_server(args, lambda c: c.health())
        finally:
            sys.stderr = old_stderr

        assert data is None
        assert code is None
        assert "unreachable" in captured_stderr.getvalue()
        assert "falling back" in captured_stderr.getvalue()

    def test_try_server_api_error(self):
        """_try_server returns error dict on API error (non-connection)."""
        import argparse
        from stepwise.cli import _try_server
        from stepwise.api_client import StepwiseAPIError

        def raise_api_error(client):
            raise StepwiseAPIError(404, "Job not found: xyz")

        args = argparse.Namespace(standalone=False, server="http://localhost:8340")
        data, code = _try_server(args, raise_api_error)
        assert code == EXIT_JOB_FAILED
        assert data["status"] == "error"
        assert "Job not found" in data["error"]

    def test_try_server_success(self):
        """_try_server returns data on success."""
        import argparse
        from unittest.mock import patch
        from stepwise.cli import _try_server

        args = argparse.Namespace(standalone=False, server="http://localhost:8340")

        # Mock the client to return data without actually connecting
        with patch("stepwise.api_client.StepwiseClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.status.return_value = {"job_id": "j1", "status": "completed"}

            data, code = _try_server(args, lambda c: c.status("j1"))
            assert code == EXIT_SUCCESS
            assert data["status"] == "completed"

    def test_standalone_flag_skips_server(self):
        """--standalone flag forces direct mode even with --server."""
        import argparse
        from stepwise.cli import _detect_server_url

        args = argparse.Namespace(standalone=True, server="http://localhost:8340")
        assert _detect_server_url(args) is None

    def test_server_flag_forces_url(self):
        """--server flag forces specific URL."""
        import argparse
        from stepwise.cli import _detect_server_url

        args = argparse.Namespace(standalone=False, server="http://myserver:9999")
        assert _detect_server_url(args) == "http://myserver:9999"


# ── Conditional Transcript Capture ────────────────────────────────────


class TestConditionalTranscriptCapture:
    """Tests that transcript capture is skipped for agent steps not in a chain."""

    def test_capture_skipped_when_no_chain(self):
        """AgentProcess.capture_transcript=False skips _capture_transcript."""
        from stepwise.agent import AgentProcess, AcpxBackend
        from unittest.mock import patch

        backend = AcpxBackend()
        process = AgentProcess(
            pid=1, pgid=1, output_path="/tmp/test.jsonl",
            working_dir="/tmp", session_name="step-test-1",
            capture_transcript=False,
        )

        with patch.object(backend, "get_session_messages") as mock_get:
            backend._capture_transcript(process)
            mock_get.assert_not_called()

    def test_capture_runs_when_in_chain(self):
        """AgentProcess.capture_transcript=True attempts capture."""
        from stepwise.agent import AgentProcess, AcpxBackend
        from unittest.mock import patch

        backend = AcpxBackend()
        process = AgentProcess(
            pid=1, pgid=1, output_path="/tmp/test.jsonl",
            working_dir="/tmp", session_name="step-test-1",
            capture_transcript=True,
        )

        with patch.object(backend, "get_session_messages", return_value=None) as mock_get:
            backend._capture_transcript(process)
            mock_get.assert_called_once()

    def test_agent_executor_sets_capture_from_chain(self):
        """AgentExecutor.start() sets capture_transcript based on context.chain."""
        from stepwise.agent import AgentExecutor, MockAgentBackend
        from stepwise.executors import ExecutionContext

        backend = MockAgentBackend()
        backend.set_auto_complete(result={"status": "done"})

        executor = AgentExecutor(backend=backend, prompt="Do stuff", output_mode="effect")

        # No chain → capture_transcript should be False
        ctx_no_chain = ExecutionContext(
            job_id="j1", step_name="test", attempt=1,
            workspace_path="/tmp", idempotency="allow_restart",
            chain=None,
        )
        result = executor.start({}, ctx_no_chain)
        assert result.executor_state["capture_transcript"] is False

        # With chain → capture_transcript should be True
        ctx_with_chain = ExecutionContext(
            job_id="j1", step_name="test", attempt=1,
            workspace_path="/tmp", idempotency="allow_restart",
            chain="my-chain",
        )
        result = executor.start({}, ctx_with_chain)
        assert result.executor_state["capture_transcript"] is True
