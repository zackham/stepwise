"""Regression tests for 1.0 RC store hardening.

Covers:
- delete_skipped_runs() commits (no stranded implicit write transaction)
- stale_jobs() PID liveness probe never raises (EPERM = alive)
- jobs table secondary indexes exist (and are recreated on reopen)
- load_events_since() is bounded by a LIMIT and supports paging
- transition_job_to_pending/approved are atomic vs concurrent cancel
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from stepwise.models import (
    Event,
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


def _make_job(store: SQLiteStore, job_id: str, status: JobStatus = JobStatus.RUNNING) -> Job:
    job = Job(
        id=job_id,
        objective="test",
        workflow=WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={}),
                outputs=["x"],
            ),
        }),
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


def _make_skipped_run(store: SQLiteStore, job_id: str, step_name: str, run_id: str) -> StepRun:
    run = StepRun(
        id=run_id,
        job_id=job_id,
        step_name=step_name,
        attempt=1,
        status=StepRunStatus.SKIPPED,
    )
    store.save_run(run)
    return run


# ── F22: delete_skipped_runs must commit ─────────────────────────────────


class TestDeleteSkippedRunsCommitsToStore:
    def test_delete_commits_and_leaves_no_open_transaction(self, store):
        _make_job(store, "j1")
        _make_skipped_run(store, "j1", "step-a", "r1")
        _make_skipped_run(store, "j1", "step-b", "r2")
        store.save_step_event("r1", "test.event", {})

        deleted = store.delete_skipped_runs("j1")
        assert deleted == 2
        # Regression: the DELETEs used to ride an open implicit write
        # transaction, stranding the WAL write lock.
        assert store._conn.in_transaction is False

    def test_delete_is_durable_across_connections(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path)
        _make_job(store, "j1")
        _make_skipped_run(store, "j1", "step-a", "r1")

        assert store.delete_skipped_runs("j1") == 1

        # A second connection must see the deletion (i.e. it was committed,
        # not left pending on the first connection).
        other = sqlite3.connect(db_path)
        try:
            count = other.execute(
                "SELECT COUNT(*) FROM step_runs WHERE job_id = 'j1'"
            ).fetchone()[0]
            assert count == 0
        finally:
            other.close()
        store.close()

    def test_delete_with_exclude_step_commits(self, store):
        _make_job(store, "j1")
        _make_skipped_run(store, "j1", "step-a", "r1")
        _make_skipped_run(store, "j1", "step-b", "r2")

        deleted = store.delete_skipped_runs("j1", exclude_step="step-b")
        assert deleted == 1
        assert store._conn.in_transaction is False
        remaining = store.runs_for_job("j1")
        assert [r.id for r in remaining] == ["r2"]

    def test_delete_noop_returns_zero(self, store):
        _make_job(store, "j1")
        assert store.delete_skipped_runs("j1") == 0


# ── F18/F23: stale_jobs PID probe must never raise ───────────────────────


class TestStaleJobsPidProbe:
    def _make_stale_cli_job(self, store, job_id="j-stale", pid=4194000):
        job = _make_job(store, job_id, status=JobStatus.RUNNING)
        job.created_by = f"cli:{pid}"
        job.runner_pid = pid
        store.save_job(job)
        return job

    def test_stale_permission_error_means_alive(self, store, monkeypatch):
        """EPERM = a process exists at that PID — treat the runner as alive."""
        job = self._make_stale_cli_job(store)

        def fake_kill(pid, sig):
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(os, "kill", fake_kill)
        stale = store.stale_jobs(max_age_seconds=60)  # must not raise
        assert not any(j.id == job.id for j in stale)

    def test_stale_unexpected_oserror_does_not_raise(self, store, monkeypatch):
        """Any other probe failure is swallowed conservatively (not stale)."""
        job = self._make_stale_cli_job(store)

        def fake_kill(pid, sig):
            raise OSError("weird kernel state")

        monkeypatch.setattr(os, "kill", fake_kill)
        stale = store.stale_jobs(max_age_seconds=60)  # must not raise
        assert not any(j.id == job.id for j in stale)

    def test_stale_dead_pid_is_detected(self, store, monkeypatch):
        job = self._make_stale_cli_job(store)

        def fake_kill(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(os, "kill", fake_kill)
        stale = store.stale_jobs(max_age_seconds=60)
        assert any(j.id == job.id for j in stale)


# ── F26: jobs table secondary indexes ────────────────────────────────────


JOBS_INDEXES = {
    "idx_jobs_status",
    "idx_jobs_created_at",
    "idx_jobs_parent",
    "idx_jobs_group",
}


def _jobs_index_names(store: SQLiteStore) -> set[str]:
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
    ).fetchall()
    return {r["name"] for r in rows}


class TestJobsTableStoreIndexes:
    def test_fresh_store_has_jobs_indexes(self, store):
        assert JOBS_INDEXES <= _jobs_index_names(store)

    def test_existing_db_gains_indexes_on_reopen(self, tmp_path):
        """Schema-init path adds the indexes to pre-existing DBs (migration)."""
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path)
        # Simulate a pre-index database
        for name in JOBS_INDEXES:
            store._conn.execute(f"DROP INDEX {name}")
        store._conn.commit()
        assert not (JOBS_INDEXES & _jobs_index_names(store))
        store.close()

        reopened = SQLiteStore(db_path)
        assert JOBS_INDEXES <= _jobs_index_names(reopened)
        reopened.close()

    def test_status_query_uses_index(self, store):
        plan = store._conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE status = 'running'"
        ).fetchall()
        plan_text = " ".join(str(tuple(r)) for r in plan)
        assert "idx_jobs_status" in plan_text


# ── F27: load_events_since is bounded ────────────────────────────────────


class TestLoadEventsSinceStoreLimit:
    def _add_events(self, store, job_id: str, n: int) -> list[int]:
        rowids = []
        for i in range(n):
            rowids.append(store.save_event(Event(
                id=f"e{i}",
                job_id=job_id,
                timestamp=_now(),
                type="step.completed",
                data={"i": i},
            )))
        return rowids

    def test_limit_bounds_result(self, store):
        _make_job(store, "j1")
        self._add_events(store, "j1", 25)
        results = store.load_events_since(since_rowid=0, limit=10)
        assert len(results) == 10

    def test_paging_with_since_rowid(self, store):
        _make_job(store, "j1")
        rowids = self._add_events(store, "j1", 25)

        seen: list[int] = []
        cursor = 0
        while True:
            page = store.load_events_since(since_rowid=cursor, limit=10)
            if not page:
                break
            seen.extend(r[0] for r in page)
            cursor = page[-1][0]
        assert seen == sorted(rowids)

    def test_limit_zero_is_unbounded(self, store):
        _make_job(store, "j1")
        self._add_events(store, "j1", 25)
        results = store.load_events_since(since_rowid=0, limit=0)
        assert len(results) == 25

    def test_default_limit_applies(self, store):
        """Default call signature stays bounded (5000)."""
        _make_job(store, "j1")
        self._add_events(store, "j1", 5)
        results = store.load_events_since(since_rowid=0)
        assert len(results) == 5  # under the bound — all returned

    def test_limit_respects_job_filter(self, store):
        _make_job(store, "j1")
        _make_job(store, "j2")
        self._add_events(store, "j1", 5)
        for i in range(5):
            store.save_event(Event(
                id=f"x{i}", job_id="j2", timestamp=_now(),
                type="step.completed", data={},
            ))
        results = store.load_events_since(since_rowid=0, job_ids={"j2"}, limit=3)
        assert len(results) == 3
        assert all(ev.job_id == "j2" for _, ev, _ in results)


# ── F28: atomic staged/approval transitions ──────────────────────────────


class TestAtomicJobTransitionsStore:
    def test_staged_to_pending(self, store):
        _make_job(store, "j1", status=JobStatus.STAGED)
        store.transition_job_to_pending("j1")
        assert store.load_job("j1").status == JobStatus.PENDING

    def test_pending_transition_rejects_wrong_status(self, store):
        _make_job(store, "j1", status=JobStatus.CANCELLED)
        with pytest.raises(ValueError, match="must be STAGED"):
            store.transition_job_to_pending("j1")

    def test_pending_transition_awaiting_approval_message(self, store):
        _make_job(store, "j1", status=JobStatus.AWAITING_APPROVAL)
        with pytest.raises(ValueError, match="requires approval"):
            store.transition_job_to_pending("j1")

    def test_approved_transition(self, store):
        _make_job(store, "j1", status=JobStatus.AWAITING_APPROVAL)
        store.transition_job_to_approved("j1")
        assert store.load_job("j1").status == JobStatus.PENDING

    def test_approved_transition_rejects_wrong_status(self, store):
        _make_job(store, "j1", status=JobStatus.STAGED)
        with pytest.raises(ValueError, match="must be awaiting_approval"):
            store.transition_job_to_approved("j1")

    def test_pending_transition_does_not_stomp_concurrent_cancel(self, store, monkeypatch):
        """TOCTOU regression: a cancel landing between the pre-check and the
        UPDATE must not be overwritten back to PENDING."""
        _make_job(store, "j1", status=JobStatus.STAGED)

        real_load = store.load_job

        def racing_load(job_id):
            job = real_load(job_id)
            if job.status == JobStatus.STAGED:
                # Simulate a concurrent cancel after the pre-check read
                store._conn.execute(
                    "UPDATE jobs SET status = ? WHERE id = ?",
                    (JobStatus.CANCELLED.value, job_id),
                )
                store._conn.commit()
            return job

        monkeypatch.setattr(store, "load_job", racing_load)
        with pytest.raises(ValueError):
            store.transition_job_to_pending("j1")
        assert real_load("j1").status == JobStatus.CANCELLED

    def test_approved_transition_does_not_stomp_concurrent_cancel(self, store, monkeypatch):
        _make_job(store, "j1", status=JobStatus.AWAITING_APPROVAL)

        real_load = store.load_job

        def racing_load(job_id):
            job = real_load(job_id)
            if job.status == JobStatus.AWAITING_APPROVAL:
                store._conn.execute(
                    "UPDATE jobs SET status = ? WHERE id = ?",
                    (JobStatus.CANCELLED.value, job_id),
                )
                store._conn.commit()
            return job

        monkeypatch.setattr(store, "load_job", racing_load)
        with pytest.raises(ValueError):
            store.transition_job_to_approved("j1")
        assert real_load("j1").status == JobStatus.CANCELLED


class TestMalformedJsonColumns:
    """A single unparseable JSON column must not take down a whole query.

    Real incident (vita, 2026-08-12): three ancient step_runs rows carried
    result = '' (empty string, not NULL) after a crash-recovery. Every
    /api/jobs?limit=500 then died with sqlite3.OperationalError "malformed
    JSON" inside batch_job_costs, and the jobs list rendered "No jobs yet" —
    one bad row anywhere in the page blanked the entire UI.
    """

    @staticmethod
    def _corrupt(store: SQLiteStore, table: str, column: str, row_id: str, value: str = "") -> None:
        store._conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, row_id))
        store._conn.commit()

    @staticmethod
    def _cost_run(store: SQLiteStore, job_id: str, run_id: str, cost: float | None) -> None:
        """Save a run, then write the result JSON straight into the column.

        StepRun.result is a HandoffEnvelope; these tests only care what the raw
        column holds, so set it directly rather than round-tripping the model.
        """
        store.save_run(StepRun(
            id=run_id,
            job_id=job_id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
        ))
        if cost is not None:
            store._conn.execute(
                "UPDATE step_runs SET result = ? WHERE id = ?",
                (json.dumps({"executor_meta": {"cost_usd": cost}}), run_id),
            )
            store._conn.commit()

    def test_batch_job_costs_survives_empty_result(self, store):
        _make_job(store, "j1")
        self._cost_run(store, "j1", "run-1", 1.25)
        self._corrupt(store, "step_runs", "result", "run-1")

        assert store.batch_job_costs(["j1"]) == {"j1": 0.0}

    def test_batch_job_costs_still_sums_healthy_rows(self, store):
        """The corrupt row is skipped; a sibling job's real cost still lands."""
        _make_job(store, "j1")
        _make_job(store, "j2")
        self._cost_run(store, "j1", "run-1", 1.25)
        self._cost_run(store, "j2", "run-2", 2.50)
        self._corrupt(store, "step_runs", "result", "run-1")

        costs = store.batch_job_costs(["j1", "j2"])
        assert costs["j1"] == 0.0
        assert costs["j2"] == pytest.approx(2.50)

    def test_batch_job_costs_survives_empty_step_event_data(self, store):
        _make_job(store, "j1")
        self._cost_run(store, "j1", "run-1", None)
        store._conn.execute(
            "INSERT INTO step_events (id, run_id, timestamp, type, data) VALUES (?, ?, ?, ?, ?)",
            ("ev-1", "run-1", _now().isoformat(), "cost", ""),
        )
        store._conn.commit()

        assert store.batch_job_costs(["j1"]) == {"j1": 0.0}

    def test_accumulated_cost_survives_empty_step_event_data(self, store):
        _make_job(store, "j1")
        self._cost_run(store, "j1", "run-1", None)
        for ev_id, data in (("ev-1", ""), ("ev-2", '{"cost_usd": 0.75}')):
            store._conn.execute(
                "INSERT INTO step_events (id, run_id, timestamp, type, data) VALUES (?, ?, ?, ?, ?)",
                (ev_id, "run-1", _now().isoformat(), "cost", data),
            )
        store._conn.commit()

        assert store.accumulated_cost("run-1") == pytest.approx(0.75)

    def test_recent_flows_survives_empty_workflow(self, store):
        _make_job(store, "j1")
        _make_job(store, "j2")
        self._corrupt(store, "jobs", "workflow", "j1")

        # j1 no longer deserializes into a Job, but the query must not raise
        # and the healthy job must still come back.
        assert "j2" in {j.id for j in store.recent_flows(limit=10)}

    def test_meta_filtered_jobs_survive_empty_metadata(self, store):
        _make_job(store, "j1")
        _make_job(store, "j2")
        store._conn.execute(
            "UPDATE jobs SET metadata = ? WHERE id = ?",
            ('{"sys": {"schedule_id": "sched-1"}}', "j2"),
        )
        store._conn.commit()
        self._corrupt(store, "jobs", "metadata", "j1")

        found = store.all_jobs(meta_filters={"sys.schedule_id": "sched-1"})
        assert [j.id for j in found] == ["j2"]

    def test_raw_json_extract_would_have_raised(self, store):
        """Guard the guard: prove the unprotected form really does blow up."""
        _make_job(store, "j1")
        self._cost_run(store, "j1", "run-1", 1.25)
        self._corrupt(store, "step_runs", "result", "run-1")

        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            store._conn.execute(
                "SELECT SUM(json_extract(result, '$.executor_meta.cost_usd')) "
                "FROM step_runs WHERE result IS NOT NULL"
            ).fetchall()
