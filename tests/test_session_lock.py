"""Tests for stepwise.session_lock — fcntl.flock-based session locking.

Path layout (post-canary correction 2026-04-07): lock files live in the
per-project Claude sessions directory alongside the JSONL files.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import stepwise.session_lock as lock_mod
import stepwise.snapshot as snapshot_mod
from stepwise.session_lock import SessionLock
from stepwise.snapshot import project_sessions_dir, project_slug


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Point CLAUDE_PROJECTS_DIR at a tmp dir; return (working_dir, sessions_dir)."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(snapshot_mod, "CLAUDE_PROJECTS_DIR", projects_root)
    working_dir = "/fake/work/test-project"
    sessions_dir = projects_root / project_slug(working_dir)
    sessions_dir.mkdir(parents=True)
    return working_dir, sessions_dir


def test_session_lock_creates_lockfile_on_first_acquire(fake_project):
    working_dir, sessions_dir = fake_project
    lock_path = sessions_dir / "myuuid.lock"
    assert not lock_path.exists()
    with SessionLock("myuuid", working_dir, "exclusive"):
        assert lock_path.exists()
    # Lock file remains after release (cleanup is deferred).
    assert lock_path.exists()


def test_session_lock_exclusive_blocks_second_exclusive(fake_project):
    """A second exclusive holder must wait until the first releases."""
    working_dir, _ = fake_project
    acquired_b = threading.Event()
    release_a = threading.Event()
    started_b = threading.Event()

    def thread_a():
        with SessionLock("myuuid", working_dir, "exclusive"):
            release_a.wait(timeout=2)

    def thread_b():
        started_b.set()
        with SessionLock("myuuid", working_dir, "exclusive"):
            acquired_b.set()

    t_a = threading.Thread(target=thread_a)
    t_a.start()
    # Give A a moment to acquire the lock.
    time.sleep(0.05)

    t_b = threading.Thread(target=thread_b)
    t_b.start()
    started_b.wait(timeout=1)
    # B should NOT have acquired yet — A still holds.
    time.sleep(0.05)
    assert not acquired_b.is_set()

    # Release A
    release_a.set()
    t_a.join(timeout=2)
    # Now B should acquire
    acquired_b.wait(timeout=2)
    assert acquired_b.is_set()
    t_b.join(timeout=2)


def test_session_lock_shared_allows_concurrent_shared(fake_project):
    """Two shared holders can acquire the lock simultaneously."""
    working_dir, _ = fake_project
    acquired_a = threading.Event()
    acquired_b = threading.Event()
    release = threading.Event()

    def thread_shared(acq_evt: threading.Event):
        with SessionLock("myuuid", working_dir, "shared"):
            acq_evt.set()
            release.wait(timeout=2)

    t_a = threading.Thread(target=thread_shared, args=(acquired_a,))
    t_b = threading.Thread(target=thread_shared, args=(acquired_b,))
    t_a.start()
    t_b.start()
    # Both should acquire concurrently
    acquired_a.wait(timeout=1)
    acquired_b.wait(timeout=1)
    assert acquired_a.is_set()
    assert acquired_b.is_set()
    release.set()
    t_a.join(timeout=2)
    t_b.join(timeout=2)


def test_session_lock_idempotent_release(fake_project):
    """A second __exit__ call after release is a no-op (no error)."""
    working_dir, _ = fake_project
    lock = SessionLock("myuuid", working_dir, "exclusive")
    lock.__enter__()
    lock.__exit__(None, None, None)
    # Should not raise
    lock.__exit__(None, None, None)


def test_session_lock_releases_on_exception(fake_project):
    """Lock is released even if the body raises."""
    working_dir, _ = fake_project
    raised = False
    try:
        with SessionLock("myuuid", working_dir, "exclusive"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised
    # Lock should be released — a new acquisition should succeed immediately.
    with SessionLock("myuuid", working_dir, "exclusive"):
        pass


def test_session_lock_uses_dedicated_lock_file_not_jsonl(fake_project):
    """The lock target is .lock, not .jsonl."""
    working_dir, sessions_dir = fake_project
    with SessionLock("myuuid", working_dir, "exclusive"):
        pass
    assert (sessions_dir / "myuuid.lock").exists()
    assert not (sessions_dir / "myuuid.jsonl").exists()


def test_session_lock_lives_in_project_sessions_dir(fake_project):
    """The lock file is in the per-project Claude sessions directory."""
    working_dir, sessions_dir = fake_project
    with SessionLock("myuuid", working_dir, "exclusive") as lock:
        assert lock.lock_path.parent == sessions_dir
