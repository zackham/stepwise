"""SQLite persistence: jobs, step_runs, events tables."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles non-serializable values (e.g. callables)."""

    def default(self, o: Any) -> Any:
        if callable(o):
            return f"<callable:{getattr(o, '__name__', 'lambda')}>"
        try:
            return super().default(o)
        except TypeError:
            return f"<unserializable:{type(o).__name__}>"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, cls=_SafeEncoder)

from stepwise.events import Event, EventType
from stepwise.models import (
    Job,
    JobStatus,
    StepRun,
    StepStatus,
    WorkflowDefinition,
)


class StepwiseStore:
    """SQLite-backed persistence for Stepwise jobs, step runs, and events."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                workflow_json TEXT NOT NULL,
                status TEXT NOT NULL,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                outputs_json TEXT,
                parent_job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS step_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                step_name TEXT NOT NULL,
                status TEXT NOT NULL,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                outputs_json TEXT,
                error TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                started_at TEXT,
                completed_at TEXT,
                iteration_index INTEGER,
                iteration_value_json TEXT,
                input_hash TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                step_name TEXT,
                data_json TEXT NOT NULL DEFAULT '{}',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_step_runs_job
                ON step_runs(job_id);
            CREATE INDEX IF NOT EXISTS idx_events_job
                ON events(job_id);
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(job_id, event_type);
            """
        )
        self._conn.commit()

    # ── Jobs ──────────────────────────────────────────────────────────────

    def save_job(self, job: Job) -> None:
        self._conn.execute(
            """
            INSERT INTO jobs
                (id, workflow_json, status, inputs_json, outputs_json,
                 parent_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workflow_json = excluded.workflow_json,
                status = excluded.status,
                inputs_json = excluded.inputs_json,
                outputs_json = excluded.outputs_json,
                parent_job_id = excluded.parent_job_id,
                updated_at = excluded.updated_at
            """,
            (
                job.id,
                _dumps(job.workflow.to_dict()),
                job.status.value,
                _dumps(job.inputs),
                _dumps(job.outputs) if job.outputs is not None else None,
                job.parent_job_id,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        # Save all step runs in the same transaction
        for sr in job.step_runs.values():
            self._save_step_run_internal(sr)
        self._conn.commit()

    def load_job(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            return None

        workflow = WorkflowDefinition.from_dict(json.loads(row["workflow_json"]))
        job = Job(
            id=row["id"],
            workflow=workflow,
            status=JobStatus(row["status"]),
            inputs=json.loads(row["inputs_json"]),
            outputs=json.loads(row["outputs_json"]) if row["outputs_json"] else None,
            parent_job_id=row["parent_job_id"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

        step_run_rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ?", (job_id,)
        ).fetchall()
        for sr_row in step_run_rows:
            sr = self._row_to_step_run(sr_row)
            job.step_runs[sr.step_name] = sr

        return job

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        if status:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM jobs ORDER BY created_at DESC"
            ).fetchall()

        jobs = []
        for row in rows:
            job = self.load_job(row["id"])
            if job:
                jobs.append(job)
        return jobs

    def delete_job(self, job_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def update_job_status(self, job_id: str, status: JobStatus) -> None:
        from datetime import datetime, timezone

        self._conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, datetime.now(timezone.utc).isoformat(), job_id),
        )
        self._conn.commit()

    # ── Step Runs ─────────────────────────────────────────────────────────

    def save_step_run(self, step_run: StepRun) -> None:
        self._save_step_run_internal(step_run)
        self._conn.commit()

    def _save_step_run_internal(self, sr: StepRun) -> None:
        self._conn.execute(
            """
            INSERT INTO step_runs
                (id, job_id, step_name, status, inputs_json, outputs_json,
                 error, attempt, started_at, completed_at,
                 iteration_index, iteration_value_json, input_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                inputs_json = excluded.inputs_json,
                outputs_json = excluded.outputs_json,
                error = excluded.error,
                attempt = excluded.attempt,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                iteration_index = excluded.iteration_index,
                iteration_value_json = excluded.iteration_value_json,
                input_hash = excluded.input_hash
            """,
            (
                sr.id,
                sr.job_id,
                sr.step_name,
                sr.status.value,
                _dumps(sr.inputs),
                _dumps(sr.outputs) if sr.outputs is not None else None,
                sr.error,
                sr.attempt,
                sr.started_at.isoformat() if sr.started_at else None,
                sr.completed_at.isoformat() if sr.completed_at else None,
                sr.iteration_index,
                _dumps(sr.iteration_value) if sr.iteration_value is not None else None,
                sr.input_hash,
            ),
        )

    def load_step_runs(self, job_id: str) -> list[StepRun]:
        rows = self._conn.execute(
            "SELECT * FROM step_runs WHERE job_id = ?", (job_id,)
        ).fetchall()
        return [self._row_to_step_run(r) for r in rows]

    def _row_to_step_run(self, row: sqlite3.Row) -> StepRun:
        return StepRun(
            id=row["id"],
            job_id=row["job_id"],
            step_name=row["step_name"],
            status=StepStatus(row["status"]),
            inputs=json.loads(row["inputs_json"]) if row["inputs_json"] else {},
            outputs=json.loads(row["outputs_json"]) if row["outputs_json"] else None,
            error=row["error"],
            attempt=row["attempt"],
            started_at=_parse_dt(row["started_at"]) if row["started_at"] else None,
            completed_at=_parse_dt(row["completed_at"]) if row["completed_at"] else None,
            iteration_index=row["iteration_index"],
            iteration_value=(
                json.loads(row["iteration_value_json"])
                if row["iteration_value_json"]
                else None
            ),
            input_hash=row["input_hash"],
        )

    # ── Events ────────────────────────────────────────────────────────────

    def save_event(self, event: Event) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO events
                (id, job_id, event_type, step_name, data_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.job_id,
                event.event_type.value,
                event.step_name,
                _dumps(event.data),
                event.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    def load_events(
        self,
        job_id: str,
        event_type: EventType | None = None,
    ) -> list[Event]:
        if event_type:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id = ? AND event_type = ? ORDER BY timestamp",
                (job_id, event_type.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id = ? ORDER BY timestamp",
                (job_id,),
            ).fetchall()

        return [
            Event(
                id=r["id"],
                job_id=r["job_id"],
                event_type=EventType(r["event_type"]),
                step_name=r["step_name"],
                data=json.loads(r["data_json"]) if r["data_json"] else {},
                timestamp=_parse_dt(r["timestamp"]),
            )
            for r in rows
        ]

    # ── Crash Recovery ────────────────────────────────────────────────────

    def recover_job(self, job_id: str) -> Job | None:
        """Load a job and reset any RUNNING steps to PENDING for re-execution."""
        job = self.load_job(job_id)
        if not job:
            return None

        changed = False
        for sr in job.step_runs.values():
            if sr.status == StepStatus.RUNNING:
                sr.status = StepStatus.PENDING
                sr.started_at = None
                sr.error = None
                changed = True

        if job.status == JobStatus.RUNNING:
            job.status = JobStatus.PENDING
            changed = True

        if changed:
            self.save_job(job)

        return job

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()


def _parse_dt(s: str) -> datetime:
    from datetime import datetime, timezone

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
