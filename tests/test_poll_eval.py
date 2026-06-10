"""Tests for poll command evaluation (poll_eval.py).

Regression coverage for RC hardening:
- F43: non-dict / non-JSON stdout on exit 0 surfaces an error instead of
  silently polling forever.
- F57: the sync evaluator kills the whole process tree on timeout
  (mirrors the async variant's process-group handling).
"""

import asyncio
import os
import time

import pytest

from stepwise.poll_eval import (
    PollResult,
    evaluate_poll_command,
    evaluate_poll_command_sync,
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ── Contract: stdout interpretation (sync) ────────────────────────────


class TestSyncStdoutContract:
    def test_dict_output_is_ready(self, tmp_path):
        result = evaluate_poll_command_sync(
            command='echo \'{"decision": "approved"}\'', cwd=str(tmp_path),
        )
        assert result.ready is True
        assert result.output == {"decision": "approved"}
        assert result.error is None

    def test_empty_output_not_ready_no_error(self, tmp_path):
        result = evaluate_poll_command_sync(command="true", cwd=str(tmp_path))
        assert result.ready is False
        assert result.error is None

    def test_nonzero_exit_is_error(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="echo nope >&2; exit 3", cwd=str(tmp_path),
        )
        assert result.ready is False
        assert "nope" in result.error

    def test_scalar_json_output_surfaces_error(self, tmp_path):
        """F43: bare JSON string (e.g. --jq '.reviewDecision') = not ready
        but with a diagnostic error, not silence."""
        result = evaluate_poll_command_sync(
            command='echo \'"approved"\'', cwd=str(tmp_path),
        )
        assert result.ready is False
        assert result.error is not None
        assert "non-dict" in result.error
        assert "approved" in result.error

    def test_array_json_output_surfaces_error(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="echo '[1, 2]'", cwd=str(tmp_path),
        )
        assert result.ready is False
        assert "non-dict" in result.error

    def test_non_json_output_surfaces_error(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="echo 'fetching data...'", cwd=str(tmp_path),
        )
        assert result.ready is False
        assert "non-JSON" in result.error
        assert "fetching data" in result.error


# ── Contract: stdout interpretation (async) ───────────────────────────


class TestAsyncStdoutContract:
    def test_scalar_json_output_surfaces_error(self, tmp_path):
        result = asyncio.run(evaluate_poll_command(
            command='echo \'"approved"\'', cwd=str(tmp_path),
        ))
        assert result.ready is False
        assert "non-dict" in result.error

    def test_non_json_output_surfaces_error(self, tmp_path):
        result = asyncio.run(evaluate_poll_command(
            command="echo plain-text", cwd=str(tmp_path),
        ))
        assert result.ready is False
        assert "non-JSON" in result.error

    def test_dict_output_is_ready(self, tmp_path):
        result = asyncio.run(evaluate_poll_command(
            command='echo \'{"ok": 1}\'', cwd=str(tmp_path),
        ))
        assert result.ready is True
        assert result.output == {"ok": 1}


# ── Timeout: process tree kill (sync) ─────────────────────────────────


class TestSyncTimeoutTreeKill:
    def test_timeout_returns_error_promptly(self, tmp_path):
        start = time.monotonic()
        result = evaluate_poll_command_sync(
            command="sleep 30", cwd=str(tmp_path), timeout_seconds=1,
        )
        elapsed = time.monotonic() - start
        assert result.ready is False
        assert "timeout" in result.error
        assert elapsed < 10

    def test_timeout_kills_child_process_tree(self, tmp_path):
        """F57: a backgrounded child of the check command must die with
        the process group — no orphan accumulation per poll tick."""
        pidfile = tmp_path / "child.pid"
        command = f"sleep 30 & echo $! > {pidfile}; wait"

        result = evaluate_poll_command_sync(
            command=command, cwd=str(tmp_path), timeout_seconds=1,
        )
        assert result.ready is False
        assert "timeout" in result.error

        child_pid = int(pidfile.read_text().strip())
        # Give the SIGKILL a moment to land
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _pid_alive(child_pid):
                break
            time.sleep(0.05)
        assert not _pid_alive(child_pid), (
            f"orphaned child {child_pid} survived poll timeout"
        )
