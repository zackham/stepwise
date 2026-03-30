"""Tests for PID-file guard preventing duplicate server processes."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from stepwise.server_detect import (
    ServerAlreadyRunning,
    acquire_pidfile_guard,
    read_pidfile,
    remove_pidfile,
    write_pidfile,
)


@pytest.fixture
def project_dir():
    """Create a temporary .stepwise/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dot_dir = Path(tmpdir) / ".stepwise"
        dot_dir.mkdir()
        yield dot_dir


class TestAcquirePidfileGuard:
    def test_no_existing_pidfile(self, project_dir):
        """Fresh start — no pidfile exists."""
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()
        assert data["port"] == 8340

    def test_stale_pidfile_cleaned_up(self, project_dir):
        """Pidfile with dead PID is cleaned up and new one written."""
        # Write a pidfile with a PID that definitely doesn't exist
        write_pidfile(project_dir, 9999, pid=99999)
        path = acquire_pidfile_guard(project_dir, 8340)
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()

    def test_live_pidfile_raises(self, project_dir):
        """Pidfile with live PID raises ServerAlreadyRunning."""
        # Use current PID (which is alive) but acquire with a different pid
        write_pidfile(project_dir, 8340, pid=os.getpid())
        with pytest.raises(ServerAlreadyRunning) as exc_info:
            acquire_pidfile_guard(project_dir, 8340, pid=os.getpid() + 1)
        assert exc_info.value.pid == os.getpid()

    def test_own_pidfile_allowed(self, project_dir):
        """Pidfile written by the same process is allowed (self-PID check)."""
        write_pidfile(project_dir, 8340, pid=os.getpid())
        # Same PID should pass (foreground path scenario)
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()

    def test_corrupt_pidfile_treated_as_absent(self, project_dir):
        """Corrupt pidfile is treated as if no pidfile exists."""
        pid_file = project_dir / "server.pid"
        pid_file.write_text("not json at all {{{")
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()

    def test_log_file_preserved(self, project_dir):
        """log_file parameter is written to the pidfile."""
        path = acquire_pidfile_guard(project_dir, 8340, log_file="/tmp/server.log")
        data = json.loads(path.read_text())
        assert data["log_file"] == "/tmp/server.log"
