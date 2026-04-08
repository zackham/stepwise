"""Tests for stepwise.snapshot — eager session snapshot via filesystem copy."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import stepwise.snapshot as snapshot_mod
from stepwise.snapshot import (
    cleanup_orphaned_tmps,
    snapshot_session,
)


@pytest.fixture
def fake_sessions_dir(tmp_path, monkeypatch):
    """Point SESSIONS_DIR at a tmp directory for the duration of the test."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(snapshot_mod, "SESSIONS_DIR", sessions)
    return sessions


def _write_session(sessions: Path, uuid: str, content: bytes = b'{"k": "v"}') -> Path:
    p = sessions / f"{uuid}.json"
    p.write_bytes(content)
    return p


# ─── snapshot_session ─────────────────────────────────────────────────────


def test_snapshot_basic_copy_succeeds(fake_sessions_dir):
    src = _write_session(fake_sessions_dir, "src-uuid", b'{"messages": []}')
    new_uuid = snapshot_session("src-uuid")
    dst = fake_sessions_dir / f"{new_uuid}.json"
    assert dst.exists()
    assert dst.read_bytes() == b'{"messages": []}'
    # Source unchanged
    assert src.read_bytes() == b'{"messages": []}'


def test_snapshot_returns_distinct_uuid(fake_sessions_dir):
    _write_session(fake_sessions_dir, "src-uuid")
    new_uuid = snapshot_session("src-uuid")
    assert new_uuid != "src-uuid"
    assert len(new_uuid) > 0


def test_snapshot_atomic_via_temp_file(fake_sessions_dir):
    """No .tmp file should remain after a successful snapshot."""
    _write_session(fake_sessions_dir, "src-uuid")
    snapshot_session("src-uuid")
    tmps = list(fake_sessions_dir.glob(".*.tmp"))
    assert tmps == []


def test_snapshot_parent_dir_fsync_called(fake_sessions_dir):
    """Parent dir fsync is called for crash safety."""
    _write_session(fake_sessions_dir, "src-uuid")
    real_fsync = os.fsync
    fsynced_fds: list[int] = []

    def tracking_fsync(fd):
        fsynced_fds.append(fd)
        return real_fsync(fd)

    with patch.object(snapshot_mod.os, "fsync", side_effect=tracking_fsync):
        snapshot_session("src-uuid")

    # Two fsync calls expected: one on the temp file, one on the parent dir.
    assert len(fsynced_fds) == 2


def test_snapshot_source_stability_retry_succeeds_on_second_attempt(fake_sessions_dir):
    """If source is unstable on first attempt but stable on second, snapshot succeeds."""
    _write_session(fake_sessions_dir, "src-uuid", b"v1")
    src_path = fake_sessions_dir / "src-uuid.json"

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
        new_uuid = snapshot_session("src-uuid", max_stability_retries=3)
    assert new_uuid != "src-uuid"


def test_snapshot_source_unstable_raises_runtime_error(fake_sessions_dir):
    """If source remains unstable, raise RuntimeError after retries."""
    _write_session(fake_sessions_dir, "src-uuid")
    src_path = fake_sessions_dir / "src-uuid.json"

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
            snapshot_session("src-uuid", max_stability_retries=3)


def test_snapshot_source_missing_raises_file_not_found(fake_sessions_dir):
    with pytest.raises(FileNotFoundError, match="source session not found"):
        snapshot_session("nonexistent-uuid")


def test_snapshot_two_calls_produce_distinct_uuids(fake_sessions_dir):
    _write_session(fake_sessions_dir, "src-uuid")
    a = snapshot_session("src-uuid")
    b = snapshot_session("src-uuid")
    assert a != b
    assert (fake_sessions_dir / f"{a}.json").exists()
    assert (fake_sessions_dir / f"{b}.json").exists()


# ─── cleanup_orphaned_tmps ────────────────────────────────────────────────


def test_cleanup_orphaned_tmps_removes_files_and_returns_count(fake_sessions_dir):
    (fake_sessions_dir / ".abc.tmp").write_bytes(b"x")
    (fake_sessions_dir / ".def.tmp").write_bytes(b"y")
    (fake_sessions_dir / "real.json").write_bytes(b"z")

    count = cleanup_orphaned_tmps()
    assert count == 2
    assert not (fake_sessions_dir / ".abc.tmp").exists()
    assert not (fake_sessions_dir / ".def.tmp").exists()
    # Real session file untouched.
    assert (fake_sessions_dir / "real.json").exists()


def test_cleanup_orphaned_tmps_handles_missing_dir(tmp_path, monkeypatch):
    nonexistent = tmp_path / "nope"
    monkeypatch.setattr(snapshot_mod, "SESSIONS_DIR", nonexistent)
    assert cleanup_orphaned_tmps() == 0


def test_cleanup_orphaned_tmps_empty_dir_returns_zero(fake_sessions_dir):
    assert cleanup_orphaned_tmps() == 0
