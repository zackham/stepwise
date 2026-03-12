"""Executor interface, registry, and built-in implementations."""

from __future__ import annotations

import json
import os
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


# ── Executor Result ────────────────────────────────────────────────────


@dataclass
class ExecutorResult:
    type: str  # "data" | "sub_job" | "watch" | "async"
    envelope: HandoffEnvelope | None = None
    sub_job_def: SubJobDefinition | None = None
    watch: WatchSpec | None = None
    executor_state: dict | None = None


# ── Executor Status (for check_status) ─────────────────────────────────


@dataclass
class ExecutorStatus:
    state: str  # "running" | "completed" | "failed"
    message: str | None = None
    result: ExecutorResult | None = None  # M4: completed async executors return result here
    error_category: str | None = None  # M4: typed failure classification
    cost_so_far: float | None = None  # M4: accumulated cost for limit enforcement


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
            NotificationDecorator,
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
                case "notification":
                    executor = NotificationDecorator(executor, dec_ref.config)
                case "fallback":
                    fallback_ref = dec_ref.config.get("fallback_ref")
                    if fallback_ref:
                        fallback_executor = self._factories[fallback_ref["type"]](
                            fallback_ref.get("config", {})
                        )
                        executor = FallbackDecorator(executor, fallback_executor, dec_ref.config)
                case _:
                    raise KeyError(f"Unknown decorator type: '{dec_ref.type}'")

        return executor


# ── ScriptExecutor ─────────────────────────────────────────────────────


class ScriptExecutor(Executor):
    """Run a shell command. Synchronous in M1."""

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

        # Write inputs to .step-io directory
        step_io_dir = Path(workspace) / ".step-io"
        step_io_dir.mkdir(parents=True, exist_ok=True)
        input_file = step_io_dir / f"{context.step_name}-{context.attempt}.input.json"
        input_file.write_text(json.dumps(inputs, default=str))

        env = {
            **os.environ,
            "JOB_ENGINE_INPUTS": str(input_file),
            "JOB_ENGINE_WORKSPACE": str(workspace),
            "STEPWISE_STEP_IO": str(step_io_dir),
        }
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
                    executor_meta={"error": str(e)},
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
                    executor_meta={"return_code": result.returncode, "failed": True},
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
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        # M1: synchronous, always completed or failed
        if state and state.get("failed"):
            return ExecutorStatus(state="failed", message=state.get("error"))
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass  # M1: synchronous, nothing to cancel


# ── HumanExecutor ──────────────────────────────────────────────────────


class HumanExecutor(Executor):
    """Immediately suspends with a human watch."""

    def __init__(self, prompt: str, notify: str | None = None) -> None:
        self.prompt = prompt
        self.notify = notify

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        config: dict[str, Any] = {"prompt": self.prompt}
        if self.notify:
            config["notify"] = self.notify

        return ExecutorResult(
            type="watch",
            watch=WatchSpec(
                mode="human",
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

    HumanExecutor doesn't know the step's declared outputs at construction time.
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
    ) -> None:
        self.client = client
        self.model = model
        self.prompt_template = prompt
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens

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
                executor_state={"failed": True, "error": str(e)},
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
            elif isinstance(v, (dict, list)):
                str_inputs[k] = json.dumps(v, indent=2)
            else:
                str_inputs[k] = str(v)
        prompt = Template(self.prompt_template).safe_substitute(str_inputs)

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
        """Build a tool schema to enforce structured output."""
        if not output_fields:
            return None
        return [{
            "type": "function",
            "function": {
                "name": "step_output",
                "description": "Provide the step output with the required fields.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        f: {"type": "string"} for f in output_fields
                    },
                    "required": output_fields,
                },
            },
        }]

    def _parse_output(
        self, response: LLMResponse, output_fields: list[str]
    ) -> tuple[dict, str] | tuple[None, str]:
        """Parse LLM response into (artifact, parse_method). Returns (None, method) on failure."""
        # 1. Tool call response (structured output)
        if response.tool_calls:
            for tc in response.tool_calls:
                if tc.get("name") == "step_output":
                    args = tc.get("arguments", {})
                    if isinstance(args, dict):
                        return args, "tool_call"

        # 2. Try parsing content as JSON
        if response.content:
            content = response.content.strip()
            # Strip markdown code fences — find first/last ``` and extract between
            fence_start = content.find("```")
            fence_end = content.rfind("```")
            if fence_start != -1 and fence_end > fence_start:
                inner = content[fence_start:fence_end + 3]
                lines = inner.split("\n")
                if len(lines) >= 3:
                    content = "\n".join(lines[1:-1]).strip()

            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed, "json_content"
            except (json.JSONDecodeError, ValueError):
                pass

            # Extract JSON object from mixed content (prose + JSON)
            import re
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, dict):
                        return parsed, "json_content"
                except (json.JSONDecodeError, ValueError):
                    pass

        # 3. Single-field shortcut
        if len(output_fields) == 1 and response.content:
            return {output_fields[0]: response.content.strip()}, "single_field"

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
