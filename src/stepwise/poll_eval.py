"""Shared poll command evaluation logic.

Used by:
- The in-job poll executor (engine._check_poll_watch) via evaluate_poll_command_sync
- The scheduler service (for poll-type schedules) via evaluate_poll_command

Contract:
- Exit 0 + valid JSON dict on stdout → ready (dict is the output)
- Exit 0 + empty/non-JSON stdout → not ready
- Non-zero exit → error
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass
class PollResult:
    """Result of evaluating a poll command."""

    ready: bool
    output: dict | None = None  # parsed JSON if ready
    error: str | None = None  # error message if failed
    duration_ms: int = 0  # wall time of execution


def evaluate_poll_command_sync(
    command: str,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 30,
) -> PollResult:
    """Run a poll command synchronously and interpret the result.

    Used by the engine's in-job poll executor where async is not available.
    """
    start = time.monotonic()
    run_env = {**os.environ, **(env or {})}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            env=run_env,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - start) * 1000)
        return PollResult(ready=False, error=f"timeout after {timeout_seconds}s", duration_ms=elapsed)
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return PollResult(ready=False, error=str(e), duration_ms=elapsed)

    elapsed = int((time.monotonic() - start) * 1000)

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip().split("\n")[-1]
        if not error_msg:
            error_msg = f"command exited with code {result.returncode}"
        return PollResult(ready=False, error=error_msg[:1000], duration_ms=elapsed)

    stdout = result.stdout.strip()
    if not stdout:
        return PollResult(ready=False, duration_ms=elapsed)

    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return PollResult(ready=True, output=payload, duration_ms=elapsed)
        # Non-dict JSON (array, scalar) = not ready
        return PollResult(ready=False, duration_ms=elapsed)
    except (json.JSONDecodeError, ValueError):
        return PollResult(ready=False, duration_ms=elapsed)


async def evaluate_poll_command(
    command: str,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 30,
) -> PollResult:
    """Run a poll command asynchronously with process group isolation.

    Used by the scheduler service. Spawns command in a new process group
    so the entire tree can be killed on timeout.
    """
    start = time.monotonic()
    run_env = {**os.environ, **(env or {})}

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=run_env,
            preexec_fn=os.setpgrp,  # new process group
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return PollResult(ready=False, error=str(e), duration_ms=elapsed)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        # Kill entire process group
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        elapsed = int((time.monotonic() - start) * 1000)
        return PollResult(ready=False, error=f"timeout after {timeout_seconds}s", duration_ms=elapsed)

    elapsed = int((time.monotonic() - start) * 1000)

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip() if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""

    if process.returncode != 0:
        error_msg = stderr or (stdout.split("\n")[-1] if stdout else "")
        if not error_msg:
            error_msg = f"command exited with code {process.returncode}"
        return PollResult(ready=False, error=error_msg[:1000], duration_ms=elapsed)

    if not stdout:
        return PollResult(ready=False, duration_ms=elapsed)

    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return PollResult(ready=True, output=payload, duration_ms=elapsed)
        return PollResult(ready=False, duration_ms=elapsed)
    except (json.JSONDecodeError, ValueError):
        return PollResult(ready=False, duration_ms=elapsed)
