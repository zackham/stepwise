"""Shared ACP NDJSON extraction helpers.

Parse ACP-format NDJSON output files to extract session IDs, costs,
text content, errors, and usage limit signals.  Used by ACPBackend
and the engine's session tracking.
"""

from __future__ import annotations

import json

from stepwise.executors import _USAGE_RESET_RE


def extract_session_id(output_path: str, result_only: bool = False) -> str | None:
    """Extract ACP session UUID from NDJSON output.

    Args:
        output_path: Path to the NDJSON output file.
        result_only: If True, only read result.sessionId (for fork/resume).
                     If False, also check params.sessionId (params-based extraction).
    """
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
                    # ACP session/update notifications (params-based extraction)
                    if not result_only:
                        params = data.get("params", {})
                        if params.get("sessionId"):
                            return params["sessionId"]
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return None


def extract_cost(output_path: str) -> float | None:
    """Extract cost from ACP usage_update events.

    Returns the last cost amount found, or None if no usage_update events.
    """
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


def extract_usage(output_path: str) -> dict | None:
    """Extract the token breakdown from the ACP prompt result.

    The agent's final JSON-RPC response carries a `result.usage` object:

        {"jsonrpc": "2.0", "id": 0, "result": {
            "stopReason": "end_turn",
            "usage": {"inputTokens": 754, "outputTokens": 943,
                      "cachedReadTokens": 79700, "cachedWriteTokens": 4078,
                      "totalTokens": 85475}}}

    This is the ONLY place the cached/uncached split is reported — the
    `usage_update` session events that `extract_cost` reads carry a running
    context-window figure (`used`/`size`) and a dollar `cost`, but no token
    breakdown.

    Why this matters: under subscription billing the dollar cost is zero by
    definition (see `AgentExecutor._build_envelope`), so cost-only metering
    reports 0 for every agent step and the actual scarce resource — rate-limit
    quota, denominated in tokens with cached reads weighted far cheaper than
    fresh input — is invisible. A caller cannot distinguish a cheap 85k-token
    turn that was 94% cache reads from an expensive one that was all fresh input.

    Returns the last complete usage object found (a resumed/multi-prompt session
    writes one per prompt), or None if the run produced none. Missing renders
    None — never a zero-filled dict, which would read as "measured, and it was
    nothing."
    """
    last_usage = None
    try:
        with open(output_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result = data.get("result")
                if not isinstance(result, dict):
                    continue
                usage = result.get("usage")
                if isinstance(usage, dict) and usage:
                    last_usage = usage
    except FileNotFoundError:
        pass
    return last_usage


def normalize_usage(usage: dict | None) -> dict | None:
    """Normalize an ACP usage object to stepwise's snake_case token schema.

    ACP reports camelCase; stepwise stores snake_case so the fields read the same
    as every other persisted metric. Unknown/absent fields stay absent rather than
    defaulting to 0 — a missing measurement must not render as a measured zero.

    `billable_input_tokens` is a derived convenience: fresh input + cache writes,
    i.e. the part that is NOT a discounted cache read. It is the number that
    tracks rate-limit burn most closely.
    """
    if not usage:
        return None
    mapping = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cachedReadTokens": "cached_read_tokens",
        "cachedWriteTokens": "cached_write_tokens",
        "totalTokens": "total_tokens",
    }
    out: dict = {}
    for src, dst in mapping.items():
        val = usage.get(src)
        if isinstance(val, (int, float)):
            out[dst] = int(val)
    if not out:
        return None
    if "input_tokens" in out or "cached_write_tokens" in out:
        out["billable_input_tokens"] = (
            out.get("input_tokens", 0) + out.get("cached_write_tokens", 0)
        )
    return out


def extract_final_text(output_path: str) -> str:
    """Extract the final assistant text from ACP NDJSON output.

    Prefers `agent_message_chunk` (the model's surfaced response). If those
    are empty but the run produced substantial `agent_thought_chunk` content
    (extended-thinking events), falls back to those — observed in the wild
    when Claude Opus routes its full prose response into the thinking
    stream and never emits a message. The thinking stream is the real
    output in that case; without this fallback, stream_result returns "".

    Both streams are concatenated in document order within their bucket;
    we return whichever bucket has content, message_chunks winning ties.

    Returns empty string only when neither stream produced content.
    """
    message_chunks: list[str] = []
    thought_chunks: list[str] = []
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
                    upd_type = update.get("sessionUpdate")
                    if upd_type not in ("agent_message_chunk", "agent_thought_chunk"):
                        continue
                    content = update.get("content", {})
                    if content.get("type") != "text":
                        continue
                    text = content.get("text", "")
                    if not text:
                        continue
                    if upd_type == "agent_message_chunk":
                        message_chunks.append(text)
                    else:
                        thought_chunks.append(text)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    if message_chunks:
        return "".join(message_chunks)
    # Fallback: model emitted everything as extended-thinking with no
    # message. Use the thought stream as the result.
    return "".join(thought_chunks)


def read_last_error(output_path: str) -> str | None:
    """Extract last error message from ACP NDJSON output."""
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


def detect_usage_limit_in_line(line: str, parse_json: bool) -> str | None:
    """Check a single line for usage limit patterns.

    Args:
        line: A single line of text (NDJSON or plain text).
        parse_json: True for NDJSON stdout, False for plain stderr.

    Returns the matching message string, or None.
    """
    if parse_json:
        try:
            data = json.loads(line)
            error = data.get("error", {})
            if isinstance(error, dict):
                msg = error.get("message", "")
                if _USAGE_RESET_RE.search(msg):
                    return msg
            params = data.get("params", {})
            update = params.get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                text = update.get("content", {}).get("text", "")
                if _USAGE_RESET_RE.search(text):
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
    else:
        if _USAGE_RESET_RE.search(line):
            return line.strip()
    return None


def tail_for_usage_limit(
    path: str, offset: int, parse_json: bool,
) -> tuple[int, str | None]:
    """Read new content from file starting at offset, check for usage limit.

    Returns (new_offset, matching_message_or_None).
    """
    try:
        with open(path) as f:
            f.seek(offset)
            new_data = f.read()
            if not new_data:
                return offset, None
            new_offset = f.tell()
            for line in new_data.split("\n"):
                line = line.strip()
                if not line:
                    continue
                hit = detect_usage_limit_in_line(line, parse_json)
                if hit:
                    return new_offset, hit
            return new_offset, None
    except FileNotFoundError:
        return offset, None
