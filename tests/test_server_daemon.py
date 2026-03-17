"""Tests for server daemon lifecycle (start/stop/restart/status)."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stepwise.server_detect import read_pidfile, write_pidfile, remove_pidfile


# ── read_pidfile / write_pidfile ──────────────────────────────────────


def test_read_pidfile_missing(tmp_path):
    assert read_pidfile(tmp_path) == {}


def test_read_pidfile_valid(tmp_path):
    write_pidfile(tmp_path, 8340)
    data = read_pidfile(tmp_path)
    assert data["port"] == 8340
    assert data["pid"] == os.getpid()
    assert data["url"] == "http://localhost:8340"
    assert "started_at" in data


def test_read_pidfile_corrupt(tmp_path):
    (tmp_path / "server.pid").write_text("not json{{{")
    assert read_pidfile(tmp_path) == {}


def test_write_pidfile_custom_pid(tmp_path):
    write_pidfile(tmp_path, 9000, pid=12345, log_file="/tmp/test.log")
    data = read_pidfile(tmp_path)
    assert data["pid"] == 12345
    assert data["port"] == 9000
    assert data["log_file"] == "/tmp/test.log"


def test_write_pidfile_started_at_is_iso(tmp_path):
    from datetime import datetime
    write_pidfile(tmp_path, 8340)
    data = read_pidfile(tmp_path)
    # Should parse as ISO datetime
    dt = datetime.fromisoformat(data["started_at"])
    assert dt.tzinfo is not None  # timezone-aware


# ── cmd_server stop ───────────────────────────────────────────────────


def _make_args(tmp_path, **overrides):
    """Build a Namespace that looks like parsed CLI args."""
    defaults = {
        "project_dir": str(tmp_path),
        "quiet": False,
        "verbose": False,
        "action": "status",
        "host": None,
        "port": None,
        "detach": False,
        "no_open": True,
    }
    defaults.update(overrides)
    ns = Namespace(**defaults)
    from stepwise.io import create_adapter
    ns._adapter = create_adapter(quiet=True)
    return ns


def _init_project(tmp_path):
    """Create a minimal .stepwise/ directory."""
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir(exist_ok=True)
    (dot_dir / "jobs").mkdir(exist_ok=True)
    (dot_dir / "templates").mkdir(exist_ok=True)
    return dot_dir


def test_cmd_server_stop_no_server(tmp_path):
    from stepwise.cli import _server_stop
    dot_dir = _init_project(tmp_path)
    args = _make_args(tmp_path, action="stop")
    result = _server_stop(args)
    assert result == 0


def test_cmd_server_stop_stale_pid(tmp_path):
    """Dead PID in pidfile → cleans up gracefully."""
    from stepwise.cli import _server_stop
    dot_dir = _init_project(tmp_path)

    # Write pidfile with a PID that doesn't exist
    write_pidfile(dot_dir, 8340, pid=999999)

    args = _make_args(tmp_path, action="stop")
    result = _server_stop(args)
    assert result == 0
    # Pidfile should be cleaned up
    assert not (dot_dir / "server.pid").exists()


# ── cmd_server status ─────────────────────────────────────────────────


def test_cmd_server_status_no_server(tmp_path):
    from stepwise.cli import _server_status
    dot_dir = _init_project(tmp_path)
    args = _make_args(tmp_path, action="status")
    result = _server_status(args)
    assert result == 0


@patch("stepwise.server_detect.detect_server")
def test_cmd_server_status_running(mock_detect, tmp_path):
    from stepwise.cli import _server_status
    dot_dir = _init_project(tmp_path)

    mock_detect.return_value = "http://localhost:8340"
    write_pidfile(dot_dir, 8340)

    args = _make_args(tmp_path, action="status")
    result = _server_status(args)
    assert result == 0


# ── detached start ────────────────────────────────────────────────────


@patch("stepwise.server_detect._probe_health", return_value=True)
@patch("subprocess.Popen")
def test_server_start_detach_spawns_bg(mock_popen, mock_health, tmp_path):
    from stepwise.cli import _server_start_detached
    from stepwise.io import create_adapter

    dot_dir = _init_project(tmp_path)
    from stepwise.project import _project_from_root
    project = _project_from_root(tmp_path)

    io = create_adapter(quiet=True)
    args = _make_args(tmp_path, detach=True, no_open=True)

    result = _server_start_detached(project, "127.0.0.1", 8340, io, args)
    assert result == 0
    assert mock_popen.called

    # Verify the command includes server_bg module
    cmd = mock_popen.call_args[0][0]
    assert "-m" in cmd
    assert "stepwise.server_bg" in cmd
    assert "--port" in cmd
    assert "8340" in cmd


@patch("stepwise.server_detect._probe_health", return_value=False)
@patch("subprocess.Popen")
def test_server_start_detach_fails_on_timeout(mock_popen, mock_health, tmp_path):
    from stepwise.cli import _server_start_detached, EXIT_JOB_FAILED
    from stepwise.io import create_adapter
    from stepwise.project import _project_from_root

    _init_project(tmp_path)
    project = _project_from_root(tmp_path)
    io = create_adapter(quiet=True)
    args = _make_args(tmp_path, detach=True, no_open=True)

    result = _server_start_detached(project, "127.0.0.1", 8340, io, args)
    assert result == EXIT_JOB_FAILED


# ── restart ───────────────────────────────────────────────────────────


@patch("stepwise.cli._server_start")
@patch("stepwise.cli._server_stop")
def test_cmd_server_restart(mock_stop, mock_start, tmp_path):
    from stepwise.cli import _server_restart

    mock_stop.return_value = 0
    mock_start.return_value = 0

    args = _make_args(tmp_path, action="restart")
    result = _server_restart(args)
    assert result == 0
    assert mock_stop.called
    assert mock_start.called
