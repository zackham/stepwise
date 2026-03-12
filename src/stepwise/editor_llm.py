"""Agent-based flow editor for Stepwise — uses acpx to piggyback on local Claude."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Generator

import httpx

from stepwise.config import load_config


# ── System Prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the Stepwise flow-builder agent. You help users create, modify, and \
understand workflow YAML files. You have read-only access to the project \
directory via your tools — use them proactively to understand the codebase \
before generating flows.

## Stepwise YAML format

A Stepwise flow is a YAML file defining steps with inputs, outputs, and executors.

```yaml
name: flow-name
description: "What this flow does"

steps:
  step_name:
    run: scripts/fetch.py        # script executor (default) — reference external scripts
    outputs: [field1, field2]

  llm_step:
    executor: llm
    prompt: "Analyze {{input_var}}"
    model: anthropic/claude-sonnet-4   # OpenRouter model ID
    outputs: [result]
    inputs:
      input_var: step_name.field1

  human_step:
    executor: human
    prompt: "Review this output"
    outputs: [approved, feedback]
    inputs:
      content: llm_step.result

  agent_step:
    executor: agent
    prompt: "Build a comprehensive report on {{topic}}"
    outputs: [report]
    inputs:
      topic: $job.topic           # $job.X reads from --var flags
```

## Executor types
- **script** (default): Runs a shell command (`run:` field). Must output JSON to stdout.
- **llm**: One-shot LLM call via OpenRouter. Fields: `prompt:`, optional `model:`, `system:`, `temperature:`.
- **human**: Pauses for human input. Fields: `prompt:` for instructions.
- **agent**: Long-running AI agent via ACP. Fields: `prompt:`.

## Key rules
- Steps form a DAG based on input dependencies — parallel by default.
- `inputs:` maps local names to `step_name.output_field`.
- `outputs:` declares what the step produces (list of field names).
- `sequencing: [step_a]` forces ordering without data dependency.
- Template variables in prompts use `{{var_name}}` (from inputs).
- `$job.field` reads job-level inputs (passed via `--var` on CLI).
- Exit rules on steps enable loops (`action: loop`, `target: step_name`).
- For-each: `for_each: { source: step.list_field, as: item }` fans out.
- Routes: `routes:` block dispatches to sub-flows based on conditions.

## Directory flow convention

Flows are stored as directories with supporting files:
```
my-flow/
  FLOW.yaml          # Main workflow definition (required)
  scripts/           # Script files referenced by steps
    fetch.py
    process.sh
  prompts/           # Prompt templates for LLM/agent steps
    system.md
```

**Scripts MUST be separate files** — never embed large scripts directly in the YAML `run:` field. \
Reference them by relative path: `run: scripts/fetch.py`.

Script files MUST output valid JSON to stdout:
```python
#!/usr/bin/env python3
import json
result = {"data": "value", "count": 42}
print(json.dumps(result))
```

## Proposing file changes

When creating or modifying flows, output each file as a fenced block with a `file:` path header. \
The path is relative to the flow directory.

Use this exact format:

```file:FLOW.yaml
name: my-flow
steps:
  gather:
    run: scripts/gather.py
    outputs: [findings]
```

```file:scripts/gather.py
#!/usr/bin/env python3
import json
# ... implementation
print(json.dumps({"findings": results}))
```

Each `file:` block will be shown to the user as a reviewable card they can apply individually. \
Always propose FLOW.yaml and all supporting scripts/prompts as separate file blocks.

## Your behavior
1. **ALWAYS use tools first** — read CLAUDE.md, existing flows, skills, scripts, or other files before generating.
2. When converting skills into flows, read the SKILL.md and ALL referenced scripts first.
3. Propose complete files using the `file:path` block format above.
4. Keep flows minimal — don't add unnecessary steps.
5. Use descriptive step names (snake_case).
6. If the project has a CLAUDE.md, read it to understand conventions.
"""


# ── File Block Parser ────────────────────────────────────────────────

# Matches ```file:path\ncontent\n``` blocks
_FILE_BLOCK_RE = re.compile(
    r"```file:([^\n]+)\n(.*?)```",
    re.DOTALL,
)

# Matches ```yaml\ncontent\n``` blocks (legacy, still supported)
_YAML_BLOCK_RE = re.compile(
    r"```yaml\n(.*?)```",
    re.DOTALL,
)


def _extract_file_blocks(text: str) -> list[dict[str, str]]:
    """Extract file:path blocks from agent output text.

    Returns list of {"path": "...", "content": "..."} dicts.
    Validates paths: rejects absolute paths and traversal attempts.
    """
    blocks = []
    for match in _FILE_BLOCK_RE.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)

        # Security: reject absolute paths and traversal
        if path.startswith("/") or ".." in path.split("/"):
            continue

        blocks.append({"path": path, "content": content.rstrip("\n")})
    return blocks


def _extract_yaml_blocks(text: str) -> list[str]:
    """Extract ```yaml blocks that are NOT file: blocks (legacy format)."""
    # Remove file blocks first so we don't double-count file:FLOW.yaml
    cleaned = _FILE_BLOCK_RE.sub("", text)
    return [m.group(1).strip() for m in _YAML_BLOCK_RE.finditer(cleaned)]


# ── Session Management ───────────────────────────────────────────────

# In-memory session registry: session_id -> session_name
# Sessions persist within a chat thread for multi-turn context
_active_sessions: dict[str, str] = {}


def get_or_create_session(session_id: str | None) -> tuple[str, str]:
    """Get existing session name or create a new one.

    Returns (session_id, session_name).
    """
    if session_id and session_id in _active_sessions:
        return session_id, _active_sessions[session_id]

    new_id = uuid.uuid4().hex[:12]
    session_name = f"editor-{new_id}"
    _active_sessions[new_id] = session_name
    return new_id, session_name


def clear_session(session_id: str) -> None:
    """Remove a session from the registry."""
    _active_sessions.pop(session_id, None)


# ── Prompt Building ─────────────────────────────────────────────────

def _build_prompt(
    user_message: str,
    history: list[dict[str, str]],
    current_yaml: str | None,
    selected_step: str | None,
    flow_dir_listing: str | None = None,
) -> str:
    """Build a single prompt string with system context, history, and user message."""
    parts = [SYSTEM_PROMPT]

    if flow_dir_listing:
        parts.append(f"\n## Current Flow Directory\n```\n{flow_dir_listing}\n```")

    if current_yaml:
        parts.append(f"\n## Current Flow YAML\n```yaml\n{current_yaml}\n```")
        if selected_step:
            parts.append(f"\nThe user has step `{selected_step}` selected.")

    if history:
        parts.append("\n## Conversation History")
        for msg in history[-8:]:
            prefix = "User" if msg.get("role") == "user" else "Assistant"
            parts.append(f"\n**{prefix}:** {msg.get('content', '')}")

    parts.append(f"\n## Current Request\n\n{user_message}")

    return "\n".join(parts)


def _get_flow_dir_listing(project_dir: Path, flow_path: str | None) -> str | None:
    """Get a tree listing of the flow directory if it's a directory flow."""
    if not flow_path:
        return None

    # Resolve flow directory from the flow path
    full_path = (project_dir / flow_path).resolve()
    if full_path.name == "FLOW.yaml":
        flow_dir = full_path.parent
    elif full_path.is_dir():
        flow_dir = full_path
    else:
        # Single-file flow, no directory to list
        return None

    if not flow_dir.is_dir():
        return None

    try:
        flow_dir.relative_to(project_dir.resolve())
    except ValueError:
        return None

    lines = []
    _tree_walk(flow_dir, flow_dir, lines, depth=0, max_depth=3)
    return "\n".join(lines) if lines else None


def _tree_walk(base: Path, current: Path, lines: list[str], depth: int, max_depth: int) -> None:
    """Build a simple tree listing."""
    skip = {".git", "__pycache__", ".venv", "node_modules", ".stepwise"}
    indent = "  " * depth
    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return
    for entry in entries:
        if entry.name in skip or entry.name.startswith("."):
            continue
        rel = entry.relative_to(base)
        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            if depth < max_depth:
                _tree_walk(base, entry, lines, depth + 1, max_depth)
        else:
            lines.append(f"{indent}{entry.name}")


# ── acpx Agent Loop ─────────────────────────────────────────────────


def chat_stream(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    current_yaml: str | None = None,
    selected_step: str | None = None,
    project_dir: Path | None = None,
    agent: str = "claude",
    session_id: str | None = None,
    flow_path: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Stream an agent chat response as NDJSON chunks.

    Uses acpx (local Claude/Codex) if available, falls back to OpenRouter.
    """
    project_dir = project_dir or Path(".")

    acpx_path = shutil.which("acpx")
    if acpx_path and agent != "simple":
        yield from _acpx_agent_loop(
            acpx_path, agent, user_message, history, current_yaml,
            selected_step, project_dir, session_id, flow_path,
        )
    else:
        config = load_config()
        if config.openrouter_api_key:
            yield from _openrouter_fallback(
                user_message, history, current_yaml, selected_step, config
            )
        else:
            yield {
                "type": "error",
                "content": "No agent available. Install Claude Code (acpx) or configure an OpenRouter API key.",
            }


def _acpx_agent_loop(
    acpx_path: str,
    agent: str,
    user_message: str,
    history: list[dict[str, str]] | None,
    current_yaml: str | None,
    selected_step: str | None,
    project_dir: Path,
    session_id: str | None = None,
    flow_path: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Run a flow-builder agent via acpx with streaming NDJSON output."""
    # Get or create persistent session
    sid, session_name = get_or_create_session(session_id)

    # Build prompt with flow directory context
    flow_listing = _get_flow_dir_listing(project_dir, flow_path)
    prompt = _build_prompt(
        user_message, history or [], current_yaml, selected_step, flow_listing,
    )

    # Write prompt to file (avoids shell escaping issues)
    io_dir = project_dir / ".stepwise" / "editor-io"
    io_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = io_dir / f"{session_name}.md"
    prompt_file.write_text(prompt)

    try:
        # Clear CLAUDECODE env to allow nested agent sessions
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        # Ensure session exists
        subprocess.run(
            [acpx_path, "--cwd", str(project_dir),
             agent, "sessions", "ensure", "--name", session_name],
            capture_output=True, timeout=30, env=env,
        )

        # Spawn acpx — read-only mode, writes denied without prompting
        args = [
            acpx_path, "--format", "json",
            "--approve-reads",
            "--non-interactive-permissions", "deny",
            "--cwd", str(project_dir),
            agent, "-s", session_name,
            "--file", str(prompt_file),
        ]

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Emit session_id so frontend can persist it
        yield {"type": "session", "session_id": sid}

        # Stream NDJSON events from stdout with keepalive support.
        # acpx goes silent during long thinking phases — without
        # periodic keepalives the HTTP stream goes idle and browsers
        # or reverse proxies close the connection.
        full_text = ""
        total_tokens = 0
        _KEEPALIVE_INTERVAL = 15  # seconds

        event_queue: queue.Queue[bytes | None] = queue.Queue()

        def _reader() -> None:
            """Read stdout lines into a queue, then signal EOF."""
            try:
                for raw_line in proc.stdout:
                    event_queue.put(raw_line)
            finally:
                event_queue.put(None)  # sentinel

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        while True:
            try:
                raw_line = event_queue.get(timeout=_KEEPALIVE_INTERVAL)
            except queue.Empty:
                # No output for KEEPALIVE_INTERVAL seconds — send ping
                yield {"type": "keepalive"}
                continue

            if raw_line is None:
                break  # EOF

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            update = event.get("params", {}).get("update", {})
            event_type = update.get("sessionUpdate")

            if event_type == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    text = content.get("text", "")
                    if text:
                        full_text += text
                        yield {"type": "text", "content": text}

            elif event_type == "tool_call":
                yield {
                    "type": "tool_use",
                    "tool_name": update.get("title", ""),
                    "tool_use_id": update.get("toolCallId", ""),
                    "tool_input": {},
                }

            elif event_type == "tool_call_update":
                if update.get("status") == "completed":
                    yield {
                        "type": "tool_result",
                        "tool_use_id": update.get("toolCallId", ""),
                        "tool_output": "",
                        "is_error": False,
                    }

            elif event_type == "usage_update":
                total_tokens = update.get("used", total_tokens)

        reader_thread.join(timeout=5)
        proc.wait()

        # Extract file blocks (new format: ```file:path)
        file_blocks = _extract_file_blocks(full_text)
        for i, fb in enumerate(file_blocks):
            yield {
                "type": "file_block",
                "path": fb["path"],
                "content": fb["content"],
                "apply_id": f"file-{i}",
            }

        # Extract legacy YAML blocks (```yaml that aren't file: blocks)
        yaml_blocks = _extract_yaml_blocks(full_text)
        for i, block in enumerate(yaml_blocks):
            yield {"type": "yaml", "content": block, "apply_id": f"yaml-{i}"}

        yield {
            "type": "done",
            "model": f"acpx/{agent}",
            "cost_usd": None,
            "input_tokens": total_tokens,
            "output_tokens": 0,
        }

    except Exception as e:
        yield {"type": "error", "content": str(e)}
    finally:
        prompt_file.unlink(missing_ok=True)


# ── OpenRouter Fallback ──────────────────────────────────────────────


def _openrouter_fallback(
    user_message: str,
    history: list[dict[str, str]] | None,
    current_yaml: str | None,
    selected_step: str | None,
    config: Any,
) -> Generator[dict[str, Any], None, None]:
    """Simple streaming chat via OpenRouter (no tool use)."""
    model = config.default_model or "anthropic/claude-sonnet-4"
    model_id = config.resolve_model(model)

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if current_yaml:
        ctx = f"Current flow YAML:\n```yaml\n{current_yaml}\n```"
        if selected_step:
            ctx += f"\n\nThe user has step `{selected_step}` selected."
        messages.append({"role": "system", "content": ctx})
    for msg in (history or [])[-8:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

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

            # Extract file blocks and yaml blocks from OpenRouter output too
            file_blocks = _extract_file_blocks(full_content)
            for i, fb in enumerate(file_blocks):
                yield {
                    "type": "file_block",
                    "path": fb["path"],
                    "content": fb["content"],
                    "apply_id": f"file-{i}",
                }

            yaml_blocks = _extract_yaml_blocks(full_content)
            for i, block in enumerate(yaml_blocks):
                yield {"type": "yaml", "content": block, "apply_id": f"yaml-{i}"}

            yield {"type": "done", "model": model_id, "cost_usd": None}

    except httpx.TimeoutException:
        yield {"type": "error", "content": "Request timed out"}
    except Exception as e:
        yield {"type": "error", "content": str(e)}
