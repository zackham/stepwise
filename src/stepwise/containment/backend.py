"""Containment backend protocols and base types.

Defines the interfaces that containment backends must implement.
The key abstraction is SpawnContext: instead of subprocess.Popen(),
ACPBackend calls spawn_context.spawn(command, env, cwd) and gets
back a ProcessHandle with stdin/stdout for JSON-RPC transport.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import IO, Any, Protocol, runtime_checkable

logger = logging.getLogger("stepwise.containment")


@dataclass
class ContainmentConfig:
    """Configuration for a containment environment.

    Fields used for VM grouping (vm_is_eq): tools, allowed_paths,
    credentials, network. Steps with the same ContainmentConfig
    share a VM.
    """

    mode: str = "none"  # "none" | "cloud-hypervisor"
    tools: list[str] | None = None
    allowed_paths: list[str] | None = None
    credentials: list[str] | None = None
    network: list[str] | None = None
    memory_mb: int = 512
    cpus: int = 2
    working_dir: str = "."


@runtime_checkable
class ProcessHandle(Protocol):
    """Process-like handle returned by SpawnContext.spawn().

    Duck-type compatible with subprocess.Popen for the fields
    that JsonRpcTransport needs (stdin, stdout, stderr, pid, poll,
    terminate, kill, wait).
    """

    stdin: IO[str] | None
    stdout: IO[str] | None
    stderr: IO[str] | None
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


@runtime_checkable
class SpawnContext(Protocol):
    """Abstract context for spawning processes.

    ACPBackend calls spawn() instead of subprocess.Popen().
    For no containment, this IS subprocess.Popen. For cloud-hypervisor,
    this runs the command inside a VM and returns a vsock-backed handle.
    """

    def spawn(
        self,
        command: list[str],
        env: dict[str, str],
        cwd: str,
        text: bool = True,
    ) -> ProcessHandle:
        """Spawn a process and return a handle with stdin/stdout.

        The returned handle must have:
        - stdin: writable stream for JSON-RPC requests
        - stdout: readable stream for JSON-RPC responses
        - stderr: readable stream (may be empty/merged with stdout)
        - pid: process ID (or VM guest PID for containment)
        - poll/terminate/kill/wait: lifecycle control
        """
        ...


class LocalSpawnContext:
    """No containment: spawn processes directly on host."""

    def spawn(
        self,
        command: list[str],
        env: dict[str, str],
        cwd: str,
        text: bool = True,
    ) -> subprocess.Popen:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            bufsize=1,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )


@runtime_checkable
class ContainmentBackend(Protocol):
    """Backend that provides SpawnContexts for agent execution.

    Manages the lifecycle of containment environments (VMs, containers).
    Uses ResourceLifecycleManager internally for reuse and cleanup.
    """

    def get_spawn_context(
        self, config: ContainmentConfig,
    ) -> SpawnContext:
        """Return a SpawnContext for this config.

        May boot a new VM or reuse an existing one based on config equality.
        """
        ...

    def release_if_unused(
        self, remaining_steps_checker: Any,
    ) -> None:
        """Release environments no longer needed."""
        ...

    def release_all(self) -> None:
        """Release all environments (job completion)."""
        ...


class NoContainmentBackend:
    """Passthrough backend: no isolation, direct host execution."""

    _local_context = LocalSpawnContext()

    def get_spawn_context(
        self, config: ContainmentConfig,
    ) -> SpawnContext:
        return self._local_context

    def release_if_unused(self, remaining_steps_checker: Any) -> None:
        pass  # nothing to release

    def release_all(self) -> None:
        pass  # nothing to release
