"""Eager snapshot via filesystem copy. Per §9 of the coordination doc.

This module is the canonical implementation of the snapshot mechanism that
makes fork-from-step semantically correct. The Claude CLI's --fork-session
forks from whatever the tail of the session is at the moment claude reads
it; if the parent session continues writing past the intended fork point,
later forks capture the wrong tail. snapshot_session() copies the session
JSON file via temp + atomic rename, producing a stable UUID that can be
forked from any number of times without race conditions.

Pure module: no engine imports. Wrapped by the engine's fork-source step
lifecycle (engine.py: _maybe_snapshot_for_fork_source).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from secrets import token_urlsafe

logger = logging.getLogger("stepwise.snapshot")

SESSIONS_DIR = Path.home() / ".claude" / "sessions"


def _generate_snapshot_uuid() -> str:
    """Return a UUID-like string suitable for use as a session filename.

    Claude CLI accepts arbitrary strings as session UUIDs (it just uses them
    as filenames). 16 bytes of urlsafe randomness matches the visual shape
    of standard UUIDs without the hyphen overhead.
    """
    return token_urlsafe(16)


def snapshot_session(uuid: str, max_stability_retries: int = 3) -> str:
    """Snapshot a Claude session JSON file via temp + atomic rename.

    Per §9.2 of the coordination doc. Returns the new UUID.

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
    new_uuid = _generate_snapshot_uuid()
    src = SESSIONS_DIR / f"{uuid}.json"
    tmp = SESSIONS_DIR / f".{new_uuid}.tmp"
    dst = SESSIONS_DIR / f"{new_uuid}.json"

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

    dir_fd = os.open(SESSIONS_DIR, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    logger.debug("snapshot_session: %s -> %s", uuid, new_uuid)
    return new_uuid


def cleanup_orphaned_tmps() -> int:
    """Sweep SESSIONS_DIR for orphaned .tmp files and unlink them.

    Per §9.3: any .{uuid}.tmp file in SESSIONS_DIR is from an interrupted
    snapshot and is safe to remove. Called on engine startup before per-job
    recovery so the recovery code never sees stale temp files.

    Returns the count of files removed. Returns 0 if SESSIONS_DIR doesn't
    exist (no sessions ever created).
    """
    if not SESSIONS_DIR.exists():
        return 0
    count = 0
    for tmp in SESSIONS_DIR.glob(".*.tmp"):
        try:
            tmp.unlink()
            count += 1
        except OSError as exc:
            logger.warning("failed to unlink orphaned tmp %s: %s", tmp, exc)
    if count:
        logger.info("cleaned up %d orphaned snapshot tmp file(s)", count)
    return count
