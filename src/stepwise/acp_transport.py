"""JSON-RPC 2.0 over stdio transport for ACP agent subprocesses.

Handles request/response multiplexing, notification dispatch,
non-JSON line filtering, and thread-safe send/receive.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from concurrent.futures import Future
from typing import Any, Callable

logger = logging.getLogger("stepwise.acp_transport")


class AcpError(Exception):
    """Error returned by an ACP server in a JSON-RPC error response."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class JsonRpcTransport:
    """JSON-RPC 2.0 over stdio to a subprocess.

    Handles:
    - Request/response multiplexing (futures keyed by request ID)
    - Notification dispatch (method -> callback)
    - Non-JSON line filtering (stdout pollution protection)
    - Thread-safe send/receive
    """

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self._next_id = 1
        self._pending: dict[int, Future] = {}
        self._notification_handlers: dict[str, list[Callable]] = {}
        self._request_handlers: dict[str, Callable] = {}
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        # Increase pipe buffer to reduce backpressure stalls
        try:
            import fcntl
            F_SETPIPE_SZ = 1031
            fcntl.fcntl(process.stdout.fileno(), F_SETPIPE_SZ, 1048576)  # 1MB
        except (OSError, AttributeError):
            pass

    def start(self) -> None:
        """Start the background reader thread."""
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"jsonrpc-reader-{self.process.pid}",
        )
        self._reader_thread.start()

    def send_request(self, method: str, params: dict | None = None) -> Future:
        """Send a JSON-RPC request, return a Future for the response."""
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            future: Future = Future()
            self._pending[req_id] = future

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        return future

    def send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def on_notification(self, method: str, handler: Callable) -> None:
        """Register a handler for incoming notifications.

        Multiple handlers can be registered for the same method.
        All registered handlers are called for each notification.
        """
        if method not in self._notification_handlers:
            self._notification_handlers[method] = []
        self._notification_handlers[method].append(handler)

    def off_notification(self, method: str, handler: Callable) -> None:
        """Unregister a notification handler."""
        handlers = self._notification_handlers.get(method, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def on_request(self, method: str, handler: Callable) -> None:
        """Register a handler for incoming requests (server→client).

        Handler receives (params) and should return a result dict.
        The transport sends the JSON-RPC response automatically.
        """
        self._request_handlers[method] = handler

    def close(self) -> None:
        """Signal reader thread to stop and cancel pending futures."""
        self._closed = True
        with self._lock:
            for future in self._pending.values():
                if not future.done():
                    future.cancel()
            self._pending.clear()

    def fail_pending(self, future: Future, exc: BaseException) -> None:
        """Remove `future` from the pending registry and set its exception.

        Used by watchdogs that decide a request is hung (e.g. idle-stream
        timeout). Dropping the registry entry prevents the reader thread
        from later trying to resolve a future that has already been failed
        and avoids leaking pending entries for requests the agent will
        never respond to.
        """
        with self._lock:
            to_remove = None
            for req_id, pending in self._pending.items():
                if pending is future:
                    to_remove = req_id
                    break
            if to_remove is not None:
                self._pending.pop(to_remove, None)
        if not future.done():
            try:
                future.set_exception(exc)
            except Exception:
                pass

    def _write(self, msg: dict) -> None:
        """Write a JSON-RPC message to subprocess stdin."""
        try:
            line = json.dumps(msg, separators=(",", ":")) + "\n"
            logger.info("[acp tx] %s", line.strip()[:500])
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.debug("Write failed (process likely exited): %s", exc)

    def _read_loop(self) -> None:
        """Read JSON-RPC messages from subprocess stdout."""
        try:
            for line in self.process.stdout:
                if self._closed:
                    break
                line = line.strip()
                if not line:
                    continue
                logger.info("[acp rx] %s", line[:500])
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.info("[acp rx non-json] %s", line[:200])
                    continue  # Skip non-JSON lines (stdout pollution)

                if "id" in msg and "method" in msg:
                    # Incoming request from the agent (server→client)
                    req_id = msg["id"]
                    method = msg["method"]
                    handler = self._request_handlers.get(method)
                    if handler:
                        try:
                            result = handler(msg.get("params", {}))
                            self._write({
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": result if result is not None else {},
                            })
                        except Exception as exc:
                            logger.debug(
                                "Request handler error for %s: %s",
                                method, exc,
                            )
                            self._write({
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32000,
                                    "message": str(exc),
                                },
                            })
                    else:
                        # No handler — return method-not-found
                        logger.warning(
                            "Unhandled incoming request: %s (id=%s)",
                            method, req_id,
                        )
                        self._write({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {method}",
                            },
                        })
                elif "id" in msg and "method" not in msg:
                    # Response to a request we sent
                    req_id = msg["id"]
                    with self._lock:
                        future = self._pending.pop(req_id, None)
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(
                                AcpError(
                                    msg["error"].get("message", "Unknown error"),
                                    msg["error"].get("code"),
                                )
                            )
                        else:
                            future.set_result(msg.get("result", {}))
                elif "method" in msg and "id" not in msg:
                    # Notification — call all registered handlers
                    for handler in self._notification_handlers.get(msg["method"], []):
                        try:
                            handler(msg.get("params", {}))
                        except Exception:
                            logger.debug(
                                "Notification handler error for %s",
                                msg["method"],
                                exc_info=True,
                            )
        except (OSError, ValueError):
            pass  # Process stdout closed
        finally:
            # Cancel any remaining pending futures
            with self._lock:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(
                            AcpError("Transport closed", code=-1)
                        )
                self._pending.clear()
