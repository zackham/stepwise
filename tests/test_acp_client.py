"""Tests for stepwise.acp_client — ACP protocol client."""

import json
import subprocess
import sys
import tempfile

import pytest

from stepwise.acp_client import ACPClient
from stepwise.acp_transport import AcpError, JsonRpcTransport

MOCK_SERVER = [sys.executable, "tests/mock_acp_server.py"]


def _spawn_mock(**kwargs) -> subprocess.Popen:
    cmd = list(MOCK_SERVER)
    caps = kwargs.get("capabilities")
    if caps:
        cmd.extend(["--capabilities", json.dumps(caps)])
    if kwargs.get("fail_session_load"):
        cmd.append("--fail-session-load")
    script = kwargs.get("response_script")
    if script:
        cmd.extend(["--response-script", script])
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


@pytest.fixture
def client():
    """Create an initialized ACPClient connected to mock server."""
    proc = _spawn_mock(capabilities={"fork": True, "sessions": True, "multi_session": True})
    transport = JsonRpcTransport(proc)
    transport.start()
    c = ACPClient(transport)
    c.initialize()
    yield c
    transport.close()
    proc.terminate()
    proc.wait(timeout=5)


class TestNewSession:
    def test_creates_session_returns_id(self, client):
        sid = client.new_session("/tmp/work")
        assert sid
        assert isinstance(sid, str)

    def test_multiple_sessions_independent(self, client):
        sid1 = client.new_session("/tmp/work", session_name="step-a")
        sid2 = client.new_session("/tmp/work", session_name="step-b")
        assert sid1 != sid2
        assert client.sessions["step-a"] == sid1
        assert client.sessions["step-b"] == sid2


class TestLoadSession:
    def test_load_existing_session(self, client):
        sid = client.new_session("/tmp/work")
        # Load should succeed (mock creates synthetic session)
        client.load_session(sid, "/tmp/work")

    def test_load_fails_when_configured(self):
        proc = _spawn_mock(fail_session_load=True)
        transport = JsonRpcTransport(proc)
        transport.start()
        c = ACPClient(transport)
        c.initialize()
        try:
            with pytest.raises(AcpError, match="Session load failed"):
                c.load_session("any-id", "/tmp/work")
        finally:
            transport.close()
            proc.terminate()
            proc.wait(timeout=5)


class TestForkSession:
    def test_fork_creates_new_session(self, client):
        parent = client.new_session("/tmp/work")
        child = client.fork_session(parent, "/tmp/work")
        assert child != parent
        assert isinstance(child, str)

    def test_fork_fails_without_capability(self):
        proc = _spawn_mock(capabilities={"fork": False, "sessions": True})
        transport = JsonRpcTransport(proc)
        transport.start()
        c = ACPClient(transport)
        c.initialize()
        sid = c.new_session("/tmp/work")
        try:
            with pytest.raises(AcpError, match="not supported"):
                c.fork_session(sid, "/tmp/work")
        finally:
            transport.close()
            proc.terminate()
            proc.wait(timeout=5)


class TestPrompt:
    def test_prompt_returns_response(self, client):
        sid = client.new_session("/tmp/work")
        result = client.prompt(sid, "Hello world")
        assert "stopReason" in result
        assert result["stopReason"] == "end_turn"

    def test_prompt_writes_ndjson(self, client, tmp_path):
        sid = client.new_session("/tmp/work")
        output_file = str(tmp_path / "output.jsonl")
        client.prompt(sid, "Hello world", output_path=output_file)

        # Verify NDJSON output
        lines = []
        with open(output_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))

        assert len(lines) >= 3  # agent_message_chunk + usage_update + result
        # Last line should be the result
        assert "result" in lines[-1]

        # Should have session/update notifications
        updates = [l for l in lines if l.get("method") == "session/update"]
        assert len(updates) >= 2

    def test_prompt_on_update_callback(self, client):
        sid = client.new_session("/tmp/work")
        updates = []
        client.prompt(sid, "Hello", on_update=lambda p: updates.append(p))
        assert len(updates) >= 2  # agent_message_chunk + usage_update


class TestCancel:
    def test_cancel_sends_notification(self, client):
        sid = client.new_session("/tmp/work")
        # Cancel should not raise (it's a notification, no response expected)
        client.cancel(sid)


class TestCloseSession:
    def test_close_removes_session(self, client):
        sid = client.new_session("/tmp/work")
        client.close_session(sid)
        # Should not raise


class TestSetSessionMode:
    def test_set_mode_sends_request(self):
        """set_session_mode sends ACP request (mock doesn't implement it, so expect error)."""
        proc = _spawn_mock()
        transport = JsonRpcTransport(proc)
        transport.start()
        c = ACPClient(transport)
        c.initialize()
        sid = c.new_session("/tmp/work")
        try:
            # Mock doesn't implement session/set_mode, so it returns method not found
            with pytest.raises(AcpError):
                c.set_session_mode(sid, "creative")
        finally:
            transport.close()
            proc.terminate()
            proc.wait(timeout=5)


class TestTimeout:
    def test_future_timeout(self):
        """Future.result with short timeout raises TimeoutError."""
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        transport = JsonRpcTransport(proc)
        transport.start()
        c = ACPClient(transport)
        try:
            future = c.transport.send_request("initialize", {"protocolVersion": 1})
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        finally:
            transport.close()
            proc.terminate()
            proc.wait(timeout=5)
