#!/usr/bin/env python3
"""Mock ACP server for testing.

A simple JSON-RPC over stdio server that implements the ACP protocol subset
needed for unit testing the Stepwise ACP client.

Usage:
    python mock_acp_server.py [OPTIONS]

Options:
    --capabilities '{"fork": true, "sessions": true}'
        JSON dict of capabilities to advertise.
    --response-script path/to/script.json
        Scripted responses: list of session/update notifications per prompt.
    --fail-session-load
        Make session/load always return an error.

Default behavior: echo the prompt back as a single agent_message_chunk,
emit a usage_update with fake cost, return end_turn.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, TextIO


@dataclass
class Session:
    """Tracked session state."""
    session_id: str
    parent_id: str | None = None
    cancelled: bool = False
    prompt_count: int = 0


@dataclass
class MockAcpServer:
    """ACP-compatible mock server over stdio."""

    capabilities: dict[str, bool] = field(default_factory=lambda: {
        "fork": False,
        "sessions": True,
        "multi_session": True,
    })
    response_script: list[list[dict]] | None = None
    fail_session_load: bool = False

    sessions: dict[str, Session] = field(default_factory=dict)
    _input: TextIO = field(default_factory=lambda: sys.stdin)
    _output: TextIO = field(default_factory=lambda: sys.stdout)
    _next_id: int = 0
    _script_index: int = 0

    def run(self) -> None:
        """Main loop: read NDJSON from stdin, dispatch, write to stdout."""
        for raw_line in self._input:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                msg = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            self._handle_initialize(msg_id, params)
        elif method == "session/new":
            self._handle_session_new(msg_id, params)
        elif method == "session/load":
            self._handle_session_load(msg_id, params)
        elif method == "session/fork":
            self._handle_session_fork(msg_id, params)
        elif method == "session/prompt":
            self._handle_session_prompt(msg_id, params)
        elif method == "session/cancel":
            self._handle_session_cancel(msg_id, params)
        elif method == "session/close":
            self._handle_session_close(msg_id, params)
        else:
            self._send_error(msg_id, -32601, f"Method not found: {method}")

    def _handle_initialize(self, msg_id: Any, params: dict) -> None:
        self._send_result(msg_id, {
            "protocolVersion": params.get("protocolVersion", 1),
            "capabilities": self.capabilities,
        })

    def _handle_session_new(self, msg_id: Any, params: dict) -> None:
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = Session(session_id=session_id)
        self._send_result(msg_id, {"sessionId": session_id})

    def _handle_session_load(self, msg_id: Any, params: dict) -> None:
        session_id = params.get("sessionId", "")
        if self.fail_session_load:
            self._send_error(msg_id, -32000, "Session load failed")
            return
        if session_id not in self.sessions:
            # Create a synthetic session (simulates loading from storage)
            self.sessions[session_id] = Session(session_id=session_id)
        self._send_result(msg_id, {"sessionId": session_id})

    def _handle_session_fork(self, msg_id: Any, params: dict) -> None:
        if not self.capabilities.get("fork"):
            self._send_error(msg_id, -32000, "Fork not supported")
            return
        parent_id = params.get("sessionId", "")
        if parent_id not in self.sessions:
            self._send_error(msg_id, -32000, f"Session not found: {parent_id}")
            return
        new_id = str(uuid.uuid4())
        self.sessions[new_id] = Session(session_id=new_id, parent_id=parent_id)
        self._send_result(msg_id, {"sessionId": new_id})

    def _handle_session_prompt(self, msg_id: Any, params: dict) -> None:
        session_id = params.get("sessionId", "")
        raw_prompt = params.get("prompt", "")
        # ACP prompt can be a string or a list of content blocks
        if isinstance(raw_prompt, list):
            prompt_text = " ".join(
                block.get("text", "") for block in raw_prompt
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            prompt_text = str(raw_prompt)

        if session_id not in self.sessions:
            self._send_error(msg_id, -32000, f"Session not found: {session_id}")
            return

        session = self.sessions[session_id]
        session.cancelled = False
        session.prompt_count += 1

        # Emit scripted notifications or default behavior
        if self.response_script and self._script_index < len(self.response_script):
            notifications = self.response_script[self._script_index]
            self._script_index += 1
            for notif in notifications:
                # Inject session_id if not present
                if "params" in notif:
                    notif["params"].setdefault("sessionId", session_id)
                self._send_notification(notif)
        else:
            # Default: echo prompt as agent_message_chunk + usage_update
            self._send_session_update(session_id, {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": prompt_text},
            })
            self._send_session_update(session_id, {
                "sessionUpdate": "usage_update",
                "used": len(prompt_text) * 4,  # fake token count
                "size": 200000,
                "cost": {"amount": 0.001, "currency": "USD"},
            })

        # Return prompt result
        self._send_result(msg_id, {
            "sessionId": session_id,
            "stopReason": "end_turn",
        })

    def _handle_session_cancel(self, msg_id: Any, params: dict) -> None:
        session_id = params.get("sessionId", "")
        if session_id in self.sessions:
            self.sessions[session_id].cancelled = True
            self._send_result(msg_id, {"sessionId": session_id, "cancelled": True})
        else:
            self._send_error(msg_id, -32000, f"Session not found: {session_id}")

    def _handle_session_close(self, msg_id: Any, params: dict) -> None:
        session_id = params.get("sessionId", "")
        if session_id in self.sessions:
            del self.sessions[session_id]
        self._send_result(msg_id, {"sessionId": session_id, "closed": True})

    # ── Output helpers ──────────────────────────────────────────────

    def _send_result(self, msg_id: Any, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _send_error(self, msg_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    def _send_notification(self, notif: dict) -> None:
        """Send a pre-built notification dict."""
        self._write(notif)

    def _send_session_update(self, session_id: str, update: dict) -> None:
        self._write({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": update},
        })

    def _write(self, msg: dict) -> None:
        self._output.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self._output.flush()


def create_server(
    capabilities: dict | None = None,
    response_script: list[list[dict]] | None = None,
    fail_session_load: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> MockAcpServer:
    """Create a configured MockAcpServer.

    For use in tests — pass StringIO objects for input/output.
    """
    server = MockAcpServer(
        capabilities=capabilities or {"fork": False, "sessions": True, "multi_session": True},
        response_script=response_script,
        fail_session_load=fail_session_load,
    )
    if input_stream is not None:
        server._input = input_stream
    if output_stream is not None:
        server._output = output_stream
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock ACP server for testing")
    parser.add_argument(
        "--capabilities",
        type=str,
        default='{"fork": false, "sessions": true, "multi_session": true}',
        help="JSON dict of capabilities",
    )
    parser.add_argument(
        "--response-script",
        type=str,
        default=None,
        help="Path to JSON file with scripted responses",
    )
    parser.add_argument(
        "--fail-session-load",
        action="store_true",
        help="Make session/load always return an error",
    )
    args = parser.parse_args()

    capabilities = json.loads(args.capabilities)
    response_script = None
    if args.response_script:
        with open(args.response_script) as f:
            response_script = json.load(f)

    server = MockAcpServer(
        capabilities=capabilities,
        response_script=response_script,
        fail_session_load=args.fail_session_load,
    )
    server.run()


if __name__ == "__main__":
    main()
