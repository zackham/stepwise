"""Generic resource lifecycle manager.

Reactive lifecycle: lazy allocation, backward-looking reuse,
deterministic cleanup. Used by ACPBackend for process management
and potentially for VM lifecycle in the future.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger("stepwise.lifecycle")

R = TypeVar("R")


@dataclass
class ManagedResource(Generic[R]):
    """A resource managed by the lifecycle manager."""

    config: Any
    resource: R
    session_names: set[str] = field(default_factory=set)


class ResourceLifecycleManager(Generic[R]):
    """Manages long-lived resources across step boundaries.

    Reactive: allocate on first need, reuse when config matches,
    cleanup when definitively no longer needed.

    Two consumers:
    - ACP process lifecycle: is_eq on agent + model + tools + allowed_paths
    - VM lifecycle (future): is_eq on tools + fs + credentials + network
    """

    def __init__(
        self,
        is_eq: Callable[[Any, Any], bool],
        factory: Callable[[Any], R],
        teardown: Callable[[R], None],
        is_alive: Callable[[R], bool] | None = None,
    ):
        self.is_eq = is_eq
        self.factory = factory
        self.teardown = teardown
        self.is_alive = is_alive
        self.active: list[ManagedResource[R]] = []

    def acquire(
        self, config: Any, session_name: str | None = None,
    ) -> ManagedResource[R]:
        """Get or create a resource for this config.

        If a compatible resource exists (is_eq returns True), reuse it
        (after verifying it's still alive). Otherwise create a new one.
        """
        for managed in self.active:
            if self.is_eq(managed.config, config):
                # Verify the resource is still alive before reusing
                if self.is_alive and not self.is_alive(managed.resource):
                    logger.warning("Resource is dead, removing and creating new one")
                    try:
                        self.teardown(managed.resource)
                    except Exception:
                        pass
                    self.active.remove(managed)
                    break  # Fall through to factory()
                if session_name:
                    managed.session_names.add(session_name)
                return managed

        resource = self.factory(config)
        managed = ManagedResource(
            config=config,
            resource=resource,
            session_names={session_name} if session_name else set(),
        )
        self.active.append(managed)
        return managed

    def release_if_unused(
        self, remaining_steps_checker: Callable[[Any], bool],
    ) -> None:
        """Release resources that are definitely no longer needed.

        remaining_steps_checker(config) returns True if there are still
        unexecuted steps that could use a resource with this config.
        """
        still_active = []
        for managed in self.active:
            if not remaining_steps_checker(managed.config):
                try:
                    self.teardown(managed.resource)
                except Exception:
                    logger.debug(
                        "Teardown error during release_if_unused",
                        exc_info=True,
                    )
            else:
                still_active.append(managed)
        self.active = still_active

    def release_all(self) -> None:
        """Release all resources (job completion or cleanup)."""
        for managed in self.active:
            try:
                self.teardown(managed.resource)
            except Exception:
                logger.debug("Teardown error during release_all", exc_info=True)
        self.active.clear()

    def find(self, config: Any) -> ManagedResource[R] | None:
        """Find existing resource matching config, without creating."""
        for managed in self.active:
            if self.is_eq(managed.config, config):
                return managed
        return None
