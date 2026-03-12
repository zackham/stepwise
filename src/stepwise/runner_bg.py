"""Background runner for --async mode.

Spawned as a detached process by run_async(). Starts the pre-created job,
ticks until terminal state, then exits. All state lives in SQLite.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from stepwise.config import load_config
from stepwise.engine import Engine
from stepwise.models import JobStatus, StepRunStatus
from stepwise.registry_factory import create_default_registry
from stepwise.store import SQLiteStore


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

    config = load_config()
    store = SQLiteStore(args.db)
    registry = create_default_registry(config)
    from pathlib import Path
    project_dir = Path(args.project_dir) if args.project_dir else None
    engine = Engine(store, registry, jobs_dir=args.jobs_dir, project_dir=project_dir)

    try:
        engine.start_job(args.job_id)

        while True:
            job = engine.get_job(args.job_id)

            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return 0

            time.sleep(0.5)
            try:
                engine.tick()
            except Exception as e:
                logging.error(f"Tick crashed: {e}")
                return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
