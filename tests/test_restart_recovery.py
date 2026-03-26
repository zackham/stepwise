"""Tests for server restart recovery of script steps.

Covers: PID stored in DB, stdout written to file, recovery when pipe is lost.
Ref: GitHub issue #4.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    ScriptExecutor,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import run_job_sync


# ── ScriptExecutor stores PID and writes stdout to file ──────────────


class TestScriptExecutorPidAndStdoutFile:
    """ScriptExecutor must store the subprocess PID and write stdout to a file."""

    def test_script_executor_writes_stdout_file(self):
        """stdout should be written to .stepwise/step-io/{step}-{attempt}.stdout"""
        with tempfile.TemporaryDirectory() as workspace:
            executor = ScriptExecutor(command='echo \'{"result": "hello"}\'')
            ctx = ExecutionContext(
                job_id="j1",
                step_name="my-step",
                attempt=1,
                workspace_path=workspace,
                idempotency="none",
            )
            captured_state = {}
            ctx.state_update_fn = lambda state: captured_state.update(state)

            result = executor.start({}, ctx)

            # Stdout file should exist with the process output
            stdout_path = Path(workspace) / ".stepwise" / "step-io" / "my-step-1.stdout"
            assert stdout_path.exists(), "stdout file must be written"
            assert '{"result": "hello"}' in stdout_path.read_text()

            # Exit code file should exist
            exitcode_path = Path(workspace) / ".stepwise" / "step-io" / "my-step-1.exitcode"
            assert exitcode_path.exists(), "exitcode file must be written"
            assert exitcode_path.read_text().strip() == "0"

            # PID should have been reported via state_update_fn
            assert "pid" in captured_state, "PID must be stored via state_update_fn"
            assert isinstance(captured_state["pid"], int)

            # Result should still work as before
            assert result.type == "data"
            assert result.envelope.artifact["result"] == "hello"

    def test_script_executor_writes_stderr_file(self):
        """stderr should be written to .stepwise/step-io/{step}-{attempt}.stderr"""
        with tempfile.TemporaryDirectory() as workspace:
            executor = ScriptExecutor(command='echo "oops" >&2; echo \'{"ok": true}\'')
            ctx = ExecutionContext(
                job_id="j1",
                step_name="err-step",
                attempt=2,
                workspace_path=workspace,
                idempotency="none",
            )
            ctx.state_update_fn = lambda state: None

            result = executor.start({}, ctx)

            stderr_path = Path(workspace) / ".stepwise" / "step-io" / "err-step-2.stderr"
            assert stderr_path.exists()
            assert "oops" in stderr_path.read_text()

    def test_script_executor_nonzero_exit_writes_files(self):
        """Even failed commands should write stdout/stderr/exitcode files."""
        with tempfile.TemporaryDirectory() as workspace:
            executor = ScriptExecutor(command='echo "partial output"; exit 1')
            ctx = ExecutionContext(
                job_id="j1",
                step_name="fail-step",
                attempt=1,
                workspace_path=workspace,
                idempotency="none",
            )
            ctx.state_update_fn = lambda state: None

            result = executor.start({}, ctx)

            stdout_path = Path(workspace) / ".stepwise" / "step-io" / "fail-step-1.stdout"
            exitcode_path = Path(workspace) / ".stepwise" / "step-io" / "fail-step-1.exitcode"
            assert stdout_path.exists()
            assert "partial output" in stdout_path.read_text()
            assert exitcode_path.exists()
            assert exitcode_path.read_text().strip() == "1"

            # Should still report failure
            assert result.executor_state and result.executor_state.get("failed")


# ── Restart recovery: pipe loss + file-based recovery ────────────────


class TestRestartRecovery:
    """Simulate pipe loss on restart and recovery via stdout file."""

    def _make_engine_and_job(self, workspace: str):
        """Create an engine, registry, and a job with a script step."""
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("script", lambda cfg: ScriptExecutor(
            command=cfg.get("command", "echo '{}'"),
        ))
        engine = AsyncEngine(store=store, registry=registry)

        wf = WorkflowDefinition(steps={
            "compute": StepDefinition(
                name="compute",
                executor=ExecutorRef(type="script", config={"command": "echo '{\"answer\": 42}'"}),
                outputs=["answer"],
            ),
        })
        job = engine.create_job(
            objective="test recovery",
            workflow=wf,
            inputs={},
            workspace_path=workspace,
        )
        return engine, store, job

    def test_recover_dead_script_with_stdout_file(self):
        """If PID is dead and stdout file exists, recover the step on restart."""
        with tempfile.TemporaryDirectory() as workspace:
            engine, store, job = self._make_engine_and_job(workspace)

            # Manually transition job to RUNNING
            job.status = JobStatus.RUNNING
            job.created_by = "server"
            job.updated_at = _now()
            store.save_job(job)

            # Create a RUNNING step run as if the executor was mid-flight
            dead_pid = 2_000_000_000  # PID that definitely doesn't exist
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="compute",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=dead_pid,
                started_at=_now(),
            )
            store.save_run(run)

            # Write the stdout file as if the subprocess completed
            step_io_dir = Path(workspace) / ".stepwise" / "step-io"
            step_io_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = step_io_dir / "compute-1.stdout"
            stdout_path.write_text('{"answer": 42}\n')
            exitcode_path = step_io_dir / "compute-1.exitcode"
            exitcode_path.write_text("0")

            # Now simulate server restart — call recover_jobs
            engine.recover_jobs()

            # The step should be COMPLETED with the recovered output
            recovered_run = store.load_run(run.id)
            assert recovered_run.status == StepRunStatus.COMPLETED, \
                f"Expected COMPLETED, got {recovered_run.status}"
            assert recovered_run.result is not None
            assert recovered_run.result.artifact["answer"] == 42
            assert recovered_run.pid is None  # PID cleared on completion

            # Job should be COMPLETED too (single step, no more work)
            recovered_job = store.load_job(job.id)
            assert recovered_job.status == JobStatus.COMPLETED

    def test_recover_dead_script_no_stdout_file_fails(self):
        """If PID is dead and no stdout file exists, fail the step."""
        with tempfile.TemporaryDirectory() as workspace:
            engine, store, job = self._make_engine_and_job(workspace)

            job.status = JobStatus.RUNNING
            job.created_by = "server"
            job.updated_at = _now()
            store.save_job(job)

            dead_pid = 2_000_000_000
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="compute",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=dead_pid,
                started_at=_now(),
            )
            store.save_run(run)

            # No stdout file written — pipe was lost with no recovery

            engine.recover_jobs()

            recovered_run = store.load_run(run.id)
            assert recovered_run.status == StepRunStatus.FAILED
            assert "no stdout file" in recovered_run.error.lower() or "pipe" in recovered_run.error.lower()

    def test_recover_dead_script_nonzero_exit(self):
        """If PID is dead and exitcode file shows failure, fail the step."""
        with tempfile.TemporaryDirectory() as workspace:
            engine, store, job = self._make_engine_and_job(workspace)

            job.status = JobStatus.RUNNING
            job.created_by = "server"
            job.updated_at = _now()
            store.save_job(job)

            dead_pid = 2_000_000_000
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="compute",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=dead_pid,
                started_at=_now(),
            )
            store.save_run(run)

            step_io_dir = Path(workspace) / ".stepwise" / "step-io"
            step_io_dir.mkdir(parents=True, exist_ok=True)
            (step_io_dir / "compute-1.stdout").write_text("error output\n")
            (step_io_dir / "compute-1.exitcode").write_text("1")
            (step_io_dir / "compute-1.stderr").write_text("something went wrong")

            engine.recover_jobs()

            recovered_run = store.load_run(run.id)
            assert recovered_run.status == StepRunStatus.FAILED

    def test_recover_skips_live_pid(self):
        """If PID is still alive, don't recover — it's still running."""
        with tempfile.TemporaryDirectory() as workspace:
            engine, store, job = self._make_engine_and_job(workspace)

            job.status = JobStatus.RUNNING
            job.created_by = "server"
            job.updated_at = _now()
            store.save_job(job)

            # Use our own PID (definitely alive)
            live_pid = os.getpid()
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="compute",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=live_pid,
                started_at=_now(),
            )
            store.save_run(run)

            engine.recover_jobs()

            # Should still be RUNNING — not touched
            recovered_run = store.load_run(run.id)
            assert recovered_run.status == StepRunStatus.RUNNING

    def test_recover_no_pid_no_recovery(self):
        """RUNNING script step with no PID can't be recovered."""
        with tempfile.TemporaryDirectory() as workspace:
            engine, store, job = self._make_engine_and_job(workspace)

            job.status = JobStatus.RUNNING
            job.created_by = "server"
            job.updated_at = _now()
            store.save_job(job)

            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="compute",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=None,  # No PID stored (legacy run)
                started_at=_now(),
            )
            store.save_run(run)

            engine.recover_jobs()

            recovered_run = store.load_run(run.id)
            assert recovered_run.status == StepRunStatus.FAILED

    def test_end_to_end_script_step_stores_pid(self):
        """Full run of a script step stores PID in DB during execution."""
        with tempfile.TemporaryDirectory() as workspace:
            store = SQLiteStore(":memory:")
            registry = ExecutorRegistry()
            registry.register("script", lambda cfg: ScriptExecutor(
                command=cfg.get("command", "echo '{}'"),
            ))
            engine = AsyncEngine(store=store, registry=registry)

            wf = WorkflowDefinition(steps={
                "greet": StepDefinition(
                    name="greet",
                    executor=ExecutorRef(
                        type="script",
                        config={"command": "echo '{\"msg\": \"hi\"}'"},
                    ),
                    outputs=["msg"],
                ),
            })
            job = engine.create_job(
                objective="e2e pid test",
                workflow=wf,
                inputs={},
                workspace_path=workspace,
            )
            result = run_job_sync(engine, job.id)
            assert result.status == JobStatus.COMPLETED

            # Verify stdout file was written
            stdout_path = Path(workspace) / ".stepwise" / "step-io" / "greet-1.stdout"
            assert stdout_path.exists()

            # Verify exitcode file was written
            exitcode_path = Path(workspace) / ".stepwise" / "step-io" / "greet-1.exitcode"
            assert exitcode_path.exists()
            assert exitcode_path.read_text().strip() == "0"
