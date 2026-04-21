"""Tests for engine-driven ResourceManager lifecycle.

Verifies that:
- `registry.resource_managers` exposes registered managers.
- Job terminal status triggers `release_for_job` on every registered manager.
- Engine shutdown triggers `release_all` on every registered manager.
- Resource managers raising during release don't break the engine.
"""

from __future__ import annotations

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore

from tests.conftest import CallableExecutor, register_step_fn, run_job


# ── Fake ResourceManager ─────────────────────────────────────────────


class FakeResourceManager:
    """Records release_for_job / release_all calls for assertion."""

    def __init__(self, *, raise_on_release: bool = False):
        self.released_jobs: list[str] = []
        self.release_all_calls: int = 0
        self._raise = raise_on_release

    def release_for_job(self, job_id: str) -> None:
        self.released_jobs.append(job_id)
        if self._raise:
            raise RuntimeError("boom")

    def release_all(self) -> None:
        self.release_all_calls += 1
        if self._raise:
            raise RuntimeError("boom")


def _make_engine_with_manager(mgr: FakeResourceManager) -> AsyncEngine:
    reg = ExecutorRegistry()
    reg.register(
        "callable",
        lambda cfg: CallableExecutor(fn_name=cfg.get("fn_name", "default")),
    )
    reg.register_resource_manager(mgr)
    store = SQLiteStore(":memory:")
    return AsyncEngine(store=store, registry=reg)


def _noop_wf() -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "s1": StepDefinition(
            name="s1", outputs=[],
            executor=ExecutorRef("callable", {"fn_name": "noop"}),
        ),
    })


# ── ExecutorRegistry exposure ────────────────────────────────────────


class TestRegistryExposure:
    def test_register_resource_manager(self):
        reg = ExecutorRegistry()
        mgr = FakeResourceManager()
        reg.register_resource_manager(mgr)
        assert mgr in reg.resource_managers

    def test_register_is_idempotent(self):
        reg = ExecutorRegistry()
        mgr = FakeResourceManager()
        reg.register_resource_manager(mgr)
        reg.register_resource_manager(mgr)
        assert len(reg.resource_managers) == 1

    def test_resource_managers_is_readonly_view(self):
        """Mutating the returned list doesn't affect the registry."""
        reg = ExecutorRegistry()
        mgr = FakeResourceManager()
        reg.register_resource_manager(mgr)
        snapshot = reg.resource_managers
        snapshot.clear()
        assert mgr in reg.resource_managers


# ── Engine-driven release_for_job on job terminal status ─────────────


class TestJobTerminalRelease:
    @pytest.mark.asyncio
    async def test_completed_job_triggers_release(self):
        register_step_fn("noop", lambda inputs: {})
        mgr = FakeResourceManager()
        engine = _make_engine_with_manager(mgr)
        job = engine.create_job("t1", _noop_wf())

        result = await run_job(engine, job.id)

        assert result.status == JobStatus.COMPLETED
        assert mgr.released_jobs == [job.id]

    @pytest.mark.asyncio
    async def test_failing_manager_does_not_break_engine(self):
        """If release_for_job raises, the job still completes cleanly."""
        register_step_fn("noop", lambda inputs: {})
        mgr = FakeResourceManager(raise_on_release=True)
        engine = _make_engine_with_manager(mgr)
        job = engine.create_job("t1", _noop_wf())

        result = await run_job(engine, job.id)

        assert result.status == JobStatus.COMPLETED
        assert mgr.released_jobs == [job.id]


# ── Engine-driven release_all on shutdown ────────────────────────────


class TestShutdownRelease:
    @pytest.mark.asyncio
    async def test_shutdown_calls_release_all_on_each_manager(self):
        mgr_a = FakeResourceManager()
        mgr_b = FakeResourceManager()

        reg = ExecutorRegistry()
        reg.register_resource_manager(mgr_a)
        reg.register_resource_manager(mgr_b)
        store = SQLiteStore(":memory:")
        engine = AsyncEngine(store=store, registry=reg)

        await engine.shutdown()

        assert mgr_a.release_all_calls == 1
        assert mgr_b.release_all_calls == 1

    @pytest.mark.asyncio
    async def test_shutdown_resilient_to_manager_exceptions(self):
        mgr_bad = FakeResourceManager(raise_on_release=True)
        mgr_good = FakeResourceManager()

        reg = ExecutorRegistry()
        reg.register_resource_manager(mgr_bad)
        reg.register_resource_manager(mgr_good)
        store = SQLiteStore(":memory:")
        engine = AsyncEngine(store=store, registry=reg)

        # Should not raise even though mgr_bad raises.
        await engine.shutdown()

        assert mgr_bad.release_all_calls == 1
        assert mgr_good.release_all_calls == 1
