"""Cloud-Hypervisor containment backend.

Manages agent execution inside cloud-hypervisor microVMs via the
vmmd daemon. The daemon runs as root and handles all privileged
operations (virtiofsd, cloud-hypervisor, shared memory). This
backend runs unprivileged and talks to vmmd over a Unix socket.

The data path (vsock for ACP stdin/stdout) goes directly from this
process to the guest agent — vmmd only handles the control plane.

Architecture:
  stepwise (unprivileged)         vmmd (root)           Guest (microVM)
  ────────────────────            ──────────            ──────────────
  CloudHypervisorBackend          VMManagerDaemon
    └─ VMManagerClient ──socket──> boot/destroy         virtiofsd → virtiofs
    └─ VMSpawnContext ───────────────────────vsock────> guest-agent (port 9999)
         └─ VsockProcessHandle                           └─ ACP command
              └─ stdin/stdout ──────────────vsock────>       └─ stdio
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import IO, Any

from stepwise.containment.backend import (
    ContainmentConfig,
    ProcessHandle,
    SpawnContext,
)

logger = logging.getLogger("stepwise.containment.cloud_hypervisor")

GUEST_AGENT_PORT = 9999


# ── VsockProcessHandle ───────────────────────────────────────────


class VsockProcessHandle:
    """Process-like handle backed by a vsock connection to guest agent.

    Bridges the vsock stream to stdin/stdout file-like interfaces
    that JsonRpcTransport expects.
    """

    def __init__(
        self,
        sock: socket.socket,
        guest_pid: int = -1,
        vm_id: str = "",
        spawn_id: int = 0,
        rfile: IO[str] | None = None,
        wfile: IO[str] | None = None,
    ):
        self._sock = sock
        self._pid = guest_pid or os.getpid()
        self._vm_id = vm_id
        self._spawn_id = spawn_id
        self._terminated = False
        self._exit_code: int | None = None

        # Use pre-created file wrappers if provided (preserves buffered data)
        self._sock_rfile = rfile or sock.makefile("r", buffering=1, encoding="utf-8")
        self._sock_wfile = wfile or sock.makefile("w", buffering=1, encoding="utf-8")

        # Stderr is a no-op — errors come through JSON-RPC protocol
        self._stderr_buf = io.StringIO()

    @property
    def stdin(self) -> IO[str]:
        return self._sock_wfile

    @property
    def stdout(self) -> IO[str]:
        return self._sock_rfile

    @property
    def stderr(self) -> IO[str]:
        return self._stderr_buf

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        if self._terminated:
            return self._exit_code or 0
        try:
            self._sock.getpeername()
            return None
        except (OSError, socket.error):
            self._terminated = True
            self._exit_code = -1
            return self._exit_code

    def terminate(self) -> None:
        self._terminated = True
        self._exit_code = -15
        try:
            self._sock.sendall(b"__KILL__\n")
        except (OSError, BrokenPipeError):
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except (OSError, socket.error):
            pass

    def kill(self) -> None:
        self._terminated = True
        self._exit_code = -9
        try:
            self._sock.close()
        except (OSError, socket.error):
            pass

    def wait(self, timeout: float | None = None) -> int:
        if self._terminated:
            return self._exit_code or 0
        self._sock.settimeout(timeout)
        try:
            while True:
                data = self._sock.recv(65536)
                if not data:
                    break
        except (socket.timeout, OSError):
            pass
        self._terminated = True
        self._exit_code = self._exit_code or 0
        return self._exit_code


# ── VMSpawnContext ────────────────────────────────────────────────


@dataclass
class VMInfo:
    """Minimal VM info held by the client-side backend."""
    vm_id: str
    vsock_socket: str
    cid: int
    _next_spawn_id: int = 0


class VMSpawnContext:
    """SpawnContext that runs commands inside a cloud-hypervisor VM.

    Connects directly to the guest agent via the vsock Unix socket
    (made accessible by vmmd). No data goes through vmmd.
    """

    def __init__(self, vm: VMInfo):
        self._vm = vm

    def spawn(
        self,
        command: list[str],
        env: dict[str, str],
        cwd: str,
        text: bool = True,
    ) -> VsockProcessHandle:
        """Spawn a process inside the VM via guest agent."""
        spawn_id = self._vm._next_spawn_id
        self._vm._next_spawn_id += 1

        # Connect to guest agent via vsock
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(self._vm.vsock_socket)

        # Cloud-hypervisor vsock CONNECT protocol
        sock.sendall(f"CONNECT {GUEST_AGENT_PORT}\n".encode())

        # Read OK response (raw socket, before wrapping in file objects)
        response = b""
        while b"\n" not in response:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError("vsock CONNECT failed: connection closed")
            response += chunk

        if not response.startswith(b"OK"):
            raise ConnectionError(f"vsock CONNECT failed: {response!r}")

        # Send spawn request to guest agent
        spawn_request = json.dumps({
            "command": command,
            "env": env,
            "cwd": cwd,
        }).encode() + b"\n"
        sock.sendall(spawn_request)

        # Wrap in file objects BEFORE reading ACK so buffered data is preserved
        sock.settimeout(None)
        sock_rfile = sock.makefile("r", buffering=1, encoding="utf-8")
        sock_wfile = sock.makefile("w", buffering=1, encoding="utf-8")

        # Read ACK ({"pid": N})
        ack_line = sock_rfile.readline()
        try:
            ack_data = json.loads(ack_line)
            guest_pid = ack_data.get("pid", -1)
        except (json.JSONDecodeError, ValueError):
            guest_pid = -1

        return VsockProcessHandle(
            sock=sock,
            guest_pid=guest_pid,
            vm_id=self._vm.vm_id,
            spawn_id=spawn_id,
            rfile=sock_rfile,
            wfile=sock_wfile,
        )


# ── CloudHypervisorBackend ───────────────────────────────────────


class CloudHypervisorBackend:
    """Containment backend using cloud-hypervisor microVMs via vmmd.

    Talks to the vmmd daemon for VM lifecycle (boot/destroy).
    Connects directly to guest VMs via vsock for ACP stdio.
    """

    def __init__(self, vmmd_socket: str | None = None):
        from stepwise.containment.vmmd_client import VMManagerClient

        self._client = VMManagerClient(
            socket_path=vmmd_socket,
            auto_start=True,
        )
        self._active_vms: dict[str, VMInfo] = {}

    def get_spawn_context(
        self, config: ContainmentConfig,
    ) -> SpawnContext:
        """Get or create a VM for this config, return a SpawnContext."""
        # Check for reusable VM in our local cache
        config_key = self._config_key(config)
        if config_key in self._active_vms:
            return VMSpawnContext(self._active_vms[config_key])

        # Request VM from vmmd (may reuse server-side)
        result = self._client.boot(config)

        vm = VMInfo(
            vm_id=result["vm_id"],
            vsock_socket=result["vsock_socket"],
            cid=result["cid"],
        )
        self._active_vms[config_key] = vm

        return VMSpawnContext(vm)

    @staticmethod
    def _config_key(config: ContainmentConfig) -> str:
        """Generate a hashable key for VM reuse."""
        return json.dumps({
            "tools": config.tools,
            "allowed_paths": config.allowed_paths,
            "credentials": config.credentials,
            "network": config.network,
        }, sort_keys=True)

    def release_if_unused(
        self, remaining_steps_checker: Any,
    ) -> None:
        """Release VMs no longer needed."""
        to_remove = []
        for key, vm in self._active_vms.items():
            config = json.loads(key)
            config_obj = ContainmentConfig(**config)
            if not remaining_steps_checker(config_obj):
                try:
                    self._client.destroy(vm.vm_id)
                except Exception:
                    logger.debug("Error destroying VM %s", vm.vm_id, exc_info=True)
                else:
                    to_remove.append(key)

        for key in to_remove:
            del self._active_vms[key]

    def release_all(self) -> None:
        """Release all VMs."""
        try:
            self._client.destroy_all()
        except Exception:
            logger.debug("Error destroying all VMs", exc_info=True)
        self._active_vms.clear()
        self._client.close()
