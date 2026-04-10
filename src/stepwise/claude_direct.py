"""ClaudeDirectBackend: Agent backend using claude CLI directly.

Produces ACP-compatible NDJSON so downstream code (UI, DB, reports)
sees no difference from AcpxBackend output.  Enables fork and resume
operations that acpx does not expose.

Translation: claude ``--output-format stream-json`` → ACP NDJSON envelope.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from stepwise.acp_ndjson import (
    detect_usage_limit_in_line as _detect_usage_limit_in_line,
    extract_cost as _extract_cost,
    extract_final_text as _extract_final_text,
    extract_session_id as _extract_session_id_shared,
    read_last_error as _read_last_error,
    tail_for_usage_limit as _tail_for_usage_limit,
)
from stepwise.agent import (
    AgentProcess,
    AgentStatus,
    _build_agent_env,
)
from stepwise.executors import ExecutionContext, parse_usage_reset_time

logger = logging.getLogger("stepwise.claude_direct")


# ── ACP NDJSON Translation ───────────────────────────────────────────


@dataclass
class _TranslatorState:
    """Mutable state carried across translated lines."""
    session_id: str | None = None
    current_tool_id: str | None = None
    current_tool_name: str | None = None
    line_count: int = 0


def translate_claude_event(event: dict, state: _TranslatorState) -> list[dict]:
    """Translate a single claude stream-json event into ACP NDJSON lines.

    Returns zero or more ACP envelope dicts.  Mutates *state* to track
    session_id and current tool context across events.
    """
    event_type = event.get("type")
    results: list[dict] = []

    if event_type == "system" and event.get("subtype") == "init":
        sid = event.get("session_id", "")
        state.session_id = sid

        # Synthesize ACP preamble: initialize request/response + session/new
        results.append({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        })
        results.append({
            "jsonrpc": "2.0",
            "id": 0,
            "result": {"protocolVersion": 1},
        })
        results.append({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"sessionId": sid},
        })

    elif event_type == "assistant":
        msg = event.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    results.append(_session_update(state.session_id, {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    }))

    elif event_type == "stream_event":
        inner = event.get("event", {})
        inner_type = inner.get("type")

        if inner_type == "content_block_start":
            cb = inner.get("content_block", {})
            if cb.get("type") == "tool_use":
                tool_id = cb.get("id", "")
                tool_name = cb.get("name", "")
                state.current_tool_id = tool_id
                state.current_tool_name = tool_name
                results.append(_session_update(state.session_id, {
                    "sessionUpdate": "tool_call",
                    "toolCallId": tool_id,
                    "title": tool_name,
                    "kind": "tool_use",
                    "status": "pending",
                }))
            elif cb.get("type") == "text":
                text = cb.get("text", "")
                if text:
                    results.append(_session_update(state.session_id, {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    }))

        elif inner_type == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    results.append(_session_update(state.session_id, {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    }))

        elif inner_type == "content_block_stop":
            if state.current_tool_id:
                results.append(_session_update(state.session_id, {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": state.current_tool_id,
                    "status": "completed",
                    "title": state.current_tool_name or "",
                }))
                state.current_tool_id = None
                state.current_tool_name = None

    elif event_type == "result":
        sid = event.get("session_id", state.session_id or "")
        state.session_id = sid or state.session_id
        cost = event.get("cost_usd") or event.get("total_cost_usd")
        usage = event.get("usage", {})
        # Sum all token fields for cumulative usage (matches acpx's "used" semantics)
        used = (
            usage.get("input_tokens", 0)
            + usage.get("output_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        # Context window size from model usage if available
        model_usage = event.get("modelUsage", {})
        size = 0
        for model_info in model_usage.values():
            if isinstance(model_info, dict):
                size = model_info.get("contextWindow", 0)
                break

        update: dict = {
            "sessionUpdate": "usage_update",
            "used": used,
            "size": size,
        }
        if cost is not None:
            update["cost"] = {"amount": cost, "currency": "USD"}
        results.append(_session_update(state.session_id, update))

        # Also emit the session/new result if we haven't seen an init event
        # (e.g. resumed sessions may not emit system init)
        if sid:
            results.append({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"sessionId": sid},
            })

    return results


def _session_update(session_id: str | None, update: dict) -> dict:
    """Build an ACP session/update notification envelope."""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": session_id or "",
            "update": update,
        },
    }


def translate_stream(lines: list[str]) -> list[str]:
    """Translate a batch of claude stream-json lines to ACP NDJSON lines.

    Convenience function for testing and offline conversion.
    """
    state = _TranslatorState()
    output: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for acp in translate_claude_event(event, state):
            output.append(json.dumps(acp, separators=(",", ":")))
    return output


# ── Extraction helpers (ACP NDJSON format) ───────────────────────────
#
# All extraction logic now lives in stepwise.acp_ndjson.
# The imports at the top of this file bring them in as:
#   _extract_cost, _extract_final_text, _read_last_error,
#   _tail_for_usage_limit, _detect_usage_limit_in_line
#
# _extract_claude_session_id is a thin wrapper that calls
# extract_session_id with result_only=True.


def _extract_claude_session_id(output_path: str) -> str | None:
    """Extract Claude session UUID from ACP NDJSON output.

    Only reads ``result.sessionId``, never ``params.sessionId``.
    Delegates to :func:`stepwise.acp_ndjson.extract_session_id`.
    """
    return _extract_session_id_shared(output_path, result_only=True)


# ── Translation Thread ───────────────────────────────────────────────


def _run_translation_thread(
    pipe: IO[str],
    output_path: str,
    stop_event: threading.Event,
) -> None:
    """Read claude stream-json from *pipe*, translate to ACP NDJSON, write to *output_path*.

    Runs in a daemon thread.  Exits when the pipe is exhausted (subprocess
    closed its stdout) or when *stop_event* is set.
    """
    state = _TranslatorState()
    try:
        with open(output_path, "a") as out_f:
            for raw_line in pipe:
                if stop_event.is_set():
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                for acp_event in translate_claude_event(event, state):
                    out_f.write(json.dumps(acp_event, separators=(",", ":")) + "\n")
                    out_f.flush()
    except Exception:
        logger.warning("Translation thread error", exc_info=True)


# ── ClaudeDirectBackend ──────────────────────────────────────────────


class ClaudeDirectBackend:
    """Backend using claude CLI directly for fork/resume operations.

    Writes ACP-compatible NDJSON so downstream code sees no difference
    from AcpxBackend.
    """

    def __init__(
        self,
        claude_path: str = "claude",
        default_permissions: str = "dangerously_skip_permissions",
    ) -> None:
        self.claude_path = claude_path
        self.default_permissions = default_permissions
        # Track translation threads for cleanup
        self._threads: dict[int, threading.Thread] = {}
        self._stop_events: dict[int, threading.Event] = {}

    def spawn(
        self,
        prompt: str,
        config: dict,
        context: ExecutionContext,
    ) -> AgentProcess:
        t0 = time.monotonic()
        thread = threading.current_thread().name
        step_id = f"{context.step_name}@{context.job_id or 'local'}"
        logger.info(f"[{step_id}] claude_direct spawn started (thread={thread})")

        working_dir = str(
            Path(config.get("working_dir", context.workspace_path)).resolve()
        )
        Path(working_dir).mkdir(parents=True, exist_ok=True)

        # Write prompt to file
        job_prefix = (
            context.job_id.replace("job-", "") if context.job_id else "local"
        )
        step_io = (
            Path(working_dir)
            / ".stepwise"
            / "step-io"
            / (context.job_id or "local")
        )
        step_io.mkdir(parents=True, exist_ok=True)
        prompt_file = step_io / f"{context.step_name}-{context.attempt}.prompt.md"
        prompt_file.write_text(prompt)

        # Output file for translated ACP NDJSON
        output_file = (
            step_io / f"{context.step_name}-{context.attempt}.output.jsonl"
        )

        # Build claude command
        cmd = [
            self.claude_path,
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        # Permission handling — map acpx-style "approve_all" to claude's flag
        permissions = config.get("permissions") or self.default_permissions
        if permissions in ("dangerously_skip_permissions", "approve_all"):
            cmd.append("--dangerously-skip-permissions")

        # Session mode: fork, resume, or fresh
        fork_from_session = config.get("_fork_from_session_id")
        resume_session = config.get("_session_uuid")

        if fork_from_session:
            cmd.extend(["--resume", fork_from_session, "--fork-session"])
        elif resume_session:
            cmd.extend(["--resume", resume_session])

        # Read prompt from file and pass as positional argument
        # (claude CLI's --file flag is for downloading file resources, not reading prompts)
        prompt_text = prompt_file.read_text()
        cmd.extend(["-p", prompt_text])

        # Build environment
        env = _build_agent_env(config, context, step_io, working_dir)

        # Open stderr file
        err_file = str(output_file) + ".stderr"
        err_f = open(err_file, "w")

        # Spawn subprocess with stdout piped for translation
        proc = subprocess.Popen(
            cmd,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=err_f,
            env=env,
            start_new_session=True,
            text=True,
        )
        err_f.close()

        # Start translation thread to read stdout and write ACP NDJSON
        stop_event = threading.Event()
        translator = threading.Thread(
            target=_run_translation_thread,
            args=(proc.stdout, str(output_file), stop_event),
            daemon=True,
            name=f"claude-translate-{proc.pid}",
        )
        translator.start()
        self._threads[proc.pid] = translator
        self._stop_events[proc.pid] = stop_event

        session_name = config.get("_session_name") or (
            f"step-{job_prefix}-{context.step_name}-{context.attempt}"
        )

        logger.info(
            f"[{step_id}] claude_direct spawned pid={proc.pid} "
            f"({time.monotonic() - t0:.1f}s)"
        )

        return AgentProcess(
            pid=proc.pid,
            pgid=os.getpgid(proc.pid),
            output_path=str(output_file),
            working_dir=working_dir,
            session_name=session_name,
            capture_transcript=False,  # No acpx transcript capture needed
            agent="claude",
        )

    def wait(
        self,
        process: AgentProcess,
        on_usage_limit=None,
    ) -> AgentStatus:
        """Block until claude subprocess exits.

        Tails translated ACP output for usage limit detection.
        """
        t0 = time.monotonic()
        thread = threading.current_thread().name
        logger.info(
            f"[pid={process.pid}] claude_direct wait started "
            f"(session={process.session_name}, thread={thread})"
        )

        exit_event = threading.Event()
        exit_info: dict = {}

        def _waiter():
            try:
                _, status = os.waitpid(process.pid, 0)
                exit_info["exit_code"] = (
                    os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                )
            except ChildProcessError:
                while self._is_process_alive(process.pid):
                    time.sleep(0.5)
                exit_info["exit_code"] = 0
                exit_info["unreliable"] = True
            exit_event.set()

        waiter_thread = threading.Thread(
            target=_waiter,
            daemon=True,
            name=f"waitpid-cd-{process.pid}",
        )
        waiter_thread.start()

        ndjson_offset = 0
        stderr_offset = 0
        limit_signaled = False
        stderr_path = process.output_path + ".stderr"

        while not exit_event.wait(timeout=2.0):
            if on_usage_limit and not limit_signaled:
                ndjson_offset, ndjson_hit = _tail_for_usage_limit(
                    process.output_path, ndjson_offset, parse_json=True,
                )
                stderr_offset, stderr_hit = _tail_for_usage_limit(
                    stderr_path, stderr_offset, parse_json=False,
                )
                hit = ndjson_hit or stderr_hit
                if hit:
                    reset_at = parse_usage_reset_time(hit)
                    logger.info(
                        f"[pid={process.pid}] Usage limit detected: {hit}"
                    )
                    on_usage_limit(reset_at, hit)
                    limit_signaled = True

        # Wait for translation thread to finish draining stdout
        translator = self._threads.pop(process.pid, None)
        stop_event = self._stop_events.pop(process.pid, None)
        if translator is not None:
            translator.join(timeout=5.0)

        elapsed = time.monotonic() - t0
        ec = exit_info.get("exit_code", -1)
        reliable = not exit_info.get("unreliable", False)
        logger.info(
            f"[pid={process.pid}] claude_direct wait done exit_code={ec} "
            f"({'reliable' if reliable else 'non-child'}, {elapsed:.1f}s)"
        )
        return self._completed_status(process, ec, exit_code_reliable=reliable)

    def check(self, process: AgentProcess) -> AgentStatus:
        """Non-blocking status check."""
        try:
            pid_result, status = os.waitpid(process.pid, os.WNOHANG)
            if pid_result != 0:
                exit_code = (
                    os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                )
                return self._completed_status(process, exit_code)
        except ChildProcessError:
            if not self._is_process_alive(process.pid):
                return self._completed_status(process, 0)

        # Still running
        session_id = _extract_claude_session_id(process.output_path)
        cost = _extract_cost(process.output_path)
        return AgentStatus(
            state="running",
            session_id=session_id,
            cost_usd=cost,
        )

    def cancel(self, process: AgentProcess) -> None:
        """Terminate the claude subprocess."""
        # Signal translation thread to stop
        stop_event = self._stop_events.pop(process.pid, None)
        if stop_event is not None:
            stop_event.set()

        # Kill process group
        pgid = process.pgid or process.pid
        if pgid:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            else:
                try:
                    time.sleep(0.5)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

        # Clean up thread reference
        translator = self._threads.pop(process.pid, None)
        if translator is not None:
            translator.join(timeout=2.0)

    @property
    def supports_resume(self) -> bool:
        return True

    # ── Internal helpers ─────────────────────────────────────────────

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
        self,
        process: AgentProcess,
        exit_code: int,
        exit_code_reliable: bool = True,
    ) -> AgentStatus:
        """Build final AgentStatus from the output file."""
        session_id = _extract_claude_session_id(process.output_path)
        cost = _extract_cost(process.output_path)

        if exit_code != 0:
            error = _read_last_error(process.output_path)
            # Also check stderr for error info
            if not error:
                stderr_path = process.output_path + ".stderr"
                try:
                    with open(stderr_path) as f:
                        stderr_content = f.read().strip()
                    if stderr_content:
                        # Take last non-empty line
                        lines = [
                            l for l in stderr_content.split("\n") if l.strip()
                        ]
                        if lines:
                            error = lines[-1][:500]
                except FileNotFoundError:
                    pass
            return AgentStatus(
                state="failed",
                exit_code=exit_code,
                session_id=session_id,
                error=error or f"Exit code {exit_code}",
                cost_usd=cost,
            )

        # For non-child PIDs, exit_code=0 is synthetic — check for errors
        if not exit_code_reliable:
            error = _read_last_error(process.output_path)
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
