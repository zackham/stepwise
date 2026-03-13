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

    def wait(self, process: AgentProcess) -> AgentStatus:
        """Block until the agent process exits. Returns final status."""
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

    def wait(self, process: AgentProcess) -> AgentStatus:
        """Block until agent subprocess exits. Safe to call from thread pool."""
        try:
            _, status = os.waitpid(process.pid, 0)  # blocking wait
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        except ChildProcessError:
            # Not our child — poll until process disappears
            while self._is_process_alive(process.pid):
                import time
                time.sleep(0.5)
            exit_code = 0
        return self._completed_status(process, exit_code)

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

        # M7a: Capture session transcript for chain context
        self._capture_transcript(process)

        return AgentStatus(
            state="completed",
            exit_code=0,
            session_id=session_id,
            cost_usd=cost,
        )

    def _capture_transcript(self, process: AgentProcess) -> None:
        """Capture the full agent session conversation for chain context.

        Calls `acpx sessions show` to retrieve the structured conversation,
        normalizes it, and saves as a transcript file that downstream chain
        members can load.
        """
        import logging
        logger = logging.getLogger("stepwise.agent")

        if not process.session_name:
            return

        try:
            session_data = self.get_session_messages(process)
            if not session_data:
                return

            messages_raw = session_data.get("result", {}).get("messages", [])
            if not messages_raw:
                return

            from stepwise.context import (
                Transcript,
                estimate_token_count,
                normalize_acpx_messages,
                save_transcript,
            )

            # Normalize with thinking included — compile-time filtering decides what to show
            normalized = normalize_acpx_messages(messages_raw, include_thinking=True)

            # Use actual token count from acpx if available, else estimate
            usage = session_data.get("result", {}).get("cumulative_token_usage", {})
            actual_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            token_count = actual_tokens if actual_tokens > 0 else estimate_token_count(normalized)

            # Parse step_name and attempt from session_name (format: "step-{name}-{attempt}")
            step_name, attempt = self._parse_session_name(process.session_name)

            transcript = Transcript(
                step=step_name,
                attempt=attempt,
                chain="",   # Set during collection by context module
                label="",   # Set during collection by context module
                token_count=token_count,
                messages=normalized,
            )
            save_transcript(process.working_dir, transcript)
            logger.debug(
                f"Captured transcript for {step_name} attempt {attempt} "
                f"({token_count} tokens, {len(normalized)} messages)"
            )
        except Exception:
            logger.warning("Failed to capture transcript for chain context", exc_info=True)

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
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                [self.acpx_path, "--format", "json",
                 self.default_agent, "sessions", "show",
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

        working_dir = config.get("working_dir", context.workspace_path)
        output_path = f"/tmp/mock-agent-{pid}.jsonl"

        self._processes[pid] = {
            "prompt": prompt,
            "config": config,
            "context_step": context.step_name,
            "context_attempt": context.attempt,
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
        )

    def wait(self, process: AgentProcess) -> AgentStatus:
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
        """Spawn agent, block until completion. Runs in thread pool via AsyncEngine."""
        # For file output mode, generate step-specific output filename to prevent
        # collisions when multiple agent steps share the same workspace.
        output_file = self.output_path
        if self.output_mode == "file" and not output_file:
            output_file = f"{context.step_name}-output.json"

        prompt = self._render_prompt(inputs, context)
        process = self.backend.spawn(prompt, self.config, context)

        # Block until agent exits (safe — AsyncEngine runs this in thread pool)
        agent_status = self.backend.wait(process)

        state = {
            "pid": process.pid,
            "pgid": process.pgid,
            "output_path": process.output_path,
            "working_dir": process.working_dir,
            "session_id": agent_status.session_id or process.session_id,
            "session_name": process.session_name,
            "output_mode": self.output_mode,
            "output_file": output_file,
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

        # Completed — extract outputs based on mode
        envelope = self._extract_output(state, self.output_mode, agent_status)
        return ExecutorResult(
            type="data",
            envelope=envelope,
            executor_state=state,
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
        str_inputs = {}
        for k, v in inputs.items():
            if isinstance(v, str):
                str_inputs[k] = v
            elif isinstance(v, (dict, list)):
                str_inputs[k] = json.dumps(v, indent=2)
            else:
                str_inputs[k] = str(v)
        # Always make objective and workspace available for prompt templates
        str_inputs.setdefault("objective", context.objective or "")
        str_inputs.setdefault("workspace", context.workspace_path or "")
        prompt = Template(self.prompt_template).safe_substitute(str_inputs)
        # Also support {{var}} (Jinja/Mustache-style) templates
        for k, v in str_inputs.items():
            prompt = prompt.replace("{{" + k + "}}", v)

        # M7a: Prepend chain context (prior conversation history) if present
        if context.chain_context:
            prompt = context.chain_context + "\n\n" + prompt

        if context.injected_context:
            prompt += "\n\nAdditional context:\n" + "\n".join(context.injected_context)

        # For file output mode, replace generic "output.json" references with
        # the step-specific filename to prevent collisions in shared workspace.
        if self.output_mode == "file":
            step_output_file = self.output_path or f"{context.step_name}-output.json"
            prompt = prompt.replace("output.json", step_output_file)

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
                        # Look in workspace dir first (where agent writes), then .step-io
                        file_path = Path(working_dir) / "output.json"
                        if not file_path.exists():
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
