"""Background server entry point for --detach mode.

Spawned as a detached process by `stepwise server start --detach`.
Sets up file logging, writes the pidfile, runs uvicorn, cleans up on exit.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--templates-dir", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--dot-dir", required=True)
    parser.add_argument("--port", type=int, default=8340)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--web-dir", default=None, help="Path to web UI dist directory")
    args = parser.parse_args()

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # File logging with rotation
    handler = RotatingFileHandler(
        str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s: %(message)s"
    ))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)

    # Redirect stdout/stderr to log file (catches stray prints from libraries)
    log_fd = open(log_path, "a")
    os.dup2(log_fd.fileno(), sys.stdout.fileno())
    os.dup2(log_fd.fileno(), sys.stderr.fileno())

    # Set env vars for server.py lifespan
    os.environ["STEPWISE_DB"] = args.db
    os.environ["STEPWISE_TEMPLATES"] = args.templates_dir
    os.environ["STEPWISE_JOBS_DIR"] = args.jobs_dir
    os.environ["STEPWISE_PROJECT_DIR"] = args.project_dir
    os.environ["STEPWISE_PORT"] = str(args.port)
    if args.web_dir:
        os.environ["STEPWISE_WEB_DIR"] = args.web_dir

    # Write pidfile with this process's PID
    from stepwise.server_detect import write_pidfile, remove_pidfile

    dot_dir = Path(args.dot_dir)
    write_pidfile(dot_dir, args.port, log_file=str(log_path))

    try:
        import uvicorn
        uvicorn.run(
            "stepwise.server:app",
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    finally:
        remove_pidfile(dot_dir)
        log_fd.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
