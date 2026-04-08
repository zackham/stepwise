"""Tests for stepwise.snapshot — eager session snapshot via filesystem copy.

Path layout (post-canary correction 2026-04-07):
    ~/.claude/projects/<project-slug>/<uuid>.jsonl

Tests use a tmp working_dir + monkeypatched CLAUDE_PROJECTS_DIR to isolate
filesystem state.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import stepwise.snapshot as snapshot_mod
from stepwise.snapshot import (
    CLAUDE_PROJECTS_DIR,
    cleanup_orphaned_tmps,
    project_sessions_dir,
    project_slug,
    snapshot_session,
)


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Point CLAUDE_PROJECTS_DIR at a tmp dir + return a (working_dir, sessions_dir) pair."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(snapshot_mod, "CLAUDE_PROJECTS_DIR", projects_root)
    working_dir = "/fake/work/test-project"
    sessions_dir = projects_root / project_slug(working_dir)
    sessions_dir.mkdir(parents=True)
    return working_dir, sessions_dir


def _write_session(sessions: Path, uuid: str, content: bytes = b'{"k": "v"}\n') -> Path:
    p = sessions / f"{uuid}.jsonl"
    p.write_bytes(content)
    return p


# ─── project_slug / project_sessions_dir ──────────────────────────────────


def test_project_slug_basic():
    assert project_slug("/home/zack/work/vita") == "-home-zack-work-vita"


def test_project_slug_tmp():
    assert project_slug("/tmp/foo") == "-tmp-foo"


def test_project_slug_path_object():
    assert project_slug(Path("/home/zack/work/stepwise")) == "-home-zack-work-stepwise"


def test_project_sessions_dir_uses_claude_projects_root(fake_project):
    working_dir, sessions_dir = fake_project
    computed = project_sessions_dir(working_dir)
    assert computed == sessions_dir


# ─── snapshot_session ─────────────────────────────────────────────────────


def test_snapshot_basic_copy_succeeds(fake_project):
    working_dir, sessions_dir = fake_project
    src = _write_session(sessions_dir, "src-uuid", b'{"messages": []}\n')
    new_uuid = snapshot_session("src-uuid", working_dir)
    dst = sessions_dir / f"{new_uuid}.jsonl"
    assert dst.exists()
    assert dst.read_bytes() == b'{"messages": []}\n'
    # Source unchanged
    assert src.read_bytes() == b'{"messages": []}\n'


def test_snapshot_returns_distinct_uuid(fake_project):
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid")
    new_uuid = snapshot_session("src-uuid", working_dir)
    assert new_uuid != "src-uuid"
    assert len(new_uuid) > 0


def test_snapshot_atomic_via_temp_file(fake_project):
    """No .tmp file should remain after a successful snapshot."""
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid")
    snapshot_session("src-uuid", working_dir)
    tmps = list(sessions_dir.glob(".*.tmp"))
    assert tmps == []


def test_snapshot_parent_dir_fsync_called(fake_project):
    """Parent dir fsync is called for crash safety."""
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid")
    real_fsync = os.fsync
    fsynced_fds: list[int] = []

    def tracking_fsync(fd):
        fsynced_fds.append(fd)
        return real_fsync(fd)

    with patch.object(snapshot_mod.os, "fsync", side_effect=tracking_fsync):
        snapshot_session("src-uuid", working_dir)

    # Two fsync calls expected: one on the temp file, one on the parent dir.
    assert len(fsynced_fds) == 2


def test_snapshot_source_stability_retry_succeeds_on_second_attempt(fake_project):
    """If source is unstable on first attempt but stable on second, snapshot succeeds."""
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid", b"v1\n")
    src_path = sessions_dir / "src-uuid.jsonl"

    real_stat = type(src_path).stat
    fake_st = src_path.stat()

    class FakeStat:
        def __init__(self, st_size, st_mtime_ns):
            self.st_size = st_size
            self.st_mtime_ns = st_mtime_ns

    call_count = {"n": 0}

    def fake_stat_method(self, *args, **kwargs):
        call_count["n"] += 1
        # First call (stat #1 of attempt 0): one size
        if call_count["n"] == 1:
            return FakeStat(99, fake_st.st_mtime_ns)
        # Second call (stat #2 of attempt 0): different size → unstable
        if call_count["n"] == 2:
            return FakeStat(100, fake_st.st_mtime_ns)
        # Subsequent calls return real stable values
        return real_stat(self, *args, **kwargs)

    with patch.object(type(src_path), "stat", fake_stat_method):
        new_uuid = snapshot_session("src-uuid", working_dir, max_stability_retries=3)
    assert new_uuid != "src-uuid"


def test_snapshot_source_unstable_raises_runtime_error(fake_project):
    """If source remains unstable, raise RuntimeError after retries."""
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid")
    src_path = sessions_dir / "src-uuid.jsonl"

    class FakeStat:
        def __init__(self, n):
            self.st_size = n
            self.st_mtime_ns = n

    counter = {"n": 0}

    def always_changing_stat(self, *args, **kwargs):
        counter["n"] += 1
        return FakeStat(counter["n"])

    with patch.object(type(src_path), "stat", always_changing_stat):
        with pytest.raises(RuntimeError, match="not stable"):
            snapshot_session("src-uuid", working_dir, max_stability_retries=3)


def test_snapshot_source_missing_raises_file_not_found(fake_project):
    working_dir, _ = fake_project
    with pytest.raises(FileNotFoundError, match="source session not found"):
        snapshot_session("nonexistent-uuid", working_dir)


def test_snapshot_two_calls_produce_distinct_uuids(fake_project):
    working_dir, sessions_dir = fake_project
    _write_session(sessions_dir, "src-uuid")
    a = snapshot_session("src-uuid", working_dir)
    b = snapshot_session("src-uuid", working_dir)
    assert a != b
    assert (sessions_dir / f"{a}.jsonl").exists()
    assert (sessions_dir / f"{b}.jsonl").exists()


# ─── cleanup_orphaned_tmps ────────────────────────────────────────────────


def test_cleanup_orphaned_tmps_removes_files_and_returns_count(fake_project):
    working_dir, sessions_dir = fake_project
    (sessions_dir / ".abc.tmp").write_bytes(b"x")
    (sessions_dir / ".def.tmp").write_bytes(b"y")
    (sessions_dir / "real.jsonl").write_bytes(b"z")

    count = cleanup_orphaned_tmps(working_dir)
    assert count == 2
    assert not (sessions_dir / ".abc.tmp").exists()
    assert not (sessions_dir / ".def.tmp").exists()
    # Real session file untouched.
    assert (sessions_dir / "real.jsonl").exists()


def test_cleanup_orphaned_tmps_handles_missing_dir(tmp_path, monkeypatch):
    """If the project directory doesn't exist, cleanup is a no-op."""
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(snapshot_mod, "CLAUDE_PROJECTS_DIR", projects_root)
    assert cleanup_orphaned_tmps("/nonexistent/working_dir") == 0


def test_cleanup_orphaned_tmps_empty_dir_returns_zero(fake_project):
    working_dir, _ = fake_project
    assert cleanup_orphaned_tmps(working_dir) == 0


def test_cleanup_orphaned_tmps_sweeps_all_projects_when_no_arg(tmp_path, monkeypatch):
    """When called with no working_dir, sweep every project subdirectory."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(snapshot_mod, "CLAUDE_PROJECTS_DIR", projects_root)

    proj_a = projects_root / "-fake-a"
    proj_b = projects_root / "-fake-b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / ".tmp1.tmp").write_bytes(b"x")
    (proj_b / ".tmp2.tmp").write_bytes(b"y")
    (proj_a / "real.jsonl").write_bytes(b"z")

    count = cleanup_orphaned_tmps()
    assert count == 2
    assert not (proj_a / ".tmp1.tmp").exists()
    assert not (proj_b / ".tmp2.tmp").exists()
    assert (proj_a / "real.jsonl").exists()
