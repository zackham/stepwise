"""Tests for stepwise.acp_ndjson — shared ACP NDJSON extraction helpers."""

import json
import os
import tempfile

import pytest

from stepwise.acp_ndjson import (
    detect_usage_limit_in_line,
    extract_cost,
    extract_final_text,
    extract_session_id,
    read_last_error,
    tail_for_usage_limit,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _write_ndjson(lines: list[dict], path: str | None = None) -> str:
    """Write a list of dicts as NDJSON to a temp file. Returns the path."""
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


def _session_update(session_id: str, update: dict) -> dict:
    """Build an ACP session/update notification."""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


# ── NDJSON samples ───────────────────────────────────────────────────

# Simulates acpx output (has both result.sessionId and params.sessionId)
ACPX_SESSION_RESULT = {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "sess-abc-123"}}
ACPX_SESSION_UPDATE = _session_update("sess-abc-123", {
    "sessionUpdate": "agent_message_chunk",
    "content": {"type": "text", "text": "Hello "},
})

# Simulates ACP translated output (result.sessionId only in result lines)
CLAUDE_DIRECT_INIT_REQ = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": 1}}
CLAUDE_DIRECT_INIT_RESP = {"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": 1}}
CLAUDE_DIRECT_SESSION = {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "claude-sess-456"}}

USAGE_UPDATE = _session_update("sess-abc-123", {
    "sessionUpdate": "usage_update",
    "used": 1500,
    "size": 200000,
    "cost": {"amount": 0.042, "currency": "USD"},
})

USAGE_UPDATE_2 = _session_update("sess-abc-123", {
    "sessionUpdate": "usage_update",
    "used": 3000,
    "size": 200000,
    "cost": {"amount": 0.085, "currency": "USD"},
})

TEXT_CHUNK_1 = _session_update("sess-abc-123", {
    "sessionUpdate": "agent_message_chunk",
    "content": {"type": "text", "text": "Hello "},
})
TEXT_CHUNK_2 = _session_update("sess-abc-123", {
    "sessionUpdate": "agent_message_chunk",
    "content": {"type": "text", "text": "world!"},
})

TOOL_CALL = _session_update("sess-abc-123", {
    "sessionUpdate": "tool_call",
    "toolCallId": "tool-1",
    "title": "read_file",
    "kind": "tool_use",
    "status": "pending",
})

ERROR_LINE = {"jsonrpc": "2.0", "error": {"code": -1, "message": "Something went wrong"}}
ERROR_LINE_2 = {"jsonrpc": "2.0", "error": {"code": -2, "message": "Final error message"}}


# ── extract_session_id ────────────────────────────────────────────────


class TestExtractSessionId:
    def test_from_result(self, tmp_path):
        path = _write_ndjson([ACPX_SESSION_RESULT], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path) == "sess-abc-123"

    def test_from_params_when_not_result_only(self, tmp_path):
        """params.sessionId is used when result_only=False."""
        path = _write_ndjson([ACPX_SESSION_UPDATE], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path, result_only=False) == "sess-abc-123"

    def test_params_ignored_when_result_only(self, tmp_path):
        """params.sessionId is ignored when result_only=True."""
        # Only has params.sessionId, no result.sessionId
        path = _write_ndjson([ACPX_SESSION_UPDATE], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path, result_only=True) is None

    def test_result_only_finds_result(self, tmp_path):
        path = _write_ndjson([CLAUDE_DIRECT_SESSION], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path, result_only=True) == "claude-sess-456"

    def test_returns_first_found(self, tmp_path):
        """Returns the first session ID encountered."""
        second = {"jsonrpc": "2.0", "id": 3, "result": {"sessionId": "sess-second"}}
        path = _write_ndjson([ACPX_SESSION_RESULT, second], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path) == "sess-abc-123"

    def test_none_on_missing(self, tmp_path):
        # Line with no sessionId in result or params
        no_session = {"jsonrpc": "2.0", "id": 99, "result": {"status": "ok"}}
        path = _write_ndjson([no_session], str(tmp_path / "out.jsonl"))
        assert extract_session_id(path) is None

    def test_none_on_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        with open(path, "w"):
            pass
        assert extract_session_id(path) is None

    def test_none_on_file_not_found(self):
        assert extract_session_id("/nonexistent/path.jsonl") is None

    def test_skips_malformed_json(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps(ACPX_SESSION_RESULT) + "\n")
        assert extract_session_id(path) == "sess-abc-123"


# ── extract_cost ──────────────────────────────────────────────────────


class TestExtractCost:
    def test_single_usage_update(self, tmp_path):
        path = _write_ndjson([USAGE_UPDATE], str(tmp_path / "out.jsonl"))
        assert extract_cost(path) == 0.042

    def test_multiple_returns_last(self, tmp_path):
        path = _write_ndjson([USAGE_UPDATE, USAGE_UPDATE_2], str(tmp_path / "out.jsonl"))
        assert extract_cost(path) == 0.085

    def test_none_on_missing(self, tmp_path):
        path = _write_ndjson([TEXT_CHUNK_1], str(tmp_path / "out.jsonl"))
        assert extract_cost(path) is None

    def test_none_on_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        with open(path, "w"):
            pass
        assert extract_cost(path) is None

    def test_none_on_file_not_found(self):
        assert extract_cost("/nonexistent/path.jsonl") is None

    def test_skips_malformed_json(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("{bad json\n")
            f.write(json.dumps(USAGE_UPDATE) + "\n")
        assert extract_cost(path) == 0.042

    def test_usage_update_without_cost_key(self, tmp_path):
        """usage_update without cost dict is skipped."""
        no_cost = _session_update("s", {"sessionUpdate": "usage_update", "used": 100})
        path = _write_ndjson([no_cost], str(tmp_path / "out.jsonl"))
        assert extract_cost(path) is None


# ── extract_final_text ────────────────────────────────────────────────


class TestExtractFinalText:
    def test_single_chunk(self, tmp_path):
        path = _write_ndjson([TEXT_CHUNK_1], str(tmp_path / "out.jsonl"))
        assert extract_final_text(path) == "Hello "

    def test_concatenates_chunks(self, tmp_path):
        path = _write_ndjson([TEXT_CHUNK_1, TEXT_CHUNK_2], str(tmp_path / "out.jsonl"))
        assert extract_final_text(path) == "Hello world!"

    def test_empty_on_no_chunks(self, tmp_path):
        path = _write_ndjson([TOOL_CALL, USAGE_UPDATE], str(tmp_path / "out.jsonl"))
        assert extract_final_text(path) == ""

    def test_empty_on_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        with open(path, "w"):
            pass
        assert extract_final_text(path) == ""

    def test_empty_on_file_not_found(self):
        assert extract_final_text("/nonexistent/path.jsonl") == ""

    def test_skips_malformed_json(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("broken\n")
            f.write(json.dumps(TEXT_CHUNK_1) + "\n")
        assert extract_final_text(path) == "Hello "

    def test_skips_non_text_content(self, tmp_path):
        """Non-text content type is ignored."""
        non_text = _session_update("s", {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "image", "data": "abc"},
        })
        path = _write_ndjson([non_text, TEXT_CHUNK_1], str(tmp_path / "out.jsonl"))
        assert extract_final_text(path) == "Hello "


# ── read_last_error ───────────────────────────────────────────────────


class TestReadLastError:
    def test_single_error(self, tmp_path):
        path = _write_ndjson([ERROR_LINE], str(tmp_path / "out.jsonl"))
        assert read_last_error(path) == "Something went wrong"

    def test_multiple_returns_last(self, tmp_path):
        path = _write_ndjson([ERROR_LINE, ERROR_LINE_2], str(tmp_path / "out.jsonl"))
        assert read_last_error(path) == "Final error message"

    def test_none_on_no_errors(self, tmp_path):
        path = _write_ndjson([TEXT_CHUNK_1, USAGE_UPDATE], str(tmp_path / "out.jsonl"))
        assert read_last_error(path) is None

    def test_none_on_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        with open(path, "w"):
            pass
        assert read_last_error(path) is None

    def test_none_on_file_not_found(self):
        assert read_last_error("/nonexistent/path.jsonl") is None

    def test_skips_malformed_json(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps(ERROR_LINE) + "\n")
        assert read_last_error(path) == "Something went wrong"

    def test_error_without_message_ignored(self, tmp_path):
        """Error dict without 'message' key is skipped."""
        no_msg = {"jsonrpc": "2.0", "error": {"code": -1}}
        path = _write_ndjson([no_msg], str(tmp_path / "out.jsonl"))
        assert read_last_error(path) is None


# ── detect_usage_limit_in_line ────────────────────────────────────────


class TestDetectUsageLimitInLine:
    def test_json_error_message(self):
        line = json.dumps({
            "error": {"message": "You've hit your usage limit. It resets 3:00pm (PDT)"}
        })
        result = detect_usage_limit_in_line(line, parse_json=True)
        assert result is not None
        assert "usage limit" in result

    def test_json_agent_message_chunk(self):
        line = json.dumps({
            "params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": "out of extra usage, resets 5pm"},
            }},
        })
        result = detect_usage_limit_in_line(line, parse_json=True)
        assert result is not None

    def test_json_no_match(self):
        line = json.dumps({"params": {"update": {"sessionUpdate": "tool_call"}}})
        assert detect_usage_limit_in_line(line, parse_json=True) is None

    def test_json_malformed(self):
        assert detect_usage_limit_in_line("not json", parse_json=True) is None

    def test_plain_text_match(self):
        result = detect_usage_limit_in_line(
            "Error: out of extra usage, resets 3:00pm (PDT)", parse_json=False,
        )
        assert result is not None
        assert "usage limit" in result or "extra usage" in result

    def test_plain_text_no_match(self):
        assert detect_usage_limit_in_line("normal output line", parse_json=False) is None

    def test_plain_text_empty(self):
        assert detect_usage_limit_in_line("", parse_json=False) is None


# ── tail_for_usage_limit ──────────────────────────────────────────────


class TestTailForUsageLimit:
    def test_reads_from_offset(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("line 1\n")
            first_end = f.tell()
        # Append a usage limit line after the first offset
        with open(path, "a") as f:
            f.write(json.dumps({
                "error": {"message": "usage limit reached, resets 5pm"}
            }) + "\n")
        new_offset, hit = tail_for_usage_limit(path, first_end, parse_json=True)
        assert hit is not None
        assert "usage limit" in hit
        assert new_offset > first_end

    def test_no_new_data(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("some line\n")
            end = f.tell()
        new_offset, hit = tail_for_usage_limit(path, end, parse_json=True)
        assert hit is None
        assert new_offset == end

    def test_no_match_returns_new_offset(self, tmp_path):
        path = str(tmp_path / "out.jsonl")
        with open(path, "w") as f:
            f.write("first\n")
            first_end = f.tell()
        with open(path, "a") as f:
            f.write(json.dumps({"params": {}}) + "\n")
        new_offset, hit = tail_for_usage_limit(path, first_end, parse_json=True)
        assert hit is None
        assert new_offset > first_end

    def test_file_not_found(self):
        new_offset, hit = tail_for_usage_limit("/nonexistent", 0, parse_json=True)
        assert hit is None
        assert new_offset == 0

    def test_plain_text_mode(self, tmp_path):
        path = str(tmp_path / "stderr.log")
        with open(path, "w") as f:
            f.write("starting up\n")
            first_end = f.tell()
        with open(path, "a") as f:
            f.write("out of extra usage resets 5pm\n")
        new_offset, hit = tail_for_usage_limit(path, first_end, parse_json=False)
        assert hit is not None


# ── Full scenario: mixed NDJSON ───────────────────────────────────────


class TestFullScenario:
    def test_acpx_style_output(self, tmp_path):
        """Full acpx-style NDJSON output parses correctly."""
        lines = [
            ACPX_SESSION_RESULT,
            TEXT_CHUNK_1,
            TEXT_CHUNK_2,
            TOOL_CALL,
            USAGE_UPDATE,
        ]
        path = _write_ndjson(lines, str(tmp_path / "out.jsonl"))
        assert extract_session_id(path) == "sess-abc-123"
        assert extract_final_text(path) == "Hello world!"
        assert extract_cost(path) == 0.042
        assert read_last_error(path) is None

    def test_acp_translated_style_output(self, tmp_path):
        """Full ACP-translated NDJSON output parses correctly."""
        lines = [
            CLAUDE_DIRECT_INIT_REQ,
            CLAUDE_DIRECT_INIT_RESP,
            CLAUDE_DIRECT_SESSION,
            TEXT_CHUNK_1,
            TEXT_CHUNK_2,
            USAGE_UPDATE_2,
        ]
        path = _write_ndjson(lines, str(tmp_path / "out.jsonl"))
        assert extract_session_id(path, result_only=True) == "claude-sess-456"
        assert extract_final_text(path) == "Hello world!"
        assert extract_cost(path) == 0.085

    def test_error_scenario(self, tmp_path):
        """Output with errors extracts correctly."""
        lines = [
            ACPX_SESSION_RESULT,
            TEXT_CHUNK_1,
            ERROR_LINE,
            ERROR_LINE_2,
        ]
        path = _write_ndjson(lines, str(tmp_path / "out.jsonl"))
        assert read_last_error(path) == "Final error message"
        assert extract_session_id(path) == "sess-abc-123"
        assert extract_final_text(path) == "Hello "
