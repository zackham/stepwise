"""AgentExecutor: Long-running agent sessions as async steps.

Wraps ACP-compatible agents as Stepwise executors.
The engine runs start() in a thread pool; the executor blocks until completion.

Protocol: Agent Client Protocol (ACP) — https://agentclientprotocol.com
"""

from __future__ import annotations

import json
import logging
import os
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
    classify_api_error,
)
from stepwise.models import HandoffEnvelope, Sidecar, SubJobDefinition, _now


EMIT_FLOW_FILENAME = "emit.flow.yaml"
EMIT_FLOW_DIR = ".stepwise"


def _detect_usage_limit_in_line(line: str, parse_json: bool) -> str | None:
    """Check a single line for usage limit patterns.

    Delegates to :func:`stepwise.acp_ndjson.detect_usage_limit_in_line`.
    """
    from stepwise.acp_ndjson import detect_usage_limit_in_line
    return detect_usage_limit_in_line(line, parse_json)


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
        """Select backend. ACPBackend handles all operations natively."""
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
