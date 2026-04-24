"""Tests for stepwise.acp_client — ACP protocol client."""

import json
import subprocess
import sys
import tempfile
import time

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
    if kwargs.get("stall_after_partial"):
        cmd.append("--stall-after-partial")
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


class TestConcurrentPromptIsolation:
    """Verify that concurrent prompts on the same ACP process write to
    separate output files without cross-contamination (P1 bug fix)."""

    def test_concurrent_prompts_write_to_own_files(self, client, tmp_path):
        """Two prompts on different sessions should only capture their own
        session's notifications, not each other's."""
        sid_a = client.new_session("/tmp/work", session_name="job-a")
        sid_b = client.new_session("/tmp/work", session_name="job-b")
        output_a = str(tmp_path / "output-a.jsonl")
        output_b = str(tmp_path / "output-b.jsonl")

        import threading

        results = {}
        errors = {}

        def run_prompt(name, sid, prompt_text, output_path):
            try:
                results[name] = client.prompt(sid, prompt_text, output_path=output_path)
            except Exception as e:
                errors[name] = e

        t_a = threading.Thread(target=run_prompt, args=("a", sid_a, "ALPHA", output_a))
        t_b = threading.Thread(target=run_prompt, args=("b", sid_b, "BRAVO", output_b))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert not errors, f"Prompt errors: {errors}"

        # Parse output files
        def read_ndjson(path):
            lines = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(json.loads(line))
            return lines

        lines_a = read_ndjson(output_a)
        lines_b = read_ndjson(output_b)

        # Each file should have content (not empty due to overwritten handler)
        assert len(lines_a) >= 2, f"Output A too short: {lines_a}"
        assert len(lines_b) >= 2, f"Output B too short: {lines_b}"

        # Extract session IDs from each file's notifications
        def session_ids_in(lines):
            ids = set()
            for line in lines:
                params = line.get("params", {})
                sid = params.get("sessionId")
                if sid:
                    ids.add(sid)
            return ids

        sids_a = session_ids_in(lines_a)
        sids_b = session_ids_in(lines_b)

        # Critical assertion: no cross-contamination
        assert sid_b not in sids_a, (
            f"Output A contains session B's data: {sids_a}"
        )
        assert sid_a not in sids_b, (
            f"Output B contains session A's data: {sids_b}"
        )

    def test_handler_cleanup_after_prompt(self, client):
        """Handlers should be unregistered after prompt completes to prevent
        leaked handlers from accumulating on long-running processes."""
        sid = client.new_session("/tmp/work")
        handlers_before = len(
            client.transport._notification_handlers.get("session/update", [])
        )
        client.prompt(sid, "test")
        handlers_after = len(
            client.transport._notification_handlers.get("session/update", [])
        )
        assert handlers_after == handlers_before, (
            f"Handler leak: {handlers_before} before, {handlers_after} after"
        )


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


class TestIdleStreamWatchdog:
    """The prompt() watchdog cancels and fails the future when the agent
    streams partial output then goes silent without sending the final
    session/prompt response (real-world failure: upstream API stream dies
    mid-turn with no message_stop, leaving the agent subprocess parked
    on epoll_wait indefinitely)."""

    def test_idle_timeout_triggers_cancel_and_raises(self, tmp_path):
        proc = _spawn_mock(
            capabilities={"fork": False, "sessions": True, "multi_session": True},
            stall_after_partial=True,
        )
        transport = JsonRpcTransport(proc)
        transport.start()
        c = ACPClient(transport)
        c.initialize()
        output_path = str(tmp_path / "out.jsonl")
        try:
            sid = c.new_session("/tmp/work")
            start = time.monotonic()
            with pytest.raises(AcpError, match="Stream idle timeout"):
                # Short idle timeout so the test completes quickly.
                c.prompt(
                    sid, "hello",
                    output_path=output_path,
                    idle_timeout_seconds=2.0,
                )
            elapsed = time.monotonic() - start
            # Watchdog polls at idle/4, then waits up to 5s for cancel to
            # land. Budget: idle(2) + poll(0.5) + cancel-wait(5) + slop.
            assert elapsed < 15.0, f"watchdog too slow: {elapsed:.1f}s"

            # Partial stream was still captured on disk.
            with open(output_path) as f:
                lines = [json.loads(line) for line in f if line.strip()]
            assert any(
                line.get("method") == "session/update"
                for line in lines
            ), "partial stream should have been written"
        finally:
            transport.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_idle_timeout_disabled_when_zero(self, client):
        """idle_timeout_seconds=0 disables the watchdog (caller opts out)."""
        sid = client.new_session("/tmp/work")
        # Mock responds normally, so this should succeed regardless.
        result = client.prompt(sid, "hello", idle_timeout_seconds=0)
        assert result["stopReason"] == "end_turn"


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
