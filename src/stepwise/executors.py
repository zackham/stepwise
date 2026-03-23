"""Executor interface, registry, and built-in implementations."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Callable

from stepwise.llm_client import LLMClient, LLMResponse

from stepwise.models import (
    HandoffEnvelope,
    Sidecar,
    SubJobDefinition,
    WatchSpec,
    _now,
)


# ── Execution Context ──────────────────────────────────────────────────


@dataclass
class ExecutionContext:
    job_id: str
    step_name: str
    attempt: int
    workspace_path: str
    idempotency: str
    objective: str = ""
    timeout_minutes: int | None = None
    injected_context: list[str] | None = None
    chain_context: str | None = None  # M7a: compiled prior-context XML block
    chain: str | None = None  # chain membership — used to decide transcript capture
    state_update_fn: Callable[[dict], None] | None = None


# ── Executor Result ────────────────────────────────────────────────────


@dataclass
class ExecutorResult:
    type: str  # "data" | "watch" | "async" | "delegate"
    envelope: HandoffEnvelope | None = None
    watch: WatchSpec | None = None
    executor_state: dict | None = None
    sub_job_def: SubJobDefinition | None = None  # for type="delegate"


# ── Executor Status (for check_status) ─────────────────────────────────


@dataclass
class ExecutorStatus:
    state: str  # "running" | "completed" | "failed"
    message: str | None = None
    result: ExecutorResult | None = None  # M4: completed async executors return result here
    error_category: str | None = None  # M4: typed failure classification
    cost_so_far: float | None = None  # M4: accumulated cost for limit enforcement


# ── Error classification ──────────────────────────────────────────────

# Patterns that indicate non-transient errors (should NOT be retried)
_AUTH_PATTERNS = ("401", "unauthorized", "403", "forbidden")
_QUOTA_PATTERNS = ("usage limit", "quota exceeded", "billing")


def classify_api_error(error_msg: str) -> str:
    """Classify an API/executor error message for retry decisions.

    Returns one of: "auth_error", "quota_error", "timeout", "context_length",
    "infra_failure", or "unknown".
    """
    lower = error_msg.lower()

    # Auth errors — non-transient, fail immediately
    for pat in _AUTH_PATTERNS:
        if pat in lower:
            return "auth_error"

    # Quota/billing errors — non-transient, fail immediately
    for pat in _QUOTA_PATTERNS:
        if pat in lower:
            return "quota_error"

    # Timeout — transient
    if "timeout" in lower or "timed out" in lower:
        return "timeout"

    # Context length — non-transient
    if "context" in lower and "length" in lower:
        return "context_length"

    # Rate limit / HTTP errors — transient (quota cases already caught above)
    if "rate limit" in lower or "429" in lower:
        return "infra_failure"
    if "network" in lower or "connection" in lower:
        return "infra_failure"
    if "overloaded" in lower or "503" in lower:
        return "infra_failure"
    if "502" in lower or "504" in lower:
        return "infra_failure"
    if "capacity" in lower:
        return "infra_failure"

    return "unknown"


# ── Executor ABC ───────────────────────────────────────────────────────


class Executor(ABC):
    @abstractmethod
    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        ...

    @abstractmethod
    def check_status(self, state: dict) -> ExecutorStatus:
        ...

    @abstractmethod
    def cancel(self, state: dict) -> None:
        ...


# ── Executor Registry ─────────────────────────────────────────────────


class ExecutorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[dict], Executor]] = {}

    def register(self, type_name: str, factory: Callable[[dict], Executor]) -> None:
        self._factories[type_name] = factory

    def create(self, ref: Any) -> Executor:
        """Create an executor from an ExecutorRef, wrapping with decorators."""
        from stepwise.decorators import (
            FallbackDecorator,
            RetryDecorator,
            TimeoutDecorator,
        )

        if ref.type not in self._factories:
            raise KeyError(f"Unknown executor type: '{ref.type}'")

        executor = self._factories[ref.type](ref.config)

        # Wrap with decorators (innermost first)
        for dec_ref in ref.decorators:
            match dec_ref.type:
                case "timeout":
                    executor = TimeoutDecorator(executor, dec_ref.config)
                case "retry":
                    executor = RetryDecorator(executor, dec_ref.config)
                case "fallback":
                    fallback_ref = dec_ref.config.get("fallback_ref")
                    if fallback_ref:
                        fallback_executor = self._factories[fallback_ref["type"]](
                            fallback_ref.get("config", {})
                        )
                        executor = FallbackDecorator(executor, fallback_executor, dec_ref.config)
                case _:
                    raise KeyError(f"Unknown decorator type: '{dec_ref.type}'")

        # Auto-apply transient retry for agent executors if no retry decorator specified
        if ref.type == "agent" and not any(d.type == "retry" for d in ref.decorators):
            executor = RetryDecorator(executor, {
                "max_retries": 5,
                "backoff": "exponential",
                "backoff_base": 30,
                "transient_only": True,
            })

        return executor


# ── Shell metacharacter detection ──────────────────────────────────────

# Pattern matching characters that require shell interpretation.
# Any command containing these must go through shell=True.
_SHELL_METACHAR_RE = re.compile(r'[|><&;$(){}*?\[\]`\\!#~]')


def _is_simple_command(cmd: str) -> bool:
    """Return True if *cmd* can be executed directly without a shell.

    A command is considered "simple" when:
      - it is a single line (no embedded newlines), AND
      - it contains no shell metacharacters: pipes ``|``, redirects ``>``/``<``,
        logical operators ``&&``/``||``, statement separators ``;``, command
        substitution ``$()``/backticks, glob wildcards ``*``/``?``/``[``,
        brace expansion ``{}``, variable expansion ``$``, escape ``\\``,
        or other shell special characters ``!``, ``#``, ``~``.

    When True, the caller should use ``shlex.split(cmd)`` and pass the resulting
    list to ``subprocess.run(..., shell=False)``.  This avoids spawning a shell
    process for every simple step and makes the execution environment more
    predictable (the command runs directly, not through ``/bin/sh -c``).

    When False (multiline script or any metacharacter present), the caller must
    use ``shell=True`` so the shell can interpret the syntax correctly.
    """
    if "\n" in cmd:
        return False
    return not bool(_SHELL_METACHAR_RE.search(cmd))


# ── ScriptExecutor ─────────────────────────────────────────────────────


class ScriptExecutor(Executor):
    """Run a shell command or inline script. Synchronous in M1.

    Execution mode is chosen automatically based on the ``run:`` value:

    * **Direct execution** (``shell=False``) — used when the command is a single
      line with no shell metacharacters.  The command is split with
      ``shlex.split()`` and passed directly to the OS, which is slightly faster
      and avoids shell-injection surprises.

    * **Shell execution** (``shell=True``) — used for multiline scripts or any
      command that contains shell metacharacters (pipes, redirects, ``&&``,
      globs, variable expansion, etc.).  This preserves full backward
      compatibility with existing flows.

    Detection is performed by :func:`_is_simple_command`.  The choice is
    recorded in ``executor_meta["shell_mode"]`` as ``"direct"`` or ``"shell"``
    for observability.
    """

    def __init__(
        self,
        command: str,
        working_dir: str | None = None,
        flow_dir: str | None = None,
    ) -> None:
        self.command = command
        self.working_dir = working_dir
        self.flow_dir = flow_dir  # M10: directory containing the flow file

    def _resolve_command(self, command: str) -> str:
        """Resolve script paths relative to flow_dir if set.

        For directory flows, scripts referenced in `run:` fields are resolved
        relative to the flow's directory. The script is then invoked with its
        absolute path so it works regardless of cwd.

        If the command starts with a relative path that exists under flow_dir,
        it's converted to absolute. Otherwise left as-is (system command,
        inline script, etc.).
        """
        if not self.flow_dir:
            return command

        # Split command into parts to find the script path
        # Handle interpreter prefixes like "python3 script.py" or "bash script.sh"
        parts = command.split(None, 1)
        if not parts:
            return command

        # Check if the first token is an interpreter (python3, bash, etc.)
        interpreters = {"python3", "python", "bash", "sh", "node", "ruby", "perl"}
        if parts[0] in interpreters and len(parts) > 1:
            # Second token is the script path
            script_parts = parts[1].split(None, 1)
            script_path = script_parts[0]
            rest = script_parts[1] if len(script_parts) > 1 else ""
            candidate = Path(self.flow_dir) / script_path
            if candidate.exists():
                resolved = f"{parts[0]} {candidate.resolve()}"
                if rest:
                    resolved += f" {rest}"
                return resolved
            return command

        # First token might be the script itself
        candidate = Path(self.flow_dir) / parts[0]
        if candidate.exists():
            resolved = str(candidate.resolve())
            if len(parts) > 1:
                resolved += f" {parts[1]}"
            return resolved

        return command

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        workspace = context.workspace_path or "."

        # Write inputs to .stepwise/step-io directory
        step_io_dir = Path(workspace) / ".stepwise" / "step-io"
        step_io_dir.mkdir(parents=True, exist_ok=True)
        input_file = step_io_dir / f"{context.step_name}-{context.attempt}.input.json"
        input_file.write_text(json.dumps(inputs, default=str))

        project_dir = str(Path(workspace).resolve())
        env = {
            **os.environ,
            "JOB_ENGINE_INPUTS": str(input_file),
            "JOB_ENGINE_WORKSPACE": str(workspace),
            "STEPWISE_STEP_IO": str(step_io_dir),
            "STEPWISE_PROJECT_DIR": project_dir,
            "STEPWISE_ATTEMPT": str(context.attempt),
        }
        # Prepend project root to PYTHONPATH so flow scripts can import project modules
        existing_pypath = os.environ.get("PYTHONPATH", "")
        env["PYTHONPATH"] = project_dir + (":" + existing_pypath if existing_pypath else "")
        # M10: Set STEPWISE_FLOW_DIR if flow_dir is available
        if self.flow_dir:
            env["STEPWISE_FLOW_DIR"] = self.flow_dir
        # Pass inputs as environment variables for convenience
        for k, v in inputs.items():
            if isinstance(v, str):
                env[k] = v
            elif isinstance(v, (dict, list)):
                env[k] = json.dumps(v)
            elif v is not None:
                env[k] = str(v)
        cwd = self.working_dir or workspace

        # M10: Resolve script path relative to flow_dir
        command = self._resolve_command(self.command)

        # Auto-detect execution mode: run simple commands directly (no shell)
        # and fall back to shell=True for multiline scripts or any command that
        # uses shell metacharacters (pipes, redirects, globs, etc.).
        #
        # If direct execution fails with FileNotFoundError (e.g. the first
        # token is a shell builtin like "exit" rather than a real executable),
        # we transparently retry through the shell so existing flows never break.
        use_shell = not _is_simple_command(command)
        shell_mode = "shell" if use_shell else "direct"
        run_arg: str | list[str] = command if use_shell else shlex.split(command)

        try:
            result = subprocess.run(
                run_arg,
                shell=use_shell,
                capture_output=True,
                text=True,
                env=env,
                cwd=cwd,
            )
        except FileNotFoundError:
            # The first token looked like a simple command but is not a real
            # binary (e.g. a shell builtin).  Fall back to shell execution.
            use_shell = True
            shell_mode = "shell"
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=cwd,
                )
            except Exception as e:
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"stdout": ""},
                        sidecar=Sidecar(),
                        workspace=workspace,
                        timestamp=_now(),
                        executor_meta={"error": str(e), "shell_mode": shell_mode},
                    ),
                )
        except Exception as e:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={"stdout": ""},
                    sidecar=Sidecar(),
                    workspace=workspace,
                    timestamp=_now(),
                    executor_meta={"error": str(e), "shell_mode": shell_mode},
                ),
            )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            # Failure
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={"stdout": stdout} if stdout else {"stdout": ""},
                    sidecar=Sidecar(),
                    workspace=workspace,
                    timestamp=_now(),
                    executor_meta={
                        "return_code": result.returncode,
                        "failed": True,
                        "shell_mode": shell_mode,
                    },
                ),
                executor_state={"failed": True, "error": stderr or f"Exit code {result.returncode}"},
            )

        # Parse stdout
        artifact: dict
        try:
            parsed = json.loads(stdout) if stdout else {}
            if isinstance(parsed, dict):
                # Check for _watch key
                if "_watch" in parsed:
                    watch_data = parsed.pop("_watch")
                    watch = WatchSpec(
                        mode=watch_data["mode"],
                        config=watch_data.get("config", {}),
                        fulfillment_outputs=watch_data.get("fulfillment_outputs", []),
                    )
                    return ExecutorResult(
                        type="watch",
                        watch=watch,
                        executor_state={"partial_output": parsed} if parsed else None,
                    )
                artifact = parsed
            else:
                artifact = {"stdout": stdout}
        except (json.JSONDecodeError, ValueError):
            artifact = {"stdout": stdout} if stdout else {"stdout": ""}

        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact=artifact,
                sidecar=Sidecar(),
                workspace=workspace,
                timestamp=_now(),
                executor_meta={"shell_mode": shell_mode},
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        # M1: synchronous, always completed or failed
        if state and state.get("failed"):
            return ExecutorStatus(state="failed", message=state.get("error"))
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass  # M1: synchronous, nothing to cancel


# ── PollExecutor ───────────────────────────────────────────────────────


class PollExecutor(Executor):
    """Suspends with a poll watch — engine periodically runs check_command.

    The check_command is a shell command that the engine runs at interval_seconds.
    - Exit 0 + JSON dict on stdout → fulfilled (dict becomes the artifact)
    - Exit 0 + empty stdout → not ready, check again next interval
    - Non-zero exit → error, retry next interval
    """

    def __init__(
        self,
        check_command: str,
        interval_seconds: int = 60,
        prompt: str = "",
    ) -> None:
        self.check_command = check_command
        self.interval_seconds = interval_seconds
        self.prompt = prompt

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Interpolate $var placeholders in check_command and prompt
        str_inputs = {k: str(v) if v is not None else "" for k, v in inputs.items()}
        check_command = Template(self.check_command).safe_substitute(str_inputs)
        prompt = Template(self.prompt).safe_substitute(str_inputs) if self.prompt else ""

        config: dict[str, Any] = {
            "check_command": check_command,
            "interval_seconds": self.interval_seconds,
        }
        if prompt:
            config["prompt"] = prompt

        return ExecutorResult(
            type="watch",
            watch=WatchSpec(
                mode="poll",
                config=config,
                fulfillment_outputs=context_to_outputs(context),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        pass


# ── ExternalExecutor ───────────────────────────────────────────────────


class ExternalExecutor(Executor):
    """Immediately suspends with an external watch."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Interpolate $var placeholders in prompt with resolved inputs
        str_inputs = {k: str(v) if v is not None else "" for k, v in inputs.items()}
        prompt = Template(self.prompt).safe_substitute(str_inputs)
        config: dict[str, Any] = {"prompt": prompt}

        return ExecutorResult(
            type="watch",
            watch=WatchSpec(
                mode="external",
                config=config,
                fulfillment_outputs=context_to_outputs(context),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        pass


def context_to_outputs(context: ExecutionContext) -> list[str]:
    """Get expected outputs from the step definition context.

    ExternalExecutor doesn't know the step's declared outputs at construction time.
    The engine sets fulfillment_outputs from the step definition after receiving the watch.
    """
    return []  # Engine will override from step definition


# ── MockLLMExecutor ────────────────────────────────────────────────────


class MockLLMExecutor(Executor):
    """Simulates LLM behavior for testing."""

    def __init__(
        self,
        failure_rate: float = 0.0,
        partial_rate: float = 0.0,
        latency_range: tuple = (0.0, 0.0),
        responses: dict[str, Any] | None = None,
    ) -> None:
        self.failure_rate = failure_rate
        self.partial_rate = partial_rate
        self.latency_range = latency_range
        self.responses = responses or {}
        self._call_count = 0

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        import random

        self._call_count += 1

        # Simulate latency
        if self.latency_range[1] > 0:
            time.sleep(random.uniform(*self.latency_range))

        # Simulate failure
        if random.random() < self.failure_rate:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace="",
                    timestamp=_now(),
                    executor_meta={"failed": True, "reason": "simulated_failure"},
                ),
                executor_state={"failed": True, "error": "Simulated LLM failure"},
            )

        # Simulate partial output
        if random.random() < self.partial_rate:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={"partial": True, "stdout": "incomplete response..."},
                    sidecar=Sidecar(),
                    workspace="",
                    timestamp=_now(),
                    executor_meta={"partial": True},
                ),
                executor_state={"partial": True},
            )

        # Check for pre-configured responses
        step_key = context.step_name
        if step_key in self.responses:
            resp = self.responses[step_key]
            if callable(resp):
                resp = resp(inputs)
            artifact = resp if isinstance(resp, dict) else {"result": resp}
        else:
            # Default: echo inputs with mock response
            artifact = {
                "result": f"mock_response_for_{context.step_name}",
                **{k: v for k, v in inputs.items()},
            }

        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact=artifact,
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        if state and state.get("failed"):
            return ExecutorStatus(state="failed", message=state.get("error"))
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


# ── LLMExecutor ───────────────────────────────────────────────────────


class LLMExecutor(Executor):
    """One-shot LLM call via OpenRouter. No retries, no tool calls beyond structured output."""

    def __init__(
        self,
        client: LLMClient,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        loop_prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.prompt_template = prompt
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.loop_prompt = loop_prompt

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        import logging
        logger = logging.getLogger("stepwise.llm")
        workspace = context.workspace_path or ""

        # Render prompt
        prompt = self._render_prompt(inputs, context)
        logger.info(f"LLM step={context.step_name} model={self.model} prompt_len={len(prompt)} output_fields={getattr(self, '_output_fields', [])}")

        # Build messages
        messages: list[dict[str, str]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})

        # Build output tool for structured output
        # The step's outputs are available from the engine context
        # We get them from the step definition via the context
        output_fields = self._get_output_fields(context)
        tools = self._build_output_tool(output_fields) if output_fields else None
        logger.info(f"LLM step={context.step_name} tools={'yes' if tools else 'no'} msgs={len(messages)}")

        # Call LLM
        try:
            response = self.client.chat_completion(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            logger.info(f"LLM step={context.step_name} response: content={response.content is not None} tool_calls={response.tool_calls is not None} usage={response.usage}")
        except Exception as e:
            logger.error(f"LLM step={context.step_name} API error: {e}")
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=workspace,
                    timestamp=_now(),
                    executor_meta={
                        "failed": True,
                        "error": str(e),
                        "model": self.model,
                        "prompt": prompt,
                    },
                ),
                executor_state={
                    "failed": True,
                    "error": str(e),
                    "error_category": classify_api_error(str(e)),
                },
            )

        # Parse output
        artifact, parse_method = self._parse_output(response, output_fields)
        logger.info(f"LLM step={context.step_name} parse: method={parse_method} artifact_keys={list(artifact.keys()) if artifact else None}")

        if artifact is None:
            # Parse failure
            logger.error(f"LLM step={context.step_name} PARSE FAILED: content_len={len(response.content) if response.content else 0} tool_calls={response.tool_calls}")
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=workspace,
                    timestamp=_now(),
                    executor_meta={
                        "failed": True,
                        "error": "Could not parse LLM output into declared output fields",
                        "model": response.model,
                        "prompt": prompt,
                        "raw_content": response.content,
                        "raw_tool_calls": response.tool_calls,
                        "usage": response.usage,
                        "cost_usd": response.cost_usd,
                        "latency_ms": response.latency_ms,
                    },
                ),
                executor_state={
                    "failed": True,
                    "error": "Output parse failure",
                },
            )

        # Validate all output fields present
        if output_fields:
            missing = [f for f in output_fields if f not in artifact]
            if missing:
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact=artifact,
                        sidecar=Sidecar(),
                        workspace=workspace,
                        timestamp=_now(),
                        executor_meta={
                            "failed": True,
                            "error": f"Missing output fields: {missing}",
                            "model": response.model,
                            "prompt": prompt,
                            "usage": response.usage,
                            "cost_usd": response.cost_usd,
                            "latency_ms": response.latency_ms,
                        },
                    ),
                    executor_state={
                        "failed": True,
                        "error": f"Missing output fields: {missing}",
                    },
                )

        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact=artifact,
                sidecar=Sidecar(),
                workspace=workspace,
                timestamp=_now(),
                executor_meta={
                    "model": response.model,
                    "prompt": prompt,
                    "response": response.content,
                    "parse_method": parse_method,
                    "usage": response.usage,
                    "cost_usd": response.cost_usd,
                    "latency_ms": response.latency_ms,
                },
            ),
        )

    def _render_prompt(self, inputs: dict, context: ExecutionContext) -> str:
        """Render the prompt template with step inputs."""
        # Convert all input values to strings for template substitution
        # Use JSON for complex types so LLMs get clean, parseable data
        str_inputs = {}
        for k, v in inputs.items():
            if isinstance(v, str):
                str_inputs[k] = v
            elif v is None:
                str_inputs[k] = ""
            elif isinstance(v, (dict, list)):
                str_inputs[k] = json.dumps(v, indent=2)
                # Flatten dict fields for dotted access: $var.field
                # e.g., reviewer: {model: "x", lens: "y"} → reviewer.model: "x", reviewer.lens: "y"
                if isinstance(v, dict):
                    for fk, fv in v.items():
                        flat_key = f"{k}.{fk}"
                        if isinstance(fv, str):
                            str_inputs[flat_key] = fv
                        elif fv is None:
                            str_inputs[flat_key] = ""
                        elif isinstance(fv, (dict, list)):
                            str_inputs[flat_key] = json.dumps(fv, indent=2)
                        else:
                            str_inputs[flat_key] = str(fv)
            else:
                str_inputs[k] = str(v)
        # Use loop_prompt on attempt > 1 if configured
        template = self.prompt_template
        if context.attempt > 1 and self.loop_prompt:
            template = self.loop_prompt
        # First pass: replace dotted vars ($var.field) before Template processing,
        # since Python's Template only handles simple $var names.
        # Sort by key length descending so $reviewer.prompt_focus matches before $reviewer.prompt
        for k in sorted(str_inputs, key=len, reverse=True):
            if "." in k:
                template = template.replace("$" + k, str_inputs[k])
        prompt = Template(template).safe_substitute(str_inputs)
        # Also support {{var}} (Jinja/Mustache-style) templates
        for k, v in str_inputs.items():
            prompt = prompt.replace("{{" + k + "}}", v)

        # M7a: Prepend chain context (prior conversation history) if present
        if context.chain_context:
            prompt = context.chain_context + "\n\n" + prompt

        # Append injected context if present
        if context.injected_context:
            prompt += "\n\nAdditional context:\n" + "\n".join(context.injected_context)

        return prompt

    def _get_output_fields(self, context: ExecutionContext) -> list[str]:
        """Get the step's declared output fields from executor config.

        The engine passes output_fields through the ExecutionContext or
        the executor config. For now, we store them on the executor instance.
        """
        # Output fields are set by the factory from step definition
        return getattr(self, "_output_fields", [])

    def _build_output_tool(self, output_fields: list[str]) -> list[dict] | None:
        """Build a tool schema to enforce structured output.

        For single-output steps (e.g. outputs: [response]), we skip tool_choice
        entirely and let the model respond naturally. The single-field shortcut
        in _parse_output will capture the raw content. This avoids models that
        truncate long-form text when forced into JSON tool call format.

        For multi-field outputs, we build a tool schema so the model returns
        structured data with all required fields.
        """
        if not output_fields:
            return None
        if len(output_fields) == 1:
            # Single-output: don't force tool_choice. Let the model respond
            # naturally and use the single-field shortcut in _parse_output.
            return None
        return [{
            "type": "function",
            "function": {
                "name": "step_output",
                "description": "Provide the step output with ALL required fields. Include your complete, detailed response for each field.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        f: {"type": "string", "description": f"Your complete response for the '{f}' field."} for f in output_fields
                    },
                    "required": output_fields,
                },
            },
        }]

    @staticmethod
    def _strip_code_fences(content: str) -> str:
        """Strip markdown code fences from content, returning inner text."""
        fence_start = content.find("```")
        fence_end = content.rfind("```")
        if fence_start != -1 and fence_end > fence_start:
            inner = content[fence_start:fence_end + 3]
            lines = inner.split("\n")
            if len(lines) >= 3:
                return "\n".join(lines[1:-1]).strip()
        return content

    def _parse_output(
        self, response: LLMResponse, output_fields: list[str]
    ) -> tuple[dict, str] | tuple[None, str]:
        """Parse LLM response into (artifact, parse_method). Returns (None, method) on failure.

        Extraction priority:
        1. For single-output steps: prefer content (single-field shortcut), then
           tool call, then JSON in content. This avoids truncation from models
           that put brief summaries in tool args and full text in content.
        2. For multi-output steps: tool call first, then JSON in content. If a
           tool call exists but content is significantly longer (3x+), prefer
           content — the model likely put the real response there.
        """
        import logging
        logger = logging.getLogger("stepwise.llm")

        # ── Single-field fast path ───────────────────────────────────────
        # For single-output steps, prefer content over tool calls.
        # Models like GPT-5.4 put full responses in content and brief
        # summaries in tool call args when forced via tool_choice.
        if len(output_fields) == 1:
            field = output_fields[0]
            if response.content and response.content.strip():
                content = response.content.strip()
                # Strip markdown code fences before JSON parse attempt
                stripped = self._strip_code_fences(content)
                # First try: if content is JSON with the expected field, extract it
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict) and field in parsed:
                        return parsed, "json_content"
                except (json.JSONDecodeError, ValueError):
                    pass
                # Otherwise use raw content as the field value
                return {field: content}, "single_field"
            # Fall back to tool call if no content
            if response.tool_calls:
                for tc in response.tool_calls:
                    if tc.get("name") == "step_output":
                        args = tc.get("arguments", {})
                        if isinstance(args, dict) and field in args:
                            return args, "tool_call"
            return None, "none"

        # ── Multi-field extraction ───────────────────────────────────────

        # Try tool call first
        tool_call_result = None
        if response.tool_calls:
            for tc in response.tool_calls:
                if tc.get("name") == "step_output":
                    args = tc.get("arguments", {})
                    if isinstance(args, dict):
                        tool_call_result = args
                        break

        # Try parsing content as JSON
        content_result = None
        if response.content:
            content = self._strip_code_fences(response.content.strip())

            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    content_result = parsed
            except (json.JSONDecodeError, ValueError):
                pass

            if content_result is None:
                # Extract JSON object from mixed content (prose + JSON)
                import re
                match = re.search(r'\{[\s\S]*\}', content)
                if match:
                    try:
                        parsed = json.loads(match.group())
                        if isinstance(parsed, dict):
                            content_result = parsed
                    except (json.JSONDecodeError, ValueError):
                        pass

        # Decide between tool call and content results
        if tool_call_result and content_result:
            # Both available — prefer whichever has more substance.
            # Some models put truncated values in tool args and full text in content.
            tc_total = sum(len(str(v)) for v in tool_call_result.values())
            ct_total = sum(len(str(v)) for v in content_result.values())
            # Check that content result has the required fields
            ct_has_fields = all(f in content_result for f in output_fields)
            if ct_has_fields and ct_total > tc_total * 3:
                logger.info(f"Preferring content ({ct_total} chars) over tool_call ({tc_total} chars)")
                return content_result, "json_content_preferred"
            return tool_call_result, "tool_call"

        if tool_call_result:
            return tool_call_result, "tool_call"

        if content_result:
            return content_result, "json_content"

        return None, "none"

    def check_status(self, state: dict) -> ExecutorStatus:
        # LLM executor is synchronous — start() blocks and returns the result
        # directly. If polled before start() returns, state is empty/None;
        # report "running" to prevent the tick loop from treating it as done.
        if not state:
            return ExecutorStatus(state="running")
        if state.get("failed"):
            return ExecutorStatus(state="failed", message=state.get("error"))
        if state.get("completed"):
            return ExecutorStatus(state="completed")
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        pass
