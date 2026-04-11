"""CLI LLM client: uses native ACP transport for one-shot LLM completions.

Fallback when no OpenRouter API key is configured but an ACP agent is available.
Implements the LLMClient protocol from llm_client.py.

Limitations:
- model parameter is ignored — the agent CLI uses its configured default
- temperature / max_tokens are not controllable through the CLI
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from stepwise.llm_client import LLMResponse

logger = logging.getLogger("stepwise.cli_llm")


def detect_cli_backend() -> tuple[str] | None:
    """Detect if an ACP agent command is available on PATH.

    Returns:
        (agent_name,) tuple if available, None otherwise.
    """
    agent = os.environ.get("STEPWISE_DEFAULT_AGENT", "claude")
    try:
        from stepwise.agent_registry import get_agent
        config = get_agent(agent)
        # Check if the base command is available on PATH
        base_cmd = config.command[0] if config.command else None
        if base_cmd and shutil.which(base_cmd):
            return (agent,)
    except (ValueError, ImportError):
        pass

    # Check other known agents if default wasn't found
    for fallback_agent, base_cmd in [("claude", "npx"), ("codex", "npx"), ("aloop", "aloop")]:
        if fallback_agent == agent:
            continue
        if shutil.which(base_cmd):
            return (fallback_agent,)

    return None


class CliLLMClient:
    """LLM client that delegates to a native ACP agent for one-shot completions.

    Spawns an ACP subprocess, sends a prompt via JSON-RPC, and collects
    the response from session/update notifications.
    """

    def __init__(self, agent: str = "claude") -> None:
        self.agent = agent

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a completion request via native ACP transport."""
        start = time.monotonic()

        # Combine messages into a single prompt
        prompt = self._build_prompt(messages, tools)

        # Write NDJSON output to tempfile for extraction
        tmpdir = tempfile.mkdtemp(prefix="stepwise-cli-llm-")
        try:
            return self._run_acp(tmpdir, prompt, start)
        finally:
            import shutil as _shutil
            _shutil.rmtree(tmpdir, ignore_errors=True)

    def _run_acp(
        self, tmpdir: str, prompt: str, start: float,
    ) -> LLMResponse:
        from stepwise.acp_client import ACPClient
        from stepwise.acp_ndjson import extract_cost, extract_final_text
        from stepwise.acp_transport import JsonRpcTransport
        from stepwise.agent_registry import resolve_config

        output_path = os.path.join(tmpdir, "output.jsonl")

        # Resolve agent config
        resolved = resolve_config(self.agent, {}, tmpdir)

        # Build env: clear CLAUDECODE, add agent env vars
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env.update(resolved.env_vars)

        proc = None
        transport = None

        try:
            # Spawn ACP server subprocess
            proc = subprocess.Popen(
                resolved.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )

            transport = JsonRpcTransport(proc)
            transport.start()
            client = ACPClient(transport)

            # ACP handshake
            client.initialize()

            # Create session
            session_id = client.new_session(tmpdir, f"cli-llm-{os.getpid()}")

            # Collect NDJSON output by registering notification handler
            ndjson_lines: list[str] = []

            def _on_session_update(params: dict) -> None:
                import json
                ndjson_lines.append(json.dumps({"params": params}))

            transport.on_notification("session/update", _on_session_update)

            # Send prompt and wait for completion
            future = transport.send_request("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": prompt}],
            })

            # Wait for prompt to complete (with timeout)
            result = future.result(timeout=900)

            # Write collected NDJSON to file for extraction helpers
            Path(output_path).write_text("\n".join(ndjson_lines) + "\n")

            elapsed_ms = int((time.monotonic() - start) * 1000)

            text = extract_final_text(output_path)
            cost = extract_cost(output_path)

            if not text:
                raise RuntimeError(
                    "CLI LLM call returned no text content"
                )

            return LLMResponse(
                content=text or None,
                tool_calls=None,
                model=f"cli:{self.agent}",
                cost_usd=cost,
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # If we got a timeout from the future
            if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
                raise RuntimeError(f"CLI LLM call timed out after 900s: {e}") from e
            raise
        finally:
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

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
