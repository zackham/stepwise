"""Eager snapshot via filesystem copy. Per §9 of the coordination doc.

This module is the canonical implementation of the snapshot mechanism that
makes fork-from-step semantically correct. The Claude CLI's --fork-session
forks from whatever the tail of the session is at the moment claude reads
it; if the parent session continues writing past the intended fork point,
later forks capture the wrong tail. snapshot_session() copies the session
JSONL file via temp + atomic rename, producing a stable UUID that can be
forked from any number of times without race conditions.

PATH LAYOUT (corrected post-canary 2026-04-07):

Claude stores sessions per project at:
    ~/.claude/projects/<project-slug>/<uuid>.jsonl

where <project-slug> is the working directory with `/` replaced by `-` and
prepended with `-`. Example:
    /home/zack/work/vita → -home-zack-work-vita

The original §9.2 design doc said `~/.claude/sessions/<uuid>.json` — that
was wrong. The first end-to-end canary run caught it: snapshot calls failed
silently because the source files didn't exist at the expected path, and
forks fell back to the live UUID, demonstrating the §9.1 race exactly as
predicted by the design.

Pure module: no engine imports. Wrapped by the engine's fork-source step
lifecycle (engine.py: _maybe_snapshot_for_fork_source).
"""

from __future__ import annotations

import logging
import os
import time
import uuid as _uuid
from pathlib import Path

logger = logging.getLogger("stepwise.snapshot")

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def project_slug(working_dir: str | Path) -> str:
    """Return the project-slug Claude uses for the given working directory.

    Algorithm: take the absolute path, replace `/` with `-`. The result
    starts with `-` because the path starts with `/`.

    Examples:
        /home/zack/work/vita → -home-zack-work-vita
        /tmp/foo → -tmp-foo
    """
    abs_path = str(Path(working_dir).resolve()) if not str(working_dir).startswith("/") else str(working_dir)
    return abs_path.replace("/", "-")


def project_sessions_dir(working_dir: str | Path) -> Path:
    """Return the directory where Claude stores session JSONL files for `working_dir`."""
    return CLAUDE_PROJECTS_DIR / project_slug(working_dir)


def _generate_snapshot_uuid() -> str:
    """Return a fresh UUID4 string suitable for use as a Claude session filename.

    Claude CLI enforces UUID format (8-4-4-4-12 hex) for session IDs passed
    to `--resume` and rejects other shapes at argument parse time with
    "not a valid UUID" — see the 2026-04-07 canary regression that caught
    this the hard way (~11s runtime failure, no stderr, just exit 1). Use
    uuid.uuid4() to match the shape Claude itself produces.
    """
    return str(_uuid.uuid4())


def snapshot_session(
    uuid: str,
    working_dir: str | Path,
    max_stability_retries: int = 3,
) -> str:
    """Snapshot a Claude session JSONL file via temp + atomic rename.

    Per §9.2 of the coordination doc (corrected path 2026-04-07). Returns
    the new UUID.

    The source is read from `~/.claude/projects/<slug>/<uuid>.jsonl`
    where <slug> is derived from `working_dir`. The destination snapshot
    is written into the same project directory.

    Algorithm:
      1. Source stability check: stat-read-stat with exponential backoff
         (10ms, 20ms, 40ms) up to max_stability_retries.
      2. Write contents to .{new_uuid}.tmp.
      3. fsync the temp file.
      4. os.replace(tmp, dst) — atomic on POSIX same-filesystem.
      5. fsync the parent directory.

    Raises FileNotFoundError if the source session doesn't exist.
    Raises RuntimeError if the source isn't stable after the retries.
    """
    sessions_dir = project_sessions_dir(working_dir)
    new_uuid = _generate_snapshot_uuid()
    src = sessions_dir / f"{uuid}.jsonl"
    tmp = sessions_dir / f".{new_uuid}.tmp"
    dst = sessions_dir / f"{new_uuid}.jsonl"

    if not src.exists():
        raise FileNotFoundError(f"source session not found: {src}")

    contents: bytes | None = None
    stable = False
    for attempt in range(max_stability_retries):
        st1 = src.stat()
        contents = src.read_bytes()
        st2 = src.stat()
        if st1.st_size == st2.st_size and st1.st_mtime_ns == st2.st_mtime_ns:
            stable = True
            break
        time.sleep(0.01 * (2 ** attempt))
    if not stable:
        raise RuntimeError(
            f"source session {uuid} not stable after {max_stability_retries} retries"
        )

    assert contents is not None
    tmp.write_bytes(contents)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(tmp, dst)

    dir_fd = os.open(sessions_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    logger.debug("snapshot_session: %s -> %s (in %s)", uuid, new_uuid, sessions_dir)
    return new_uuid


def cleanup_orphaned_tmps(working_dir: str | Path | None = None) -> int:
    """Sweep project sessions directories for orphaned .tmp files and unlink them.

    Per §9.3: any .{uuid}.tmp file in a project sessions directory is from
    an interrupted snapshot and is safe to remove. Called on engine startup
    before per-job recovery so the recovery code never sees stale temp files.

    If `working_dir` is provided, only sweeps that project's directory. If
    None, sweeps every directory under CLAUDE_PROJECTS_DIR.

    Returns the count of files removed.
    """
    if working_dir is not None:
        dirs_to_sweep = [project_sessions_dir(working_dir)]
    elif CLAUDE_PROJECTS_DIR.exists():
        dirs_to_sweep = [
            d for d in CLAUDE_PROJECTS_DIR.iterdir() if d.is_dir()
        ]
    else:
        return 0

    count = 0
    for sessions_dir in dirs_to_sweep:
        if not sessions_dir.exists():
            continue
        for tmp in sessions_dir.glob(".*.tmp"):
            try:
                tmp.unlink()
                count += 1
            except OSError as exc:
                logger.warning("failed to unlink orphaned tmp %s: %s", tmp, exc)
    if count:
        logger.info("cleaned up %d orphaned snapshot tmp file(s)", count)
    return count
