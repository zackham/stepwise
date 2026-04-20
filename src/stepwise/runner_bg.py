"""Background runner for --async mode.

Spawned as a detached process by run_async(). Starts the pre-created job,
runs until terminal state via AsyncEngine, then exits. All state lives in SQLite.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from stepwise.config import load_config
from stepwise.engine import AsyncEngine
from stepwise.models import JobStatus
from stepwise.registry_factory import create_default_registry
from stepwise.store import SQLiteStore


async def _run(args) -> int:
    config = load_config()
    store = SQLiteStore(args.db)
    registry = create_default_registry(config)
    from pathlib import Path
    project_dir = Path(args.project_dir) if args.project_dir else None
    engine = AsyncEngine(
        store, registry, jobs_dir=args.jobs_dir, project_dir=project_dir,
        billing_mode=config.billing, config=config,
        max_concurrent_jobs=config.max_concurrent_jobs,
    )

    import os
    # Mark job as owned by this background process
    job = store.load_job(args.job_id)
    job.created_by = f"cli:{os.getpid()}"
    job.runner_pid = os.getpid()
    store.save_job(job)

    async def _heartbeat_loop():
        """Send periodic heartbeats so the server knows this runner is alive."""
        while True:
            try:
                store.heartbeat(args.job_id)
            except Exception:
                pass
            await asyncio.sleep(10)

    engine_task = asyncio.create_task(engine.run())
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        engine.start_job(args.job_id)

        # wait_for_job relies on an in-process asyncio.Event.  If another
        # engine instance mutates the job status directly in SQLite (e.g.
        # the stuck-step watchdog from a concurrent runner), the event is
        # never set and this process zombies.  Poll the DB as a fallback.
        while True:
            try:
                await asyncio.wait_for(
                    engine.wait_for_job(args.job_id), timeout=15.0
                )
                break
            except asyncio.TimeoutError:
                job = store.load_job(args.job_id)
                if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
                    logging.warning(
                        "Job %s reached %s via external mutation — exiting",
                        args.job_id, job.status.value,
                    )
                    break

        job = engine.store.load_job(args.job_id)
        return 0 if job.status == JobStatus.COMPLETED else 1
    except Exception as e:
        logging.error(f"Background runner error: {e}")
        return 1
    finally:
        heartbeat_task.cancel()
        engine_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        try:
            await engine_task
        except asyncio.CancelledError:
            pass
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--project-dir", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
