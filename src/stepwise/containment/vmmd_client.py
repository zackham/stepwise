"""vmmd client — talks to the VM Manager Daemon over Unix socket.

Used by CloudHypervisorBackend to request VM boot/destroy without
needing root privileges. The vmmd daemon runs as root and handles
all privileged operations.

The data path (vsock for ACP stdio) goes directly from this process
to the guest — vmmd only handles the control plane.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

from stepwise.containment.backend import ContainmentConfig
from stepwise.containment.vmmd import PID_NAME, SOCKET_NAME, _default_vmm_dir

logger = logging.getLogger("stepwise.containment.vmmd_client")


def _default_socket() -> Path:
    """Invoking-user-aware vmmd socket path.

    Honors SUDO_USER so that `sudo stepwise vmmd {status,stop,restart}`
    finds the socket at the invoking user's ~/.stepwise/vmm, not root's.
    Evaluated lazily (not a module-level constant) so tests can
    monkeypatch SUDO_USER between calls.
    """
    return _default_vmm_dir() / SOCKET_NAME


def _default_pid() -> Path:
    return _default_vmm_dir() / PID_NAME


# Backwards-compatible module-level names — computed at import time
# against the user in play when the module first loads. Prefer the
# functions above in code paths that might run under sudo.
DEFAULT_SOCKET = _default_socket()
DEFAULT_PID = _default_pid()


class VMManagerNotRunning(RuntimeError):
    """vmmd daemon is not running."""
    pass


class VMManagerClient:
    """Client for the vmmd daemon.

    Connects to vmmd over Unix socket, sends JSON-RPC requests,
    returns parsed responses. Auto-starts vmmd if not running.
    """

    def __init__(
        self,
        socket_path: str | Path | None = None,
        auto_start: bool = True,
    ):
        self._socket_path = Path(socket_path or _default_socket())
        self._auto_start = auto_start
        self._conn: socket.socket | None = None
        self._rfile: IO[str] | None = None
        self._wfile: IO[str] | None = None
        self._req_id = 0

    def _connect(self) -> None:
        """Connect to vmmd socket. Auto-start if needed."""
        if self._conn:
            return

        if not self._socket_path.exists():
            if self._auto_start:
                self._start_vmmd()
            else:
                raise VMManagerNotRunning(
                    f"vmmd not running (socket not found: {self._socket_path}). "
                    f"Start with: sudo stepwise vmmd start --detach"
                )

        try:
            self._conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._conn.settimeout(120)  # Long timeout for boot operations
            self._conn.connect(str(self._socket_path))
            self._rfile = self._conn.makefile("r", encoding="utf-8")
            self._wfile = self._conn.makefile("w", encoding="utf-8")
        except (ConnectionRefusedError, FileNotFoundError) as e:
            self._conn = None
            raise VMManagerNotRunning(
                f"Cannot connect to vmmd: {e}. "
                f"Start with: sudo stepwise vmmd start --detach"
            ) from e

    def _disconnect(self) -> None:
        """Close the connection."""
        for f in (self._rfile, self._wfile):
            try:
                if f:
                    f.close()
            except Exception:
                pass
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._rfile = None
        self._wfile = None

    def _call(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        """Send a request to vmmd and return the result."""
        self._connect()

        self._req_id += 1
        request = json.dumps({
            "method": method,
            "params": params or {},
            "id": self._req_id,
        }) + "\n"

        try:
            # Set per-call timeout (boot can take 30-60s)
            self._conn.settimeout(timeout)

            self._wfile.write(request)
            self._wfile.flush()

            response_line = self._rfile.readline()
            if not response_line:
                raise ConnectionError("vmmd closed connection")

            response = json.loads(response_line)

            if "error" in response:
                err = response["error"]
                raise RuntimeError(f"vmmd error: {err.get('message', str(err))}")

            return response.get("result", {})

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._disconnect()
            raise VMManagerNotRunning(f"vmmd connection lost: {e}") from e

    def _start_vmmd(self) -> None:
        """Auto-start vmmd via sudo."""
        logger.info("vmmd not running, starting...")

        # Determine the vmmd module path
        vmmd_module = "stepwise.containment.vmmd"

        # Use the same Python interpreter
        python = sys.executable

        work_dir = str(self._socket_path.parent)

        proc = subprocess.Popen(
            ["sudo", "-S", python, "-m", vmmd_module,
             "--work-dir", work_dir],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Wait for socket to appear
        for _ in range(100):
            if self._socket_path.exists():
                logger.info("vmmd started (pid=%d)", proc.pid)
                return
            time.sleep(0.1)

        raise VMManagerNotRunning(
            "vmmd failed to start within 10s. "
            "Try starting manually: sudo stepwise vmmd start"
        )

    # ── Public API ────────────────────────────────────────────

    def boot(self, config: ContainmentConfig) -> dict:
        """Boot a VM (or reuse existing). Returns {vm_id, vsock_socket, cid, reused}."""
        return self._call("boot", {
            "tools": config.tools,
            "allowed_paths": config.allowed_paths,
            "credentials": config.credentials,
            "network": config.network,
            "memory_mb": config.memory_mb,
            "cpus": config.cpus,
            "working_dir": config.working_dir,
        }, timeout=300)

    def destroy(self, vm_id: str) -> dict:
        """Destroy a specific VM."""
        return self._call("destroy", {"vm_id": vm_id})

    def destroy_all(self) -> dict:
        """Destroy all VMs."""
        return self._call("destroy_all")

    def list_vms(self) -> list[dict]:
        """List running VMs."""
        result = self._call("list")
        return result.get("vms", [])

    def ping(self) -> dict:
        """Health check."""
        return self._call("ping")

    def status(self) -> dict:
        """Detailed daemon status."""
        return self._call("status")

    def close(self) -> None:
        """Close the client connection."""
        self._disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── Utility functions ────────────────────────────────────────────


def is_vmmd_running(socket_path: Path | None = None) -> bool:
    """Check if vmmd is running by probing the socket."""
    sock_path = Path(socket_path or _default_socket())
    if not sock_path.exists():
        return False
    try:
        client = VMManagerClient(socket_path=sock_path, auto_start=False)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


def get_vmmd_pid(pid_path: Path | None = None) -> int | None:
    """Read vmmd PID from PID file. Returns None if not running."""
    path = Path(pid_path or _default_pid())
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        # Check if process exists
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return None


def stop_vmmd(pid_path: Path | None = None, socket_path: Path | None = None) -> bool:
    """Stop a running vmmd daemon. Returns True if stopped."""
    pid = get_vmmd_pid(pid_path)
    if pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        # Need sudo to kill root-owned process
        subprocess.run(["sudo", "kill", str(pid)], capture_output=True)

    # Wait for exit
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)

    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        subprocess.run(["sudo", "kill", "-9", str(pid)], capture_output=True)

    return True
