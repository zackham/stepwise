"""File-level session lock via fcntl.flock. Per §13 of the coordination doc.

Defense in depth against runtime bugs that could in principle interleave
session JSON writes. The lock target is a dedicated .lock file (not the
JSONL file directly) per §13's recommendation: cleaner debugging surface
and avoids POSIX edge cases when locking actively-written files.

PATH LAYOUT (corrected post-canary 2026-04-07): the .lock files live
alongside the session JSONL files in the per-project Claude sessions
directory. See snapshot.py for the path computation.

Lock semantics:
  - exclusive (LOCK_EX): writers and snapshotters
  - shared (LOCK_SH): snapshot reads (multiple shared can proceed simultaneously)

The lock is advisory — only protects mutual cooperation between processes
that all use it. Claude CLI itself doesn't know about the lock; the model
is that stepwise serializes its own invocations against a session.

Pure module: no engine imports. Cleanup of .lock files is deferred (v1.1).
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import Literal

from stepwise.snapshot import project_sessions_dir

logger = logging.getLogger("stepwise.session_lock")

LockMode = Literal["exclusive", "shared"]


class SessionLock:
    """Context manager wrapping fcntl.flock on a per-session .lock file.

    Usage:
        with SessionLock(uuid, working_dir, "exclusive"):
            # critical section: only one exclusive holder at a time
            ...
    """

    def __init__(self, uuid: str, working_dir: str | Path, mode: LockMode):
        self.uuid = uuid
        self.working_dir = working_dir
        self.mode = mode
        sessions_dir = project_sessions_dir(working_dir)
        self.lock_path = sessions_dir / f"{uuid}.lock"
        self._fd: int | None = None

    def __enter__(self) -> "SessionLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Use os.open with O_CREAT so we don't leak Python file objects.
        self._fd = os.open(
            str(self.lock_path),
            os.O_RDWR | os.O_CREAT,
            0o644,
        )
        flock_mode = fcntl.LOCK_EX if self.mode == "exclusive" else fcntl.LOCK_SH
        fcntl.flock(self._fd, flock_mode)
        logger.debug("acquired %s lock on %s", self.mode, self.uuid)
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
                logger.debug("released %s lock on %s", self.mode, self.uuid)
