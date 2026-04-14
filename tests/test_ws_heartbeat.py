"""Tests for the `/ws` job-watcher heartbeat and receive-timeout.

Regression: long-running jobs with quiet periods let idle WebSocket
connections get silently killed by cloud proxies / load balancers. The server
now emits a heartbeat message every `WS_HEARTBEAT_INTERVAL_SECONDS` so the
connection is never idle, and wraps `receive_text()` in a
`WS_RECEIVE_TIMEOUT_SECONDS` timeout so a wedged reader doesn't pin a dead
socket indefinitely.
"""

from __future__ import annotations

import os
import time

import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app


@pytest.fixture
def client(tmp_path):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    os.environ.clear()
    os.environ.update(old_env)


class TestWebSocketHeartbeat:
    def test_heartbeat_emitted_periodically(self, client, monkeypatch):
        """Server emits `{"type":"heartbeat"}` every WS_HEARTBEAT_INTERVAL_SECONDS.

        Proves proxies can't silently kill idle connections — there's always
        traffic on the wire at least every 30s (configured; patched to 0.1s
        here for speed).
        """
        monkeypatch.setattr(srv, "WS_HEARTBEAT_INTERVAL_SECONDS", 0.05)
        monkeypatch.setattr(srv, "WS_RECEIVE_TIMEOUT_SECONDS", 10.0)

        with client.websocket_connect("/ws") as ws:
            # Collect the first two messages — both should be heartbeats.
            start = time.monotonic()
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()
            elapsed = time.monotonic() - start

            assert msg1 == {"type": "heartbeat"}
            assert msg2 == {"type": "heartbeat"}
            # Two heartbeats at 0.05s interval = ~0.1s minimum.
            # Upper bound 2s is generous slack for CI scheduling.
            assert 0.05 <= elapsed <= 2.0, (
                f"expected 2 heartbeats in ~0.1s, got them in {elapsed:.3f}s"
            )

    def test_heartbeat_message_type_is_backward_compatible(self, client, monkeypatch):
        """The heartbeat message type is new; the existing client
        `useStepwiseWebSocket` handler ignores unknown message types silently.
        This test documents that contract from the server side: heartbeat is
        a simple `{"type": "heartbeat"}` with no extra fields, so clients
        that don't recognise it can safely no-op."""
        monkeypatch.setattr(srv, "WS_HEARTBEAT_INTERVAL_SECONDS", 0.05)
        monkeypatch.setattr(srv, "WS_RECEIVE_TIMEOUT_SECONDS", 10.0)

        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert set(msg.keys()) == {"type"}
            assert msg["type"] == "heartbeat"
