"""Tests for server_detect.py — path-based server discovery (N23)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.server_detect import (
    GLOBAL_REGISTRY,
    _detect_server_from_pidfile,
    _load_registry,
    _save_registry,
    detect_server,
    detect_server_for_project,
    register_server,
    write_pidfile,
)


# ── helpers ───────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path, entries: dict) -> Path:
    """Write a registry file inside tmp_path and return its path."""
    reg = tmp_path / "servers.json"
    reg.write_text(json.dumps(entries, indent=2))
    return reg


def _project_root(tmp_path: Path, name: str = "myproject") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".stepwise").mkdir()
    return root


# ── detect_server_for_project: registry match ─────────────────────────


def test_detect_server_for_project_found(tmp_path):
    """Returns URL when registry has a live, healthy entry for the project."""
    root = _project_root(tmp_path)

    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root): {
            "project_path": str(root),
            "pid": os.getpid(),  # this PID is alive
            "port": 9876,
            "url": "http://localhost:9876",
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url = detect_server_for_project(root)

    assert url == "http://localhost:9876"


def test_detect_server_for_project_no_registry(tmp_path):
    """Returns None when registry file does not exist."""
    root = _project_root(tmp_path)
    missing_reg = tmp_path / "nonexistent.json"

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", missing_reg):
        url = detect_server_for_project(root)

    assert url is None


def test_detect_server_for_project_different_project(tmp_path):
    """Does not match an entry registered for a different project path."""
    root_a = _project_root(tmp_path, "projectA")
    root_b = _project_root(tmp_path, "projectB")

    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root_a): {
            "project_path": str(root_a),
            "pid": os.getpid(),
            "port": 9001,
            "url": "http://localhost:9001",
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url = detect_server_for_project(root_b)

    assert url is None


def test_detect_server_for_project_dead_pid_pruned(tmp_path):
    """Registry entry with dead PID is pruned and None is returned."""
    root = _project_root(tmp_path)

    reg_path = tmp_path / "servers.json"
    dead_pid = 999999  # assumed dead
    reg_path.write_text(json.dumps({
        str(root): {
            "project_path": str(root),
            "pid": dead_pid,
            "port": 9002,
            "url": "http://localhost:9002",
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._pid_alive", return_value=False):
        url = detect_server_for_project(root)

    assert url is None
    # Stale entry should be removed from the registry file
    remaining = json.loads(reg_path.read_text())
    assert str(root) not in remaining


def test_detect_server_for_project_unhealthy_server(tmp_path):
    """Returns None when the PID is alive but health probe fails."""
    root = _project_root(tmp_path)

    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root): {
            "project_path": str(root),
            "pid": os.getpid(),
            "port": 9003,
            "url": "http://localhost:9003",
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=False):
        url = detect_server_for_project(root)

    assert url is None


def test_detect_server_for_project_resolves_symlinks(tmp_path):
    """Path comparison uses resolved absolute paths (handles symlinks / relative input)."""
    root = _project_root(tmp_path)

    # Registry stores resolved absolute path
    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root.resolve()): {
            "project_path": str(root.resolve()),
            "pid": os.getpid(),
            "port": 9004,
            "url": "http://localhost:9004",
        }
    }))

    # Pass the unresolved (but equivalent) path
    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url = detect_server_for_project(root)

    assert url == "http://localhost:9004"


def test_detect_server_for_project_multiple_servers(tmp_path):
    """With multiple servers registered, only the matching project's URL is returned."""
    root_a = _project_root(tmp_path, "projectA")
    root_b = _project_root(tmp_path, "projectB")

    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root_a): {
            "project_path": str(root_a),
            "pid": os.getpid(),
            "port": 9010,
            "url": "http://localhost:9010",
        },
        str(root_b): {
            "project_path": str(root_b),
            "pid": os.getpid(),
            "port": 9011,
            "url": "http://localhost:9011",
        },
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url_a = detect_server_for_project(root_a)
        url_b = detect_server_for_project(root_b)

    assert url_a == "http://localhost:9010"
    assert url_b == "http://localhost:9011"


def test_detect_server_for_project_entry_missing_url(tmp_path):
    """Entry without a URL field is skipped gracefully."""
    root = _project_root(tmp_path)

    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root): {
            "project_path": str(root),
            "pid": os.getpid(),
            # url is intentionally absent
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path):
        url = detect_server_for_project(root)

    assert url is None


# ── detect_server: registry-first resolution order ───────────────────


def test_detect_server_registry_takes_priority_over_pidfile(tmp_path):
    """Registry lookup takes precedence over .stepwise/server.pid."""
    root = _project_root(tmp_path)
    dot_dir = root / ".stepwise"

    # Write a pidfile pointing at a different port
    write_pidfile(dot_dir, 8000, pid=os.getpid())

    # Registry points at a different URL
    reg_path = tmp_path / "servers.json"
    reg_path.write_text(json.dumps({
        str(root): {
            "project_path": str(root),
            "pid": os.getpid(),
            "port": 9999,
            "url": "http://localhost:9999",
        }
    }))

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url = detect_server(dot_dir)

    assert url == "http://localhost:9999"


def test_detect_server_falls_back_to_pidfile_when_registry_empty(tmp_path):
    """Falls back to .stepwise/server.pid when registry has no matching entry."""
    root = _project_root(tmp_path)
    dot_dir = root / ".stepwise"

    write_pidfile(dot_dir, 8340, pid=os.getpid())

    # Empty registry
    reg_path = tmp_path / "servers.json"
    reg_path.write_text("{}")

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path), \
         patch("stepwise.server_detect._probe_health", return_value=True):
        url = detect_server(dot_dir)

    assert url == "http://localhost:8340"


def test_detect_server_none_when_both_miss(tmp_path):
    """Returns None when neither registry nor pidfile has a live server."""
    root = _project_root(tmp_path)
    dot_dir = root / ".stepwise"

    # No pidfile, empty registry
    reg_path = tmp_path / "servers.json"
    reg_path.write_text("{}")

    with patch("stepwise.server_detect.GLOBAL_REGISTRY", reg_path):
        url = detect_server(dot_dir)

    assert url is None


def test_detect_server_none_project_dir_is_none():
    """Returns None immediately when project_dir is None."""
    url = detect_server(None)
    assert url is None


# ── _detect_server_from_pidfile: isolated pidfile tests ───────────────


def test_detect_server_from_pidfile_no_file(tmp_path):
    assert _detect_server_from_pidfile(tmp_path) is None


def test_detect_server_from_pidfile_stale_pid(tmp_path):
    write_pidfile(tmp_path, 8340, pid=999999)
    with patch("stepwise.server_detect._pid_alive", return_value=False):
        url = _detect_server_from_pidfile(tmp_path)
    assert url is None
    # Stale pidfile removed
    assert not (tmp_path / "server.pid").exists()


def test_detect_server_from_pidfile_healthy(tmp_path):
    write_pidfile(tmp_path, 8340, pid=os.getpid())
    with patch("stepwise.server_detect._probe_health", return_value=True):
        url = _detect_server_from_pidfile(tmp_path)
    assert url == "http://localhost:8340"


def test_detect_server_from_pidfile_corrupt(tmp_path):
    (tmp_path / "server.pid").write_text("not json{{")
    assert _detect_server_from_pidfile(tmp_path) is None
