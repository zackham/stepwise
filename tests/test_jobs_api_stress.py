"""Stress test for /api/jobs N+1 + thread-safety fix.

Proves two things:

1. **Correctness of batch queries**: for 500 jobs with varied runs,
   `batch_completed_step_counts`, `batch_first_running_run`,
   `batch_last_terminal_run`, and `batch_job_ids_with_suspended_runs`
   return the same answers as the per-job methods they replace.

2. **Thread safety**: 32 workers hitting /api/jobs concurrently from
   FastAPI's threadpool for 500 total requests produce zero
   `sqlite3.InterfaceError` failures and fully correct responses.
   This is the case that used to produce "bad parameter or other API
   misuse" errors with the old single-connection-plus-lock scheme.

Run this after any change to:
- `ThreadSafeStore` / `_ThreadLocalConnProxy`
- `SQLiteStore.batch_*` query implementations
- `_serialize_job` / `_build_summary_lookups`
"""

from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from stepwise.models import (
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_wf(step_names: list[str]) -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        name: StepDefinition(
            name=name,
            executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
            outputs=["result"],
        )
        for name in step_names
    })


def _make_run(
    job_id: str,
    step: str,
    status: StepRunStatus,
    completed_at: datetime | None = None,
) -> StepRun:
    now = datetime.now(timezone.utc)
    return StepRun(
        id=_gen_id("run"),
        job_id=job_id,
        step_name=step,
        attempt=1,
        status=status,
        started_at=now - timedelta(seconds=30),
        completed_at=completed_at,
    )


@pytest.fixture
def stress_db(tmp_path):
    """A TestClient + ThreadSafeStore populated with 500 jobs + runs.

    Each job has 3-5 steps in varied states (completed/running/failed/
    suspended) so the batch query shapes get full coverage.
    """
    db_path = tmp_path / "stress.db"
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = str(db_path)
    (tmp_path / ".stepwise").mkdir(exist_ok=True)

    from stepwise.server import app
    from stepwise import server as srv

    with TestClient(app) as client:
        store = srv._get_engine().store

        # 500 jobs, varying shapes:
        #   - 200 completed (3 completed runs each + 1 last terminal)
        #   - 100 running (2 completed + 1 running)
        #   - 100 failed (2 completed + 1 failed)
        #   - 50 with suspended runs
        #   - 50 paused
        now = datetime.now(timezone.utc)
        jobs: list[Job] = []
        step_runs: list[StepRun] = []

        def add(n: int, status: JobStatus, run_shaper):
            for i in range(n):
                job_id = f"job-stress-{len(jobs):04d}"
                wf = _make_wf([f"s{j}" for j in range(4)])
                job = Job(
                    id=job_id, objective="stress", workflow=wf,
                    status=status, inputs={},
                )
                jobs.append(job)
                step_runs.extend(run_shaper(job_id, now))

        def completed_shape(jid, _now):
            # 3 distinct completed steps, different completed_at times
            return [
                _make_run(jid, f"s{i}", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=20 - i))
                for i in range(3)
            ]

        def running_shape(jid, _now):
            return [
                _make_run(jid, "s0", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=20)),
                _make_run(jid, "s1", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=15)),
                _make_run(jid, "s2", StepRunStatus.RUNNING),
            ]

        def failed_shape(jid, _now):
            return [
                _make_run(jid, "s0", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=20)),
                _make_run(jid, "s1", StepRunStatus.FAILED,
                          completed_at=_now - timedelta(seconds=10)),
            ]

        def suspended_shape(jid, _now):
            return [
                _make_run(jid, "s0", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=20)),
                _make_run(jid, "s1", StepRunStatus.SUSPENDED),
            ]

        def paused_shape(jid, _now):
            return [
                _make_run(jid, "s0", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=20)),
                _make_run(jid, "s1", StepRunStatus.COMPLETED,
                          completed_at=_now - timedelta(seconds=10)),
            ]

        add(200, JobStatus.COMPLETED, completed_shape)
        add(100, JobStatus.RUNNING, running_shape)
        add(100, JobStatus.FAILED, failed_shape)
        add(50,  JobStatus.RUNNING, suspended_shape)  # running jobs with a suspended step
        add(50,  JobStatus.PAUSED, paused_shape)

        for j in jobs:
            store.save_job(j)
        for r in step_runs:
            store.save_run(r)

        yield {
            "client": client,
            "store": store,
            "job_ids": [j.id for j in jobs],
        }


# ── Correctness: batch == loop ────────────────────────────────────────


class TestBatchQueriesMatchPerJobQueries:
    """The batch_* methods must return the same data as the per-job
    methods they replaced. Otherwise we shipped an N+1 fix that
    silently drifts job list output."""

    def test_batch_completed_step_counts_matches_loop(self, stress_db):
        store = stress_db["store"]
        ids = stress_db["job_ids"]

        batch = store.batch_completed_step_counts(ids)
        per_job = {jid: store.completed_step_count(jid) for jid in ids}

        assert batch == per_job

    def test_batch_suspended_ids_matches_loop(self, stress_db):
        store = stress_db["store"]
        ids = stress_db["job_ids"]

        batch = store.batch_job_ids_with_suspended_runs(ids)
        per_job = {jid for jid in ids if store.suspended_runs(jid)}

        assert batch == per_job

    def test_batch_first_running_run_matches_loop(self, stress_db):
        store = stress_db["store"]
        ids = stress_db["job_ids"]

        batch = store.batch_first_running_run(ids)
        per_job = {}
        for jid in ids:
            running = store.running_runs(jid)
            if running:
                per_job[jid] = running[0]

        assert set(batch.keys()) == set(per_job.keys())
        for jid, run in batch.items():
            assert run.step_name == per_job[jid].step_name
            assert run.status == per_job[jid].status

    def test_batch_last_terminal_run_matches_loop(self, stress_db):
        store = stress_db["store"]
        ids = stress_db["job_ids"]

        batch = store.batch_last_terminal_run(ids)
        per_job = {}
        for jid in ids:
            runs = store.runs_for_job(jid)
            terminal = [r for r in runs if r.completed_at]
            if terminal:
                per_job[jid] = max(terminal, key=lambda r: r.completed_at)

        assert set(batch.keys()) == set(per_job.keys())
        for jid, run in batch.items():
            assert run.step_name == per_job[jid].step_name
            assert run.completed_at == per_job[jid].completed_at


# ── Performance: one call should be fast ──────────────────────────────


class TestListJobsPerformance:
    def test_list_jobs_completes_under_one_second(self, stress_db):
        """Before the fix: 2258 jobs → 10+ seconds due to N+1. After
        the fix: the batch lookups should keep even a 500-job list
        under a second on any reasonable hardware."""
        import time
        client = stress_db["client"]

        start = time.perf_counter()
        r = client.get("/api/jobs?limit=500")
        elapsed = time.perf_counter() - start

        assert r.status_code == 200
        jobs = r.json()
        assert len(jobs) >= 500
        assert elapsed < 1.0, f"/api/jobs took {elapsed:.2f}s — N+1 likely regressed"


# ── Thread safety: concurrent access must not raise ──────────────────


class TestConcurrentJobsListing:
    """Hammers /api/jobs from many threads at once. The pre-fix
    `ThreadSafeStore` used a single sqlite3.Connection behind a
    per-call lock, and cursors from thread A would collide mid-fetch
    with thread B issuing a new query, producing
    `sqlite3.InterfaceError: bad parameter or other API misuse`.

    The thread-local connection proxy eliminates the shared cursor
    state, so this test should see zero failures even under heavy
    concurrent load.
    """

    def test_32_workers_500_requests_zero_failures(self, stress_db):
        client = stress_db["client"]
        errors: list[str] = []
        errors_lock = threading.Lock()

        def worker(i: int):
            try:
                r = client.get("/api/jobs?limit=500")
                if r.status_code != 200:
                    with errors_lock:
                        errors.append(
                            f"req {i}: HTTP {r.status_code} {r.text[:200]}"
                        )
                    return
                jobs = r.json()
                if len(jobs) < 500:
                    with errors_lock:
                        errors.append(f"req {i}: got {len(jobs)} jobs")
                    return
                # Sanity-check a few per-job fields to make sure the
                # batch lookups are actually populating correctly
                # under load, not just returning zeros.
                completed_counts = [
                    j.get("completed_steps", 0) for j in jobs
                ]
                if not any(c > 0 for c in completed_counts):
                    with errors_lock:
                        errors.append(
                            f"req {i}: all completed_steps == 0 (batch broken)",
                        )
            except Exception as e:
                with errors_lock:
                    errors.append(f"req {i}: exception {type(e).__name__}: {e}")

        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = [pool.submit(worker, i) for i in range(500)]
            for f in as_completed(futures):
                f.result()

        assert not errors, (
            f"{len(errors)} failures out of 500 concurrent requests. "
            f"First 5: {errors[:5]}"
        )

    def test_concurrent_store_reads_via_batch_methods(self, stress_db):
        """Lower-level stress: 16 threads call the batch_* methods
        directly on ThreadSafeStore (bypassing HTTP). If the
        thread-local proxy is working correctly, zero errors and all
        results consistent with the single-threaded baseline."""
        store = stress_db["store"]
        ids = stress_db["job_ids"]

        # Baseline single-threaded answers
        baseline_counts = store.batch_completed_step_counts(ids)
        baseline_suspended = store.batch_job_ids_with_suspended_runs(ids)
        baseline_running = {
            jid: (r.step_name, r.status)
            for jid, r in store.batch_first_running_run(ids).items()
        }

        errors: list[str] = []
        errors_lock = threading.Lock()

        def worker(i: int):
            try:
                counts = store.batch_completed_step_counts(ids)
                if counts != baseline_counts:
                    with errors_lock:
                        errors.append(f"{i}: counts drift")
                suspended = store.batch_job_ids_with_suspended_runs(ids)
                if suspended != baseline_suspended:
                    with errors_lock:
                        errors.append(f"{i}: suspended drift")
                running = {
                    jid: (r.step_name, r.status)
                    for jid, r in store.batch_first_running_run(ids).items()
                }
                if running != baseline_running:
                    with errors_lock:
                        errors.append(f"{i}: running drift")
            except Exception as e:
                with errors_lock:
                    errors.append(f"{i}: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker, i) for i in range(200)]
            for f in as_completed(futures):
                f.result()

        assert not errors, (
            f"{len(errors)} concurrent-read failures. First 5: {errors[:5]}"
        )
