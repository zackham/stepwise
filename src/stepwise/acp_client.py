"""ACP protocol client speaking to a single agent subprocess.

Built on :class:`JsonRpcTransport`. One ACPClient per subprocess.
Supports multiple sessions on the same connection (agents like
claude-agent-acp and aloop support multi-session per process).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from stepwise.acp_transport import JsonRpcTransport

logger = logging.getLogger("stepwise.acp_client")


class ACPClient:
    """ACP protocol client for a single agent subprocess."""

    def __init__(self, transport: JsonRpcTransport):
        self.transport = transport
        self.sessions: dict[str, str] = {}  # session_name -> session_id

    def initialize(self) -> dict:
        """ACP handshake. Returns agent capabilities."""
        future = self.transport.send_request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "stepwise", "version": "0.38.0"},
        })
        return future.result(timeout=30)

    def new_session(self, cwd: str, session_name: str | None = None) -> str:
        """Create new ACP session. Returns session_id."""
        future = self.transport.send_request("session/new", {
            "cwd": cwd,
            "mcpServers": [],
        })
        result = future.result(timeout=30)
        session_id = result["sessionId"]
        if session_name:
            self.sessions[session_name] = session_id
        return session_id

    def load_session(self, session_id: str, cwd: str) -> None:
        """Load existing session for continuation."""
        future = self.transport.send_request("session/load", {
            "sessionId": session_id,
            "cwd": cwd,
            "mcpServers": [],
        })
        future.result(timeout=30)

    def fork_session(self, session_id: str, cwd: str) -> str:
        """Fork from existing session. Returns new session_id."""
        future = self.transport.send_request("session/fork", {
            "sessionId": session_id,
            "cwd": cwd,
            "mcpServers": [],
        })
        result = future.result(timeout=30)
        return result["sessionId"]

    def prompt(
        self,
        session_id: str,
        text: str,
        on_update: Callable[[dict], None] | None = None,
        output_path: str | None = None,
    ) -> dict:
        """Send prompt, stream updates, return PromptResponse.

        Args:
            session_id: The session to prompt.
            text: The prompt text.
            on_update: Called for each session/update notification.
            output_path: If set, write all ACP messages to this NDJSON file.
        """
        output_file = open(output_path, "a") if output_path else None

        def _on_session_update(params: dict) -> None:
            if output_file:
                output_file.write(
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "session/update", "params": params},
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                output_file.flush()
            if on_update:
                on_update(params)

        self.transport.on_notification("session/update", _on_session_update)

        try:
            future = self.transport.send_request("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            })
            result = future.result(timeout=14400)  # 4 hour timeout

            # Write the final response to NDJSON
            if output_file:
                output_file.write(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": 0, "result": result},
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                output_file.flush()

            return result
        finally:
            if output_file:
                output_file.close()

    def cancel(self, session_id: str) -> None:
        """Cancel in-flight prompt."""
        self.transport.send_notification("session/cancel", {
            "sessionId": session_id,
        })

    def close_session(self, session_id: str) -> None:
        """Close session and release resources."""
        try:
            future = self.transport.send_request("session/close", {
                "sessionId": session_id,
            })
            future.result(timeout=5)
        except Exception:
            pass  # Best effort

    def set_session_mode(self, session_id: str, mode: str) -> None:
        """Set session mode (for agents that support it)."""
        future = self.transport.send_request("session/set_mode", {
            "sessionId": session_id,
            "modeId": mode,
        })
        future.result(timeout=10)

    def set_session_model(self, session_id: str, model: str) -> None:
        """Set session model."""
        future = self.transport.send_request("session/set_model", {
            "sessionId": session_id,
            "modelId": model,
        })
        future.result(timeout=10)
