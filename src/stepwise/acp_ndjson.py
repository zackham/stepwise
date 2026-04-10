"""Shared ACP NDJSON extraction helpers.

Parse ACP-format NDJSON output files to extract session IDs, costs,
text content, errors, and usage limit signals.  Used by both
AcpxBackend (agent.py) and ClaudeDirectBackend (claude_direct.py).
"""

from __future__ import annotations

import json

from stepwise.executors import _USAGE_RESET_RE


def extract_session_id(output_path: str, result_only: bool = False) -> str | None:
    """Extract ACP session UUID from NDJSON output.

    Args:
        output_path: Path to the NDJSON output file.
        result_only: If True, only read result.sessionId (for fork/resume).
                     If False, also check params.sessionId (legacy acpx compat).
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
                    # ACP session/update notifications (legacy acpx compat)
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


def extract_final_text(output_path: str) -> str:
    """Extract the final assistant text from ACP NDJSON output.

    Concatenates all agent_message_chunk text content.
    Returns empty string if no chunks found.
    """
    chunks: list[str] = []
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
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        content = update.get("content", {})
                        if content.get("type") == "text":
                            text = content.get("text", "")
                            if text:
                                chunks.append(text)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return "".join(chunks)


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
