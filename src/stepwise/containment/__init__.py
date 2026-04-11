"""Stepwise containment: hardware-isolated agent execution.

Containment wraps agent subprocess spawning in hardware-isolated
environments (cloud-hypervisor microVMs). The isolation boundary
is per agent session config — steps with the same tools, paths,
and credentials share a VM; different configs get different VMs.

Non-agent steps (script, llm, polling, external) always run on host.

Containment is opt-in: default is no isolation (equivalent to
running claude with accept-edits). Enable via:
  - Agent settings: agents.claude.containment: cloud-hypervisor
  - Flow-level: containment: cloud-hypervisor
  - Step-level: containment: cloud-hypervisor
  - CLI: stepwise run --containment cloud-hypervisor
"""

from stepwise.containment.backend import (
    ContainmentBackend,
    ContainmentConfig,
    NoContainmentBackend,
    ProcessHandle,
    SpawnContext,
)

__all__ = [
    "ContainmentBackend",
    "ContainmentConfig",
    "NoContainmentBackend",
    "ProcessHandle",
    "SpawnContext",
]
