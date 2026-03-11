"""AgentExecutor: Long-running agent sessions as async steps.

Wraps ACP-compatible agents (via acpx) as Stepwise executors.
The engine polls check_status() each tick; the executor manages the subprocess.

Protocol: Agent Client Protocol (ACP) — https://agentclientprotocol.com
Client: acpx — headless CLI client for ACP agents
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Protocol

from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import HandoffEnvelope, Sidecar, _now


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

    def check(self, process: AgentProcess) -> AgentStatus:
        ...

    def cancel(self, process: AgentProcess) -> None:
        ...

    @property
    def supports_resume(self) -> bool:
        ...


# ── ACP Backend (via acpx) ──────────────────────────────────────────


class AcpxBackend:
    """Agent backend using acpx CLI to communicate via ACP protocol.

    Spawns `acpx {agent}` as a subprocess with named sessions per step.
    Supports any ACP-compatible agent (claude, codex, gemini, etc.).
    """

    def __init__(self, acpx_path: str = "acpx", default_agent: str = "claude") -> None:
        self.acpx_path = acpx_path
        self.default_agent = default_agent

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        working_dir = str(Path(config.get("working_dir", context.workspace_path)).resolve())
        Path(working_dir).mkdir(parents=True, exist_ok=True)

        agent = config.get("agent", self.default_agent)
        session_name = f"step-{context.step_name}-{context.attempt}"

        # Write prompt to file for acpx --file
        step_io = Path(working_dir) / ".step-io"
        step_io.mkdir(parents=True, exist_ok=True)
        prompt_file = step_io / f"{context.step_name}-{context.attempt}.prompt.md"
        prompt_file.write_text(prompt)

        # Output file for NDJSON stream
        output_file = step_io / f"{context.step_name}-{context.attempt}.output.jsonl"

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        # Create named session first (acpx requires it before prompting)
        subprocess.run(
            [self.acpx_path, "--cwd", working_dir,
             agent, "sessions", "ensure", "--name", session_name],
            capture_output=True, timeout=30, env=env,
        )

        # Build acpx prompt command
        args = [self.acpx_path, "--format", "json", "--approve-all",
                "--cwd", working_dir]

        timeout_sec = config.get("timeout")
        if timeout_sec:
            args.extend(["--timeout", str(timeout_sec)])

        args.extend([agent, "-s", session_name, "--file", str(prompt_file)])

        # Spawn — clear CLAUDECODE to allow nested agent sessions
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        with open(output_file, "w") as out_f:
            proc = subprocess.Popen(
                args,
                cwd=working_dir,
                stdout=out_f,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
            )

        return AgentProcess(
            pid=proc.pid,
            pgid=os.getpgid(proc.pid),
            output_path=str(output_file),
            working_dir=working_dir,
            session_name=session_name,
        )

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
        # Try cooperative ACP cancel first
        if process.session_name:
            try:
                agent = self.default_agent
                env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
                subprocess.run(
                    [self.acpx_path, agent, "cancel", "-s", process.session_name],
                    cwd=process.working_dir,
                    capture_output=True,
                    timeout=5,
                    env=env,
                )
                return
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Fall back to SIGTERM
        try:
            os.killpg(process.pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    @property
    def supports_resume(self) -> bool:
        return True

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

    def _completed_status(self, process: AgentProcess, exit_code: int) -> AgentStatus:
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

        return AgentStatus(
            state="completed",
            exit_code=0,
            session_id=session_id,
            cost_usd=cost,
        )

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
    """

    def __init__(self) -> None:
        self._processes: dict[int, dict] = {}
        self._next_pid = 10000
        self._completions: dict[int, AgentStatus] = {}
        self.spawn_count = 0
        self.cancel_count = 0

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        pid = self._next_pid
        self._next_pid += 1
        self.spawn_count += 1

        working_dir = config.get("working_dir", context.workspace_path)
        output_path = f"/tmp/mock-agent-{pid}.jsonl"

        self._processes[pid] = {
            "prompt": prompt,
            "config": config,
            "context_step": context.step_name,
            "context_attempt": context.attempt,
        }

        return AgentProcess(
            pid=pid,
            pgid=pid,
            output_path=output_path,
            working_dir=working_dir,
        )

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
        prompt: str = "",
        output_mode: str = "effect",
        output_path: str | None = None,
        **config: Any,
    ) -> None:
        self.backend = backend
        self.prompt_template = prompt
        self.output_mode = output_mode
        self.output_path = output_path
        self.config = config

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        prompt = self._render_prompt(inputs, context)
        process = self.backend.spawn(prompt, self.config, context)

        return ExecutorResult(
            type="async",
            executor_state={
                "pid": process.pid,
                "pgid": process.pgid,
                "output_path": process.output_path,
                "working_dir": process.working_dir,
                "session_id": process.session_id,
                "session_name": process.session_name,
                "output_mode": self.output_mode,
                "output_file": self.output_path,
            },
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        process = AgentProcess(
            pid=state["pid"],
            pgid=state["pgid"],
            output_path=state["output_path"],
            working_dir=state["working_dir"],
            session_id=state.get("session_id"),
            session_name=state.get("session_name"),
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
        process = AgentProcess(
            pid=state.get("pid", 0),
            pgid=state.get("pgid", 0),
            output_path=state.get("output_path", ""),
            working_dir=state.get("working_dir", ""),
            session_name=state.get("session_name"),
        )
        self.backend.cancel(process)

    def _render_prompt(self, inputs: dict, context: ExecutionContext) -> str:
        str_inputs = {k: str(v) if not isinstance(v, str) else v for k, v in inputs.items()}
        # Always make objective and workspace available for prompt templates
        str_inputs.setdefault("objective", context.objective or "")
        str_inputs.setdefault("workspace", context.workspace_path or "")
        prompt = Template(self.prompt_template).safe_substitute(str_inputs)
        if context.injected_context:
            prompt += "\n\nAdditional context:\n" + "\n".join(context.injected_context)
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
                        file_path = Path(working_dir) / output_file
                    else:
                        file_path = Path(state["output_path"]).parent / "output.json"

                    try:
                        artifact = json.loads(file_path.read_text())
                        if not isinstance(artifact, dict):
                            artifact = {"result": artifact}
                    except (FileNotFoundError, json.JSONDecodeError):
                        artifact = {"status": "completed", "output_file_missing": True}

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

        return HandoffEnvelope(
            artifact=artifact,
            sidecar=Sidecar(),
            workspace=working_dir,
            timestamp=_now(),
            executor_meta={
                "session_id": agent_status.session_id,
                "cost_usd": agent_status.cost_usd,
                "exit_code": agent_status.exit_code,
            },
        )

    def _classify_error(self, status: AgentStatus) -> str:
        """Classify error type for exit rule routing."""
        error = (status.error or "").lower()
        if "timeout" in error or "timed out" in error:
            return "timeout"
        if "context" in error and "length" in error:
            return "context_length"
        if "rate limit" in error or "429" in error:
            return "infra_failure"
        if "network" in error or "connection" in error:
            return "infra_failure"
        return "agent_failure"
