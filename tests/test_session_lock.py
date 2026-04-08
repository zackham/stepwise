"""Tests for stepwise.session_lock — fcntl.flock-based session locking."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import stepwise.session_lock as lock_mod
import stepwise.snapshot as snapshot_mod
from stepwise.session_lock import SessionLock


@pytest.fixture
def fake_sessions_dir(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(snapshot_mod, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(lock_mod, "SESSIONS_DIR", sessions)
    return sessions


def test_session_lock_creates_lockfile_on_first_acquire(fake_sessions_dir):
    lock_path = fake_sessions_dir / "myuuid.lock"
    assert not lock_path.exists()
    with SessionLock("myuuid", "exclusive"):
        assert lock_path.exists()
    # Lock file remains after release (cleanup is deferred).
    assert lock_path.exists()


def test_session_lock_exclusive_blocks_second_exclusive(fake_sessions_dir):
    """A second exclusive holder must wait until the first releases."""
    acquired_b = threading.Event()
    release_a = threading.Event()
    started_b = threading.Event()

    def thread_a():
        with SessionLock("myuuid", "exclusive"):
            release_a.wait(timeout=2)

    def thread_b():
        started_b.set()
        with SessionLock("myuuid", "exclusive"):
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


def test_session_lock_shared_allows_concurrent_shared(fake_sessions_dir):
    """Two shared holders can acquire the lock simultaneously."""
    acquired_a = threading.Event()
    acquired_b = threading.Event()
    release = threading.Event()

    def thread_shared(acq_evt: threading.Event):
        with SessionLock("myuuid", "shared"):
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


def test_session_lock_idempotent_release(fake_sessions_dir):
    """A second __exit__ call after release is a no-op (no error)."""
    lock = SessionLock("myuuid", "exclusive")
    lock.__enter__()
    lock.__exit__(None, None, None)
    # Should not raise
    lock.__exit__(None, None, None)


def test_session_lock_releases_on_exception(fake_sessions_dir):
    """Lock is released even if the body raises."""
    raised = False
    try:
        with SessionLock("myuuid", "exclusive"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised
    # Lock should be released — a new acquisition should succeed immediately.
    with SessionLock("myuuid", "exclusive"):
        pass


def test_session_lock_uses_dedicated_lock_file_not_json(fake_sessions_dir):
    """The lock target is .lock, not .json."""
    with SessionLock("myuuid", "exclusive"):
        pass
    assert (fake_sessions_dir / "myuuid.lock").exists()
    assert not (fake_sessions_dir / "myuuid.json").exists()
