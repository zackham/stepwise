# Data wiring verified
"""SQLite persistence: jobs, step_runs, events tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from stepwise.models import (
    Event,
    HandoffEnvelope,
    Job,
    JobConfig,
    JobStatus,
    StepRun,
    StepRunStatus,
    WatchSpec,
    WorkflowDefinition,
)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SQLiteStore:
    """SQLite-backed persistence for Stepwise."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                objective TEXT,
                workflow TEXT,
                status TEXT,
                inputs TEXT,
                parent_job_id TEXT,
                parent_step_run_id TEXT,
                workspace_path TEXT,
                config TEXT,
                created_at TEXT,
                updated_at TEXT,
                created_by TEXT DEFAULT 'server',
                runner_pid INTEGER,
                heartbeat_at TEXT
            );

            CREATE TABLE IF NOT EXISTS step_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id),
                step_name TEXT,
                attempt INTEGER,
                status TEXT,
                inputs TEXT,
                dep_run_ids TEXT,
                result TEXT,
                error TEXT,
                error_category TEXT,
                executor_state TEXT,
                watch TEXT,
                sub_job_id TEXT,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id),
                timestamp TEXT,
                type TEXT,
                data TEXT,
                is_effector INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS step_events (
                id TEXT PRIMARY KEY,
                run_id TEXT,
                timestamp TEXT,
                type TEXT,
                data TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_step_runs_job
                ON step_runs(job_id, step_name, attempt);
            CREATE INDEX IF NOT EXISTS idx_step_runs_status
                ON step_runs(job_id, status);
            CREATE INDEX IF NOT EXISTS idx_events_job
                ON events(job_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_step_events_run
                ON step_events(run_id, timestamp);

            CREATE TABLE IF NOT EXISTS job_dependencies (
                job_id TEXT NOT NULL,
                depends_on_job_id TEXT NOT NULL,
                PRIMARY KEY (job_id, depends_on_job_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (depends_on_job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_deps_depends_on
                ON job_dependencies(depends_on_job_id);
        """)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns that may not exist in older databases."""
        cursor = self._conn.execute("PRAGMA table_info(step_runs)")
        run_columns = {row[1] for row in cursor.fetchall()}
        if "error_category" not in run_columns:
            self._conn.execute("ALTER TABLE step_runs ADD COLUMN error_category TEXT")
            self._conn.commit()
        if "pid" not in run_columns:
            self._conn.execute("ALTER TABLE step_runs ADD COLUMN pid INTEGER")
            self._conn.commit()

        cursor = self._conn.execute("PRAGMA table_info(jobs)")
        job_columns = {row[1] for row in cursor.fetchall()}
        for col, typ, default in [
            ("created_by", "TEXT", "'server'"),
            ("runner_pid", "INTEGER", None),
            ("heartbeat_at", "TEXT", None),
            ("notify_url", "TEXT", None),
            ("notify_context", "TEXT", None),
            ("name", "TEXT", None),
            ("metadata", "TEXT", "'{}'"),
            ("job_group", "TEXT", None),
        ]:
            if col not in job_columns:
                default_clause = f" DEFAULT {default}" if default else ""
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}{default_clause}")
        self._conn.commit()

    # ── Jobs ──────────────────────────────────────────────────────────────

    def save_job(self, job: Job) -> None:
        self._conn.execute(
            """INSERT INTO jobs
                (id, objective, workflow, status, inputs, parent_job_id,
                 parent_step_run_id, workspace_path, config, created_at, updated_at,
                 created_by, runner_pid, heartbeat_at, notify_url, notify_context, name,
                 metadata, job_group)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                objective = excluded.objective,
                workflow = excluded.workflow,
                status = excluded.status,
                inputs = excluded.inputs,
                parent_job_id = excluded.parent_job_id,
                parent_step_run_id = excluded.parent_step_run_id,
                workspace_path = excluded.workspace_path,
                config = excluded.config,
                updated_at = excluded.updated_at,
                created_by = excluded.created_by,
                runner_pid = excluded.runner_pid,
                heartbeat_at = excluded.heartbeat_at,
                notify_url = excluded.notify_url,
                notify_context = excluded.notify_context,
                name = excluded.name,
                metadata = excluded.metadata,
                job_group = excluded.job_group
            """,
            (
                job.id,
                job.objective,
                _dumps(job.workflow.to_dict()),
                job.status.value,
                _dumps(job.inputs),
                job.parent_job_id,
                job.parent_step_run_id,
                job.workspace_path,
                _dumps(job.config.to_dict()),
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                job.created_by,
                job.runner_pid,
                job.heartbeat_at.isoformat() if job.heartbeat_at else None,
                job.notify_url,
                _dumps(job.notify_context) if job.notify_context else None,
                job.name,
                _dumps(job.metadata),
                job.job_group,
            ),
        )
        self._conn.commit()

    def load_job(self, job_id: str) -> Job:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Job not found: {job_id}")
        job = self._row_to_job(row)
        job.depends_on = self.get_job_dependencies(job_id)
        return job

    def _row_to_job(self, row: sqlite3.Row) -> "Job":
        from stepwise.models import _now
        return Job(
            id=row["id"],
            objective=row["objective"] or "",
            name=row["name"] if "name" in row.keys() else None,
            workflow=WorkflowDefinition.from_dict(json.loads(row["workflow"])) if row["workflow"] else WorkflowDefinition(),
            status=JobStatus(row["status"]) if row["status"] else JobStatus.PENDING,
            inputs=json.loads(row["inputs"]) if row["inputs"] else {},
            parent_job_id=row["parent_job_id"],
            parent_step_run_id=row["parent_step_run_id"],
            workspace_path=row["workspace_path"] or "",
            config=JobConfig.from_dict(json.loads(row["config"])) if row["config"] else JobConfig(),
            created_at=_parse_dt(row["created_at"]) if row["created_at"] else _now(),
            updated_at=_parse_dt(row["updated_at"]) if row["updated_at"] else _now(),
            created_by=row["created_by"] or "server",
            runner_pid=row["runner_pid"],
            heartbeat_at=_parse_dt(row["heartbeat_at"]) if row["heartbeat_at"] else None,
            notify_url=row["notify_url"] if "notify_url" in row.keys() else None,
            notify_context=json.loads(row["notify_context"]) if "notify_context" in row.keys() and row["notify_context"] else {},
            metadata=json.loads(row["metadata"]) if "metadata" in row.keys() and row["metadata"] else {"sys": {}, "app": {}},
            job_group=row["job_group"] if "job_group" in row.keys() else None,
        )

    def active_jobs(self) -> list[Job]:
        """Return all jobs in RUNNING status."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ?", (JobStatus.RUNNING.value,)
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def pending_jobs(self) -> list[Job]:
        """Return all jobs in PENDING status, ordered by creation time (FIFO)."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at",
            (JobStatus.PENDING.value,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        """Delete a job and all associated runs, events, and dependency edges."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM job_dependencies WHERE job_id = ? OR depends_on_job_id = ?",
                (job_id, job_id),
            )
            self._conn.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
            self._conn.execute("DELETE FROM step_runs WHERE job_id = ?", (job_id,))
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    # ── Job Dependencies ─────────────────────────────────────────────────

    def add_job_dependency(self, job_id: str, depends_on_job_id: str) -> None:
        """Add a dependency edge: job_id depends on depends_on_job_id.

        Caller must validate: both jobs exist, dependent is STAGED, no cycle.
        INSERT OR IGNORE makes this idempotent (silent no-op on duplicate).
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO job_dependencies (job_id, depends_on_job_id) VALUES (?, ?)",
                (job_id, depends_on_job_id),
            )

    def remove_job_dependency(self, job_id: str, depends_on_job_id: str) -> None:
        """Remove a dependency edge. Caller must validate dependent is STAGED."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM job_dependencies WHERE job_id = ? AND depends_on_job_id = ?",
                (job_id, depends_on_job_id),
            )

    def get_job_dependencies(self, job_id: str) -> list[str]:
        """Return list of job IDs that this job depends on."""
        rows = self._conn.execute(
            "SELECT depends_on_job_id FROM job_dependencies WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_job_dependents(self, depends_on_job_id: str) -> list[str]:
        """Return list of job IDs that depend on the given job."""
        rows = self._conn.execute(
            "SELECT job_id FROM job_dependencies WHERE depends_on_job_id = ?",
            (depends_on_job_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def would_create_cycle(self, job_id: str, depends_on_job_id: str) -> bool:
        """Check if adding edge (job_id depends_on depends_on_job_id) creates a cycle.

        When adding A->B ("A depends on B"), check: does B already transitively
        depend on A? BFS from B through existing forward edges; if we reach A,
        adding A->B would create A->B->...->A.
        """
        if job_id == depends_on_job_id:
            return True
        visited: set[str] = set()
        queue = [depends_on_job_id]
        while queue:
            current = queue.pop(0)
            if current == job_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self.get_job_dependencies(current))
        return False

    def jobs_in_group(self, group: str) -> list[Job]:
        """Return all jobs belonging to a group."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE job_group = ? ORDER BY created_at",
            (group,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def job_dependents(self, job_id: str) -> list[Job]:
        """Return Job objects that depend on the given job."""
        dep_ids = self.get_job_dependents(job_id)
        return [self.load_job(jid) for jid in dep_ids]

    def pending_jobs_with_deps_met(self) -> list[Job]:
        """Return PENDING jobs whose dependencies are all COMPLETED (or have no deps).

        Uses LEFT JOIN to handle missing dep rows defensively — if a dep's job record
        was forcefully deleted, the dep is treated as unmet (job stays PENDING).
        """
        rows = self._conn.execute(
            """SELECT j.* FROM jobs j
               WHERE j.status = ?
                 AND NOT EXISTS (
                   SELECT 1 FROM job_dependencies d
                   LEFT JOIN jobs dep ON dep.id = d.depends_on_job_id
                   WHERE d.job_id = j.id
                     AND (dep.id IS NULL OR dep.status != ?)
                 )
               ORDER BY j.created_at""",
            (JobStatus.PENDING.value, JobStatus.COMPLETED.value),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def transition_group_to_pending(self, group: str) -> list[str]:
        """Atomically transition all STAGED jobs in a group to PENDING. Returns transitioned job IDs."""
        from stepwise.models import _now
        now = _now().isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_group = ? AND status = ?",
                (JobStatus.PENDING.value, now, group, JobStatus.STAGED.value),
            )
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE job_group = ? AND status = ? AND updated_at = ?",
                (group, JobStatus.PENDING.value, now),
            ).fetchall()
        return [r[0] for r in rows]

    def transition_job_to_pending(self, job_id: str) -> None:
        """Transition a single STAGED job to PENDING. Raises ValueError if not STAGED."""
        from stepwise.models import _now
        job = self.load_job(job_id)
        if job.status == JobStatus.AWAITING_APPROVAL:
            raise ValueError(
                f"Job {job_id} requires approval first (use 'stepwise job approve {job_id}')"
            )
        if job.status != JobStatus.STAGED:
            raise ValueError(f"Cannot run job in status {job.status.value} (must be STAGED)")
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (JobStatus.PENDING.value, _now().isoformat(), job_id),
            )

    def transition_job_to_approved(self, job_id: str) -> None:
        """Approve a job: AWAITING_APPROVAL → PENDING. Raises ValueError if not AWAITING_APPROVAL."""
        from stepwise.models import _now
        job = self.load_job(job_id)
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise ValueError(
                f"Cannot approve job in status {job.status.value} (must be awaiting_approval)"
            )
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (JobStatus.PENDING.value, _now().isoformat(), job_id),
            )

    def recent_flows(self, limit: int = 5) -> list[Job]:
        """Return the most recent job for each distinct flow, ordered by recency."""
        rows = self._conn.execute("""
            WITH ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(
                            json_extract(workflow, '$.metadata.name'),
                            json_extract(workflow, '$.source_dir'),
                            objective
                        )
                        ORDER BY created_at DESC
                    ) AS rn
                FROM jobs
                WHERE parent_job_id IS NULL
            )
            SELECT * FROM ranked WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [self._row_to_job(r) for r in rows]

    def all_jobs(self, status: JobStatus | None = None, top_level_only: bool = False, limit: int = 0, meta_filters: dict[str, str] | None = None) -> list[Job]:
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if top_level_only:
            clauses.append("parent_job_id IS NULL")
        if meta_filters:
            for key, value in meta_filters.items():
                clauses.append("json_extract(metadata, ?) = ?")
                params.extend([f"$.{key}", value])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = f" LIMIT {int(limit)}" if limit > 0 else ""
        rows = self._conn.execute(
            f"SELECT * FROM jobs{where} ORDER BY created_at DESC{limit_clause}",
            params,
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    # ── Step Runs ─────────────────────────────────────────────────────────

    def save_run(self, run: StepRun) -> None:
        self._conn.execute(
            """INSERT INTO step_runs
                (id, job_id, step_name, attempt, status, inputs, dep_run_ids,
                 result, error, error_category, executor_state, watch, sub_job_id,
                 pid, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                inputs = excluded.inputs,
                dep_run_ids = excluded.dep_run_ids,
                result = excluded.result,
                error = excluded.error,
                error_category = excluded.error_category,
                executor_state = excluded.executor_state,
                watch = excluded.watch,
                sub_job_id = excluded.sub_job_id,
                pid = excluded.pid,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            (
                run.id,
                run.job_id,
                run.step_name,
                run.attempt,
                run.status.value,
                _dumps(run.inputs) if run.inputs is not None else None,
                _dumps(run.dep_run_ids) if run.dep_run_ids is not None else None,
                _dumps(run.result.to_dict()) if run.result else None,
                run.error,
                run.error_category,
                _dumps(run.executor_state) if run.executor_state is not None else None,
                _dumps(run.watch.to_dict()) if run.watch else None,
                run.sub_job_id,
                run.pid,
                run.started_at.isoformat() if run.started_at else None,
                run.completed_at.isoformat() if run.completed_at else None,
            ),
        )
        self._conn.commit()

    def load_run(self, run_id: str) -> StepRun:
        row = self._conn.execute(
            "SELECT * FROM step_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"StepRun not found: {run_id}")
        return self._row_to_run(row)

    def _row_to_run(self, row: sqlite3.Row) -> StepRun:
        result_data = json.loads(row["result"]) if row["result"] else None
        watch_data = json.loads(row["watch"]) if row["watch"] else None
        return StepRun(
            id=row["id"],
            job_id=row["job_id"],
            step_name=row["step_name"],
            attempt=row["attempt"],
            status=StepRunStatus(row["status"]) if row["status"] else StepRunStatus.RUNNING,
            inputs=json.loads(row["inputs"]) if row["inputs"] else None,
            dep_run_ids=json.loads(row["dep_run_ids"]) if row["dep_run_ids"] else None,
            result=HandoffEnvelope.from_dict(result_data) if result_data else None,
            error=row["error"],
            error_category=row["error_category"] if "error_category" in row.keys() else None,
            executor_state=json.loads(row["executor_state"]) if row["executor_state"] else None,
            watch=WatchSpec.from_dict(watch_data) if watch_data else None,
            sub_job_id=row["sub_job_id"],
            pid=row["pid"] if "pid" in row.keys() else None,
            started_at=_parse_dt(row["started_at"]) if row["started_at"] else None,
            completed_at=_parse_dt(row["completed_at"]) if row["completed_at"] else None,
        )

    def runs_for_job(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? ORDER BY attempt",
            (job_id,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def runs_for_step(self, job_id: str, step_name: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND step_name = ? ORDER BY attempt",
            (job_id, step_name),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def running_runs(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND status = ?",
            (job_id, StepRunStatus.RUNNING.value),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def all_running_runs(self) -> list[StepRun]:
        """Return ALL running step runs across ALL jobs (regardless of job status).

        Used by the periodic cleanup to protect active agent sessions even when
        the parent job has failed (e.g., one step in a parallel group failed but
        others are still executing).
        """
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE status = ?",
            (StepRunStatus.RUNNING.value,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def completed_runs(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND status = ?",
            (job_id, StepRunStatus.COMPLETED.value),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def suspended_runs(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND status = ?",
            (job_id, StepRunStatus.SUSPENDED.value),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def delegated_runs(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND status = ?",
            (job_id, StepRunStatus.DELEGATED.value),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def latest_run(self, job_id: str, step_name: str) -> StepRun | None:
        """Latest run (any status) for a step — by attempt number."""
        row = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND step_name = ? ORDER BY attempt DESC LIMIT 1",
            (job_id, step_name),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def latest_completed_run(self, job_id: str, step_name: str) -> StepRun | None:
        """Latest completed run for a step."""
        row = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ? AND step_name = ? AND status = ? ORDER BY attempt DESC LIMIT 1",
            (job_id, step_name, StepRunStatus.COMPLETED.value),
        ).fetchone()
        return self._row_to_run(row) if row else None

    def next_attempt(self, job_id: str, step_name: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(attempt) as max_attempt FROM step_runs WHERE job_id = ? AND step_name = ?",
            (job_id, step_name),
        ).fetchone()
        if row and row["max_attempt"] is not None:
            return row["max_attempt"] + 1
        return 1

    def completed_run_count(self, job_id: str, step_name: str) -> int:
        """Count of COMPLETED runs for a step (for iteration tracking)."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM step_runs WHERE job_id = ? AND step_name = ? AND status = ?",
            (job_id, step_name, StepRunStatus.COMPLETED.value),
        ).fetchone()
        return row["cnt"] if row else 0

    def run_count(self, job_id: str, step_name: str) -> int:
        """Total run count for a step (all statuses)."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM step_runs WHERE job_id = ? AND step_name = ?",
            (job_id, step_name),
        ).fetchone()
        return row["cnt"] if row else 0

    # ── Job Ownership & Heartbeat ────────────────────────────────────────

    def heartbeat(self, job_id: str) -> None:
        """Update heartbeat_at for a running job."""
        from stepwise.models import _now
        self._conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
            (_now().isoformat(), job_id),
        )
        self._conn.commit()

    def stale_jobs(self, max_age_seconds: int = 60) -> list[Job]:
        """RUNNING jobs whose owner hasn't heartbeated recently."""
        import os
        from datetime import timedelta
        from stepwise.models import _now
        cutoff = (_now() - timedelta(seconds=max_age_seconds)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = 'running' AND created_by != 'server' "
            "AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            (cutoff,),
        ).fetchall()
        result = []
        for r in rows:
            job = self._row_to_job(r)
            if job.runner_pid:
                try:
                    os.kill(job.runner_pid, 0)
                except ProcessLookupError:
                    pass  # Process dead — definitely stuck
                result.append(job)
            else:
                result.append(job)
        return result

    def running_jobs(self, exclude_owner: str | None = None) -> list[Job]:
        """Return RUNNING jobs, optionally excluding a specific owner."""
        if exclude_owner:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'running' AND created_by != ?",
                (exclude_owner,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'running'",
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    # ── Atomic Step Claiming ─────────────────────────────────────────────

    def claim_step(self, job_id: str, step_name: str) -> int | None:
        """Atomically claim a step. Returns attempt number or None if already claimed.

        Checks if a step already has an active run (running/suspended/delegated).
        If so, returns None. Otherwise, returns the next attempt number.

        For multi-process safety (standalone CLIs sharing a DB), the caller should
        hold a write lock or use BEGIN IMMEDIATE at a higher level. For single-process
        server use, ThreadSafeStore's _LockedConnection provides serialization.
        """
        row = self._conn.execute(
            "SELECT 1 FROM step_runs WHERE job_id = ? AND step_name = ? "
            "AND status IN ('running', 'suspended', 'delegated') LIMIT 1",
            (job_id, step_name),
        ).fetchone()
        if row:
            return None
        max_row = self._conn.execute(
            "SELECT MAX(attempt) FROM step_runs WHERE job_id = ? AND step_name = ?",
            (job_id, step_name),
        ).fetchone()
        return (max_row[0] or 0) + 1

    # ── Step Events (M4: fine-grained agent activity) ───────────────────

    def save_step_event(self, run_id: str, event_type: str, data: dict | None = None) -> None:
        from stepwise.models import _gen_id, _now
        self._conn.execute(
            "INSERT INTO step_events (id, run_id, timestamp, type, data) VALUES (?, ?, ?, ?, ?)",
            (_gen_id("sevt"), run_id, _now().isoformat(), event_type, _dumps(data or {})),
        )
        self._conn.commit()

    def save_step_events_batch(self, events: list[tuple[str, str, str, dict]]) -> None:
        """Batch insert step events: [(run_id, timestamp, type, data), ...]"""
        from stepwise.models import _gen_id
        self._conn.executemany(
            "INSERT INTO step_events (id, run_id, timestamp, type, data) VALUES (?, ?, ?, ?, ?)",
            [(_gen_id("sevt"), run_id, ts, evt_type, _dumps(data)) for run_id, ts, evt_type, data in events],
        )
        self._conn.commit()

    def load_step_events(self, run_id: str, since: str | None = None, limit: int = 200) -> list[dict]:
        if since:
            rows = self._conn.execute(
                "SELECT * FROM step_events WHERE run_id = ? AND timestamp > ? ORDER BY timestamp LIMIT ?",
                (run_id, since, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM step_events WHERE run_id = ? ORDER BY timestamp LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [{"id": r["id"], "run_id": r["run_id"], "timestamp": r["timestamp"],
                 "type": r["type"], "data": json.loads(r["data"]) if r["data"] else {}} for r in rows]

    def step_event_count(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM step_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    def accumulated_cost(self, run_id: str) -> float:
        """Sum cost from step_events for a run."""
        row = self._conn.execute(
            """SELECT SUM(json_extract(data, '$.cost_usd')) as total
               FROM step_events WHERE run_id = ? AND type = 'cost'""",
            (run_id,),
        ).fetchone()
        return float(row["total"]) if row and row["total"] else 0.0

    # ── Events ────────────────────────────────────────────────────────────

    def save_event(self, event: Event) -> int:
        cursor = self._conn.execute(
            """INSERT INTO events (id, job_id, timestamp, type, data, is_effector)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.job_id,
                event.timestamp.isoformat(),
                event.type,
                _dumps(event.data),
                1 if event.is_effector else 0,
            ),
        )
        rowid = cursor.lastrowid
        self._conn.commit()
        return rowid

    def load_events(self, job_id: str, since: datetime | None = None) -> list[Event]:
        if since:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id = ? AND timestamp >= ? ORDER BY timestamp",
                (job_id, since.isoformat()),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id = ? ORDER BY timestamp",
                (job_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            job_id=row["job_id"],
            timestamp=_parse_dt(row["timestamp"]),
            type=row["type"],
            data=json.loads(row["data"]) if row["data"] else {},
            is_effector=bool(row["is_effector"]),
        )

    def load_events_since(
        self,
        since_rowid: int = 0,
        job_ids: set[str] | None = None,
    ) -> list[tuple[int, Event, dict]]:
        """Load events with rowid > since_rowid.

        Returns list of (rowid, Event, job_metadata_dict) tuples ordered by rowid.
        If job_ids is provided, only events for those jobs are returned.
        The caller is responsible for building envelopes from the raw data.
        """
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            sql = f"""
                SELECT e.rowid, e.*, j.metadata AS job_metadata
                FROM events e LEFT JOIN jobs j ON e.job_id = j.id
                WHERE e.rowid > ? AND e.job_id IN ({placeholders})
                ORDER BY e.rowid
            """
            params: list = [since_rowid, *job_ids]
        else:
            sql = """
                SELECT e.rowid, e.*, j.metadata AS job_metadata
                FROM events e LEFT JOIN jobs j ON e.job_id = j.id
                WHERE e.rowid > ?
                ORDER BY e.rowid
            """
            params = [since_rowid]

        rows = self._conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            meta_raw = row["job_metadata"]
            metadata = json.loads(meta_raw) if meta_raw else {"sys": {}, "app": {}}
            event = self._row_to_event(row)
            results.append((row["rowid"], event, metadata))
        return results

    # ── Cross-Job Data Resolution ────────────────────────────────────────

    def get_job_output_field(self, job_id: str, field_path: str) -> tuple[Any, bool]:
        """Resolve a field from a completed job's outputs.

        Searches terminal steps (no downstream dependents) first, then all
        completed runs. Supports nested field access via dot-path.

        Returns (value, found).
        """
        job = self.load_job(job_id)
        completed = self.completed_runs(job_id)
        if not completed:
            return None, False

        # Identify terminal steps: steps with no downstream deps in the workflow
        all_dep_targets = set()
        for step_def in job.workflow.steps.values():
            for inp in step_def.inputs:
                if inp.source_step and inp.source_step != "$job":
                    all_dep_targets.add(inp.source_step)
        terminal_steps = {name for name in job.workflow.steps if name not in all_dep_targets}

        def _extract_field(artifact: dict, path: str) -> tuple[Any, bool]:
            """Navigate dot-path into artifact. Returns (value, found)."""
            top_key = path.split(".")[0] if "." in path else path
            value = artifact.get(top_key)
            if value is not None or top_key in artifact:
                if "." in path:
                    parts = path.split(".")
                    value = artifact
                    for part in parts:
                        if isinstance(value, dict):
                            if part not in value:
                                return None, False
                            value = value[part]
                        else:
                            return None, False
                return value, True
            return None, False

        # Search terminal steps first (latest completed_at wins)
        terminal_runs = [r for r in completed if r.step_name in terminal_steps]
        terminal_runs.sort(key=lambda r: r.completed_at or r.started_at, reverse=True)
        for run in terminal_runs:
            if run.result and run.result.artifact:
                val, found = _extract_field(run.result.artifact, field_path)
                if found:
                    return val, True

        # Fallback: scan all completed runs (latest first)
        all_runs = sorted(completed, key=lambda r: r.completed_at or r.started_at, reverse=True)
        for run in all_runs:
            if run.result and run.result.artifact:
                val, found = _extract_field(run.result.artifact, field_path)
                if found:
                    return val, True

        return None, False

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def new_connection(self) -> SQLiteStore:
        """Create a new store from the same database file (for crash recovery testing)."""
        return SQLiteStore(self._db_path)
