"""Agent-based flow editor for Stepwise — uses ACP agents for flow building."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Generator

import httpx

from stepwise.config import load_config

logger = logging.getLogger("stepwise.editor_llm")


# ── System Prompt ────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are the Stepwise flow-builder agent. You help users create, modify, and \
understand workflow YAML files. You have full read/write access to the project \
directory via your tools.

## Your workspace

You are working on a flow at: `{flow_dir}`

**IMPORTANT: Only write files inside that flow directory.** You can read files \
anywhere in the project to understand the codebase, but all file writes (Write, Edit) \
must target paths within the flow directory above. Do not modify files outside it.

Use your Write and Edit tools directly to create and modify files — do not output \
file contents as fenced code blocks for the user to copy. Just write them.

**DO NOT read Stepwise source code to learn the YAML format.** The full reference is below.

{flow_reference}

## Your behavior
1. Read existing flows, CLAUDE.md, and referenced files to understand project context before generating.
2. When converting skills into flows, read the SKILL.md and ALL referenced scripts first.
3. Write files directly using your Write and Edit tools. Create the directory structure as needed.
4. Keep flows minimal — don't add unnecessary steps.
5. Use descriptive step names (snake_case).
6. After generating, validate with `stepwise validate <flow>`.
"""

# Canonical flow reference — lives alongside this module in the package
_FLOW_REFERENCE_PATH = Path(__file__).parent / "flow-reference.md"
_flow_reference_cache: str | None = None


def _load_flow_reference() -> str:
    """Load the flow reference doc. Cached after first read."""
    global _flow_reference_cache
    if _flow_reference_cache is None:
        if _FLOW_REFERENCE_PATH.exists():
            _flow_reference_cache = _FLOW_REFERENCE_PATH.read_text()
        else:
            _flow_reference_cache = "(Flow reference not found — expected at src/stepwise/flow-reference.md)"
    return _flow_reference_cache


def get_system_prompt(flow_dir: str = "(no flow selected)") -> str:
    """Build the full system prompt with flow reference injected."""
    return _SYSTEM_PROMPT_TEMPLATE.replace("{flow_dir}", flow_dir).replace(
        "{flow_reference}", _load_flow_reference()
    )


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
    flow_dir_path: str | None = None,
) -> str:
    """Build a single prompt string with system context, history, and user message."""
    system = get_system_prompt(flow_dir_path or "(no flow selected)")
    parts = [system]

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


# ── ACP Agent Loop ──────────────────────────────────────────────────


def _is_agent_available(agent_name: str) -> bool:
    """Check if an ACP agent can be resolved (has a config entry)."""
    try:
        from stepwise.agent_registry import get_agent
        get_agent(agent_name)
        return True
    except (ValueError, ImportError):
        return False


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

    Uses ACP agent (native protocol) if available, falls back to OpenRouter.
    """
    project_dir = project_dir or Path(".")

    if agent != "simple" and _is_agent_available(agent):
        yield from _acp_agent_loop(
            agent, user_message, history, current_yaml,
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
                "content": "No agent available. Install claude-agent-acp or configure an OpenRouter API key.",
            }


def _acp_agent_loop(
    agent: str,
    user_message: str,
    history: list[dict[str, str]] | None,
    current_yaml: str | None,
    selected_step: str | None,
    project_dir: Path,
    session_id: str | None = None,
    flow_path: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Run a flow-builder agent via native ACP with streaming events."""
    from stepwise.acp_client import ACPClient
    from stepwise.acp_transport import JsonRpcTransport
    from stepwise.agent_registry import resolve_config

    # Get or create persistent session
    sid, session_name = get_or_create_session(session_id)

    # Resolve the flow directory path for the system prompt
    flow_dir_abs = None
    if flow_path:
        fp = (project_dir / flow_path).resolve()
        if fp.name == "FLOW.yaml":
            flow_dir_abs = str(fp.parent)
        elif fp.is_dir():
            flow_dir_abs = str(fp)
        else:
            flow_dir_abs = str(fp.parent)

    # Build prompt with flow directory context
    flow_listing = _get_flow_dir_listing(project_dir, flow_path)
    prompt = _build_prompt(
        user_message, history or [], current_yaml, selected_step,
        flow_listing, flow_dir_path=flow_dir_abs,
    )

    proc = None
    transport = None

    try:
        # Resolve agent config
        resolved = resolve_config(agent, {}, str(project_dir))

        # Build env: clear CLAUDECODE, add agent env vars
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env.update(resolved.env_vars)

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
        acp_session_id = client.new_session(str(project_dir), session_name)

        # Emit session_id so frontend can persist it
        yield {"type": "session", "session_id": sid}

        # Stream ACP events via notification handler with keepalive support.
        # Agent goes silent during long thinking phases — without periodic
        # keepalives the HTTP stream goes idle and browsers or reverse
        # proxies close the connection.
        total_tokens = 0
        files_written: list[str] = []
        tool_kinds: dict[str, str] = {}  # tool_id -> kind
        _KEEPALIVE_INTERVAL = 15  # seconds

        event_queue: queue.Queue[dict | None] = queue.Queue()

        def _on_session_update(params: dict) -> None:
            """Notification handler: push ACP events into the queue."""
            event_queue.put(params)

        transport.on_notification("session/update", _on_session_update)

        # Send prompt (non-blocking — use the future to detect completion)
        prompt_future = transport.send_request("session/prompt", {
            "sessionId": acp_session_id,
            "prompt": [{"type": "text", "text": prompt}],
        })

        while True:
            # Use short timeout when prompt is done (just drain remaining),
            # full keepalive interval otherwise.
            wait_timeout = 0.1 if prompt_future.done() else _KEEPALIVE_INTERVAL
            try:
                params = event_queue.get(timeout=wait_timeout)
            except queue.Empty:
                if prompt_future.done():
                    break  # Prompt completed and queue drained
                yield {"type": "keepalive"}
                continue

            if params is None:
                break

            update = params.get("update", {})
            event_type = update.get("sessionUpdate")

            if event_type == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    text = content.get("text", "")
                    if text:
                        yield {"type": "text", "content": text}

            elif event_type == "tool_call":
                tool_id = update.get("toolCallId", "")
                title = update.get("title", "")
                kind = update.get("kind", "")
                raw_input = update.get("rawInput", {})
                tool_kinds[tool_id] = kind
                yield {
                    "type": "tool_use",
                    "tool_name": title,
                    "tool_use_id": tool_id,
                    "tool_input": raw_input if isinstance(raw_input, dict) else {},
                    "tool_kind": kind,
                }

            elif event_type == "tool_call_update":
                tool_id = update.get("toolCallId", "")
                status = update.get("status")
                kind = update.get("kind", "") or tool_kinds.get(tool_id, "")
                title = update.get("title", "")

                # Track file writes for the files_changed event
                if kind in ("edit", "write") and status == "completed":
                    prev_len = len(files_written)
                    for loc in update.get("locations", []):
                        path = loc.get("path", "")
                        if path:
                            files_written.append(path)
                    # locations may be absent for write ops — title has the path
                    if len(files_written) == prev_len and title:
                        files_written.append(title)

                if status in ("completed", "failed"):
                    yield {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "tool_output": title,
                        "is_error": status == "failed",
                        "tool_kind": kind,
                    }

            elif event_type == "usage_update":
                total_tokens = update.get("used", total_tokens)

        # Always tell the frontend to refresh — the agent likely modified files
        # even if we couldn't extract paths from ACP events
        yield {"type": "files_changed", "paths": files_written or []}

        yield {
            "type": "done",
            "model": f"acp/{agent}",
            "cost_usd": None,
            "input_tokens": total_tokens,
            "output_tokens": 0,
        }

    except Exception as e:
        yield {"type": "error", "content": str(e)}
    finally:
        if transport:
            transport.close()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


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

    messages: list[dict[str, str]] = [{"role": "system", "content": get_system_prompt()}]
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
