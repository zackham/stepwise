"""Tests for the mock ACP server (tests/mock_acp_server.py)."""

import json
from io import StringIO

import pytest

from tests.mock_acp_server import MockAcpServer, create_server


# ── Helpers ───────────────────────────────────────────────────────────


def _run_messages(server: MockAcpServer, messages: list[dict]) -> list[dict]:
    """Feed messages to the server and collect all output lines."""
    input_text = "\n".join(json.dumps(m) for m in messages) + "\n"
    server._input = StringIO(input_text)
    output = StringIO()
    server._output = output
    server.run()
    output.seek(0)
    results = []
    for line in output:
        line = line.strip()
        if line:
            results.append(json.loads(line))
    return results


def _send_one(server: MockAcpServer, msg: dict) -> list[dict]:
    """Send a single message and return all output."""
    return _run_messages(server, [msg])


def _init_msg(msg_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "initialize", "params": {"protocolVersion": 1}}


def _session_new_msg(msg_id: int = 2) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "session/new", "params": {}}


def _prompt_msg(session_id: str, prompt: str, msg_id: int = 3) -> dict:
    return {
        "jsonrpc": "2.0", "id": msg_id, "method": "session/prompt",
        "params": {"sessionId": session_id, "prompt": prompt},
    }


def _cancel_msg(session_id: str, msg_id: int = 4) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "session/cancel", "params": {"sessionId": session_id}}


def _close_msg(session_id: str, msg_id: int = 5) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "session/close", "params": {"sessionId": session_id}}


def _fork_msg(session_id: str, msg_id: int = 6) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "session/fork", "params": {"sessionId": session_id}}


def _load_msg(session_id: str, msg_id: int = 7) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "session/load", "params": {"sessionId": session_id}}


# ── Initialize ────────────────────────────────────────────────────────


class TestInitialize:
    def test_returns_protocol_version(self):
        server = create_server()
        responses = _send_one(server, _init_msg())
        assert len(responses) == 1
        r = responses[0]
        assert r["id"] == 1
        assert r["result"]["protocolVersion"] == 1

    def test_returns_capabilities(self):
        caps = {"fork": True, "sessions": True, "multi_session": False}
        server = create_server(capabilities=caps)
        responses = _send_one(server, _init_msg())
        assert responses[0]["result"]["capabilities"] == caps

    def test_default_capabilities(self):
        server = create_server()
        responses = _send_one(server, _init_msg())
        caps = responses[0]["result"]["capabilities"]
        assert caps["sessions"] is True
        assert caps["fork"] is False


# ── Session Creation ──────────────────────────────────────────────────


class TestSessionNew:
    def test_creates_session_with_unique_id(self):
        server = create_server()
        r1 = _run_messages(server, [_session_new_msg(1)])
        sid1 = r1[0]["result"]["sessionId"]
        assert sid1

        server2 = create_server()
        r2 = _run_messages(server2, [_session_new_msg(2)])
        sid2 = r2[0]["result"]["sessionId"]
        assert sid2
        assert sid1 != sid2  # UUIDs should be unique

    def test_session_tracked_internally(self):
        server = create_server()
        responses = _send_one(server, _session_new_msg())
        sid = responses[0]["result"]["sessionId"]
        assert sid in server.sessions


# ── Session Load ──────────────────────────────────────────────────────


class TestSessionLoad:
    def test_load_creates_session(self):
        server = create_server()
        responses = _send_one(server, _load_msg("test-session-id"))
        assert responses[0]["result"]["sessionId"] == "test-session-id"
        assert "test-session-id" in server.sessions

    def test_load_fails_when_configured(self):
        server = create_server(fail_session_load=True)
        responses = _send_one(server, _load_msg("test-session-id"))
        assert "error" in responses[0]
        assert responses[0]["error"]["message"] == "Session load failed"


# ── Session Fork ──────────────────────────────────────────────────────


class TestSessionFork:
    def test_fork_creates_new_session(self):
        server = create_server(capabilities={"fork": True, "sessions": True})
        # Create parent session first
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        parent_id = r[0]["result"]["sessionId"]

        # Fork it
        r2 = _run_messages(server, [_fork_msg(parent_id, 2)])
        child_id = r2[0]["result"]["sessionId"]
        assert child_id != parent_id
        assert server.sessions[child_id].parent_id == parent_id

    def test_fork_fails_without_capability(self):
        server = create_server(capabilities={"fork": False, "sessions": True})
        server.sessions["parent"] = __import__("tests.mock_acp_server", fromlist=["Session"]).Session(session_id="parent")
        r = _run_messages(server, [_fork_msg("parent", 1)])
        assert "error" in r[0]
        assert "not supported" in r[0]["error"]["message"].lower()

    def test_fork_fails_on_missing_session(self):
        server = create_server(capabilities={"fork": True, "sessions": True})
        r = _run_messages(server, [_fork_msg("nonexistent", 1)])
        assert "error" in r[0]


# ── Session Prompt ────────────────────────────────────────────────────


class TestSessionPrompt:
    def test_default_echo_behavior(self):
        server = create_server()
        # Create session, then prompt
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        sid = r[0]["result"]["sessionId"]

        responses = _run_messages(server, [_prompt_msg(sid, "Hello world")])
        # Should get: agent_message_chunk notification, usage_update notification, result
        assert len(responses) == 3

        # First: agent_message_chunk with echoed text
        chunk = responses[0]
        assert chunk["method"] == "session/update"
        assert chunk["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        assert chunk["params"]["update"]["content"]["text"] == "Hello world"

        # Second: usage_update
        usage = responses[1]
        assert usage["params"]["update"]["sessionUpdate"] == "usage_update"
        assert "cost" in usage["params"]["update"]

        # Third: result with end_turn
        result = responses[2]
        assert result["result"]["stopReason"] == "end_turn"
        assert result["result"]["sessionId"] == sid

    def test_scripted_responses(self):
        script = [[
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Scripted reply"},
                }},
            },
        ]]
        server = create_server(response_script=script)
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        sid = r[0]["result"]["sessionId"]

        responses = _run_messages(server, [_prompt_msg(sid, "anything")])
        # Script notification + result
        assert len(responses) == 2
        assert responses[0]["params"]["update"]["content"]["text"] == "Scripted reply"
        assert responses[1]["result"]["stopReason"] == "end_turn"

    def test_prompt_to_unknown_session_errors(self):
        server = create_server()
        responses = _run_messages(server, [_prompt_msg("nonexistent", "test")])
        assert "error" in responses[0]

    def test_prompt_increments_count(self):
        server = create_server()
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        sid = r[0]["result"]["sessionId"]

        _run_messages(server, [_prompt_msg(sid, "first")])
        assert server.sessions[sid].prompt_count == 1

        _run_messages(server, [_prompt_msg(sid, "second")])
        assert server.sessions[sid].prompt_count == 2


# ── Session Cancel ────────────────────────────────────────────────────


class TestSessionCancel:
    def test_cancel_sets_flag(self):
        server = create_server()
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        sid = r[0]["result"]["sessionId"]

        responses = _run_messages(server, [_cancel_msg(sid)])
        assert responses[0]["result"]["cancelled"] is True
        assert server.sessions[sid].cancelled is True

    def test_cancel_unknown_session_errors(self):
        server = create_server()
        responses = _run_messages(server, [_cancel_msg("nonexistent")])
        assert "error" in responses[0]


# ── Session Close ─────────────────────────────────────────────────────


class TestSessionClose:
    def test_close_removes_session(self):
        server = create_server()
        msgs = [_session_new_msg(1)]
        r = _run_messages(server, msgs)
        sid = r[0]["result"]["sessionId"]
        assert sid in server.sessions

        _run_messages(server, [_close_msg(sid)])
        assert sid not in server.sessions


# ── Multiple Sessions ─────────────────────────────────────────────────


class TestMultipleSessions:
    def test_concurrent_sessions(self):
        """Sequential prompts to different sessions work independently."""
        server = create_server()
        r1 = _run_messages(server, [_session_new_msg(1)])
        sid1 = r1[0]["result"]["sessionId"]
        r2 = _run_messages(server, [_session_new_msg(2)])
        sid2 = r2[0]["result"]["sessionId"]

        # Prompt both
        _run_messages(server, [_prompt_msg(sid1, "Hello from session 1")])
        _run_messages(server, [_prompt_msg(sid2, "Hello from session 2")])

        assert server.sessions[sid1].prompt_count == 1
        assert server.sessions[sid2].prompt_count == 1
        assert sid1 != sid2


# ── Well-Formed NDJSON ────────────────────────────────────────────────


class TestNdjsonFormat:
    def test_all_output_is_valid_json(self):
        server = create_server()
        msgs = [_init_msg(1), _session_new_msg(2)]

        input_text = "\n".join(json.dumps(m) for m in msgs) + "\n"
        server._input = StringIO(input_text)
        output = StringIO()
        server._output = output
        server.run()

        output.seek(0)
        for line in output:
            line = line.strip()
            if line:
                parsed = json.loads(line)  # Should not raise
                assert "jsonrpc" in parsed
                assert parsed["jsonrpc"] == "2.0"

    def test_all_results_have_id(self):
        server = create_server()
        msgs = [_init_msg(42), _session_new_msg(99)]
        responses = _run_messages(server, msgs)
        assert responses[0]["id"] == 42
        assert responses[1]["id"] == 99

    def test_notifications_have_no_id(self):
        server = create_server()
        r = _run_messages(server, [_session_new_msg(1)])
        sid = r[0]["result"]["sessionId"]

        responses = _run_messages(server, [_prompt_msg(sid, "test")])
        # Notifications (first two) should not have "id"
        for notif in responses[:-1]:
            assert "id" not in notif
            assert "method" in notif
        # Result (last) should have "id"
        assert "id" in responses[-1]


# ── Unknown Method ────────────────────────────────────────────────────


class TestUnknownMethod:
    def test_returns_error(self):
        server = create_server()
        responses = _run_messages(server, [
            {"jsonrpc": "2.0", "id": 1, "method": "bogus/method", "params": {}},
        ])
        assert "error" in responses[0]
        assert responses[0]["error"]["code"] == -32601

    def test_malformed_input_skipped(self):
        """Non-JSON input lines are silently skipped."""
        server = create_server()
        input_text = "not json\n" + json.dumps(_init_msg(1)) + "\n"
        server._input = StringIO(input_text)
        output = StringIO()
        server._output = output
        server.run()
        output.seek(0)
        lines = [l.strip() for l in output if l.strip()]
        assert len(lines) == 1  # Only the init response
