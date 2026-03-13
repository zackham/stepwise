"""CLI LLM client: uses acpx exec for one-shot LLM completions.

Fallback when no OpenRouter API key is configured but acpx is available.
Implements the LLMClient protocol from llm_client.py.

Limitations:
- model parameter is ignored — the agent CLI uses its configured default
- temperature / max_tokens are not controllable through the CLI
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time

from stepwise.llm_client import LLMResponse

logger = logging.getLogger("stepwise.cli_llm")


def detect_cli_backend() -> tuple[str, str] | None:
    """Detect if acpx is available for CLI-based LLM calls.

    Returns:
        (acpx_path, agent_name) if available, None otherwise.
    """
    acpx_path = shutil.which(os.environ.get("ACPX_PATH", "acpx"))
    if acpx_path is None:
        return None
    agent = os.environ.get("STEPWISE_DEFAULT_AGENT", "claude")
    return (acpx_path, agent)


class CliLLMClient:
    """LLM client that delegates to acpx exec for one-shot completions.

    Uses `acpx --format json --approve-all <agent> exec -f <prompt_file>`
    which outputs NDJSON on stderr with agent_message_chunk events.
    """

    def __init__(self, acpx_path: str = "acpx", agent: str = "claude") -> None:
        self.acpx_path = acpx_path
        self.agent = agent

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a completion request via acpx exec."""
        start = time.monotonic()

        # Combine messages into a single prompt
        prompt = self._build_prompt(messages, tools)

        # Write prompt to tempfile
        tmpdir = tempfile.mkdtemp(prefix="stepwise-cli-llm-")
        prompt_file = os.path.join(tmpdir, "prompt.md")
        with open(prompt_file, "w") as f:
            f.write(prompt)

        # Build command
        cmd = [
            self.acpx_path, "--format", "json", "--approve-all",
            "--cwd", tmpdir,
            self.agent, "exec", "-f", prompt_file,
        ]

        # Remove CLAUDECODE from env (required for nested sessions)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"CLI LLM call timed out after 600s: {e}") from e
        except FileNotFoundError as e:
            raise RuntimeError(
                f"acpx not found at '{self.acpx_path}'. "
                f"Install it or set ACPX_PATH: {e}"
            ) from e

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Parse NDJSON from stderr (exec mode outputs to stderr)
        ndjson = result.stderr or ""

        if result.returncode != 0:
            logger.warning(
                "acpx exec exited with code %d, attempting to extract partial response",
                result.returncode,
            )

        text = self._extract_text(ndjson)
        cost = self._extract_cost(ndjson)

        if not text and result.returncode != 0:
            raise RuntimeError(
                f"CLI LLM call failed (exit code {result.returncode}): "
                f"{result.stderr[:500] if result.stderr else 'no output'}"
            )

        return LLMResponse(
            content=text or None,
            tool_calls=None,
            model=f"cli:{self.agent}",
            cost_usd=cost,
            latency_ms=elapsed_ms,
        )

    def _build_prompt(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
    ) -> str:
        """Combine messages into a single prompt string.

        If tools are provided, extract output field names from the step_output
        tool schema and append JSON output instructions.
        """
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                parts.append(content)
            else:
                parts.append(content)

        # If tools provided, add JSON output instructions
        if tools:
            fields = self._extract_output_fields(tools)
            if fields:
                field_spec = ", ".join(f'"{f}": "string"' for f in fields)
                parts.append(
                    f"\nRespond with ONLY a valid JSON object: {{{field_spec}}}. "
                    f"No other text."
                )

        return "\n\n".join(parts)

    @staticmethod
    def _extract_output_fields(tools: list[dict]) -> list[str]:
        """Extract field names from the step_output tool schema."""
        for tool in tools:
            func = tool.get("function", {})
            if func.get("name") == "step_output":
                params = func.get("parameters", {})
                props = params.get("properties", {})
                return list(props.keys())
        return []

    @staticmethod
    def _extract_text(ndjson: str) -> str:
        """Extract text from agent_message_chunk events in NDJSON."""
        chunks: list[str] = []
        for line in ndjson.splitlines():
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
        return "".join(chunks)

    @staticmethod
    def _extract_cost(ndjson: str) -> float | None:
        """Extract cost from the last usage_update event in NDJSON."""
        last_cost = None
        for line in ndjson.splitlines():
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
        return last_cost
