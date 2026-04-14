"""ACP containment bridge: in-VM tool executor for claude/codex.

When a flow uses `containment: cloud-hypervisor` with claude or codex,
the ACP adapter (npx `@agentclientprotocol/claude-agent-acp` or
`@zed-industries/codex-acp`) runs on the HOST — it needs network and
API keys — but the filesystem / shell operations it delegates back to
the client must execute INSIDE the microVM's sandboxed filesystem so
containment is real.

This module provides:

- `BRIDGE_PORT`: the vsock port the bridge listens on inside the VM.
- `ACP_BRIDGE_SCRIPT`: the guest-side Python script, embedded as a
  string constant (same pattern as `GUEST_AGENT_SCRIPT`). It's copied
  into the rootfs by `rootfs.build_rootfs()` and launched by
  `GUEST_INIT_SCRIPT` alongside the existing guest-agent.
- `BridgeClient`: host-side request/response client. Takes any stream
  pair (socket file handles) so tests can drive it over a Unix socket
  without needing a VM.
- `translate_path`: maps a host absolute path under `host_workdir` to
  the equivalent path under the VM's `/mnt/workspace` virtiofs mount.

aloop is unaffected — it runs entirely inside the VM via
`VMSpawnContext.spawn` and handles tools internally.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import IO, Any

BRIDGE_PORT = 9998
GUEST_WORKSPACE_MOUNT = "/mnt/workspace"


# ── Path translation ─────────────────────────────────────────────


def translate_path(path: str, host_workdir: str) -> str:
    """Rewrite a host-side path so it resolves inside the VM.

    Host-relative paths pass through unchanged; the bridge resolves
    them against its cwd, which is `/mnt/workspace` (the virtiofs
    mount mirroring `host_workdir`).

    Host-absolute paths under `host_workdir` are rewritten to
    `/mnt/workspace/<relative>`.

    Host-absolute paths outside `host_workdir` pass through unchanged
    — they will naturally fail inside the VM's filesystem, which is
    the containment guarantee.
    """
    if not path or not os.path.isabs(path):
        return path

    host_workdir = os.path.abspath(host_workdir).rstrip("/")
    abs_path = os.path.abspath(path)

    if abs_path == host_workdir:
        return GUEST_WORKSPACE_MOUNT
    prefix = host_workdir + "/"
    if abs_path.startswith(prefix):
        return GUEST_WORKSPACE_MOUNT + "/" + abs_path[len(prefix):]
    return path


# ── Host-side BridgeClient ───────────────────────────────────────


class BridgeError(RuntimeError):
    """Bridge returned a structured error for a call."""


@dataclass
class _StreamPair:
    """Duck-typed wrapper for a bidirectional newline stream."""

    rfile: IO[str]
    wfile: IO[str]


class BridgeClient:
    """Issues newline-delimited JSON-RPC requests to the in-VM bridge.

    Transport-agnostic — takes any rfile/wfile pair. In production the
    caller hands it vsock-backed file objects obtained via the
    cloud-hypervisor `CONNECT 9998` protocol. Tests drive it over a
    Unix socket.

    Thread-safe: a single lock serializes request/response turns so
    multiple host-side handler threads can share one connection.
    """

    def __init__(
        self,
        rfile: IO[str],
        wfile: IO[str],
        close_fn: Any = None,
        host_workdir: str = "",
    ):
        self._rfile = rfile
        self._wfile = wfile
        self._close_fn = close_fn
        self._host_workdir = host_workdir
        self._next_id = 0
        self._lock = threading.Lock()
        self._closed = False

    @property
    def host_workdir(self) -> str:
        return self._host_workdir

    def call(self, method: str, params: dict) -> dict:
        """Send a request, wait for the matching response."""
        with self._lock:
            if self._closed:
                raise BridgeError("bridge connection closed")
            self._next_id += 1
            req_id = self._next_id
            payload = json.dumps(
                {"id": req_id, "method": method, "params": params}
            )
            try:
                self._wfile.write(payload + "\n")
                self._wfile.flush()
            except (OSError, ValueError) as exc:
                self._closed = True
                raise BridgeError(f"bridge write failed: {exc}") from exc

            try:
                line = self._rfile.readline()
            except (OSError, ValueError) as exc:
                self._closed = True
                raise BridgeError(f"bridge read failed: {exc}") from exc

            if not line:
                self._closed = True
                raise BridgeError("bridge connection closed mid-call")

            try:
                resp = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BridgeError(f"bridge response not JSON: {line!r}") from exc

            if resp.get("id") != req_id:
                raise BridgeError(
                    f"bridge response id mismatch: sent {req_id}, got {resp.get('id')}"
                )
            if "error" in resp:
                raise BridgeError(resp["error"].get("message", "bridge error"))
            return resp.get("result", {})

    def ping(self) -> bool:
        """Health check. Returns True iff bridge responded with pong."""
        try:
            result = self.call("ping", {})
            return bool(result.get("pong"))
        except BridgeError:
            return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for f in (self._wfile, self._rfile):
            try:
                f.close()
            except Exception:
                pass
        if self._close_fn:
            try:
                self._close_fn()
            except Exception:
                pass


# ── Guest-side script (embedded in rootfs) ───────────────────────


ACP_BRIDGE_SCRIPT = r'''#!/usr/bin/env python3
"""Stepwise ACP containment bridge — runs inside the microVM.

Listens on vsock port 9998. For each connection, reads newline-
delimited JSON-RPC requests and executes them inside the VM's
filesystem (sandbox = VM rootfs + virtiofs `/mnt/workspace` mount).

Supported methods mirror the ACP client handlers:

    fs/read_text_file   {"path": str}                 -> {"content": str}
    fs/write_text_file  {"path": str, "content": str} -> {}
    terminal/create     {"command": str, "cwd"?: str} -> {"terminalId": str, "pid": int}
    terminal/output     {"terminalId": str}           -> {"output": str, "isEof": bool}
    terminal/wait_for_exit {"terminalId": str, "timeoutMs"?: int}
                                                      -> {"exitCode": int|null, "output"?: str}
    terminal/kill       {"terminalId": str}           -> {}
    terminal/release    {"terminalId": str}           -> {}
    ping                {}                            -> {"pong": true}

Per-connection state (open terminals) is isolated so concurrent ACP
processes sharing a VM don't collide.

All paths are interpreted relative to `/mnt/workspace` unless
absolute — matching the `translate_path` rewrite done host-side.
"""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
from uuid import uuid4

LISTEN_PORT = 9998
BUF_SIZE = 65536
WORKSPACE = "/mnt/workspace"


def _ok(req_id, result):
    return json.dumps({"id": req_id, "result": result}) + "\n"


def _err(req_id, message):
    return json.dumps({"id": req_id, "error": {"message": str(message)}}) + "\n"


def _resolve(path):
    if not path:
        return WORKSPACE
    if os.path.isabs(path):
        return path
    return os.path.join(WORKSPACE, path)


def handle_connection(conn):
    terminals = {}
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
    except OSError:
        pass
    rfile = conn.makefile("r", buffering=1, encoding="utf-8")
    wfile = conn.makefile("w", buffering=1, encoding="utf-8")

    def send(line):
        try:
            wfile.write(line)
            wfile.flush()
        except (OSError, BrokenPipeError):
            pass

    try:
        for line in rfile:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {}) or {}

            try:
                if method == "ping":
                    send(_ok(req_id, {"pong": True}))
                elif method == "fs/read_text_file":
                    target = _resolve(params.get("path", ""))
                    with open(target, "r") as f:
                        send(_ok(req_id, {"content": f.read()}))
                elif method == "fs/write_text_file":
                    target = _resolve(params.get("path", ""))
                    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                    with open(target, "w") as f:
                        f.write(params.get("content", ""))
                    send(_ok(req_id, {}))
                elif method == "terminal/create":
                    cmd = params.get("command", "")
                    cwd = params.get("cwd") or WORKSPACE
                    if not os.path.isabs(cwd):
                        cwd = os.path.join(WORKSPACE, cwd)
                    try:
                        os.makedirs(cwd, exist_ok=True)
                    except OSError:
                        pass
                    proc = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=cwd,
                        start_new_session=True,
                    )
                    tid = str(uuid4())
                    terminals[tid] = proc
                    send(_ok(req_id, {"terminalId": tid, "pid": proc.pid}))
                elif method == "terminal/output":
                    tid = params.get("terminalId", "")
                    proc = terminals.get(tid)
                    if not proc or not proc.stdout:
                        send(_ok(req_id, {"output": "", "isEof": True}))
                        continue
                    ready, _, _ = select.select([proc.stdout], [], [], 0.1)
                    if ready:
                        chunk = proc.stdout.read(BUF_SIZE) if proc.stdout.readable() else ""
                        is_eof = (chunk == "") and (proc.poll() is not None)
                        send(_ok(req_id, {"output": chunk or "", "isEof": is_eof}))
                    else:
                        send(_ok(req_id, {"output": "", "isEof": proc.poll() is not None}))
                elif method == "terminal/wait_for_exit":
                    tid = params.get("terminalId", "")
                    timeout_ms = params.get("timeoutMs", 30000)
                    proc = terminals.get(tid)
                    if not proc:
                        send(_ok(req_id, {"exitCode": -1}))
                        continue
                    try:
                        proc.wait(timeout=timeout_ms / 1000)
                        remaining = proc.stdout.read() if proc.stdout else ""
                        send(_ok(req_id, {"exitCode": proc.returncode, "output": remaining}))
                    except subprocess.TimeoutExpired:
                        send(_ok(req_id, {"exitCode": None, "timedOut": True}))
                elif method == "terminal/kill":
                    tid = params.get("terminalId", "")
                    proc = terminals.get(tid)
                    if proc:
                        try:
                            proc.kill()
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                    send(_ok(req_id, {}))
                elif method == "terminal/release":
                    tid = params.get("terminalId", "")
                    proc = terminals.pop(tid, None)
                    if proc and proc.poll() is None:
                        try:
                            proc.kill()
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                    send(_ok(req_id, {}))
                else:
                    send(_err(req_id, f"unknown method: {method}"))
            except Exception as exc:
                send(_err(req_id, str(exc)))
    finally:
        for proc in list(terminals.values()):
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            rfile.close()
        except Exception:
            pass
        try:
            wfile.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def main():
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((socket.VMADDR_CID_ANY, LISTEN_PORT))
    sock.listen(16)

    print(f"ACP bridge listening on vsock port {LISTEN_PORT}", file=sys.stderr)

    while True:
        try:
            conn, _addr = sock.accept()
            t = threading.Thread(target=handle_connection, args=(conn,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"bridge accept error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
'''
