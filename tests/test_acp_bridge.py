"""Tests for stepwise.containment.acp_bridge + host-side proxy wiring.

Covers the three independently-verifiable pieces of the containment
bridge architecture:

1. `translate_path` — pure function, rewrites host-absolute paths under
   the working directory to the VM's `/mnt/workspace` mount.
2. `BridgeClient` — newline-delimited JSON-RPC client. Drives the
   client against a Unix socketpair + mock server thread (the real
   production transport is vsock, but the client is transport-agnostic
   because it takes generic rfile/wfile handles).
3. `ACPBackend._register_client_handlers` — with and without a bridge
   client, verifies routing: fs/terminal go to the bridge when one is
   present, permission handling stays local either way.

The embedded `ACP_BRIDGE_SCRIPT` string (guest-side) is validated
end-to-end on a real microVM; that integration test is KVM-gated and
lives in test_containment.py.
"""

from __future__ import annotations

import json
import socket
import threading
from unittest.mock import MagicMock

import pytest

from stepwise.acp_backend import ACPBackend
from stepwise.containment.acp_bridge import (
    GUEST_WORKSPACE_MOUNT,
    BridgeClient,
    BridgeError,
    translate_path,
)


# ── translate_path ────────────────────────────────────────────────


class TestTranslatePath:
    def test_relative_path_unchanged(self):
        assert translate_path("scripts/foo.py", "/home/zack/work") == "scripts/foo.py"

    def test_empty_unchanged(self):
        assert translate_path("", "/home/zack/work") == ""

    def test_absolute_under_workdir_rewritten(self):
        assert (
            translate_path("/home/zack/work/scripts/foo.py", "/home/zack/work")
            == f"{GUEST_WORKSPACE_MOUNT}/scripts/foo.py"
        )

    def test_workdir_itself_rewritten_to_mount(self):
        assert translate_path("/home/zack/work", "/home/zack/work") == GUEST_WORKSPACE_MOUNT

    def test_workdir_with_trailing_slash(self):
        assert (
            translate_path("/home/zack/work/a.txt", "/home/zack/work/")
            == f"{GUEST_WORKSPACE_MOUNT}/a.txt"
        )

    def test_absolute_outside_workdir_unchanged(self):
        # Path is outside the sandbox — passes through so the bridge
        # sees it and (correctly) fails inside the VM's filesystem.
        assert translate_path("/etc/passwd", "/home/zack/work") == "/etc/passwd"

    def test_prefix_collision_not_rewritten(self):
        # /home/zack/work2/foo should NOT match /home/zack/work.
        assert (
            translate_path("/home/zack/work2/foo", "/home/zack/work")
            == "/home/zack/work2/foo"
        )


# ── BridgeClient over a Unix socketpair ──────────────────────────


def _make_mock_bridge_server(
    responses: dict | None = None,
    fail_ids: set | None = None,
):
    """Return (client_socket, server_thread). Server echoes canned results."""
    responses = responses or {}
    fail_ids = fail_ids or set()

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    server_done = threading.Event()
    received = []

    def serve():
        rfile = b.makefile("r", buffering=1, encoding="utf-8")
        wfile = b.makefile("w", buffering=1, encoding="utf-8")
        try:
            for line in rfile:
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                received.append(req)
                req_id = req["id"]
                method = req["method"]
                if req_id in fail_ids:
                    resp = {"id": req_id, "error": {"message": "synthetic failure"}}
                else:
                    resp = {"id": req_id, "result": responses.get(method, {})}
                wfile.write(json.dumps(resp) + "\n")
                wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            server_done.set()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    rfile = a.makefile("r", buffering=1, encoding="utf-8")
    wfile = a.makefile("w", buffering=1, encoding="utf-8")

    def close_client():
        try:
            a.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        a.close()

    client = BridgeClient(
        rfile=rfile,
        wfile=wfile,
        close_fn=close_client,
        host_workdir="/home/zack/work",
    )
    return client, received, server_done, b


class TestBridgeClient:
    def test_successful_call(self):
        client, received, done, server_sock = _make_mock_bridge_server(
            responses={"fs/read_text_file": {"content": "hello"}},
        )
        try:
            result = client.call("fs/read_text_file", {"path": "foo.txt"})
            assert result == {"content": "hello"}
            assert received[0]["method"] == "fs/read_text_file"
            assert received[0]["params"] == {"path": "foo.txt"}
            assert received[0]["id"] == 1
        finally:
            client.close()
            server_sock.close()

    def test_ids_increment(self):
        client, received, done, server_sock = _make_mock_bridge_server(
            responses={"ping": {"pong": True}},
        )
        try:
            client.call("ping", {})
            client.call("ping", {})
            client.call("ping", {})
            assert [r["id"] for r in received] == [1, 2, 3]
        finally:
            client.close()
            server_sock.close()

    def test_error_response_raises(self):
        client, received, done, server_sock = _make_mock_bridge_server(
            fail_ids={1},
        )
        try:
            with pytest.raises(BridgeError, match="synthetic failure"):
                client.call("fs/read_text_file", {"path": "x"})
        finally:
            client.close()
            server_sock.close()

    def test_ping_returns_true(self):
        client, received, done, server_sock = _make_mock_bridge_server(
            responses={"ping": {"pong": True}},
        )
        try:
            assert client.ping() is True
        finally:
            client.close()
            server_sock.close()

    def test_host_workdir_exposed(self):
        client, received, done, server_sock = _make_mock_bridge_server()
        try:
            assert client.host_workdir == "/home/zack/work"
        finally:
            client.close()
            server_sock.close()

    def test_call_after_close_raises(self):
        client, received, done, server_sock = _make_mock_bridge_server()
        client.close()
        server_sock.close()
        with pytest.raises(BridgeError, match="closed"):
            client.call("ping", {})


# ── _register_client_handlers routing ─────────────────────────────


class FakeTransport:
    """Records handlers registered via on_request for direct invocation."""

    def __init__(self):
        self.handlers: dict = {}

    def on_request(self, method: str, handler) -> None:
        self.handlers[method] = handler


class FakeBridge:
    """Stand-in for BridgeClient that records calls + returns canned results."""

    def __init__(self, results: dict | None = None, host_workdir: str = "/home/zack/work"):
        self._results = results or {}
        self.host_workdir = host_workdir
        self.calls: list = []

    def call(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        if method not in self._results:
            return {}
        return self._results[method]


class TestRegisterHandlersWithBridge:
    def test_read_text_file_proxies_through_bridge(self):
        transport = FakeTransport()
        bridge = FakeBridge(results={"fs/read_text_file": {"content": "from-vm"}})
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        result = transport.handlers["fs/read_text_file"]({"path": "foo.txt"})

        assert result == {"content": "from-vm"}
        assert bridge.calls == [("fs/read_text_file", {"path": "foo.txt"})]

    def test_read_text_file_rewrites_absolute_workdir_paths(self):
        transport = FakeTransport()
        bridge = FakeBridge(results={"fs/read_text_file": {"content": ""}})
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        transport.handlers["fs/read_text_file"](
            {"path": "/home/zack/work/scripts/foo.py"}
        )

        assert bridge.calls[0][1]["path"] == f"{GUEST_WORKSPACE_MOUNT}/scripts/foo.py"

    def test_read_text_file_leaves_outside_paths_alone(self):
        transport = FakeTransport()
        bridge = FakeBridge(results={"fs/read_text_file": {"content": ""}})
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        transport.handlers["fs/read_text_file"]({"path": "/etc/passwd"})

        assert bridge.calls[0][1]["path"] == "/etc/passwd"

    def test_write_text_file_proxies(self):
        transport = FakeTransport()
        bridge = FakeBridge(results={"fs/write_text_file": {}})
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        transport.handlers["fs/write_text_file"](
            {"path": "/home/zack/work/out.txt", "content": "x"}
        )

        assert bridge.calls[0][0] == "fs/write_text_file"
        assert bridge.calls[0][1]["path"] == f"{GUEST_WORKSPACE_MOUNT}/out.txt"
        assert bridge.calls[0][1]["content"] == "x"

    def test_terminal_ops_all_proxy(self):
        transport = FakeTransport()
        bridge = FakeBridge(results={
            "terminal/create": {"terminalId": "tid-1", "pid": 99},
            "terminal/output": {"output": "hello", "isEof": False},
            "terminal/wait_for_exit": {"exitCode": 0},
            "terminal/kill": {},
            "terminal/release": {},
        })
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        create = transport.handlers["terminal/create"]({"command": "echo hi"})
        assert create["terminalId"] == "tid-1"

        transport.handlers["terminal/output"]({"terminalId": "tid-1"})
        transport.handlers["terminal/wait_for_exit"]({"terminalId": "tid-1"})
        transport.handlers["terminal/kill"]({"terminalId": "tid-1"})
        transport.handlers["terminal/release"]({"terminalId": "tid-1"})

        methods = [c[0] for c in bridge.calls]
        assert methods == [
            "terminal/create",
            "terminal/output",
            "terminal/wait_for_exit",
            "terminal/kill",
            "terminal/release",
        ]

    def test_bridge_error_becomes_runtime_error(self):
        transport = FakeTransport()
        bridge = MagicMock()
        bridge.host_workdir = "/home/zack/work"
        bridge.call.side_effect = BridgeError("boom")
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        with pytest.raises(RuntimeError, match="Cannot read"):
            transport.handlers["fs/read_text_file"]({"path": "foo.txt"})

    def test_permission_handler_is_not_proxied(self):
        transport = FakeTransport()
        bridge = FakeBridge()
        ACPBackend._register_client_handlers(transport, bridge_client=bridge)

        result = transport.handlers["session/request_permission"]({
            "options": [{"optionId": "allow_once"}, {"optionId": "reject"}]
        })

        assert result["outcome"]["outcome"] == "selected"
        assert result["outcome"]["optionId"] == "allow_once"
        assert bridge.calls == []  # permission never touches the bridge


class TestRegisterHandlersWithoutBridge:
    def test_local_read_write_roundtrip(self, tmp_path):
        transport = FakeTransport()
        ACPBackend._register_client_handlers(transport, bridge_client=None)

        target = tmp_path / "out.txt"
        transport.handlers["fs/write_text_file"](
            {"path": str(target), "content": "hello local"}
        )
        result = transport.handlers["fs/read_text_file"]({"path": str(target)})

        assert target.read_text() == "hello local"
        assert result == {"content": "hello local"}

    def test_permission_auto_approves(self):
        transport = FakeTransport()
        ACPBackend._register_client_handlers(transport, bridge_client=None)

        result = transport.handlers["session/request_permission"]({
            "options": [
                {"optionId": "reject_once"},
                {"optionId": "allow_always"},
            ]
        })

        assert result["outcome"]["optionId"] == "allow_always"
