"""Tests for stepwise.acp_transport — JSON-RPC 2.0 over stdio."""

import json
import subprocess
import sys
import time

import pytest

from stepwise.acp_transport import AcpError, JsonRpcTransport

MOCK_SERVER = [sys.executable, "tests/mock_acp_server.py"]


def _spawn_mock(**kwargs) -> subprocess.Popen:
    """Spawn mock ACP server as a subprocess."""
    cmd = list(MOCK_SERVER)
    caps = kwargs.get("capabilities")
    if caps:
        cmd.extend(["--capabilities", json.dumps(caps)])
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


@pytest.fixture
def transport():
    """Create a JsonRpcTransport connected to the mock server."""
    proc = _spawn_mock()
    t = JsonRpcTransport(proc)
    t.start()
    yield t
    t.close()
    proc.terminate()
    proc.wait(timeout=5)


class TestInitializeHandshake:
    def test_initialize_returns_capabilities(self, transport):
        future = transport.send_request("initialize", {"protocolVersion": 1})
        result = future.result(timeout=5)
        assert "protocolVersion" in result
        assert "capabilities" in result


class TestRequestResponse:
    def test_matching_ids(self, transport):
        """Responses match the correct request ID."""
        f1 = transport.send_request("initialize", {"protocolVersion": 1})
        r1 = f1.result(timeout=5)
        assert "protocolVersion" in r1

        f2 = transport.send_request("session/new", {})
        r2 = f2.result(timeout=5)
        assert "sessionId" in r2

    def test_error_response_raises(self, transport):
        """Error responses set exception on future."""
        # Initialize first
        transport.send_request("initialize", {"protocolVersion": 1}).result(timeout=5)
        # session/fork without fork capability → error
        f = transport.send_request("session/fork", {"sessionId": "nonexistent"})
        with pytest.raises(AcpError):
            f.result(timeout=5)


class TestNotificationDispatch:
    def test_notification_handler_called(self, transport):
        """Registered handlers receive notifications."""
        received = []
        transport.on_notification("session/update", lambda params: received.append(params))

        # Initialize + create session
        transport.send_request("initialize", {"protocolVersion": 1}).result(timeout=5)
        f = transport.send_request("session/new", {})
        sid = f.result(timeout=5)["sessionId"]

        # Prompt triggers session/update notifications
        f2 = transport.send_request("session/prompt", {
            "sessionId": sid,
            "prompt": "hello",
        })
        f2.result(timeout=5)

        # Should have received agent_message_chunk and usage_update notifications
        assert len(received) >= 2
        update_types = [p.get("update", {}).get("sessionUpdate") for p in received]
        assert "agent_message_chunk" in update_types
        assert "usage_update" in update_types


class TestNonJsonLines:
    def test_non_json_lines_skipped(self):
        """Non-JSON lines in stdout don't crash the transport."""
        # Use a script that emits garbage before valid JSON
        proc = subprocess.Popen(
            [sys.executable, "-c",
             'import json, sys\n'
             'sys.stdout.write("this is not json\\n")\n'
             'sys.stdout.flush()\n'
             'for line in sys.stdin:\n'
             '  msg = json.loads(line.strip())\n'
             '  sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"ok":True}}) + "\\n")\n'
             '  sys.stdout.flush()\n'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        t = JsonRpcTransport(proc)
        t.start()
        try:
            future = t.send_request("test", {})
            result = future.result(timeout=5)
            assert result["ok"] is True
        finally:
            t.close()
            proc.terminate()
            proc.wait(timeout=5)


class TestMultipleConcurrentRequests:
    def test_futures_resolve_independently(self, transport):
        """Multiple requests in flight resolve to correct futures."""
        transport.send_request("initialize", {"protocolVersion": 1}).result(timeout=5)

        # Create two sessions
        f1 = transport.send_request("session/new", {})
        f2 = transport.send_request("session/new", {})

        r1 = f1.result(timeout=5)
        r2 = f2.result(timeout=5)

        assert "sessionId" in r1
        assert "sessionId" in r2
        assert r1["sessionId"] != r2["sessionId"]


class TestProcessExit:
    def test_reader_exits_cleanly(self):
        """Reader thread handles process exit gracefully."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        t = JsonRpcTransport(proc)
        t.start()
        proc.wait(timeout=5)
        # Reader thread should exit without hanging
        if t._reader_thread:
            t._reader_thread.join(timeout=2)
            assert not t._reader_thread.is_alive()
        t.close()


class TestConcurrentWrites:
    def test_concurrent_writers_do_not_interleave_frames(self):
        """_write must be atomic per JSON-RPC line (F47 regression).

        process.stdin is a TextIOWrapper (not thread-safe); without the
        write lock, concurrent writers (executor threads, the reader
        thread answering server→client requests, idle watchdogs) can
        interleave partial lines and corrupt the agent's stdin stream.
        Uses a slow char-by-char writer to make interleaving virtually
        certain if writes are unlocked.
        """
        import threading
        import types

        class SlowWriter:
            """Writes one character at a time with a tiny yield."""

            def __init__(self):
                self.chunks: list[str] = []

            def write(self, s: str) -> None:
                for ch in s:
                    self.chunks.append(ch)
                    time.sleep(0.00005)

            def flush(self) -> None:
                pass

        writer = SlowWriter()
        fake_proc = types.SimpleNamespace(stdin=writer, stdout=None, pid=0)
        t = JsonRpcTransport(fake_proc)  # no start(): write path only

        n_threads, n_msgs = 8, 25

        def _spam(tid: int) -> None:
            for i in range(n_msgs):
                t.send_notification(
                    "session/update",
                    {"thread": tid, "seq": i, "pad": "x" * 50},
                )

        threads = [
            threading.Thread(target=_spam, args=(tid,))
            for tid in range(n_threads)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        raw = "".join(writer.chunks)
        lines = [line for line in raw.split("\n") if line]
        assert len(lines) == n_threads * n_msgs
        seen = set()
        for line in lines:
            msg = json.loads(line)  # raises if frames interleaved
            params = msg["params"]
            seen.add((params["thread"], params["seq"]))
        assert len(seen) == n_threads * n_msgs


class TestClose:
    def test_close_cancels_pending(self, transport):
        """Close cancels pending futures."""
        transport.send_request("initialize", {"protocolVersion": 1}).result(timeout=5)
        sid = transport.send_request("session/new", {}).result(timeout=5)["sessionId"]

        # Send a request but close before waiting
        future = transport.send_request("session/prompt", {
            "sessionId": sid,
            "prompt": "hello",
        })
        # The response might arrive before close, so this test just verifies
        # close doesn't hang or crash
        time.sleep(0.1)
        transport.close()
        # Future should either have a result or be cancelled/errored
        assert future.done() or future.cancelled()
