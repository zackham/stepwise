"""Context chain compilation for M7a.

Loads transcripts from prior chain members, compiles them into an XML
context block, and handles overflow. The compiled prefix is a deterministic
function of the workflow topology and completed run transcripts — no
ordering ambiguity from parallel execution.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stepwise.models import ChainConfig, StepDefinition, WorkflowDefinition


@dataclass
class Transcript:
    """A captured agent conversation for chain context injection."""
    step: str
    attempt: int
    chain: str
    label: str
    token_count: int
    messages: list[dict]

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "attempt": self.attempt,
            "chain": self.chain,
            "label": self.label,
            "token_count": self.token_count,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Transcript:
        return cls(
            step=d["step"],
            attempt=d["attempt"],
            chain=d["chain"],
            label=d.get("label", ""),
            token_count=d.get("token_count", 0),
            messages=d.get("messages", []),
        )


def topological_chain_order(
    workflow: WorkflowDefinition, chain_name: str
) -> list[str]:
    """Return chain member step names in topological order.

    Ties broken alphabetically for determinism. Only includes steps
    that are members of the named chain.
    """
    members = {n for n, s in workflow.steps.items() if s.chain == chain_name}
    if not members:
        return []

    # Build adjacency from full workflow DAG, filtered to chain members
    adj: dict[str, set[str]] = {n: set() for n in members}
    in_degree: dict[str, int] = {n: 0 for n in members}

    for name in members:
        step = workflow.steps[name]
        deps: set[str] = set()
        for binding in step.inputs:
            if binding.source_step != "$job" and binding.source_step in members:
                deps.add(binding.source_step)
        for seq in step.sequencing:
            if seq in members:
                deps.add(seq)
        for dep in deps:
            adj[dep].add(name)
            in_degree[name] += 1

    # Kahn's algorithm with alphabetical tie-breaking
    queue: list[str] = sorted(n for n, d in in_degree.items() if d == 0)
    result: list[str] = []

    while queue:
        node = queue.pop(0)  # alphabetically first
        result.append(node)
        neighbors = sorted(adj[node])
        for neighbor in neighbors:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                # Insert in sorted position
                queue.append(neighbor)
                queue.sort()

    return result


def load_transcript(workspace_path: str, step_name: str, attempt: int) -> Transcript | None:
    """Load a transcript file from the step-io directory."""
    path = Path(workspace_path) / ".step-io" / f"{step_name}-{attempt}.transcript.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return Transcript.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def save_transcript(workspace_path: str, transcript: Transcript) -> Path:
    """Save a transcript to the step-io directory. Returns the file path."""
    step_io = Path(workspace_path) / ".step-io"
    step_io.mkdir(parents=True, exist_ok=True)
    path = step_io / f"{transcript.step}-{transcript.attempt}.transcript.json"
    path.write_text(json.dumps(transcript.to_dict(), indent=2))
    return path


def collect_chain_transcripts(
    workflow: WorkflowDefinition,
    chain_name: str,
    chain_config: ChainConfig,
    current_step: str,
    workspace_path: str,
    get_latest_completed_attempt: Any,  # callable(step_name) -> int | None
) -> list[Transcript]:
    """Collect transcripts from prior chain members in topological order.

    Args:
        workflow: The workflow definition.
        chain_name: Name of the chain to collect.
        chain_config: Configuration for this chain.
        current_step: The step about to run (excluded from collection).
        workspace_path: Job workspace path for loading transcript files.
        get_latest_completed_attempt: Callable that returns the latest
            completed attempt number for a step, or None if no completed run.

    Returns:
        List of Transcripts in topological order, prior to current_step.
    """
    ordered = topological_chain_order(workflow, chain_name)

    # Only include steps that come before current_step in topo order
    try:
        current_idx = ordered.index(current_step)
    except ValueError:
        return []
    prior_steps = ordered[:current_idx]

    transcripts: list[Transcript] = []
    for step_name in prior_steps:
        attempt = get_latest_completed_attempt(step_name)
        if attempt is None:
            continue

        # Derive label from step definition
        step_def = workflow.steps.get(step_name)
        label = (step_def.chain_label if step_def else None) or step_name

        if chain_config.accumulation == "latest":
            # Only the most recent attempt
            t = load_transcript(workspace_path, step_name, attempt)
            if t:
                t.chain = chain_name
                t.label = label
                transcripts.append(t)
        else:
            # "full" — all completed attempts for this step
            for a in range(1, attempt + 1):
                t = load_transcript(workspace_path, step_name, a)
                if t:
                    t.chain = chain_name
                    t.label = label
                    transcripts.append(t)

    return transcripts


def apply_overflow(
    transcripts: list[Transcript],
    max_tokens: int,
    strategy: str = "drop_oldest",
) -> list[Transcript]:
    """Apply overflow strategy to keep total tokens under max_tokens.

    Drops whole transcripts — never truncates mid-conversation.
    """
    total = sum(t.token_count for t in transcripts)
    if total <= max_tokens:
        return transcripts

    if strategy == "drop_middle":
        # Keep first + remove from middle until under budget
        if len(transcripts) <= 2:
            # Can't drop middle with 2 or fewer — fall back to drop_oldest
            return _drop_oldest(transcripts, max_tokens)

        first = transcripts[0]
        remaining_budget = max_tokens - first.token_count
        if remaining_budget <= 0:
            return [first]  # Even first exceeds budget, but keep it

        # Keep as many from the end as fit
        kept_tail: list[Transcript] = []
        for t in reversed(transcripts[1:]):
            if remaining_budget >= t.token_count:
                kept_tail.insert(0, t)
                remaining_budget -= t.token_count
            else:
                break
        return [first] + kept_tail

    # Default: drop_oldest
    return _drop_oldest(transcripts, max_tokens)


def _drop_oldest(transcripts: list[Transcript], max_tokens: int) -> list[Transcript]:
    """Drop oldest transcripts until under budget."""
    total = sum(t.token_count for t in transcripts)
    result = list(transcripts)
    while total > max_tokens and len(result) > 1:
        dropped = result.pop(0)
        total -= dropped.token_count
    return result


def compile_chain_prefix(
    transcripts: list[Transcript],
    chain_name: str,
    include_thinking: bool = False,
) -> str:
    """Compile transcripts into an XML context block for prompt injection.

    Returns an XML string that can be prepended to the agent's prompt.
    The format prevents identity confusion (clearly labeled as prior context)
    and sidesteps API role constraints.
    """
    if not transcripts:
        return ""

    parts: list[str] = [f'<prior_context chain="{_esc(chain_name)}">']

    for t in transcripts:
        label_attr = f' label="{_esc(t.label)}"' if t.label else ""
        parts.append(
            f'  <step name="{_esc(t.step)}" attempt="{t.attempt}"'
            f'{label_attr} tokens="{t.token_count}">'
        )

        for msg in t.messages:
            role = _get_role(msg)
            content = _format_message_content(msg, include_thinking)
            if content:
                parts.append(f"    <{role}>{content}</{role}>")

        parts.append("  </step>")

    parts.append("</prior_context>")
    return "\n".join(parts)


def _esc(s: str) -> str:
    """Escape for XML attributes."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _get_role(msg: dict) -> str:
    """Extract role from a message dict (handles both Anthropic and acpx formats)."""
    if "role" in msg:
        return msg["role"]
    if "User" in msg:
        return "user"
    if "Agent" in msg:
        return "assistant"
    return "unknown"


def _format_message_content(msg: dict, include_thinking: bool) -> str:
    """Format message content for XML injection."""
    # Anthropic-style normalized format
    if "role" in msg:
        content = msg.get("content", "")
        if isinstance(content, str):
            return _esc(content)
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(_esc(block.get("text", "")))
                    elif btype == "thinking" and include_thinking:
                        parts.append(f"[Thinking: {_esc(block.get('text', '')[:200])}...]")
                    elif btype == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        inp_str = json.dumps(inp)[:200] if inp else ""
                        parts.append(f"[Tool: {_esc(name)}({_esc(inp_str)})]")
                    elif btype == "tool_result":
                        result_text = block.get("content", "")
                        if isinstance(result_text, str):
                            parts.append(f"[Result: {_esc(result_text[:200])}]")
                elif isinstance(block, str):
                    parts.append(_esc(block))
            return "\n".join(parts)
    return ""


def normalize_acpx_messages(acpx_messages: list[dict], include_thinking: bool = False) -> list[dict]:
    """Convert acpx session messages to normalized Anthropic-style format.

    acpx format: [{"User": {"content": [{"Text": "..."}]}}, {"Agent": {"content": [...]}}]
    Normalized:  [{"role": "user", "content": [{"type": "text", "text": "..."}]}, ...]
    """
    normalized: list[dict] = []

    for msg in acpx_messages:
        if "User" in msg:
            user_data = msg["User"]
            content_blocks = _normalize_content_blocks(user_data.get("content", []))
            if content_blocks:
                normalized.append({"role": "user", "content": content_blocks})

        elif "Agent" in msg:
            agent_data = msg["Agent"]
            content_blocks = _normalize_content_blocks(
                agent_data.get("content", []), include_thinking
            )
            if content_blocks:
                normalized.append({"role": "assistant", "content": content_blocks})

    return normalized


def _normalize_content_blocks(
    blocks: list, include_thinking: bool = False
) -> list[dict]:
    """Convert acpx content blocks to normalized format."""
    result: list[dict] = []

    for block in blocks:
        if isinstance(block, str):
            result.append({"type": "text", "text": block})
        elif isinstance(block, dict):
            if "Text" in block:
                result.append({"type": "text", "text": block["Text"]})
            elif "Thinking" in block and include_thinking:
                thinking = block["Thinking"]
                text = thinking.get("text", "") if isinstance(thinking, dict) else str(thinking)
                result.append({"type": "thinking", "text": text})
            elif "ToolUse" in block:
                tu = block["ToolUse"]
                result.append({
                    "type": "tool_use",
                    "id": tu.get("id", ""),
                    "name": tu.get("name", ""),
                    "input": tu.get("raw_input", {}),
                })
            elif "ToolResult" in block:
                tr = block["ToolResult"]
                content = tr.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in content
                    )
                result.append({
                    "type": "tool_result",
                    "tool_use_id": tr.get("tool_use_id", ""),
                    "content": content if isinstance(content, str) else str(content),
                })

    return result


def estimate_token_count(messages: list[dict]) -> int:
    """Rough token count estimate from normalized messages.

    Uses ~4 chars per token as a conservative estimate.
    Exact counts would require a tokenizer, but this is sufficient
    for overflow decisions — the budget is a soft limit.
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    for v in block.values():
                        if isinstance(v, str):
                            total_chars += len(v)
                        elif isinstance(v, dict):
                            total_chars += len(json.dumps(v))
    return max(1, total_chars // 4)
