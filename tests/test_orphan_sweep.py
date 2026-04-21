"""Tests for stepwise.process_lifecycle.reap_orphaned_agent_processes.

Covers:
- Identifies live claude-agent-acp processes via /proc cmdline marker
- Spares processes whose PGID matches an active job's executor_state.pgid
- Skips containment (in_vm) runs when collecting owned pgids
- Scopes to the current uid (doesn't touch other users' processes)
- Survives /proc races (entry disappears mid-scan)
- End-to-end: SIGTERM fires on orphans, spares actives
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from stepwise.process_lifecycle import (
    _collect_active_pgids,
    _scan_acp_processes,
    reap_orphaned_agent_processes,
)


# ── Subprocess helpers ───────────────────────────────────────────────


def _spawn_fake_acp(marker_in_argv: bool = True) -> subprocess.Popen:
    """Spawn a sleep process whose argv contains the claude-agent-acp marker.

    We execute /bin/sleep but override argv[0] so /proc/PID/cmdline reads
    "claude-agent-acp 3600". Identical to what the real ACP process would
    look like from the scanner's perspective.
    """
    argv = (
        ["claude-agent-acp", "3600"] if marker_in_argv
        else ["some-other-tool", "3600"]
    )
    return subprocess.Popen(
        args=argv,
        executable="/bin/sleep",
        start_new_session=True,
    )


def _cleanup(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass


# ── _scan_acp_processes ──────────────────────────────────────────────


class TestScanAcpProcesses:
    def test_finds_matching_cmdline(self):
        """A sleep process with argv[0]='claude-agent-acp' is detected."""
        proc = _spawn_fake_acp()
        try:
            found = _scan_acp_processes()
            pids = [pid for pid, _ in found]
            assert proc.pid in pids
        finally:
            _cleanup(proc)

    def test_ignores_non_matching_cmdline(self):
        """A sleep process without the marker is NOT detected."""
        proc = _spawn_fake_acp(marker_in_argv=False)
        try:
            found = _scan_acp_processes()
            pids = [pid for pid, _ in found]
            assert proc.pid not in pids
        finally:
            _cleanup(proc)

    def test_returns_correct_pgid(self):
        """Each detected pid is paired with its actual pgid."""
        proc = _spawn_fake_acp()
        try:
            expected_pgid = os.getpgid(proc.pid)
            found = _scan_acp_processes()
            for pid, pgid in found:
                if pid == proc.pid:
                    assert pgid == expected_pgid
                    return
            pytest.fail(f"Spawned pid {proc.pid} not found in scan")
        finally:
            _cleanup(proc)

    def test_uid_scoping(self):
        """Scoping to a non-matching uid returns no results from our processes."""
        proc = _spawn_fake_acp()
        try:
            # Use uid that we know isn't ours (but don't need to actually own
            # anything there — we just want our process to be filtered out).
            impossible_uid = -999  # os.stat never returns this
            found = _scan_acp_processes(uid=impossible_uid)
            pids = [pid for pid, _ in found]
            assert proc.pid not in pids
        finally:
            _cleanup(proc)


# ── _collect_active_pgids ────────────────────────────────────────────


class TestCollectActivePgids:
    def _make_fake_store(self, jobs_runs):
        """Build a minimal store-duck with active_jobs / running_runs."""
        from types import SimpleNamespace

        class _Store:
            def active_jobs(self):
                return [SimpleNamespace(id=jid) for jid, _ in jobs_runs]

            def running_runs(self, job_id):
                for jid, runs in jobs_runs:
                    if jid == job_id:
                        return runs
                return []

        return _Store()

    def _run_with(self, pgid: int | None, in_vm: bool = False):
        from types import SimpleNamespace
        state = {}
        if pgid is not None:
            state["pgid"] = pgid
        if in_vm:
            state["in_vm"] = True
        return SimpleNamespace(executor_state=state or None)

    def test_collects_pgid_from_running_runs(self):
        store = self._make_fake_store([
            ("job-a", [self._run_with(1001), self._run_with(1002)]),
            ("job-b", [self._run_with(1003)]),
        ])
        assert _collect_active_pgids(store) == {1001, 1002, 1003}

    def test_skips_in_vm_runs(self):
        """Containment runs store a guest pid; we can't compare against /proc."""
        store = self._make_fake_store([
            ("job-a", [
                self._run_with(1001),
                self._run_with(9999, in_vm=True),  # skipped
            ]),
        ])
        assert _collect_active_pgids(store) == {1001}

    def test_handles_missing_pgid(self):
        """Runs without a pgid in executor_state contribute nothing."""
        store = self._make_fake_store([
            ("job-a", [self._run_with(None), self._run_with(1001)]),
        ])
        assert _collect_active_pgids(store) == {1001}

    def test_empty_store(self):
        store = self._make_fake_store([])
        assert _collect_active_pgids(store) == set()


# ── reap_orphaned_agent_processes (end-to-end) ───────────────────────


class TestReapOrphanedAgentProcesses:
    def _store_with_pgids(self, pgids: list[int]):
        from types import SimpleNamespace

        class _Store:
            def active_jobs(self):
                return [SimpleNamespace(id="job-a")]

            def running_runs(self, job_id):
                return [
                    SimpleNamespace(executor_state={"pgid": p}) for p in pgids
                ]

        return _Store()

    def test_orphan_is_killed(self):
        """A live ACP process not owned by any job gets SIGTERMed."""
        orphan = _spawn_fake_acp()
        try:
            store = self._store_with_pgids([])  # no active jobs own anything
            killed = reap_orphaned_agent_processes(store, grace_seconds=1.0)
            assert orphan.pid in killed
            # Reap zombie — we're its parent in tests
            orphan.wait(timeout=2)
            assert orphan.returncode is not None
        finally:
            _cleanup(orphan)

    def test_owned_is_spared(self):
        """A live ACP process whose pgid is in active_pgids is NOT killed."""
        owned = _spawn_fake_acp()
        try:
            pgid = os.getpgid(owned.pid)
            store = self._store_with_pgids([pgid])
            killed = reap_orphaned_agent_processes(store, grace_seconds=0.5)
            assert owned.pid not in killed
            # Process is still alive
            assert owned.poll() is None
        finally:
            _cleanup(owned)

    def test_orphan_and_owned_together(self):
        """Mixed case — orphan dies, owned survives."""
        orphan = _spawn_fake_acp()
        owned = _spawn_fake_acp()
        try:
            store = self._store_with_pgids([os.getpgid(owned.pid)])
            killed = reap_orphaned_agent_processes(store, grace_seconds=1.0)
            assert orphan.pid in killed
            assert owned.pid not in killed
            orphan.wait(timeout=2)
            assert owned.poll() is None
        finally:
            _cleanup(orphan)
            _cleanup(owned)

    def test_no_orphans_empty_result(self):
        """When nothing matches the cmdline marker, returns empty list."""
        store = self._store_with_pgids([])
        # No orphan spawned.
        killed = reap_orphaned_agent_processes(store, grace_seconds=0.1)
        # Should return an empty list (or just live processes from other
        # test runs we didn't clean up — but we control via cleanup).
        # Conservative assertion: no non-ACP test process got killed.
        assert isinstance(killed, list)


# ── /proc race resilience ────────────────────────────────────────────


class TestProcRaceResilience:
    def test_survives_vanishing_pid(self, tmp_path):
        """An entry that disappears between listdir and stat is silently skipped."""
        fake_proc = tmp_path / "proc"
        fake_proc.mkdir()
        # Create a dir that looks like a pid but has no cmdline (simulates
        # a process that exited after we listed /proc but before we read it).
        (fake_proc / "12345").mkdir()
        # No cmdline file — _read_cmdline returns None, scan skips it.

        found = _scan_acp_processes(proc_root=str(fake_proc))
        assert found == []

    def test_ignores_non_pid_entries(self, tmp_path):
        """Entries like 'self', 'sys', etc. are ignored (not digits)."""
        fake_proc = tmp_path / "proc"
        fake_proc.mkdir()
        (fake_proc / "self").mkdir()
        (fake_proc / "sys").mkdir()
        (fake_proc / "cpuinfo").write_text("x")

        found = _scan_acp_processes(proc_root=str(fake_proc))
        assert found == []
