"""AgentExecutor: Long-running agent sessions as async steps.

Wraps ACP-compatible agents (via acpx) as Stepwise executors.
The engine polls check_status() each tick; the executor manages the subprocess.

Protocol: Agent Client Protocol (ACP) — https://agentclientprotocol.com
Client: acpx — headless CLI client for ACP agents
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Protocol

logger = logging.getLogger("stepwise.agent")

from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
    _USAGE_RESET_RE,
    classify_api_error,
    parse_usage_reset_time,
)
from stepwise.models import HandoffEnvelope, Sidecar, SubJobDefinition, _now


EMIT_FLOW_FILENAME = "emit.flow.yaml"
EMIT_FLOW_DIR = ".stepwise"
ACPX_QUEUES_DIR = os.path.expanduser("~/.acpx/queues")


def _detect_usage_limit_in_line(line: str, parse_json: bool) -> str | None:
    """Check a single line for usage limit patterns.

    parse_json=True for NDJSON stdout, False for plain stderr.
    Returns the matching message string, or None.
    """
    if parse_json:
        try:
            data = json.loads(line)
            error = data.get("error", {})
            if isinstance(error, dict):
                msg = error.get("message", "")
                if _USAGE_RESET_RE.search(msg):
                    return msg
            params = data.get("params", {})
            update = params.get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                text = update.get("content", {}).get("text", "")
                if _USAGE_RESET_RE.search(text):
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
    else:
        if _USAGE_RESET_RE.search(line):
            return line.strip()
    return None


# ── Agent Backend Protocol ────────────────────────────────────────────


@dataclass
class AgentProcess:
    """Handle to a running agent process."""
    pid: int
    pgid: int
    output_path: str
    working_dir: str
    session_id: str | None = None
    session_name: str | None = None
    capture_transcript: bool = True
    agent: str | None = None


@dataclass
class AgentStatus:
    """Status from checking an agent process."""
    state: str  # "running" | "completed" | "failed"
    exit_code: int | None = None
    session_id: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    result: dict | None = None  # Artifact data from completed agent


class AgentBackend(Protocol):
    """Transport abstraction for agent execution."""

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        ...

    def wait(self, process: AgentProcess, on_usage_limit=None) -> AgentStatus:
        """Block until the agent process exits. Returns final status."""
        ...

    def check(self, process: AgentProcess) -> AgentStatus:
        ...

    def cancel(self, process: AgentProcess) -> None:
        ...

    @property
    def supports_resume(self) -> bool:
        ...


# ── Queue Owner Detection & Cleanup ───────────────────────────────────


@dataclass
class QueueOwnerInfo:
    """Parsed info from an acpx queue lock file."""
    pid: int
    session_id: str
    lock_path: str
    created_at: str = ""


def _parse_queue_lock(lock_path: str) -> QueueOwnerInfo | None:
    """Parse an acpx queue lock file."""
    try:
        with open(lock_path) as f:
            data = json.loads(f.read())
        return QueueOwnerInfo(
            pid=data["pid"],
            session_id=data["sessionId"],
            lock_path=lock_path,
            created_at=data.get("createdAt", ""),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _find_queue_owners() -> list[QueueOwnerInfo]:
    """Scan ~/.acpx/queues/ for queue owner lock files."""
    results = []
    if not os.path.isdir(ACPX_QUEUES_DIR):
        return results
    for filename in os.listdir(ACPX_QUEUES_DIR):
        if not filename.endswith(".lock"):
            continue
        info = _parse_queue_lock(os.path.join(ACPX_QUEUES_DIR, filename))
        if info is not None:
            results.append(info)
    return results


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Alive but can't signal


def find_orphaned_queue_owners(
    active_session_ids: set[str] | None = None,
    active_pids: set[int] | None = None,
) -> list[QueueOwnerInfo]:
    """Find acpx queue owner processes not associated with any active session.

    Args:
        active_session_ids: ACP session IDs/names currently in use by running steps.
        active_pids: PIDs/PGIDs of running agent steps. Queue owners in the same
            process group as an active step are protected.

    Returns:
        List of orphaned QueueOwnerInfo entries (alive PID, not in active set).
    """
    orphaned = []
    for info in _find_queue_owners():
        if not _is_pid_alive(info.pid):
            continue
        # Protected by session ID match
        if active_session_ids is not None and info.session_id in active_session_ids:
            continue
        # Protected by PID/PGID match — the queue owner's process group
        # may contain an active step's PID
        if active_pids is not None:
            pgid = _get_process_pgid(info.pid)
            if pgid is not None and pgid in active_pids:
                continue
            if info.pid in active_pids:
                continue
        orphaned.append(info)
    return orphaned


def cleanup_orphaned_queue_owners(
    active_session_ids: set[str] | None = None,
    active_pids: set[int] | None = None,
    kill: bool = False,
) -> list[QueueOwnerInfo]:
    """Detect and optionally terminate orphaned acpx queue owner processes.

    Args:
        active_session_ids: ACP session IDs currently in use by running steps.
        active_pids: PIDs/PGIDs of running agent steps.
        kill: If True, SIGTERM orphaned processes. If False, log only.

    Returns:
        List of orphaned queue owner info entries.
    """
    orphaned = find_orphaned_queue_owners(active_session_ids, active_pids)
    for info in orphaned:
        if kill:
            try:
                os.kill(info.pid, signal.SIGTERM)
                logger.info(
                    "Terminated orphaned acpx queue owner: pid=%d session=%s created=%s",
                    info.pid, info.session_id, info.created_at,
                )
            except (ProcessLookupError, PermissionError):
                logger.debug("Could not terminate queue owner pid=%d", info.pid)
        else:
            logger.warning(
                "Orphaned acpx queue owner detected: pid=%d session=%s created=%s",
                info.pid, info.session_id, info.created_at,
            )
    return orphaned


def _scan_queue_owner_pids() -> list[tuple[int, str]]:
    """Scan /proc for acpx queue owner processes.

    Returns list of (pid, cmdline_summary) for processes with __queue-owner in cmdline.
    Falls back to empty list on non-Linux systems.
    """
    results = []
    proc = Path("/proc")
    if not proc.is_dir():
        return results
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace")
            if "__queue-owner" in cmdline:
                summary = cmdline.replace("\x00", " ").strip()
                results.append((int(entry.name), summary))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return results


def find_zombie_queue_owners(
    active_session_ids: set[str] | None = None,
    active_pids: set[int] | None = None,
) -> list[tuple[int, str]]:
    """Find acpx queue owner processes not associated with any active session.

    Uses process-table scanning (/proc) to find __queue-owner processes,
    then cross-references with lock files and active PIDs to determine
    which are legitimately active.

    Args:
        active_session_ids: Session IDs/names currently in use by running steps.
        active_pids: PIDs/PGIDs of running agent steps.

    Returns:
        List of (pid, cmdline_summary) for orphaned queue owner processes.
    """
    # Map session_id → pid from lock files for active sessions
    protected_pids: set[int] = set()
    if active_session_ids:
        for info in _find_queue_owners():
            if info.session_id in active_session_ids:
                protected_pids.add(info.pid)
    if active_pids:
        protected_pids.update(active_pids)

    # Scan process table for all queue owner processes
    all_owners = _scan_queue_owner_pids()

    # Filter out those associated with active sessions or active step PIDs
    result = []
    for pid, cmd in all_owners:
        if pid in protected_pids:
            continue
        # Also check process group — queue owner may share PGID with active step
        pgid = _get_process_pgid(pid)
        if pgid is not None and active_pids and pgid in active_pids:
            continue
        result.append((pid, cmd))
    return result


def verify_agent_pid(pid: int, expected_pgid: int | None = None) -> bool:
    """Verify a PID belongs to an acpx/claude-agent process.

    Checks /proc/{pid}/cmdline for known agent markers (same set as
    _scan_acpx_processes: "acpx", "claude-agent-acp", "__queue-owner")
    and optionally validates PGID matches expected_pgid via /proc/{pid}/stat.

    Returns False if: process dead, cmdline doesn't match, PGID mismatch.
    Falls back to os.kill(pid, 0) on non-Linux (no cmdline check).
    """
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.parent.is_dir():
        # Non-Linux fallback: can only detect dead PIDs, not identity
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    try:
        cmdline = proc_cmdline.read_bytes().decode("utf-8", errors="replace")
    except (FileNotFoundError, PermissionError):
        return False  # dead or inaccessible

    if not ("acpx" in cmdline or "claude-agent-acp" in cmdline
            or "__queue-owner" in cmdline):
        return False  # PID recycled into unrelated process

    if expected_pgid is not None:
        actual_pgid = _get_process_pgid(pid)
        if actual_pgid is not None and actual_pgid != expected_pgid:
            return False  # PID recycled into different process group

    return True


def _scan_acpx_processes() -> list[tuple[int, str]]:
    """Scan /proc for acpx-related processes (queue owners and claude agents).

    Returns list of (pid, cmdline_summary) for processes matching
    "claude-agent-acp" or "__queue-owner" in cmdline.
    Falls back to empty list on non-Linux systems.
    """
    results = []
    proc = Path("/proc")
    if not proc.is_dir():
        return results
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace")
            if "claude-agent-acp" in cmdline or "__queue-owner" in cmdline:
                summary = cmdline.replace("\x00", " ").strip()
                results.append((int(entry.name), summary))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return results


def _get_process_pgid(pid: int) -> int | None:
    """Get the process group ID for a given PID via /proc."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # /proc/<pid>/stat format: pid (comm) state ppid pgrp ...
        # pgrp is field 5 (0-indexed after splitting)
        parts = stat.rsplit(")", 1)[-1].split()
        return int(parts[2])  # state=0, ppid=1, pgrp=2
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return None


# Queue owners use setsid; protected via session-ID matching in periodic cleanup
def cleanup_orphaned_acpx(active_pids: set[int] | None = None) -> list[tuple[int, str]]:
    """Detect and terminate orphaned acpx/claude processes.

    Scans for processes matching "claude-agent-acp" or "acpx.*__queue-owner",
    cross-references their process group IDs with PIDs from running steps,
    and kills any that are orphaned.

    Detached queue owners (setsid) are protected via session-ID matching against active_pids.

    Args:
        active_pids: PIDs from currently running step_runs (executor_state.pid).
            Processes whose pgid matches any active PID are considered active.
            If None, all matched processes are considered orphaned.

    Returns:
        List of (pid, cmdline_summary) for orphaned processes that were killed.
    """
    all_procs = _scan_acpx_processes()
    if not all_procs:
        return []

    active_pids = active_pids or set()
    killed = []

    for pid, cmdline in all_procs:
        # A process belongs to a running step if its pgid matches any active step PID
        # (acpx spawns with start_new_session=True, so children share pgid = acpx pid)
        pgid = _get_process_pgid(pid)
        if pgid is not None and pgid in active_pids:
            continue
        # Also check if the process itself is an active step PID
        if pid in active_pids:
            continue

        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(
                "Terminated orphaned acpx process: pid=%d pgid=%s cmd=%s",
                pid, pgid, cmdline[:120],
            )
            killed.append((pid, cmdline))
        except (ProcessLookupError, PermissionError):
            logger.debug("Could not terminate orphaned process pid=%d", pid)

    if killed:
        logger.info("Cleaned up %d orphaned acpx process(es)", len(killed))

    return killed


# ── Agent environment builder ─────────────────────────────────────


def _build_agent_env(
    config: dict,
    context: ExecutionContext,
    step_io: Path,
    working_dir: str,
) -> dict[str, str]:
    """Build environment variables for an agent subprocess.

    Mirrors ScriptExecutor's STEPWISE_* env vars and adds
    STEPWISE_OUTPUT_FILE when the step declares outputs.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")}

    # Stepwise env vars for agent processes (parity with ScriptExecutor)
    env["STEPWISE_STEP_NAME"] = context.step_name
    env["STEPWISE_ATTEMPT"] = str(context.attempt)
    env["STEPWISE_STEP_IO"] = str(step_io)

    # Output file for structured output bridging
    output_fields = config.get("output_fields")
    if output_fields:
        output_filename = config.get("output_path") or f"{context.step_name}-output.json"
        output_file_abs = str((Path(working_dir) / output_filename).resolve())
        env["STEPWISE_OUTPUT_FILE"] = output_file_abs

    return env


# ── ACP Backend (via acpx) ──────────────────────────────────────────


class AcpxBackend:
    """Agent backend using acpx CLI to communicate via ACP protocol.

    Spawns `acpx {agent}` as a subprocess with named sessions per step.
    Supports any ACP-compatible agent (claude, codex, gemini, etc.).
    """

    def __init__(self, acpx_path: str = "acpx", default_agent: str = "claude",
                 default_permissions: str = "approve_all") -> None:
        self.acpx_path = acpx_path
        self.default_agent = default_agent
        self.default_permissions = default_permissions

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        t0 = time.monotonic()
        thread = threading.current_thread().name
        step_id = f"{context.step_name}@{context.job_id or 'local'}"
        logger.info(f"[{step_id}] spawn started (thread={thread})")

        default_dir = context.flow_source_dir or context.workspace_path
        working_dir = str(Path(config.get("working_dir", default_dir)).resolve())
        Path(working_dir).mkdir(parents=True, exist_ok=True)

        agent = config.get("agent", self.default_agent)
        # Use stable session name if provided (session continuity), else per-attempt
        # Include job_id in session name to prevent collisions when multiple jobs
        # target the same working_dir (e.g., concurrent impl-dispatch jobs)
        job_prefix = context.job_id.replace("job-", "") if context.job_id else "local"
        session_name = config.get("_session_name") or f"step-{job_prefix}-{context.step_name}-{context.attempt}"

        # Write prompt to file for acpx --file
        # Use per-job subdirectory to prevent file collisions across concurrent jobs
        step_io = Path(working_dir) / ".stepwise" / "step-io" / (context.job_id or "local")
        step_io.mkdir(parents=True, exist_ok=True)
        prompt_file = step_io / f"{context.step_name}-{context.attempt}.prompt.md"
        prompt_file.write_text(prompt)

        # Output file for NDJSON stream
        output_file = step_io / f"{context.step_name}-{context.attempt}.output.jsonl"

        env = _build_agent_env(config, context, step_io, working_dir)

        # Ensure named session exists (acpx requires it before prompting).
        # Short timeout + non-fatal: if ensure fails, acpx prompt will fail
        # with a clear error rather than blocking the thread pool for 30s.
        # Parse the acpxRecordId (UUID) from stdout — needed so the periodic
        # queue-owner cleanup can match lock files to running steps.
        t_ensure = time.monotonic()
        logger.info(f"[{step_id}] sessions ensure starting (session={session_name})")
        session_id: str | None = None
        try:
            ensure_result = subprocess.run(
                [self.acpx_path, "--cwd", working_dir,
                 agent, "sessions", "ensure", "--name", session_name],
                capture_output=True, timeout=10, env=env, text=True,
            )
            # acpx sessions ensure prints "UUID\t(created|existing)" to stdout
            raw = ensure_result.stdout.strip().split("\n")[-1].strip() if ensure_result.stdout else ""
            # Extract UUID before the tab (e.g. "263ce38b-...\t(existing)" → "263ce38b-...")
            uuid_part = raw.split("\t")[0].strip() if raw else ""
            if uuid_part and len(uuid_part) < 128:
                session_id = uuid_part
                logger.info(f"[{step_id}] resolved session_id={session_id}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning(f"[{step_id}] sessions ensure timed out or not found")
        logger.info(f"[{step_id}] sessions ensure done ({time.monotonic() - t_ensure:.1f}s)")

        # Build acpx prompt command
        permissions = config.get("permissions") or self.default_permissions
        args = [self.acpx_path, "--format", "json", "--ttl", "14400", "--cwd", working_dir]
        if permissions == "approve_all":
            args.append("--approve-all")
        elif permissions == "deny":
            args.append("--deny-all")

        timeout_sec = config.get("timeout")
        if timeout_sec:
            args.extend(["--timeout", str(timeout_sec)])

        args.extend([agent, "-s", session_name, "--file", str(prompt_file)])

        # Open output + stderr files. NOT context managers — Popen is non-blocking
        # so `with` would close fds before the subprocess writes anything.
        # The OS dups fds into the child; closing parent copies after Popen is safe.
        out_f = open(output_file, "w")
        err_file = str(output_file) + ".stderr"
        err_f = open(err_file, "w")
        proc = subprocess.Popen(
            args,
            cwd=working_dir,
            stdout=out_f,
            stderr=err_f,
            env=env,
            start_new_session=True,
        )
        out_f.close()
        err_f.close()

        logger.info(f"[{step_id}] process spawned pid={proc.pid} ({time.monotonic() - t0:.1f}s total)")

        return AgentProcess(
            pid=proc.pid,
            pgid=os.getpgid(proc.pid),
            output_path=str(output_file),
            working_dir=working_dir,
            session_id=session_id,
            session_name=session_name,
            agent=agent,
        )

    def wait(self, process: AgentProcess, on_usage_limit=None) -> AgentStatus:
        """Block until agent subprocess exits. Tails output for usage limit detection.

        Args:
            process: The agent subprocess handle.
            on_usage_limit: Optional callback(reset_at: datetime|None, message: str)
                fired once when usage limit detected in output. Does not interrupt wait.
        """
        t0 = time.monotonic()
        thread = threading.current_thread().name
        logger.info(f"[pid={process.pid}] wait started (session={process.session_name}, thread={thread})")

        exit_event = threading.Event()
        exit_info: dict = {}

        def _waiter():
            try:
                _, status = os.waitpid(process.pid, 0)
                exit_info["exit_code"] = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            except ChildProcessError:
                while self._is_process_alive(process.pid):
                    time.sleep(0.5)
                exit_info["exit_code"] = 0
                exit_info["unreliable"] = True
            exit_event.set()

        waiter_thread = threading.Thread(target=_waiter, daemon=True,
                                          name=f"waitpid-{process.pid}")
        waiter_thread.start()

        ndjson_offset = 0
        stderr_offset = 0
        limit_signaled = False
        stderr_path = process.output_path + ".stderr"

        while not exit_event.wait(timeout=2.0):
            if on_usage_limit and not limit_signaled:
                ndjson_offset, ndjson_hit = self._tail_for_usage_limit(
                    process.output_path, ndjson_offset, parse_json=True)
                stderr_offset, stderr_hit = self._tail_for_usage_limit(
                    stderr_path, stderr_offset, parse_json=False)
                hit = ndjson_hit or stderr_hit
                if hit:
                    reset_at = parse_usage_reset_time(hit)
                    logger.info(f"[pid={process.pid}] Usage limit detected: {hit}")
                    logger.info(f"[pid={process.pid}] Reset at: {reset_at}")
                    on_usage_limit(reset_at, hit)
                    limit_signaled = True

        elapsed = time.monotonic() - t0
        ec = exit_info.get("exit_code", -1)
        reliable = not exit_info.get("unreliable", False)
        logger.info(f"[pid={process.pid}] wait done exit_code={ec} "
                    f"({'reliable' if reliable else 'non-child'}, {elapsed:.1f}s)")
        return self._completed_status(process, ec, exit_code_reliable=reliable)

    def check(self, process: AgentProcess) -> AgentStatus:
        # Try waitpid first (works when we're the parent)
        try:
            pid_result, status = os.waitpid(process.pid, os.WNOHANG)
            if pid_result != 0:
                exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                return self._completed_status(process, exit_code)
        except ChildProcessError:
            # Not our child — fall back to /proc check
            if not self._is_process_alive(process.pid):
                return self._completed_status(process, 0)

        # Still running — extract progress from NDJSON stream
        session_id = process.session_id or self._extract_session_id(process.output_path)
        cost = self._extract_cost(process.output_path)
        return AgentStatus(
            state="running",
            session_id=session_id,
            cost_usd=cost,
        )

    def cancel(self, process: AgentProcess) -> None:
        # Nothing to kill if no pid/pgid
        if not process.pid and not process.pgid:
            logger.warning("cancel() called with no pid/pgid — nothing to kill")
        else:
            cooperative_cancelled = False

            # Try cooperative ACP cancel first
            if process.session_name:
                try:
                    agent = process.agent or self.default_agent
                    env = {k: v for k, v in os.environ.items()
                           if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")}
                    result = subprocess.run(
                        [self.acpx_path, agent, "cancel", "-s", process.session_name],
                        cwd=process.working_dir,
                        capture_output=True,
                        timeout=5,
                        env=env,
                    )
                    cooperative_cancelled = result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

            # Fall back to SIGTERM → SIGKILL
            if not cooperative_cancelled:
                pgid = process.pgid or process.pid
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                else:
                    # Brief wait, then SIGKILL if still alive
                    try:
                        time.sleep(0.5)
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass  # Already dead — good

        if process.session_name or process.session_id:
            try:
                self.cleanup_session_queue_owner(
                    process.session_id,
                    session_name=process.session_name,
                    agent=process.agent,
                )
            except Exception:
                logger.debug("Queue owner cleanup after cancel failed", exc_info=True)

    @property
    def supports_resume(self) -> bool:
        return True

    def cleanup_session_queue_owner(
        self,
        session_id: str | None,
        session_name: str | None = None,
        agent: str | None = None,
    ) -> None:
        """Cleanly shut down the queue owner for a completed session.

        First tries `acpx claude sessions close --name <session>` for a cooperative
        shutdown. Falls back to SIGTERM on the queue owner process via lock file.
        """
        # Try cooperative close via acpx first
        if session_name:
            try:
                env = {k: v for k, v in os.environ.items()
                       if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")}
                result = subprocess.run(
                    [self.acpx_path, agent or self.default_agent, "sessions", "close",
                     "--name", session_name],
                    capture_output=True, timeout=10, env=env,
                )
                if result.returncode == 0:
                    logger.info(
                        "Closed session %s via acpx sessions close", session_name,
                    )
                    return
                else:
                    logger.debug(
                        "acpx sessions close failed (rc=%d) for %s, falling back to kill",
                        result.returncode, session_name,
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                logger.debug("acpx sessions close timed out for %s", session_name)

        # Fall back to killing queue owner process via lock file
        if not session_id:
            return
        for info in _find_queue_owners():
            if info.session_id == session_id:
                if _is_pid_alive(info.pid):
                    try:
                        os.kill(info.pid, signal.SIGTERM)
                        logger.info(
                            "Cleaned up queue owner pid=%d for completed session %s",
                            info.pid, session_id,
                        )
                    except (ProcessLookupError, PermissionError):
                        pass
                break

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process is alive (not zombie) via /proc."""
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("State:"):
                        return "Z" not in line
            return True
        except FileNotFoundError:
            return False

    def _completed_status(
        self, process: AgentProcess, exit_code: int,
        exit_code_reliable: bool = True,
    ) -> AgentStatus:
        session_id = self._extract_session_id(process.output_path)
        cost = self._extract_cost(process.output_path)

        if exit_code != 0:
            error = self._read_last_error(process.output_path)
            return AgentStatus(
                state="failed",
                exit_code=exit_code,
                session_id=session_id,
                error=error or f"Exit code {exit_code}",
                cost_usd=cost,
            )

        # For non-child PIDs, exit_code=0 is synthetic — check output for errors.
        if not exit_code_reliable:
            error = self._read_last_error(process.output_path)
            if error:
                return AgentStatus(
                    state="failed",
                    exit_code=-1,
                    session_id=session_id,
                    error=f"Agent failed (non-child): {error}",
                    cost_usd=cost,
                )

        return AgentStatus(
            state="completed",
            exit_code=0,
            session_id=session_id,
            cost_usd=cost,
        )

    @staticmethod
    def _parse_session_name(session_name: str) -> tuple[str, int]:
        """Parse step name and attempt from session name format 'step-{name}-{attempt}'."""
        name_part = session_name.removeprefix("step-")
        parts = name_part.rsplit("-", 1)
        if len(parts) == 2:
            try:
                return parts[0], int(parts[1])
            except ValueError:
                pass
        return session_name, 1

    def get_session_messages(self, process: AgentProcess) -> dict | None:
        """Retrieve the full session conversation via acpx sessions show."""
        if not process.session_name:
            return None
        try:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")}
            result = subprocess.run(
                [self.acpx_path, "--format", "json",
                 process.agent or self.default_agent, "sessions", "show",
                 "--name", process.session_name],
                cwd=process.working_dir,
                capture_output=True, text=True, timeout=30, env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return None

    def _extract_session_id(self, output_path: str) -> str | None:
        """Extract sessionId from ACP NDJSON output."""
        try:
            with open(output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # ACP session/new result
                        result = data.get("result", {})
                        if isinstance(result, dict) and result.get("sessionId"):
                            return result["sessionId"]
                        # ACP session/update notifications
                        params = data.get("params", {})
                        if params.get("sessionId"):
                            return params["sessionId"]
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return None

    def _extract_cost(self, output_path: str) -> float | None:
        """Extract cost from ACP usage_update events."""
        last_cost = None
        try:
            with open(output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        params = data.get("params", {})
                        update = params.get("update", {})
                        if update.get("sessionUpdate") == "usage_update":
                            cost = update.get("cost", {})
                            if isinstance(cost, dict) and "amount" in cost:
                                last_cost = cost["amount"]
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return last_cost

    def _read_last_error(self, output_path: str) -> str | None:
        """Extract last error from ACP NDJSON output."""
        try:
            with open(output_path) as f:
                last_error = None
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        error = data.get("error", {})
                        if isinstance(error, dict) and error.get("message"):
                            last_error = error["message"]
                    except json.JSONDecodeError:
                        continue
                return last_error
        except FileNotFoundError:
            return None

    @staticmethod
    def _tail_for_usage_limit(path: str, offset: int,
                               parse_json: bool) -> tuple[int, str | None]:
        """Read new content from file starting at offset, check for usage limit.

        Returns (new_offset, matching_message_or_None).
        """
        try:
            with open(path) as f:
                f.seek(offset)
                new_data = f.read()
                if not new_data:
                    return offset, None
                new_offset = f.tell()
                for line in new_data.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    hit = _detect_usage_limit_in_line(line, parse_json)
                    if hit:
                        return new_offset, hit
                return new_offset, None
        except FileNotFoundError:
            return offset, None

    def _extract_final_text(self, output_path: str) -> str:
        """Extract the final assistant text from ACP NDJSON output."""
        chunks: list[str] = []
        try:
            with open(output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        params = data.get("params", {})
                        update = params.get("update", {})
                        if update.get("sessionUpdate") == "agent_message_chunk":
                            content = update.get("content", {})
                            if content.get("type") == "text":
                                text = content.get("text", "")
                                if text:
                                    chunks.append(text)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return "".join(chunks)


# ── Mock Agent Backend (for testing) ──────────────────────────────────


class MockAgentBackend:
    """Simulates agent execution for testing.

    Tracks spawned processes and allows test code to control when they complete.

    Two modes:
    - Immediate: pre-set a result via auto_complete/auto_fail before spawning.
      start() returns immediately. Used with AsyncEngine.
    - Deferred: spawn first, then call complete_process()/fail_process().
      wait() blocks until the result is set.
    """

    def __init__(self) -> None:
        self._processes: dict[int, dict] = {}
        self._next_pid = 10000
        self._completions: dict[int, AgentStatus] = {}
        self._auto_result: AgentStatus | None = None
        self.spawn_count = 0
        self.cancel_count = 0

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        pid = self._next_pid
        self._next_pid += 1
        self.spawn_count += 1

        default_dir = getattr(context, 'flow_source_dir', None) or context.workspace_path
        working_dir = config.get("working_dir", default_dir)
        output_path = f"/tmp/mock-agent-{pid}.jsonl"
        session_name = config.get("_session_name") or f"step-{context.step_name}-{context.attempt}"

        self._processes[pid] = {
            "prompt": prompt,
            "config": config,
            "context_step": context.step_name,
            "context_attempt": context.attempt,
            "session_name": session_name,
        }

        # Auto-complete if configured
        if self._auto_result is not None:
            self._completions[pid] = self._auto_result
            if self._auto_result.result:
                self._processes[pid]["result"] = self._auto_result.result

        return AgentProcess(
            pid=pid,
            pgid=pid,
            output_path=output_path,
            working_dir=working_dir,
            session_name=session_name,
            agent=config.get("agent"),
        )

    def wait(self, process: AgentProcess, on_usage_limit=None) -> AgentStatus:
        """Block until process is marked complete via complete_process() or fail_process()."""
        import time
        while process.pid not in self._completions:
            time.sleep(0.01)
        return self._completions[process.pid]

    def check(self, process: AgentProcess) -> AgentStatus:
        if process.pid in self._completions:
            return self._completions[process.pid]
        return AgentStatus(state="running")

    def cancel(self, process: AgentProcess) -> None:
        self.cancel_count += 1
        self._completions[process.pid] = AgentStatus(
            state="failed",
            error="Cancelled",
        )

    @property
    def supports_resume(self) -> bool:
        return False

    # ── Test control methods ──────────────────────────────────────────

    def set_auto_complete(self, result: dict | None = None,
                          cost_usd: float | None = None) -> None:
        """Pre-set: all future spawns immediately complete with this result."""
        self._auto_result = AgentStatus(
            state="completed", exit_code=0,
            cost_usd=cost_usd, result=result or {},
        )

    def set_auto_fail(self, error: str = "Mock failure") -> None:
        """Pre-set: all future spawns immediately fail with this error."""
        self._auto_result = AgentStatus(
            state="failed", exit_code=1, error=error,
        )

    def clear_auto(self) -> None:
        """Clear auto-completion — return to deferred mode."""
        self._auto_result = None

    def complete_process(self, pid: int, result: dict | None = None,
                         cost_usd: float | None = None) -> None:
        """Mark a mock process as completed with given result."""
        self._completions[pid] = AgentStatus(
            state="completed",
            exit_code=0,
            cost_usd=cost_usd,
            result=result or {},
        )
        self._processes[pid]["result"] = result or {}

    def fail_process(self, pid: int, error: str = "Mock failure",
                     error_category: str | None = None) -> None:
        """Mark a mock process as failed."""
        self._completions[pid] = AgentStatus(
            state="failed",
            exit_code=1,
            error=error,
        )
        self._processes[pid]["error_category"] = error_category

    def get_process_info(self, pid: int) -> dict | None:
        return self._processes.get(pid)


# ── Agent Executor ────────────────────────────────────────────────────


class AgentExecutor(Executor):
    """Wraps an AgentBackend as a Stepwise executor.

    Output modes:
    - "effect" (default): Workspace changes ARE the output. Artifact is status metadata.
    - "file": Read JSON from output_path. For structured data passing.
    - "stream_result": Extract final text from agent output stream.
    """

    def __init__(
        self,
        backend: AgentBackend,
        claude_direct_backend: AgentBackend | None = None,
        prompt: str = "",
        output_mode: str = "effect",
        output_path: str | None = None,
        **config: Any,
    ) -> None:
        self.backend = backend
        self.claude_direct = claude_direct_backend
        self.prompt_template = prompt
        self.output_mode = output_mode
        self.output_path = output_path
        self.config = config
        # Auto-promote output mode: when the engine injected output_fields but the
        # user didn't explicitly write output_mode in YAML, upgrade from "effect" to
        # "file" so the agent gets structured output instructions and env vars.
        user_set = config.pop("_user_set_output_mode", False)
        self._auto_promoted = False
        if not user_set and self.output_mode == "effect" and self.config.get("output_fields"):
            self.output_mode = "file"
            self._auto_promoted = True
        # Session continuity fields (flow through from step definition via config)
        self.continue_session = config.get("continue_session", False)
        self.loop_prompt = config.get("loop_prompt")
        self.max_continuous_attempts = config.get("max_continuous_attempts")
        # Named session fields
        self._session_name = config.get("_session_name")

    def _select_backend(self, config: dict) -> AgentBackend:
        """Select backend based on config. Returns claude_direct for fork/resume."""
        if config.get("_backend_type") == "claude_direct" and self.claude_direct:
            return self.claude_direct
        return self.backend

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        """Spawn agent, block until completion. Runs in thread pool via AsyncEngine."""
        t0 = time.monotonic()
        thread = threading.current_thread().name
        step_id = f"{context.step_name}@{context.job_id or 'local'}"
        logger.info(f"[{step_id}] executor start (thread={thread}, attempt={context.attempt})")

        # For file output mode, generate step-specific output filename to prevent
        # collisions when multiple agent steps share the same workspace.
        output_file = self.output_path
        if self.output_mode == "file" and not output_file:
            output_file = f"{context.step_name}-output.json"

        # Circuit breaker: fail the step if named session exceeds max_continuous_attempts
        if (self._session_name
                and self.max_continuous_attempts is not None
                and context.attempt > self.max_continuous_attempts):
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path,
                    timestamp=_now(),
                    executor_meta={"failed": True},
                ),
                executor_state={
                    "failed": True,
                    "error": f"Circuit breaker: attempt {context.attempt} > max_continuous_attempts {self.max_continuous_attempts}",
                    "error_category": "circuit_breaker",
                },
            )

        prompt = self._render_prompt(inputs, context)

        # Session naming strategy
        spawn_config = dict(self.config)

        # Named sessions (new mechanism)
        if self._session_name:
            # Named session — _session_name already set in config by engine
            pass  # spawn_config already contains _session_name from self.config

        # Legacy continue_session support
        elif self.continue_session:
            # Check circuit breaker
            use_existing = True
            if (self.max_continuous_attempts is not None
                    and context.attempt > self.max_continuous_attempts):
                use_existing = False  # fresh session after breaker

            prev_session = spawn_config.pop("_prev_session_name", None)
            if use_existing and prev_session:
                # Continue existing session — use stable name (no attempt suffix)
                spawn_config["_session_name"] = prev_session
            elif use_existing and context.attempt > 1:
                # Previous session should exist but config missing — use stable name
                job_prefix = context.job_id.replace("job-", "") if context.job_id else "local"
                spawn_config["_session_name"] = f"step-{job_prefix}-{context.step_name}"

        # Also support received session from _session_id input (legacy)
        if "_session_id" in inputs and inputs["_session_id"]:
            spawn_config["_session_name"] = inputs["_session_id"]

        # Select backend based on config
        backend = self._select_backend(spawn_config)
        process = backend.spawn(prompt, spawn_config, context)
        logger.info(f"[{step_id}] spawn complete ({time.monotonic() - t0:.1f}s elapsed)")
        process.capture_transcript = False

        if context.state_update_fn:
            context.state_update_fn({
                "pid": process.pid,
                "pgid": process.pgid,
                "output_path": process.output_path,
                "working_dir": process.working_dir,
                "session_id": process.session_id,
                "session_name": process.session_name,
                "agent": process.agent,
                "output_mode": self.output_mode,
                "output_file": output_file,
            })

        # Callback for usage limit detection — updates executor_state for UI
        def _on_usage_limit(reset_at, message):
            logger.info(f"[{step_id}] Usage limit detected, reset_at={reset_at}")
            if context.state_update_fn:
                context.state_update_fn({
                    "pid": process.pid,
                    "pgid": process.pgid,
                    "output_path": process.output_path,
                    "working_dir": process.working_dir,
                    "session_id": process.session_id,
                    "session_name": process.session_name,
                    "agent": process.agent,
                    "output_mode": self.output_mode,
                    "output_file": output_file,
                    "usage_limit_waiting": True,
                    "reset_at": reset_at.isoformat() if reset_at else None,
                    "usage_limit_message": message,
                })

        # Block until agent exits (safe — AsyncEngine runs this in thread pool)
        agent_status = self.backend.wait(process, on_usage_limit=_on_usage_limit)

        # Clear usage limit flag after process exits
        if context.state_update_fn:
            context.state_update_fn({
                "pid": process.pid,
                "pgid": process.pgid,
                "output_path": process.output_path,
                "working_dir": process.working_dir,
                "session_id": process.session_id,
                "session_name": process.session_name,
                "agent": process.agent,
                "output_mode": self.output_mode,
                "output_file": output_file,
                "usage_limit_waiting": False,
            })

        logger.info(f"[{step_id}] executor done ({time.monotonic() - t0:.1f}s total, status={agent_status.state})")

        return self._finalize_after_wait(process, agent_status, inputs, context, output_file)

    def _finalize_after_wait(
        self,
        process: AgentProcess,
        agent_status: AgentStatus,
        inputs: dict,
        context: ExecutionContext | None,
        output_file: str | None = None,
    ) -> ExecutorResult:
        """Shared post-wait logic: cleanup, emit_flow, output extraction, _session_id.

        Used by both start() (normal path) and finalize_surviving() (reattach path).
        """
        step_id = f"{context.step_name}@{context.job_id or 'local'}" if context else "reattach"
        workspace_path = context.workspace_path if context else process.working_dir

        # Clean up lingering queue owner process for this completed session.
        # Skip cleanup when continue_session or named session is set — the queue
        # owner and session must stay alive for downstream steps that reuse this session.
        if (hasattr(self.backend, 'cleanup_session_queue_owner')
                and not self.continue_session
                and not self._session_name):
            try:
                self.backend.cleanup_session_queue_owner(
                    agent_status.session_id,
                    session_name=process.session_name,
                    agent=process.agent,
                )
            except Exception as e:
                logger.warning(f"[{step_id}] cleanup_session_queue_owner failed: {e}")

        state = {
            "pid": process.pid,
            "pgid": process.pgid,
            "output_path": process.output_path,
            "working_dir": process.working_dir,
            "session_id": agent_status.session_id or process.session_id,
            "session_name": process.session_name,
            "agent": process.agent,
            "output_mode": self.output_mode,
            "output_file": output_file,
            "capture_transcript": process.capture_transcript,
        }

        if agent_status.state == "failed":
            error_cat = self._classify_error(agent_status)
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=process.working_dir,
                    timestamp=_now(),
                    executor_meta={"failed": True},
                ),
                executor_state={
                    **state,
                    "failed": True,
                    "error": agent_status.error or "Agent failed",
                    "error_category": error_cat,
                },
            )

        # Check for emitted flow (only if emit_flow enabled in config)
        if self.config.get("emit_flow"):
            working_dir = state.get("working_dir", workspace_path)
            emit_path = os.path.join(working_dir, EMIT_FLOW_DIR, EMIT_FLOW_FILENAME)
            if os.path.exists(emit_path):
                step_outputs = self.config.get("output_fields", [])
                result = self._build_delegate_result(emit_path, state, context, step_outputs)
                # Consume the emit file so it doesn't persist across loop iterations
                try:
                    os.remove(emit_path)
                except OSError:
                    pass
                return result

        # Completed — extract outputs based on mode
        try:
            envelope = self._extract_output(state, self.output_mode, agent_status)
        except Exception as e:
            logger.error(f"[{step_id}] _extract_output CRASHED: {type(e).__name__}: {e}", exc_info=True)
            raise

        # Auto-inject _session_id into artifact for cross-step session sharing
        # Only for legacy continue_session — named sessions don't need this.
        if not self._session_name:
            if self.continue_session and process.session_name:
                envelope.artifact["_session_id"] = process.session_name
            elif "_session_id" in inputs and inputs["_session_id"] and process.session_name:
                # Pass through received session ID
                envelope.artifact["_session_id"] = inputs["_session_id"]

        return ExecutorResult(
            type="data",
            envelope=envelope,
            executor_state=state,
        )

    def finalize_surviving(self, executor_state: dict) -> ExecutorResult:
        """Finalize a surviving agent process after server restart.

        Reconstructs AgentProcess from executor_state, calls backend.wait(),
        then runs the shared _finalize_after_wait() path.
        """
        process = AgentProcess(
            pid=executor_state["pid"],
            pgid=executor_state["pgid"],
            output_path=executor_state["output_path"],
            working_dir=executor_state["working_dir"],
            session_id=executor_state.get("session_id"),
            session_name=executor_state.get("session_name"),
            capture_transcript=executor_state.get("capture_transcript", True),
            agent=executor_state.get("agent"),
        )
        agent_status = self.backend.wait(process)
        output_file = executor_state.get("output_file")
        return self._finalize_after_wait(process, agent_status, inputs={}, context=None, output_file=output_file)

    def _build_delegate_result(
        self, flow_path: str, state: dict, context: ExecutionContext,
        step_outputs: list[str],
    ) -> ExecutorResult:
        """Build a delegate ExecutorResult from an emitted flow file."""
        from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

        try:
            workflow = load_workflow_yaml(flow_path)
        except (YAMLLoadError, Exception) as e:
            logger.warning(f"Agent emitted invalid flow: {e}")
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path,
                    timestamp=_now(),
                    executor_meta={"failed": True, "error": f"Invalid emitted flow: {e}"},
                ),
                executor_state={**state, "failed": True, "error": f"Invalid emitted flow: {e}"},
            )

        # Validate terminal steps produce all required outputs
        if step_outputs:
            terminal = workflow.terminal_steps()
            if not terminal:
                error = "Emitted flow has no terminal steps"
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={}, sidecar=Sidecar(),
                        workspace=context.workspace_path, timestamp=_now(),
                        executor_meta={"failed": True, "error": error},
                    ),
                    executor_state={**state, "failed": True, "error": error},
                )
            for term_name in terminal:
                term_outputs = set(workflow.steps[term_name].outputs)
                missing = [o for o in step_outputs if o not in term_outputs]
                if missing:
                    error = (
                        f"Emitted flow terminal step '{term_name}' missing "
                        f"outputs {missing} required by parent step"
                    )
                    return ExecutorResult(
                        type="data",
                        envelope=HandoffEnvelope(
                            artifact={}, sidecar=Sidecar(),
                            workspace=context.workspace_path, timestamp=_now(),
                            executor_meta={"failed": True, "error": error},
                        ),
                        executor_state={**state, "failed": True, "error": error},
                    )

        sub_def = SubJobDefinition(
            objective=f"Agent-emitted flow from step {context.step_name}",
            workflow=workflow,
        )
        return ExecutorResult(
            type="delegate",
            sub_job_def=sub_def,
            executor_state={**state, "emitted_flow": True},
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        process = AgentProcess(
            pid=state["pid"],
            pgid=state["pgid"],
            output_path=state["output_path"],
            working_dir=state["working_dir"],
            session_id=state.get("session_id"),
            session_name=state.get("session_name"),
            capture_transcript=state.get("capture_transcript", True),
            agent=state.get("agent"),
        )

        agent_status = self.backend.check(process)

        if agent_status.state == "running":
            if agent_status.session_id and not state.get("session_id"):
                state["session_id"] = agent_status.session_id
            return ExecutorStatus(
                state="running",
                cost_so_far=agent_status.cost_usd,
            )

        if agent_status.state == "failed":
            error_cat = self._classify_error(agent_status)
            return ExecutorStatus(
                state="failed",
                message=agent_status.error,
                error_category=error_cat,
            )

        # Completed — extract outputs based on mode
        output_mode = state.get("output_mode", "effect")
        envelope = self._extract_output(state, output_mode, agent_status)

        return ExecutorStatus(
            state="completed",
            result=ExecutorResult(type="data", envelope=envelope),
            cost_so_far=agent_status.cost_usd,
        )

    def cancel(self, state: dict) -> None:
        if not state:
            logger.warning("AgentExecutor.cancel() called with empty state")
            return
        process = AgentProcess(
            pid=state.get("pid", 0),
            pgid=state.get("pgid", 0),
            output_path=state.get("output_path", ""),
            working_dir=state.get("working_dir", ""),
            session_id=state.get("session_id"),
            session_name=state.get("session_name"),
            agent=state.get("agent"),
        )
        self.backend.cancel(process)

    def _render_prompt(self, inputs: dict, context: ExecutionContext) -> str:
        str_inputs = {}
        for k, v in inputs.items():
            if isinstance(v, str):
                str_inputs[k] = v
            elif v is None:
                str_inputs[k] = ""
            elif isinstance(v, (dict, list)):
                str_inputs[k] = json.dumps(v, indent=2)
            else:
                str_inputs[k] = str(v)
        # Always make objective and workspace available for prompt templates
        str_inputs.setdefault("objective", context.objective or "")
        str_inputs.setdefault("workspace", context.workspace_path or "")
        # Use loop_prompt on attempt > 1 if configured
        template = self.prompt_template
        if context.attempt > 1 and self.loop_prompt:
            template = self.loop_prompt
        prompt = Template(template).safe_substitute(str_inputs)
        # Also support {{var}} (Jinja/Mustache-style) templates
        for k, v in str_inputs.items():
            prompt = prompt.replace("{{" + k + "}}", v)

        if context.injected_context:
            prompt += "\n\nAdditional context:\n" + "\n".join(context.injected_context)

        if self.config.get("emit_flow"):
            from stepwise.agent_help import build_emit_flow_instructions
            prompt += build_emit_flow_instructions(
                registry=self.config.get("_registry"),
                config=self.config.get("_config"),
                depth_remaining=self.config.get("_depth_remaining"),
                project_dir=self.config.get("_project_dir"),
            )

        # For file output mode, replace generic "output.json" references with
        # the step-specific filename to prevent collisions in shared workspace.
        if self.output_mode == "file":
            step_output_file = self.output_path or f"{context.step_name}-output.json"
            import re
            # Only replace standalone "output.json", not when part of a longer filename
            # like "explore-output.json". Match when preceded by whitespace, backtick, or quote.
            prompt = re.sub(r'(?<=[\s`"\'])output\.json(?=[\s`"\',)])', step_output_file, prompt)

        # Append structured output instructions when step declares outputs and
        # mode is "file" (whether explicit or auto-promoted).
        output_fields = self.config.get("output_fields", [])
        if output_fields and self.output_mode == "file":
            output_file = self.output_path or f"{context.step_name}-output.json"
            field_list = ", ".join(f'"{f}"' for f in output_fields)
            example = {f: f"<{f} value>" for f in output_fields}
            prompt += (
                f"\n\n<stepwise-output>\n"
                f"When you have completed your task, write your structured output "
                f"as a JSON file to: {output_file}\n\n"
                f"Required JSON keys: {field_list}\n"
                f"Example:\n```json\n"
                f"{json.dumps(example, indent=2)}\n```\n"
                f"\nThe file path is also available as $STEPWISE_OUTPUT_FILE.\n"
                f"Write this file as one of your final actions.\n"
                f"</stepwise-output>"
            )

        return prompt

    def _extract_output(self, state: dict, output_mode: str,
                        agent_status: AgentStatus) -> HandoffEnvelope:
        working_dir = state.get("working_dir", "")

        if agent_status.result:
            artifact = agent_status.result
        else:
            match output_mode:
                case "file":
                    output_file = state.get("output_file")
                    if output_file:
                        file_path = (Path(working_dir) / output_file).resolve()
                        if not file_path.is_relative_to(Path(working_dir).resolve()):
                            raise ValueError(
                                f"output_file escapes working directory: {output_file!r}"
                            )
                    else:
                        # Look in workspace dir first (where agent writes), then .stepwise/step-io
                        file_path = Path(working_dir) / "output.json"
                        if not file_path.exists():
                            file_path = Path(state["output_path"]).parent / "output.json"

                    try:
                        content = file_path.read_text()
                        # Retry once if empty — filesystem flush delay
                        if not content.strip():
                            import time
                            time.sleep(0.1)
                            content = file_path.read_text()
                        artifact = json.loads(content)
                        if not isinstance(artifact, dict):
                            artifact = {"result": artifact}
                    except (FileNotFoundError, json.JSONDecodeError):
                        # Retry once after brief delay — handles filesystem flush
                        import time
                        time.sleep(0.1)
                        try:
                            artifact = json.loads(file_path.read_text())
                            if not isinstance(artifact, dict):
                                artifact = {"result": artifact}
                        except (FileNotFoundError, json.JSONDecodeError) as exc:
                            expected_fields = self.config.get("output_fields", [])
                            artifact = {
                                "status": "completed",
                                "output_file_missing": True,
                                "_error": (
                                    f"Agent did not write output file: {file_path}. "
                                    f"Expected JSON with keys: {expected_fields}. "
                                    f"Error: {type(exc).__name__}: {exc}"
                                ),
                            }

                case "stream_result":
                    # Extract text from ACP agent_message_chunk events
                    if hasattr(self.backend, '_extract_final_text'):
                        text = self.backend._extract_final_text(state["output_path"])
                    else:
                        text = ""
                    artifact = {"result": text}

                case _:  # "effect"
                    artifact = {
                        "status": "completed",
                        "session_id": agent_status.session_id,
                    }

        # Zero out cost for subscription billing (agent runs are free on Max/subscription)
        cost = agent_status.cost_usd
        if self.config.get("_billing_mode") == "subscription":
            cost = 0

        return HandoffEnvelope(
            artifact=artifact,
            sidecar=Sidecar(),
            workspace=working_dir,
            timestamp=_now(),
            executor_meta={
                "session_id": agent_status.session_id,
                "cost_usd": cost,
                "exit_code": agent_status.exit_code,
            },
        )

    def _classify_error(self, status: AgentStatus) -> str:
        """Classify error type for exit rule routing."""
        result = classify_api_error(status.error or "")
        # Map "unknown" back to "agent_failure" for agent-specific default
        return "agent_failure" if result == "unknown" else result
