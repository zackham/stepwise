"""Tests for runner process lifecycle management (pause/cancel kill + zombie reaping)."""

import asyncio
import os
import signal
import subprocess
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.process_lifecycle import (
    _is_pid_alive,
    _kill_pid,
    _kill_process_group,
    kill_run_process,
    kill_job_processes,
    reap_dead_processes,
    reap_expired_processes,
    run_health_check,
    DEFAULT_AGENT_TTL_SECONDS,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ──────────────────────────────────────────────────────────


def _make_store():
    return SQLiteStore(":memory:")


def _make_simple_workflow():
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "slow"}),
            outputs=["result"],
        ),
    })


def _make_run(
    job_id: str,
    step_name: str = "step-a",
    pid: int | None = None,
    pgid: int | None = None,
    started_at: datetime | None = None,
    status: StepRunStatus = StepRunStatus.RUNNING,
    in_vm: bool = False,
) -> StepRun:
    """Create a StepRun with given PID/PGID for testing."""
    executor_state = {}
    if pid:
        executor_state["pid"] = pid
    if pgid:
        executor_state["pgid"] = pgid
    if in_vm:
        executor_state["in_vm"] = True

    return StepRun(
        id=_gen_id("run"),
        job_id=job_id,
        step_name=step_name,
        attempt=1,
        status=status,
        pid=pid,
        executor_state=executor_state if executor_state else None,
        started_at=started_at or _now(),
    )


def _spawn_sleeper() -> subprocess.Popen:
    """Spawn a subprocess that sleeps forever (for testing kill)."""
    proc = subprocess.Popen(
        ["sleep", "3600"],
        start_new_session=True,
    )
    return proc


# ── Unit tests: kill functions ──────────────────────────────────────


class TestKillRunProcess:
    def test_no_pid_no_pgid(self):
        """Returns False and warns when called with no PID/PGID."""
        result = kill_run_process(pid=None, pgid=None, run_id="test", step_name="s")
        assert result is False

    def test_kill_already_dead_pid(self):
        """Returns True when PID doesn't exist."""
        # Use a very high PID that's almost certainly not alive
        result = kill_run_process(pid=99999999, pgid=None, run_id="test", step_name="s")
        assert result is True

    def test_kill_live_process_sigterm(self):
        """SIGTERM kills a live process (no grace = SIGTERM only)."""
        proc = _spawn_sleeper()
        try:
            assert _is_pid_alive(proc.pid)
            result = kill_run_process(
                pid=proc.pid,
                pgid=proc.pid,  # sleep runs in its own session
                grace_seconds=0,
                run_id="test",
                step_name="s",
            )
            # Process should be dead or dying — use proc.wait() to reap
            # the zombie (since we're the parent in tests)
            proc.wait(timeout=2)
            assert proc.returncode is not None
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass

    def test_kill_live_process_with_grace(self):
        """SIGTERM → grace → SIGKILL sequence for stubborn processes."""
        proc = _spawn_sleeper()
        try:
            assert _is_pid_alive(proc.pid)
            result = kill_run_process(
                pid=proc.pid,
                pgid=proc.pid,
                grace_seconds=1,  # short grace for test
                run_id="test",
                step_name="s",
            )
            assert result is True
            assert not _is_pid_alive(proc.pid)
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass

    def test_kill_process_group(self):
        """Kills entire process group via PGID."""
        proc = _spawn_sleeper()
        pgid = os.getpgid(proc.pid)
        try:
            result = kill_run_process(
                pid=None,
                pgid=pgid,
                grace_seconds=1,
                run_id="test",
                step_name="s",
            )
            assert result is True
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass


class TestKillJobProcesses:
    def test_empty_runs(self):
        """No-op with empty list."""
        result = kill_job_processes([], grace_seconds=0)
        assert result == []

    def test_kills_live_processes(self):
        """Kills processes for all runs with PIDs."""
        proc1 = _spawn_sleeper()
        proc2 = _spawn_sleeper()
        try:
            run1 = _make_run("job-1", pid=proc1.pid, pgid=os.getpgid(proc1.pid))
            run2 = _make_run("job-1", step_name="step-b", pid=proc2.pid, pgid=os.getpgid(proc2.pid))

            killed = kill_job_processes([run1, run2], grace_seconds=1)
            assert len(killed) == 2
            assert run1.id in killed
            assert run2.id in killed

            time.sleep(0.3)
            assert not _is_pid_alive(proc1.pid)
            assert not _is_pid_alive(proc2.pid)
        finally:
            for p in [proc1, proc2]:
                try:
                    p.kill()
                    p.wait()
                except ProcessLookupError:
                    pass

    def test_skips_runs_without_pid(self):
        """Runs without PID are silently skipped."""
        run = _make_run("job-1", pid=None, pgid=None)
        result = kill_job_processes([run], grace_seconds=0)
        assert result == []

    def test_already_dead_processes(self):
        """Dead processes are reported as killed (cleaned up)."""
        run = _make_run("job-1", pid=99999999)
        result = kill_job_processes([run], grace_seconds=0)
        assert run.id in result


# ── Unit tests: reap functions ──────────────────────────────────────


class TestReapDeadProcesses:
    def test_detects_dead_pid(self):
        """Fails runs whose PID is no longer alive."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        run = _make_run(job.id, pid=99999999)
        store.save_run(run)

        cleaned = reap_dead_processes(store, engine=None)
        assert run.id in cleaned

        # Run should be FAILED now
        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.FAILED
        assert "no longer alive" in updated.error
        assert updated.pid is None

    def test_ignores_alive_pid(self):
        """Does not touch runs with alive PIDs."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        # Use our own PID — guaranteed alive
        run = _make_run(job.id, pid=os.getpid())
        store.save_run(run)

        cleaned = reap_dead_processes(store, engine=None)
        assert cleaned == []

        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.RUNNING

    def test_ignores_runs_without_pid(self):
        """Runs without PID are skipped."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        run = _make_run(job.id, pid=None)
        store.save_run(run)

        cleaned = reap_dead_processes(store, engine=None)
        assert cleaned == []

    def test_skips_in_vm_runs_with_unreachable_pid(self):
        """Runs flagged in_vm=True keep RUNNING even with a pid the host
        can't see — the pid is a guest pid, not reachable via os.kill."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        # Guest pids start low (typically 1, 2, 3...) — a small number is
        # unlikely to match any host process and will look "dead" to
        # _is_pid_alive. Without the in_vm skip, this would get reaped.
        run = _make_run(job.id, pid=640, in_vm=True)
        store.save_run(run)

        cleaned = reap_dead_processes(store, engine=None)
        assert cleaned == []

        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.RUNNING
        assert updated.pid == 640  # pid preserved


class TestReapExpiredProcesses:
    def test_kills_expired_run(self):
        """Kills and fails runs that exceed TTL."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        # Create a run that started 3 hours ago with a dead PID
        old_start = datetime.now(timezone.utc) - timedelta(hours=3)
        run = _make_run(job.id, pid=99999999, started_at=old_start)
        store.save_run(run)

        killed = reap_expired_processes(store, ttl_seconds=7200)
        assert run.id in killed

        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.FAILED
        assert "expired" in updated.error

    def test_disabled_when_ttl_zero(self):
        """TTL=0 disables expiration — no runs killed regardless of age."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        old_start = datetime.now(timezone.utc) - timedelta(hours=100)
        run = _make_run(job.id, pid=99999999, started_at=old_start)
        store.save_run(run)

        killed = reap_expired_processes(store, ttl_seconds=0)
        assert killed == []

        # Run should still be RUNNING
        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.RUNNING

    def test_spares_young_run(self):
        """Does not touch runs within TTL."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        # Use our own PID so it's alive, and a recent start time
        run = _make_run(job.id, pid=os.getpid(), started_at=_now())
        store.save_run(run)

        killed = reap_expired_processes(store, ttl_seconds=7200)
        assert killed == []

    def test_skips_in_vm_runs_past_ttl(self):
        """TTL reaper leaves in_vm=True runs alone even past TTL — killing
        a containment-VM guest pid requires vmmd, not os.kill."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        old_start = datetime.now(timezone.utc) - timedelta(hours=3)
        run = _make_run(job.id, pid=640, started_at=old_start, in_vm=True)
        store.save_run(run)

        killed = reap_expired_processes(store, ttl_seconds=7200)
        assert killed == []

        updated = store.load_run(run.id)
        assert updated.status == StepRunStatus.RUNNING

    def test_kills_live_expired_process(self):
        """Actually kills a live process that has exceeded TTL."""
        proc = _spawn_sleeper()
        try:
            store = _make_store()
            job = Job(
                id=_gen_id("job"),
                objective="test",
                workflow=_make_simple_workflow(),
                status=JobStatus.RUNNING,
                inputs={},
            )
            store.save_job(job)

            old_start = datetime.now(timezone.utc) - timedelta(hours=3)
            run = _make_run(
                job.id,
                pid=proc.pid,
                pgid=os.getpgid(proc.pid),
                started_at=old_start,
            )
            store.save_run(run)

            killed = reap_expired_processes(store, ttl_seconds=7200)
            assert run.id in killed

            time.sleep(0.5)
            assert not _is_pid_alive(proc.pid)
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass


class TestRunHealthCheck:
    def test_combined_check(self):
        """run_health_check combines dead + expired reaping."""
        store = _make_store()
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_make_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
        )
        store.save_job(job)

        # Dead PID
        run_dead = _make_run(job.id, step_name="dead-step", pid=99999999)
        store.save_run(run_dead)

        # Expired PID (also dead)
        old_start = datetime.now(timezone.utc) - timedelta(hours=3)
        run_expired = _make_run(
            job.id,
            step_name="expired-step",
            pid=99999998,
            started_at=old_start,
        )
        store.save_run(run_expired)

        result = run_health_check(store, ttl_seconds=7200)

        # dead_cleaned catches the dead PID first, so expired may already be
        # handled by dead reaper (since 99999998 is also dead)
        total = len(result.dead_cleaned) + len(result.expired_killed)
        assert total >= 1  # At least the dead one
        assert result.errors == []


# ── Integration: cancel_job kills processes ─────────────────────────


class TestCancelJobKillsProcesses:
    def test_cancel_kills_runner_subprocess(self, store, registry):
        """cancel_job kills the actual subprocess for running steps."""
        proc = _spawn_sleeper()
        try:
            engine = AsyncEngine(store=store, registry=registry)

            # Create a job with a slow step
            register_step_fn("slow", lambda inputs: (time.sleep(3600), {"result": "done"})[1])
            wf = _make_simple_workflow()
            job = engine.create_job(objective="test", workflow=wf, inputs={})
            job.status = JobStatus.RUNNING
            store.save_job(job)

            # Create a running step run with the subprocess PID
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="step-a",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=proc.pid,
                executor_state={"pid": proc.pid, "pgid": os.getpgid(proc.pid)},
                started_at=_now(),
            )
            store.save_run(run)

            assert _is_pid_alive(proc.pid)

            # Cancel the job
            engine.cancel_job(job.id)

            # Process should be dead — reap zombie since we're the parent
            proc.wait(timeout=10)
            assert proc.returncode is not None

            # Run should be CANCELLED
            updated = store.load_run(run.id)
            assert updated.status == StepRunStatus.CANCELLED
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass


class TestPauseJobKillsProcesses:
    def test_pause_kills_runner_subprocess(self, store, registry):
        """pause_job kills the actual subprocess for running steps."""
        proc = _spawn_sleeper()
        try:
            engine = AsyncEngine(store=store, registry=registry)

            register_step_fn("slow", lambda inputs: (time.sleep(3600), {"result": "done"})[1])
            wf = _make_simple_workflow()
            job = engine.create_job(objective="test", workflow=wf, inputs={})
            job.status = JobStatus.RUNNING
            store.save_job(job)

            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name="step-a",
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=proc.pid,
                executor_state={"pid": proc.pid, "pgid": os.getpgid(proc.pid)},
                started_at=_now(),
            )
            store.save_run(run)

            assert _is_pid_alive(proc.pid)

            engine.pause_job(job.id)

            # Process should be dead — reap zombie since we're the parent
            proc.wait(timeout=5)
            assert proc.returncode is not None

            # Run should be SUSPENDED (pause suspends, not cancels)
            updated = store.load_run(run.id)
            assert updated.status == StepRunStatus.SUSPENDED
        finally:
            try:
                proc.kill()
                proc.wait()
            except ProcessLookupError:
                pass
