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
    engine = AsyncEngine(store, registry, jobs_dir=args.jobs_dir, project_dir=project_dir)

    import os
    # Mark job as owned by this background process
    job = store.load_job(args.job_id)
    job.created_by = f"cli:{os.getpid()}"
    job.runner_pid = os.getpid()
    store.save_job(job)

    engine_task = asyncio.create_task(engine.run())
    try:
        engine.start_job(args.job_id)
        await engine.wait_for_job(args.job_id)
        job = engine.store.load_job(args.job_id)
        return 0 if job.status == JobStatus.COMPLETED else 1
    except Exception as e:
        logging.error(f"Background runner error: {e}")
        return 1
    finally:
        engine_task.cancel()
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
