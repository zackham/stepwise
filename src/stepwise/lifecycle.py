"""Generic resource lifecycle manager.

Reactive lifecycle: lazy allocation, backward-looking reuse,
deterministic cleanup. Used by ACPBackend for process management
and potentially for VM lifecycle in the future.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger("stepwise.lifecycle")

R = TypeVar("R")


@runtime_checkable
class ResourceManager(Protocol):
    """Minimum contract for a scope-aware resource manager.

    Both ResourceLifecycleManager (ACP processes) and
    ContainmentBackend (microVMs) conform. The engine drives
    cleanup through this protocol so the same caller covers both
    domains.
    """

    def release_for_job(self, job_id: str) -> None:
        """Drop this job's reference; tear down resources with no refs left."""
        ...

    def release_all(self) -> None:
        """Tear down every managed resource (server shutdown)."""
        ...


@dataclass
class ManagedResource(Generic[R]):
    """A resource managed by the lifecycle manager.

    `job_refs` is the set of jobs that currently reference this resource.
    `job_scoped` records whether any job ever acquired this resource —
    once True, the resource is torn down when refs drain to empty. A
    resource acquired without a job_id stays unscoped and is only
    cleaned up via release_all().
    """

    config: Any
    resource: R
    session_names: set[str] = field(default_factory=set)
    job_refs: set[str] = field(default_factory=set)
    job_scoped: bool = False


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
        # Reentrant lock — factory/teardown may call back into the manager
        # in future, and acquire() is called from many executor threads.
        # Without this, two threads could each see an empty `active` list,
        # both call factory(), and orphan one of the resources.
        self._lock = threading.RLock()

    def acquire(
        self,
        config: Any,
        session_name: str | None = None,
        job_id: str | None = None,
    ) -> tuple[ManagedResource[R], bool]:
        """Get or create a resource for this config.

        Returns (managed, was_newly_created). If a compatible resource exists
        (is_eq returns True), reuse it (after verifying it's still alive).
        Otherwise create a new one.

        The `was_newly_created` flag lets callers clean up the resource if
        their post-acquire setup (e.g. ACP session/new) fails — without it
        we'd orphan the just-spawned subprocess.

        When `job_id` is supplied, it's added to the resource's job_refs
        set. `release_for_job` uses those refs to tear down resources only
        when every referencing job has reached a terminal state.
        """
        with self._lock:
            for managed in self.active:
                if self.is_eq(managed.config, config):
                    # Verify the resource is still alive before reusing
                    if self.is_alive and not self.is_alive(managed.resource):
                        logger.warning(
                            "Resource is dead, removing and creating new one",
                        )
                        try:
                            self.teardown(managed.resource)
                        except Exception:
                            pass
                        self.active.remove(managed)
                        break  # Fall through to factory()
                    if session_name:
                        managed.session_names.add(session_name)
                    if job_id:
                        managed.job_refs.add(job_id)
                        managed.job_scoped = True
                    return managed, False

            resource = self.factory(config)
            managed = ManagedResource(
                config=config,
                resource=resource,
                session_names={session_name} if session_name else set(),
                job_refs={job_id} if job_id else set(),
                job_scoped=bool(job_id),
            )
            self.active.append(managed)
            return managed, True

    def discard(self, managed: ManagedResource[R]) -> None:
        """Tear down a managed resource and remove it from active.

        Use when the caller's post-acquire setup failed and the resource
        is unusable (e.g. ACP subprocess spawned but session/new errored).
        Safe to call on a resource that's already been removed.
        """
        with self._lock:
            try:
                self.teardown(managed.resource)
            except Exception:
                logger.debug("Teardown error during discard", exc_info=True)
            try:
                self.active.remove(managed)
            except ValueError:
                pass

    def release_if_unused(
        self, remaining_steps_checker: Callable[[Any], bool],
    ) -> None:
        """Release resources that are definitely no longer needed.

        remaining_steps_checker(config) returns True if there are still
        unexecuted steps that could use a resource with this config.
        """
        with self._lock:
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
        """Release all resources (server shutdown)."""
        with self._lock:
            for managed in self.active:
                try:
                    self.teardown(managed.resource)
                except Exception:
                    logger.debug(
                        "Teardown error during release_all", exc_info=True,
                    )
            self.active.clear()

    def release_for_job(self, job_id: str) -> None:
        """Drop job_id from every resource's refs; tear down any with no refs left.

        A resource acquired by multiple jobs survives until every referencing
        job has called release_for_job. Resources acquired without a job_id
        (job_scoped=False) are left alone — release_all owns those.
        """
        with self._lock:
            still_active = []
            for managed in self.active:
                managed.job_refs.discard(job_id)
                if managed.job_scoped and not managed.job_refs:
                    try:
                        self.teardown(managed.resource)
                    except Exception:
                        logger.debug(
                            "Teardown error during release_for_job",
                            exc_info=True,
                        )
                else:
                    still_active.append(managed)
            self.active = still_active

    def find(self, config: Any) -> ManagedResource[R] | None:
        """Find existing resource matching config, without creating."""
        with self._lock:
            for managed in self.active:
                if self.is_eq(managed.config, config):
                    return managed
            return None
