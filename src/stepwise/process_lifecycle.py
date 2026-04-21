"""Runner process lifecycle management: kill on pause/cancel, zombie reaping.

Provides direct PID-based process termination (bypassing executor abstractions)
and periodic health checks for detecting dead/expired runner processes.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("stepwise.process_lifecycle")

# Default TTL for agent processes: 0 = disabled (no limit).
# Agent steps can run as long as they need. Use per-step
# limits.max_duration_minutes in FLOW.yaml for intentional timeouts.
# Override globally via config (agent_process_ttl) or env (STEPWISE_AGENT_TTL).
DEFAULT_AGENT_TTL_SECONDS = 0

# Health check interval: 15 seconds (aggressive to catch zombie steps quickly)
HEALTH_CHECK_INTERVAL_SECONDS = 15


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive (not zombie, not dead).

    Tries os.waitpid() first to reap zombies if we're the parent,
    then falls back to os.kill(pid, 0).
    """
    # Try to reap zombie if we're the parent
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            return False  # reaped zombie — process is dead
    except ChildProcessError:
        pass  # not our child — use kill(0)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Alive but can't signal

    # On Linux, check /proc/{pid}/status for zombie state
    try:
        status_path = f"/proc/{pid}/status"
        with open(status_path) as f:
            for line in f:
                if line.startswith("State:"):
                    return "Z" not in line  # Z = zombie
    except (FileNotFoundError, PermissionError):
        pass

    return True


def _kill_process_group(pgid: int, sig: int) -> bool:
    """Send signal to a process group. Returns True if signal was sent."""
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_pid(pid: int, sig: int) -> bool:
    """Send signal to a single process. Returns True if signal was sent."""
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_run_process(
    pid: int | None,
    pgid: int | None,
    grace_seconds: float = 0,
    run_id: str = "",
    step_name: str = "",
) -> bool:
    """Kill a runner process by PID/PGID with optional SIGKILL follow-up.

    Args:
        pid: Process ID of the runner.
        pgid: Process group ID (preferred — kills all children).
        grace_seconds: If > 0, wait this long after SIGTERM then SIGKILL
                       if still alive. If 0, SIGTERM only.
        run_id: For logging.
        step_name: For logging.

    Returns:
        True if the process was confirmed dead after signaling.
    """
    if not pid and not pgid:
        logger.warning(
            "kill_run_process called with no pid/pgid (run=%s step=%s)",
            run_id, step_name,
        )
        return False

    label = f"run={run_id} step={step_name}" if run_id else f"pid={pid}"

    # SIGTERM — prefer process group to kill children too
    sent = False
    if pgid:
        sent = _kill_process_group(pgid, signal.SIGTERM)
    if not sent and pid:
        sent = _kill_pid(pid, signal.SIGTERM)

    if not sent:
        logger.debug("Process already dead before SIGTERM (%s)", label)
        return True

    logger.info("Sent SIGTERM to runner process (%s, pid=%s pgid=%s)", label, pid, pgid)

    if grace_seconds <= 0:
        # No SIGKILL follow-up — just verify after brief wait
        time.sleep(0.2)
        check_pid = pid or pgid
        alive = _is_pid_alive(check_pid) if check_pid else False
        if alive:
            logger.warning("Process still alive after SIGTERM (%s)", label)
        return not alive

    # Wait for graceful shutdown, then SIGKILL
    deadline = time.monotonic() + grace_seconds
    check_pid = pid or pgid
    while time.monotonic() < deadline:
        if check_pid and not _is_pid_alive(check_pid):
            logger.info("Process died after SIGTERM (%s)", label)
            return True
        time.sleep(0.5)

    # Still alive — SIGKILL
    killed = False
    if pgid:
        killed = _kill_process_group(pgid, signal.SIGKILL)
    if not killed and pid:
        killed = _kill_pid(pid, signal.SIGKILL)

    if killed:
        logger.info("Sent SIGKILL to runner process (%s)", label)
    else:
        logger.debug("Process already dead before SIGKILL (%s)", label)

    # Final verification
    time.sleep(0.2)
    alive = _is_pid_alive(check_pid) if check_pid else False
    if alive:
        logger.error("Process STILL alive after SIGKILL (%s) — cannot reap", label)
    return not alive


def kill_job_processes(
    runs: list,
    grace_seconds: float = 0,
) -> list[str]:
    """Kill runner processes for a list of StepRuns.

    Args:
        runs: StepRun objects with pid, executor_state fields.
        grace_seconds: Grace period before SIGKILL (0 = SIGTERM only).

    Returns:
        List of run IDs where the process was successfully killed.
    """
    killed_run_ids = []
    for run in runs:
        pid = run.pid
        pgid = None
        if run.executor_state:
            pgid = run.executor_state.get("pgid")
            if not pid:
                pid = run.executor_state.get("pid")

        if not pid and not pgid:
            continue

        # Check if still alive before signaling
        check_pid = pid or pgid
        if not _is_pid_alive(check_pid):
            logger.debug(
                "Process already dead for run %s step %s (pid=%s)",
                run.id, run.step_name, check_pid,
            )
            killed_run_ids.append(run.id)
            continue

        dead = kill_run_process(
            pid=pid,
            pgid=pgid,
            grace_seconds=grace_seconds,
            run_id=run.id,
            step_name=run.step_name,
        )
        if dead:
            killed_run_ids.append(run.id)

    return killed_run_ids


@dataclass
class ReapResult:
    """Result of a process health check cycle."""
    dead_cleaned: list[str] = field(default_factory=list)    # run IDs with dead PIDs cleaned up
    expired_killed: list[str] = field(default_factory=list)  # run IDs killed due to TTL
    errors: list[str] = field(default_factory=list)          # error messages


def reap_dead_processes(store, engine) -> list[str]:
    """Detect RUNNING step runs whose process is dead and fail them.

    Args:
        store: SQLiteStore with running runs.
        engine: Engine instance for _fail_run / emit.

    Returns:
        List of run IDs that were cleaned up.
    """
    cleaned = []
    for job in store.active_jobs():
        for run in store.running_runs(job.id):
            pid = run.pid
            if not pid:
                if run.executor_state:
                    pid = run.executor_state.get("pid")
            if not pid:
                continue

            # Skip runs whose executor is living inside a containment
            # VM. The recorded `pid` in that case is a GUEST pid that
            # cannot be looked up on the host, so `os.kill(pid, 0)`
            # would always raise ProcessLookupError and we'd
            # falsely mark every containment run dead on every tick.
            # ACPBackend sets `in_vm: True` in executor_state when
            # spawning through VMSpawnContext.
            if run.executor_state and run.executor_state.get("in_vm"):
                continue

            if _is_pid_alive(pid):
                continue

            # PID is dead but run is still RUNNING — clean up
            logger.warning(
                "Dead process detected: run=%s step=%s job=%s pid=%d — failing run",
                run.id, run.step_name, job.id, pid,
            )
            from stepwise.models import StepRunStatus, _now
            run.status = StepRunStatus.FAILED
            run.error = f"Runner process died (PID {pid} no longer alive)"
            run.pid = None
            run.completed_at = _now()
            store.save_run(run)
            cleaned.append(run.id)

    return cleaned


def reap_expired_processes(
    store,
    ttl_seconds: int = DEFAULT_AGENT_TTL_SECONDS,
) -> list[str]:
    """Detect and kill RUNNING step runs that have exceeded TTL.

    Only targets agent-type executors (which are long-running subprocesses).

    Args:
        store: SQLiteStore with running runs.
        ttl_seconds: Maximum allowed runtime in seconds (default 2h).

    Returns:
        List of run IDs that were killed.
    """
    from stepwise.models import StepRunStatus, _now

    if ttl_seconds <= 0:
        return []  # TTL disabled — agent steps run without time limit

    now = datetime.now(timezone.utc)
    killed = []

    for job in store.active_jobs():
        for run in store.running_runs(job.id):
            if not run.started_at:
                continue

            age_seconds = (now - run.started_at).total_seconds()
            if age_seconds < ttl_seconds:
                continue

            # Containment-VM runs: the stored pid is a guest pid that
            # can't be looked up or killed from the host. TTL-killing
            # them requires routing through vmmd.destroy_vm, which is
            # a deliberate operation the flow-level `limits` already
            # handles. Skip here to avoid false-positive "already dead"
            # failures against live guest processes.
            if run.executor_state and run.executor_state.get("in_vm"):
                continue

            pid = run.pid
            pgid = None
            if run.executor_state:
                pgid = run.executor_state.get("pgid")
                if not pid:
                    pid = run.executor_state.get("pid")

            if not pid and not pgid:
                continue

            check_pid = pid or pgid
            if not _is_pid_alive(check_pid):
                # Already dead — just clean up the run
                logger.info(
                    "Expired run %s (step=%s, age=%.0fs) — process already dead, cleaning up",
                    run.id, run.step_name, age_seconds,
                )
                run.status = StepRunStatus.FAILED
                run.error = f"Runner process expired (TTL {ttl_seconds}s, ran for {age_seconds:.0f}s)"
                run.pid = None
                run.completed_at = _now()
                store.save_run(run)
                killed.append(run.id)
                continue

            logger.warning(
                "Killing expired runner: run=%s step=%s job=%s pid=%s age=%.0fs (TTL=%ds)",
                run.id, run.step_name, job.id, pid, age_seconds, ttl_seconds,
            )

            dead = kill_run_process(
                pid=pid,
                pgid=pgid,
                grace_seconds=5,
                run_id=run.id,
                step_name=run.step_name,
            )

            run.status = StepRunStatus.FAILED
            run.error = f"Runner process expired (TTL {ttl_seconds}s, ran for {age_seconds:.0f}s)"
            run.pid = None
            run.completed_at = _now()
            store.save_run(run)
            killed.append(run.id)

            if not dead:
                logger.error(
                    "Failed to kill expired runner pid=%s for run %s", pid, run.id,
                )

    return killed


def run_health_check(
    store,
    ttl_seconds: int = DEFAULT_AGENT_TTL_SECONDS,
) -> ReapResult:
    """Combined health check: reap dead processes + kill expired ones.

    Args:
        store: SQLiteStore.
        ttl_seconds: Maximum allowed runtime for agent processes.

    Returns:
        ReapResult with lists of cleaned/killed run IDs.
    """
    result = ReapResult()

    try:
        result.dead_cleaned = reap_dead_processes(store, engine=None)
    except Exception as e:
        logger.error("Error in reap_dead_processes: %s", e, exc_info=True)
        result.errors.append(f"reap_dead: {e}")

    try:
        result.expired_killed = reap_expired_processes(store, ttl_seconds=ttl_seconds)
    except Exception as e:
        logger.error("Error in reap_expired_processes: %s", e, exc_info=True)
        result.errors.append(f"reap_expired: {e}")

    total = len(result.dead_cleaned) + len(result.expired_killed)
    if total > 0:
        logger.info(
            "Health check: %d dead cleaned, %d expired killed",
            len(result.dead_cleaned), len(result.expired_killed),
        )

    return result


# ── Startup orphan sweep ─────────────────────────────────────────────


# cmdline marker used to identify ACP agent processes. Matches both the
# npm exec wrapper and the node invocation (both carry the string in
# argv), but does NOT match the standalone `claude` CLI (the Claude
# Code binary used for interactive sessions) — that has a different
# cmdline entirely.
_ACP_CMDLINE_MARKER = "claude-agent-acp"


def _iter_proc_pids(proc_root: str = "/proc"):
    """Yield integer pids from /proc. Silently skips non-pid entries."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        yield int(entry)


def _read_cmdline(pid: int, proc_root: str = "/proc") -> str | None:
    """Read /proc/{pid}/cmdline as a space-joined string. None on failure."""
    try:
        with open(f"{proc_root}/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")


def _proc_uid(pid: int, proc_root: str = "/proc") -> int | None:
    """Return the effective UID of a pid via /proc stat. None on failure."""
    try:
        st = os.stat(f"{proc_root}/{pid}")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    return st.st_uid


def _scan_acp_processes(
    proc_root: str = "/proc",
    uid: int | None = None,
    marker: str = _ACP_CMDLINE_MARKER,
) -> list[tuple[int, int]]:
    """Return (pid, pgid) tuples for live claude-agent-acp processes owned by `uid`.

    `uid` defaults to the current process's uid. Pids whose /proc entry
    disappears mid-scan are silently skipped.
    """
    if uid is None:
        uid = os.getuid()

    found: list[tuple[int, int]] = []
    for pid in _iter_proc_pids(proc_root):
        if _proc_uid(pid, proc_root) != uid:
            continue
        cmdline = _read_cmdline(pid, proc_root)
        if cmdline is None or marker not in cmdline:
            continue
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            continue
        found.append((pid, pgid))
    return found


def _collect_active_pgids(store) -> set[int]:
    """Gather PGIDs owned by non-terminal jobs' running step_runs.

    Walks store.active_jobs() and reads executor_state.pgid off each
    running run. Containment (in_vm) runs are skipped — their pgid is a
    guest pid that can't be compared to host /proc entries, and VMs are
    reaped by vmmd rather than by this sweep.
    """
    owned: set[int] = set()
    for job in store.active_jobs():
        for run in store.running_runs(job.id):
            state = run.executor_state or {}
            if state.get("in_vm"):
                continue
            pgid = state.get("pgid")
            if pgid:
                try:
                    owned.add(int(pgid))
                except (TypeError, ValueError):
                    continue
    return owned


def reap_orphaned_agent_processes(
    store,
    grace_seconds: float = 5.0,
    proc_root: str = "/proc",
) -> list[int]:
    """Startup sweep: SIGTERM claude-agent-acp processes not owned by any active job.

    Closes the gap where agents spawned by a previous server incarnation
    survive into a new one (server SIGKILL, upgrade, crash) and reparent
    to systemd --user. Scans /proc for live ACP processes owned by the
    current uid, intersects with executor_state.pgid of active jobs, and
    SIGTERMs the rest.

    SIGTERMs by PGID so the whole npm-exec/node-adapter tree goes down
    together. Duplicate PGIDs are deduped (one SIGTERM per group). After
    the grace period, PGIDs still alive are SIGKILLed.

    Returns the list of PIDs observed as orphans (for logging). Safe to
    run at any time, but intended for server startup — during normal
    operation, release_for_job handles cleanup on job-terminal.
    """
    active = _collect_active_pgids(store)
    live = _scan_acp_processes(proc_root=proc_root)

    # Group by pgid — one SIGTERM per group is enough.
    orphan_pgids: set[int] = set()
    orphan_pids: list[int] = []
    for pid, pgid in live:
        if pgid in active:
            continue
        orphan_pgids.add(pgid)
        orphan_pids.append(pid)

    if not orphan_pgids:
        return []

    logger.warning(
        "Startup sweep: found %d orphan claude-agent-acp process(es) across "
        "%d process group(s) — SIGTERM",
        len(orphan_pids), len(orphan_pgids),
    )

    for pgid in orphan_pgids:
        _kill_process_group(pgid, signal.SIGTERM)

    if grace_seconds > 0:
        time.sleep(grace_seconds)

    # Anything still alive → SIGKILL
    still_alive = [p for p in orphan_pgids if _is_pid_alive(p)]
    for pgid in still_alive:
        logger.warning(
            "Startup sweep: pgid %d survived SIGTERM, sending SIGKILL", pgid,
        )
        _kill_process_group(pgid, signal.SIGKILL)

    return orphan_pids
