"""ACP protocol client speaking to a single agent subprocess.

Built on :class:`JsonRpcTransport`. One ACPClient per subprocess.
Supports multiple sessions on the same connection (agents like
claude-agent-acp and aloop support multi-session per process).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Callable

from stepwise.acp_transport import AcpError, JsonRpcTransport

logger = logging.getLogger("stepwise.acp_client")

# Default idle-stream timeout: if no session/update notification arrives for
# this many seconds while a prompt is pending, the watchdog cancels the
# prompt and fails with AcpError. Guards against silent upstream API stalls
# where the agent subprocess streams partial output and then goes quiet
# without emitting the final session/prompt response (observed: Anthropic
# stream dying mid-turn with no message_stop; claude-agent-acp's own stream
# idle detector does not always catch it). Override via
# STEPWISE_ACP_IDLE_TIMEOUT env var or per-call argument.
DEFAULT_IDLE_TIMEOUT_SECONDS = 900.0


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
        idle_timeout_seconds: float | None = None,
    ) -> dict:
        """Send prompt, stream updates, return PromptResponse.

        Args:
            session_id: The session to prompt.
            text: The prompt text.
            on_update: Called for each session/update notification.
            output_path: If set, write all ACP messages to this NDJSON file.
            idle_timeout_seconds: Cancel the prompt if no session/update
                notification arrives for this many seconds. Defaults to
                STEPWISE_ACP_IDLE_TIMEOUT env var or
                DEFAULT_IDLE_TIMEOUT_SECONDS. Set to 0 or negative to disable.
        """
        if idle_timeout_seconds is None:
            env_val = os.environ.get("STEPWISE_ACP_IDLE_TIMEOUT")
            if env_val:
                try:
                    idle_timeout_seconds = float(env_val)
                except ValueError:
                    idle_timeout_seconds = DEFAULT_IDLE_TIMEOUT_SECONDS
            else:
                idle_timeout_seconds = DEFAULT_IDLE_TIMEOUT_SECONDS

        # Use a write queue to decouple pipe reading from file I/O.
        # The reader thread must drain the pipe as fast as possible to prevent
        # backpressure stalls. File writes happen in a dedicated writer thread.
        write_queue: queue.Queue[str | None] = queue.Queue(maxsize=0)
        writer_thread = None

        if output_path:
            output_file = open(output_path, "a", buffering=65536)

            def _writer():
                """Drain write queue to output file."""
                while True:
                    item = write_queue.get()
                    if item is None:
                        break  # Sentinel: prompt done
                    output_file.write(item)
                    # Flush after every batch drain for UI responsiveness
                    if write_queue.empty():
                        output_file.flush()

            writer_thread = threading.Thread(
                target=_writer, daemon=True, name="ndjson-writer"
            )
            writer_thread.start()
        else:
            output_file = None

        last_update_monotonic = time.monotonic()

        def _on_session_update(params: dict) -> None:
            # Only handle updates for this session — when multiple prompts
            # share an ACP process, each handler must ignore other sessions'
            # notifications to prevent cross-job output corruption.
            if params.get("sessionId") != session_id:
                return
            nonlocal last_update_monotonic
            last_update_monotonic = time.monotonic()
            if output_file:
                line = (
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "session/update", "params": params},
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                write_queue.put_nowait(line)
            if on_update:
                on_update(params)

        self.transport.on_notification("session/update", _on_session_update)

        future = self.transport.send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        })

        watchdog_stop = threading.Event()
        watchdog_thread: threading.Thread | None = None

        if idle_timeout_seconds and idle_timeout_seconds > 0:
            def _idle_watchdog():
                # Poll at idle/4 up to 30s — frequent enough to detect
                # breach promptly, infrequent enough to avoid overhead.
                poll_interval = max(5.0, min(30.0, idle_timeout_seconds / 4))
                while not watchdog_stop.wait(timeout=poll_interval):
                    if future.done():
                        return
                    idle = time.monotonic() - last_update_monotonic
                    if idle < idle_timeout_seconds:
                        continue
                    logger.warning(
                        "ACP session %s idle for %.0fs (limit %.0fs) — "
                        "cancelling prompt",
                        session_id, idle, idle_timeout_seconds,
                    )
                    try:
                        self.cancel(session_id)
                    except Exception:
                        logger.debug(
                            "session/cancel failed during idle watchdog",
                            exc_info=True,
                        )
                    # Give the agent up to 5s to honor the cancel
                    # (returns a session/prompt result with stopReason
                    # "cancelled"). If it doesn't, force-fail the future
                    # so the caller stops waiting.
                    for _ in range(10):
                        if future.done() or watchdog_stop.is_set():
                            return
                        time.sleep(0.5)
                    if not future.done():
                        self.transport.fail_pending(
                            future,
                            AcpError(
                                f"Stream idle timeout: no session/update for "
                                f"{idle:.0f}s (limit {idle_timeout_seconds:.0f}s); "
                                f"session {session_id} did not respond to cancel",
                                code=-32001,
                            ),
                        )
                    return

            watchdog_thread = threading.Thread(
                target=_idle_watchdog,
                daemon=True,
                name=f"acp-idle-{session_id[:8]}",
            )
            watchdog_thread.start()

        try:
            result = future.result(timeout=14400)  # 4 hour timeout

            # Write the final response to NDJSON
            if output_file:
                write_queue.put(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": 0, "result": result},
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            return result
        finally:
            watchdog_stop.set()
            if watchdog_thread:
                watchdog_thread.join(timeout=2)
            self.transport.off_notification("session/update", _on_session_update)
            if writer_thread:
                write_queue.put(None)  # Signal writer to exit
                writer_thread.join(timeout=5)
            if output_file:
                output_file.flush()
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
