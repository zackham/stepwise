"""Tests for ClaudeDirectBackend and ACP NDJSON translation layer.

Covers:
- translate_claude_event() for all event types
- translate_stream() batch convenience
- _extract_claude_session_id() — result.sessionId only
- _extract_cost() / _extract_final_text() on translated output
- _read_last_error()
- _tail_for_usage_limit()
- _parse_ndjson_events() compatibility (server.py)
- Edge cases: empty text, multiple assistant messages, error events
- ClaudeDirectBackend spawn command construction
- Translation thread integration
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import threading
import io

import pytest

from stepwise.claude_direct import (
    ClaudeDirectBackend,
    _TranslatorState,
    _extract_claude_session_id,
    _extract_cost,
    _extract_final_text,
    _read_last_error,
    _run_translation_thread,
    _tail_for_usage_limit,
    translate_claude_event,
    translate_stream,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_output(tmp_path):
    """Provide a temporary output file path."""
    return str(tmp_path / "output.jsonl")


def _write_ndjson(path: str, events: list[dict]) -> None:
    """Write a list of dicts as NDJSON to a file."""
    with open(path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


# ── Claude stream-json fixtures ──────────────────────────────────────

SYSTEM_INIT = {
    "type": "system",
    "subtype": "init",
    "session_id": "sess-abc-123",
}

ASSISTANT_MESSAGE = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "text", "text": "Hello, I'll help you with that."},
        ],
    },
}

ASSISTANT_MULTI_BLOCK = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "text", "text": "First block. "},
            {"type": "text", "text": "Second block."},
        ],
    },
}

ASSISTANT_EMPTY_TEXT = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "text", "text": ""},
        ],
    },
}

TOOL_USE_START = {
    "type": "stream_event",
    "event": {
        "type": "content_block_start",
        "content_block": {
            "type": "tool_use",
            "id": "tool-xyz-789",
            "name": "Read",
        },
    },
}

TOOL_USE_STOP = {
    "type": "stream_event",
    "event": {"type": "content_block_stop"},
}

TEXT_BLOCK_START = {
    "type": "stream_event",
    "event": {
        "type": "content_block_start",
        "content_block": {
            "type": "text",
            "text": "Streaming start text",
        },
    },
}

TEXT_DELTA = {
    "type": "stream_event",
    "event": {
        "type": "content_block_delta",
        "delta": {
            "type": "text_delta",
            "text": " more text",
        },
    },
}

RESULT_EVENT = {
    "type": "result",
    "session_id": "sess-abc-123",
    "cost_usd": 0.042,
    "usage": {
        "input_tokens": 1500,
        "output_tokens": 500,
        "cache_read_input_tokens": 10000,
        "cache_creation_input_tokens": 5000,
    },
    "modelUsage": {
        "claude-opus-4-6": {
            "contextWindow": 200000,
        },
    },
}

RESULT_NO_INIT = {
    "type": "result",
    "session_id": "sess-new-456",
    "cost_usd": 0.01,
    "usage": {"input_tokens": 100, "output_tokens": 50},
}


# ── translate_claude_event tests ─────────────────────────────────────


class TestTranslateClaudeEvent:
    """Test translation of individual claude stream-json events."""

    def test_system_init(self):
        state = _TranslatorState()
        results = translate_claude_event(SYSTEM_INIT, state)

        assert len(results) == 3
        assert state.session_id == "sess-abc-123"

        # Initialize request
        assert results[0]["method"] == "initialize"
        assert results[0]["jsonrpc"] == "2.0"

        # Initialize response
        assert results[1]["result"]["protocolVersion"] == 1

        # Session/new result with sessionId
        assert results[2]["result"]["sessionId"] == "sess-abc-123"

    def test_assistant_message_single_block(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(ASSISTANT_MESSAGE, state)

        assert len(results) == 1
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "agent_message_chunk"
        assert update["content"]["type"] == "text"
        assert update["content"]["text"] == "Hello, I'll help you with that."

    def test_assistant_message_multi_block(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(ASSISTANT_MULTI_BLOCK, state)

        assert len(results) == 2
        assert results[0]["params"]["update"]["content"]["text"] == "First block. "
        assert results[1]["params"]["update"]["content"]["text"] == "Second block."

    def test_assistant_empty_text_filtered(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(ASSISTANT_EMPTY_TEXT, state)

        assert len(results) == 0

    def test_tool_use_start(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(TOOL_USE_START, state)

        assert len(results) == 1
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "tool_call"
        assert update["toolCallId"] == "tool-xyz-789"
        assert update["title"] == "Read"
        assert update["kind"] == "tool_use"
        assert update["status"] == "pending"
        assert state.current_tool_id == "tool-xyz-789"
        assert state.current_tool_name == "Read"

    def test_tool_use_stop(self):
        state = _TranslatorState(
            session_id="sess-abc-123",
            current_tool_id="tool-xyz-789",
            current_tool_name="Read",
        )
        results = translate_claude_event(TOOL_USE_STOP, state)

        assert len(results) == 1
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "tool_call_update"
        assert update["toolCallId"] == "tool-xyz-789"
        assert update["status"] == "completed"
        assert update["title"] == "Read"
        assert state.current_tool_id is None

    def test_content_block_stop_without_tool(self):
        """content_block_stop with no active tool should produce no output."""
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(TOOL_USE_STOP, state)

        assert len(results) == 0

    def test_text_block_start(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(TEXT_BLOCK_START, state)

        assert len(results) == 1
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "agent_message_chunk"
        assert update["content"]["text"] == "Streaming start text"

    def test_text_delta(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(TEXT_DELTA, state)

        assert len(results) == 1
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "agent_message_chunk"
        assert update["content"]["text"] == " more text"

    def test_empty_text_delta_filtered(self):
        state = _TranslatorState(session_id="sess-abc-123")
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": ""},
            },
        }
        results = translate_claude_event(event, state)
        assert len(results) == 0

    def test_result_event(self):
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(RESULT_EVENT, state)

        assert len(results) == 2  # usage_update + session/new result

        # Usage update
        update = results[0]["params"]["update"]
        assert update["sessionUpdate"] == "usage_update"
        assert update["cost"]["amount"] == 0.042
        assert update["cost"]["currency"] == "USD"
        assert update["used"] == 17000  # 1500 + 500 + 10000 + 5000
        assert update["size"] == 200000  # from modelUsage

        # Session result
        assert results[1]["result"]["sessionId"] == "sess-abc-123"

    def test_result_event_updates_session_id(self):
        """Result event should update state.session_id."""
        state = _TranslatorState()
        translate_claude_event(RESULT_NO_INIT, state)
        assert state.session_id == "sess-new-456"

    def test_result_without_cost(self):
        state = _TranslatorState(session_id="sess-abc-123")
        event = {
            "type": "result",
            "session_id": "sess-abc-123",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        results = translate_claude_event(event, state)
        update = results[0]["params"]["update"]
        assert "cost" not in update

    def test_unknown_event_type(self):
        state = _TranslatorState()
        results = translate_claude_event({"type": "unknown_type"}, state)
        assert results == []

    def test_session_id_carried_in_params(self):
        """All session/update events should carry the session_id in params."""
        state = _TranslatorState(session_id="sess-abc-123")
        results = translate_claude_event(ASSISTANT_MESSAGE, state)
        assert results[0]["params"]["sessionId"] == "sess-abc-123"

    def test_tool_sequence(self):
        """Full tool lifecycle: start → stop."""
        state = _TranslatorState(session_id="s1")

        start = translate_claude_event(TOOL_USE_START, state)
        assert len(start) == 1
        assert start[0]["params"]["update"]["sessionUpdate"] == "tool_call"

        stop = translate_claude_event(TOOL_USE_STOP, state)
        assert len(stop) == 1
        assert stop[0]["params"]["update"]["sessionUpdate"] == "tool_call_update"
        assert stop[0]["params"]["update"]["status"] == "completed"

    def test_multiple_tools_sequential(self):
        """Two tools in sequence should have separate start/stop pairs."""
        state = _TranslatorState(session_id="s1")

        # First tool
        translate_claude_event(TOOL_USE_START, state)
        translate_claude_event(TOOL_USE_STOP, state)
        assert state.current_tool_id is None

        # Second tool
        tool2_start = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "tool-2",
                    "name": "Edit",
                },
            },
        }
        results = translate_claude_event(tool2_start, state)
        assert state.current_tool_id == "tool-2"
        assert results[0]["params"]["update"]["toolCallId"] == "tool-2"

        stop = translate_claude_event(TOOL_USE_STOP, state)
        assert stop[0]["params"]["update"]["toolCallId"] == "tool-2"


# ── translate_stream tests ───────────────────────────────────────────


class TestTranslateStream:
    """Test batch translation convenience function."""

    def test_full_session(self):
        lines = [
            json.dumps(SYSTEM_INIT),
            json.dumps(ASSISTANT_MESSAGE),
            json.dumps(TOOL_USE_START),
            json.dumps(TOOL_USE_STOP),
            json.dumps(RESULT_EVENT),
        ]
        output = translate_stream(lines)

        # system init → 3 lines, assistant → 1, tool_start → 1,
        # tool_stop → 1, result → 2
        assert len(output) == 8

        # All valid JSON
        for line in output:
            data = json.loads(line)
            assert "jsonrpc" in data

    def test_empty_lines_skipped(self):
        lines = ["", "  ", json.dumps(SYSTEM_INIT), ""]
        output = translate_stream(lines)
        assert len(output) == 3  # init → 3 lines

    def test_invalid_json_skipped(self):
        lines = ["not json", json.dumps(SYSTEM_INIT)]
        output = translate_stream(lines)
        assert len(output) == 3  # init lines only

    def test_multiple_assistant_messages(self):
        """Multiple assistant messages should all produce text chunks."""
        lines = [
            json.dumps(SYSTEM_INIT),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "First"}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Second"}]}}),
        ]
        output = translate_stream(lines)
        # 3 (init) + 1 + 1 = 5
        assert len(output) == 5

        # Check text chunks
        texts = []
        for line in output:
            data = json.loads(line)
            update = data.get("params", {}).get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                texts.append(update["content"]["text"])
        assert texts == ["First", "Second"]


# ── _extract_claude_session_id tests ─────────────────────────────────


class TestExtractClaudeSessionId:
    """Test that only result.sessionId is read, never params.sessionId."""

    def test_from_session_new_result(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "uuid-from-result"}},
        ])
        assert _extract_claude_session_id(tmp_output) == "uuid-from-result"

    def test_ignores_params_session_id(self, tmp_output):
        _write_ndjson(tmp_output, [
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "acpx-record-id-WRONG",
                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}},
                },
            },
        ])
        # Should NOT return params.sessionId
        assert _extract_claude_session_id(tmp_output) is None

    def test_result_before_params(self, tmp_output):
        """When both result and params have sessionId, result wins (appears first)."""
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "correct-uuid"}},
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": "wrong-uuid", "update": {}},
            },
        ])
        assert _extract_claude_session_id(tmp_output) == "correct-uuid"

    def test_params_before_result(self, tmp_output):
        """Even when params appears first, should skip it and find result."""
        _write_ndjson(tmp_output, [
            {
                "jsonrpc": "2.0",
                "method": "session/load",
                "params": {"sessionId": "acpx-record-id"},
            },
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "claude-uuid"}},
        ])
        # First line has no result.sessionId; second line has it
        assert _extract_claude_session_id(tmp_output) == "claude-uuid"

    def test_file_not_found(self):
        assert _extract_claude_session_id("/nonexistent/file.jsonl") is None

    def test_empty_file(self, tmp_output):
        with open(tmp_output, "w") as f:
            pass
        assert _extract_claude_session_id(tmp_output) is None

    def test_translated_output(self, tmp_output):
        """Test extraction from actual translated output."""
        lines = translate_stream([json.dumps(SYSTEM_INIT)])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_claude_session_id(tmp_output) == "sess-abc-123"

    def test_result_with_non_dict(self, tmp_output):
        """Result field that is not a dict should be skipped."""
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": 1}},
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "real-id"}},
        ])
        # First result has no sessionId, second does
        assert _extract_claude_session_id(tmp_output) == "real-id"


# ── _extract_cost tests ──────────────────────────────────────────────


class TestExtractCost:

    def test_from_translated_output(self, tmp_output):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(RESULT_EVENT),
        ])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_cost(tmp_output) == 0.042

    def test_multiple_usage_updates(self, tmp_output):
        """Should return the LAST cost."""
        events = [
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "usage_update",
                        "cost": {"amount": 0.01, "currency": "USD"},
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "usage_update",
                        "cost": {"amount": 0.05, "currency": "USD"},
                    },
                },
            },
        ]
        _write_ndjson(tmp_output, events)
        assert _extract_cost(tmp_output) == 0.05

    def test_no_cost(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": 1}},
        ])
        assert _extract_cost(tmp_output) is None

    def test_file_not_found(self):
        assert _extract_cost("/nonexistent/file.jsonl") is None


# ── _extract_final_text tests ────────────────────────────────────────


class TestExtractFinalText:

    def test_from_translated_output(self, tmp_output):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(ASSISTANT_MESSAGE),
        ])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_final_text(tmp_output) == "Hello, I'll help you with that."

    def test_multiple_chunks(self, tmp_output):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(ASSISTANT_MULTI_BLOCK),
        ])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_final_text(tmp_output) == "First block. Second block."

    def test_streaming_text(self, tmp_output):
        """Text from content_block_start and content_block_delta."""
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(TEXT_BLOCK_START),
            json.dumps(TEXT_DELTA),
        ])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_final_text(tmp_output) == "Streaming start text more text"

    def test_empty_file(self, tmp_output):
        with open(tmp_output, "w") as f:
            pass
        assert _extract_final_text(tmp_output) == ""

    def test_file_not_found(self):
        assert _extract_final_text("/nonexistent/file.jsonl") == ""


# ── _read_last_error tests ───────────────────────────────────────────


class TestReadLastError:

    def test_error_message(self, tmp_output):
        _write_ndjson(tmp_output, [
            {
                "jsonrpc": "2.0",
                "error": {"code": -1, "message": "Something broke"},
            },
        ])
        assert _read_last_error(tmp_output) == "Something broke"

    def test_multiple_errors_returns_last(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "error": {"message": "First error"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}},
            {"jsonrpc": "2.0", "error": {"message": "Last error"}},
        ])
        assert _read_last_error(tmp_output) == "Last error"

    def test_no_error(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}},
        ])
        assert _read_last_error(tmp_output) is None

    def test_file_not_found(self):
        assert _read_last_error("/nonexistent/file.jsonl") is None


# ── _tail_for_usage_limit tests ──────────────────────────────────────


class TestTailForUsageLimit:

    def test_detects_usage_limit_in_ndjson(self, tmp_output):
        events = [
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": "You've run out of extra usage. Usage limit resets 3:00pm (US/Pacific)",
                        },
                    },
                },
            },
        ]
        _write_ndjson(tmp_output, events)
        new_offset, hit = _tail_for_usage_limit(tmp_output, 0, parse_json=True)
        assert hit is not None
        assert "usage limit" in hit.lower()
        assert new_offset > 0

    def test_detects_usage_limit_in_error(self, tmp_output):
        events = [
            {
                "jsonrpc": "2.0",
                "error": {
                    "message": "out of extra usage. Usage limit resets 5pm (UTC)"
                },
            },
        ]
        _write_ndjson(tmp_output, events)
        new_offset, hit = _tail_for_usage_limit(tmp_output, 0, parse_json=True)
        assert hit is not None

    def test_no_usage_limit(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}},
        ])
        new_offset, hit = _tail_for_usage_limit(tmp_output, 0, parse_json=True)
        assert hit is None

    def test_offset_advances(self, tmp_output):
        _write_ndjson(tmp_output, [
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}},
        ])
        offset1, _ = _tail_for_usage_limit(tmp_output, 0, parse_json=True)
        offset2, _ = _tail_for_usage_limit(tmp_output, offset1, parse_json=True)
        assert offset2 == offset1  # no new data

    def test_stderr_plain_text(self, tmp_output):
        with open(tmp_output, "w") as f:
            f.write("Some warning\n")
            f.write("out of extra usage. Usage limit resets 2pm (UTC)\n")
        _, hit = _tail_for_usage_limit(tmp_output, 0, parse_json=False)
        assert hit is not None

    def test_file_not_found(self):
        offset, hit = _tail_for_usage_limit("/nonexistent", 0, parse_json=True)
        assert offset == 0
        assert hit is None


# ── _parse_ndjson_events compatibility ───────────────────────────────


class TestParseNdjsonEventsCompat:
    """Verify translated output works with server.py's _parse_ndjson_events.

    The parser is inlined here to avoid importing stepwise.server (which
    depends on FastAPI).  The logic is an exact copy of server.py:273-314.
    """

    @staticmethod
    def _parse(ndjson_lines: list[str]) -> list[dict]:
        """Inline copy of server.py _parse_ndjson_events."""
        events = []
        raw = "\n".join(ndjson_lines)
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                update = data.get("params", {}).get("update", {})
                su = update.get("sessionUpdate")

                if su == "agent_message_chunk":
                    content = update.get("content", {})
                    if content.get("type") == "text" and content.get("text"):
                        events.append({"t": "text", "text": content["text"]})
                elif su == "tool_call":
                    events.append({
                        "t": "tool_start",
                        "id": update.get("toolCallId", ""),
                        "title": update.get("title", ""),
                        "kind": update.get("kind", ""),
                    })
                elif su == "tool_call_update" and update.get("status") in ("completed", "failed"):
                    ev: dict = {
                        "t": "tool_end",
                        "id": update.get("toolCallId", ""),
                    }
                    title = update.get("title", "")
                    if title:
                        ev["output"] = title
                    if update.get("status") == "failed":
                        ev["error"] = True
                    events.append(ev)
                elif su == "usage_update":
                    events.append({
                        "t": "usage",
                        "used": update.get("used", 0),
                        "size": update.get("size", 0),
                    })
            except json.JSONDecodeError:
                continue
        return events

    def test_text_chunk(self):
        lines = translate_stream([json.dumps(ASSISTANT_MESSAGE)])
        events = self._parse(lines)
        text_events = [e for e in events if e["t"] == "text"]
        assert len(text_events) == 1
        assert text_events[0]["text"] == "Hello, I'll help you with that."

    def test_tool_start(self):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(TOOL_USE_START),
        ])
        events = self._parse(lines)
        tool_events = [e for e in events if e["t"] == "tool_start"]
        assert len(tool_events) == 1
        assert tool_events[0]["id"] == "tool-xyz-789"
        assert tool_events[0]["title"] == "Read"
        assert tool_events[0]["kind"] == "tool_use"

    def test_tool_end(self):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(TOOL_USE_START),
            json.dumps(TOOL_USE_STOP),
        ])
        events = self._parse(lines)
        end_events = [e for e in events if e["t"] == "tool_end"]
        assert len(end_events) == 1
        assert end_events[0]["id"] == "tool-xyz-789"

    def test_usage_update(self):
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(RESULT_EVENT),
        ])
        events = self._parse(lines)
        usage_events = [e for e in events if e["t"] == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["used"] == 17000  # includes cache tokens
        assert usage_events[0]["size"] == 200000

    def test_full_session_produces_all_event_types(self):
        """Complete session should produce text, tool_start, tool_end, usage."""
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(ASSISTANT_MESSAGE),
            json.dumps(TOOL_USE_START),
            json.dumps(TOOL_USE_STOP),
            json.dumps(RESULT_EVENT),
        ])
        events = self._parse(lines)
        types = {e["t"] for e in events}
        assert types == {"text", "tool_start", "tool_end", "usage"}

    def test_streaming_text_produces_text_events(self):
        """Streaming content_block_start + delta should produce text events."""
        lines = translate_stream([
            json.dumps(SYSTEM_INIT),
            json.dumps(TEXT_BLOCK_START),
            json.dumps(TEXT_DELTA),
        ])
        events = self._parse(lines)
        text_events = [e for e in events if e["t"] == "text"]
        assert len(text_events) == 2
        assert text_events[0]["text"] == "Streaming start text"
        assert text_events[1]["text"] == " more text"


# ── Translation thread integration ───────────────────────────────────


class TestTranslationThread:
    """Test the translation thread reads from pipe and writes to file."""

    def test_basic_translation(self, tmp_output):
        # Simulate a pipe with StringIO
        claude_lines = "\n".join([
            json.dumps(SYSTEM_INIT),
            json.dumps(ASSISTANT_MESSAGE),
            json.dumps(RESULT_EVENT),
            "",  # EOF
        ])
        pipe = io.StringIO(claude_lines)
        stop = threading.Event()

        _run_translation_thread(pipe, tmp_output, stop)

        # Verify output file has ACP NDJSON
        with open(tmp_output) as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) > 0

        # Verify extraction functions work on the output
        assert _extract_claude_session_id(tmp_output) == "sess-abc-123"
        assert _extract_cost(tmp_output) == 0.042
        assert "Hello" in _extract_final_text(tmp_output)

    def test_stop_event(self, tmp_output):
        """Translation thread should stop when stop_event is set."""
        # Create a pipe that blocks until we signal stop
        r_fd, w_fd = os.pipe()
        r_pipe = os.fdopen(r_fd, "r")
        w_pipe = os.fdopen(w_fd, "w")
        stop = threading.Event()

        thread = threading.Thread(
            target=_run_translation_thread,
            args=(r_pipe, tmp_output, stop),
            daemon=True,
        )
        thread.start()

        # Write one event
        w_pipe.write(json.dumps(SYSTEM_INIT) + "\n")
        w_pipe.flush()

        # Give it a moment then signal stop and close pipe
        import time
        time.sleep(0.1)
        stop.set()
        w_pipe.close()

        thread.join(timeout=2.0)
        assert not thread.is_alive()
        r_pipe.close()

    def test_invalid_json_in_pipe(self, tmp_output):
        """Invalid JSON lines should be silently skipped."""
        claude_lines = "\n".join([
            "not valid json",
            json.dumps(SYSTEM_INIT),
            "also invalid {{{",
            json.dumps(ASSISTANT_MESSAGE),
        ])
        pipe = io.StringIO(claude_lines)
        stop = threading.Event()

        _run_translation_thread(pipe, tmp_output, stop)

        # Should have translated the valid events
        text = _extract_final_text(tmp_output)
        assert "Hello" in text


# ── ClaudeDirectBackend command construction ─────────────────────────


class TestClaudeDirectBackendCommand:
    """Test that spawn builds correct claude CLI commands.

    We can't easily test actual subprocess spawning, but we can verify
    the command construction logic by patching subprocess.Popen.
    """

    def _make_context(self, tmp_path):
        from stepwise.executors import ExecutionContext

        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace, exist_ok=True)
        return ExecutionContext(
            job_id="job-test-123",
            step_name="review",
            attempt=1,
            workspace_path=workspace,
            idempotency="test-idem",
        )

    def test_fresh_session_command(self, tmp_path, monkeypatch):
        """Fresh session: no --resume, no --fork-session."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        captured_cmd = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        config = {}
        backend.spawn("test prompt", config, context)

        assert "/usr/bin/claude" in captured_cmd
        assert "--output-format" in captured_cmd
        assert "stream-json" in captured_cmd
        assert "--dangerously-skip-permissions" in captured_cmd
        assert "--resume" not in captured_cmd
        assert "--fork-session" not in captured_cmd

    def test_resume_session_command(self, tmp_path, monkeypatch):
        """Resume: --resume <uuid>, no --fork-session."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        captured_cmd = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        config = {"_session_uuid": "uuid-resume-abc"}
        backend.spawn("test prompt", config, context)

        assert "--resume" in captured_cmd
        idx = captured_cmd.index("--resume")
        assert captured_cmd[idx + 1] == "uuid-resume-abc"
        assert "--fork-session" not in captured_cmd

    def test_fork_session_command(self, tmp_path, monkeypatch):
        """Fork: --resume <parent> --fork-session."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        captured_cmd = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        config = {"_fork_from_session_id": "uuid-parent-xyz"}
        backend.spawn("test prompt", config, context)

        assert "--resume" in captured_cmd
        idx = captured_cmd.index("--resume")
        assert captured_cmd[idx + 1] == "uuid-parent-xyz"
        assert "--fork-session" in captured_cmd

    def test_fork_takes_precedence_over_resume(self, tmp_path, monkeypatch):
        """When both fork and resume are set, fork wins."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        captured_cmd = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        config = {
            "_fork_from_session_id": "fork-parent",
            "_session_uuid": "resume-uuid",
        }
        backend.spawn("test prompt", config, context)

        # fork should be used, not resume
        idx = captured_cmd.index("--resume")
        assert captured_cmd[idx + 1] == "fork-parent"
        assert "--fork-session" in captured_cmd

    def test_prompt_written_to_file(self, tmp_path, monkeypatch):
        """Prompt should be written to a file and referenced via --file."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        captured_cmd = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        backend.spawn("Hello, world!", {}, context)

        # Should have -p followed by the prompt text (read from file)
        assert "-p" in captured_cmd
        p_idx = captured_cmd.index("-p")
        prompt_arg = captured_cmd[p_idx + 1]
        assert prompt_arg == "Hello, world!"

    def test_session_name_from_config(self, tmp_path, monkeypatch):
        """Custom session name from config should be used."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        proc = backend.spawn("test", {"_session_name": "my-session"}, context)
        assert proc.session_name == "my-session"

    def test_default_session_name(self, tmp_path, monkeypatch):
        """Default session name includes job prefix and step name."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        proc = backend.spawn("test", {}, context)
        assert "review" in proc.session_name
        assert "test-123" in proc.session_name

    def test_start_new_session_flag(self, tmp_path, monkeypatch):
        """Popen should be called with start_new_session=True."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)
        popen_kwargs = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                popen_kwargs.update(kwargs)
                self.pid = 12345
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: pid)

        backend.spawn("test", {}, context)
        assert popen_kwargs.get("start_new_session") is True

    def test_agent_process_fields(self, tmp_path, monkeypatch):
        """AgentProcess should have correct fields set."""
        backend = ClaudeDirectBackend(claude_path="/usr/bin/claude")
        context = self._make_context(tmp_path)

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.pid = 99999
                self.stdout = io.StringIO("")

        monkeypatch.setattr("stepwise.claude_direct.subprocess.Popen", FakePopen)
        monkeypatch.setattr("stepwise.claude_direct.os.getpgid", lambda pid: 99999)

        proc = backend.spawn("test", {}, context)
        assert proc.pid == 99999
        assert proc.pgid == 99999
        assert proc.output_path.endswith(".output.jsonl")
        assert proc.agent == "claude"
        assert proc.capture_transcript is False


# ── Backend supports_resume property ─────────────────────────────────


class TestBackendProperties:

    def test_supports_resume(self):
        backend = ClaudeDirectBackend()
        assert backend.supports_resume is True


# ── End-to-end translation + extraction ──────────────────────────────


class TestEndToEnd:
    """Verify a complete claude session translates into working ACP output."""

    def test_full_session(self, tmp_output):
        """Translate a realistic session and verify all extractors work."""
        claude_events = [
            SYSTEM_INIT,
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll read the file."}]}},
            TOOL_USE_START,
            TOOL_USE_STOP,
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Here's what I found."}]}},
            RESULT_EVENT,
        ]
        lines = translate_stream([json.dumps(e) for e in claude_events])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")

        assert _extract_claude_session_id(tmp_output) == "sess-abc-123"
        assert _extract_cost(tmp_output) == 0.042
        text = _extract_final_text(tmp_output)
        assert "I'll read the file." in text
        assert "Here's what I found." in text

    def test_resumed_session_no_init(self, tmp_output):
        """Resumed sessions may skip system init, getting session_id from result."""
        claude_events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Continuing..."}]}},
            RESULT_NO_INIT,
        ]
        lines = translate_stream([json.dumps(e) for e in claude_events])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")

        assert _extract_claude_session_id(tmp_output) == "sess-new-456"
        assert _extract_cost(tmp_output) == 0.01
        assert _extract_final_text(tmp_output) == "Continuing..."

    def test_result_with_total_cost_usd(self, tmp_output):
        """Some claude versions use total_cost_usd instead of cost_usd."""
        event = {
            "type": "result",
            "session_id": "s1",
            "total_cost_usd": 0.123,
            "usage": {"input_tokens": 50, "output_tokens": 50},
        }
        lines = translate_stream([json.dumps(SYSTEM_INIT), json.dumps(event)])
        with open(tmp_output, "w") as f:
            for line in lines:
                f.write(line + "\n")
        assert _extract_cost(tmp_output) == 0.123
