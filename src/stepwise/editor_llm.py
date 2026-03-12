"""LLM-assisted flow editing for the Stepwise editor (M13)."""

from __future__ import annotations

import json
import re
from typing import Any, Generator

import httpx

from stepwise.config import load_config


SYSTEM_PROMPT = """\
You are a Stepwise flow editor assistant. You help users create and modify workflow YAML files.

## Stepwise YAML format

A Stepwise flow is a YAML file defining steps with inputs, outputs, and executors.

```yaml
name: flow-name
steps:
  step_name:
    run: shell-command          # script executor
    outputs: [field1, field2]

  llm_step:
    executor: llm
    prompt: "Analyze {{input_var}}"
    model: anthropic/claude-sonnet-4
    outputs: [result]
    inputs:
      input_var: step_name.field1

  human_step:
    executor: human
    prompt: "Review this output"
    outputs: [approved]

  agent_step:
    executor: agent
    prompt: "Build a report"
    outputs: [report]
```

## Executor types
- **script**: Runs a shell command. Use `run:` field.
- **llm**: One-shot LLM call. Use `prompt:`, optional `model:`, `system:`, `temperature:`.
- **human**: Pauses for human input. Use `prompt:` for instructions.
- **agent**: Long-running AI agent. Use `prompt:`.

## Key rules
- Steps execute based on input dependencies (DAG)
- `inputs:` maps local names to `step_name.output_field`
- `outputs:` declares what the step produces
- `sequencing: [step_a]` forces ordering without data dependency
- Template variables in prompts use `{{var_name}}`

## Your behavior
- When asked to CREATE a flow, output a complete YAML block
- When asked to MODIFY a flow, output the full modified YAML
- When asked to EXPLAIN, describe what the flow/step does
- Always wrap YAML in ```yaml code blocks
- Keep flows minimal — don't add unnecessary steps
- Use descriptive step names (snake_case)
"""


def _build_messages(
    user_message: str,
    history: list[dict[str, str]],
    current_yaml: str | None,
    selected_step: str | None,
) -> list[dict[str, str]]:
    """Build the message list for the LLM call."""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add current flow context
    if current_yaml:
        context = f"Current flow YAML:\n```yaml\n{current_yaml}\n```"
        if selected_step:
            context += f"\n\nThe user has step `{selected_step}` selected."
        messages.append({"role": "system", "content": context})

    # Add conversation history (capped)
    token_budget = 8
    for msg in history[-token_budget:]:
        messages.append(msg)

    messages.append({"role": "user", "content": user_message})
    return messages


def chat_stream(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    current_yaml: str | None = None,
    selected_step: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Stream a chat response, yielding NDJSON chunks.

    Chunk types:
    - {"type": "text", "content": "..."}
    - {"type": "yaml", "content": "...", "apply_id": "..."}
    - {"type": "done", "model": "...", "cost_usd": ...}
    - {"type": "error", "content": "..."}
    """
    config = load_config()

    if not config.openrouter_api_key:
        yield {"type": "error", "content": "No OpenRouter API key configured. Set it in Settings."}
        return

    model = config.default_model or "anthropic/claude-sonnet-4"
    model_id = config.resolve_model(model)

    messages = _build_messages(
        user_message,
        history or [],
        current_yaml,
        selected_step,
    )

    headers = {
        "Authorization": f"Bearer {config.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://stepwise.local",
        "X-Title": "Stepwise Editor",
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": True,
    }

    try:
        with httpx.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                body = resp.read().decode(errors="replace")[:500]
                yield {"type": "error", "content": f"OpenRouter error {resp.status_code}: {body}"}
                return

            full_content = ""
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    full_content += token
                    yield {"type": "text", "content": token}

            # Extract YAML blocks from the full content
            yaml_blocks = re.findall(r"```yaml\n(.*?)```", full_content, re.DOTALL)
            for i, block in enumerate(yaml_blocks):
                yield {
                    "type": "yaml",
                    "content": block.strip(),
                    "apply_id": f"yaml-{i}",
                }

            # Extract cost from response headers
            cost = None
            cost_header = resp.headers.get("x-openrouter-cost")
            if cost_header:
                try:
                    cost = float(cost_header)
                except ValueError:
                    pass

            yield {"type": "done", "model": model_id, "cost_usd": cost}

    except httpx.TimeoutException:
        yield {"type": "error", "content": "Request timed out"}
    except Exception as e:
        yield {"type": "error", "content": str(e)}
