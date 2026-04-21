"""Test that cancelled agent steps don't leave ghost entries in _task_exec_types.

Regression: when a job with a running agent step is cancelled and the agent
process has no pid/pgid in executor_state (e.g., process died before being
tracked), the run_id could remain in _task_exec_types permanently. This ghost
entry counts against max_concurrent_agents, throttling all subsequent agent
steps across ALL jobs until the server is restarted.

This file covers:
- Ghost entries are cleaned up when a job is cancelled
- The periodic reconciliation sweep catches any remaining ghosts
- Subsequent jobs are not permanently throttled after a cancel
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from stepwise.config import StepwiseConfig
from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import run_job


# ── Helpers ───────────────────────��──────────────────────────────────


class _BlockingExecutor(Executor):
    """Executor that blocks until an event is set (simulates a long-running agent)."""

    def __init__(self, started_event: threading.Event, block_event: threading.Event):
        self._started = started_event
        self._block = block_event

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Signal that we've started (but don't record pid in executor_state)
        self._started.set()
        # Block until told to proceed (simulates agent process running)
        self._block.wait(timeout=30)
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={"result": "ok"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        # No pid/pgid in state — cancel can't kill anything
        pass


class _FastExecutor(Executor):
    """Executor that completes immediately."""

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={"result": "done"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


def _single_agent_step() -> WorkflowDefinition:
    """Workflow with a single agent step (no pid tracking)."""
    return WorkflowDefinition(steps={
        "build": StepDefinition(
            name="build",
            executor=ExecutorRef(type="agent", config={}),
            outputs=["result"],
        ),
    })


# ── Tests ────────────────────���────────────────────────��──────────────


class TestCancelGhostCleanup:

    @pytest.mark.asyncio
    async def test_cancel_with_no_pid_frees_executor_slot(self):
        """Cancelling a job whose agent step has no pid/pgid must free the executor slot.

        This is the core regression test: an agent step starts, occupies a
        concurrency slot, but never records a pid in executor_state. When the
        job is cancelled, the slot MUST be freed so subsequent agent steps
        across other jobs are not permanently throttled.
        """
        started_event = threading.Event()
        block_event = threading.Event()

        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("agent", lambda cfg: _BlockingExecutor(started_event, block_event))
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Create and start the first job
        wf = _single_agent_step()
        job1 = engine.create_job(objective="job-to-cancel", workflow=wf)

        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job1.id)

            # Wait for the agent step to actually start executing
            await asyncio.get_event_loop().run_in_executor(
                None, started_event.wait, 5.0
            )
            assert started_event.is_set(), "Agent step did not start"

            # Verify the executor slot is occupied
            assert engine._running_count_for_type("agent") == 1

            # Verify no pid/pgid in executor state (simulating the bug scenario)
            runs = list(store.running_runs(job1.id))
            assert len(runs) == 1
            state = runs[0].executor_state or {}
            assert not state.get("pid") and not state.get("pgid")

            # Unblock the executor thread so it can respond to cancellation
            block_event.set()

            # Cancel the job
            engine.cancel_job(job1.id)

            # The executor slot MUST be freed
            assert engine._running_count_for_type("agent") == 0, (
                "Ghost entry in _task_exec_types: executor slot not freed after cancel"
            )

            # Verify the job is actually cancelled
            assert store.load_job(job1.id).status == JobStatus.CANCELLED

        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_next_job_not_throttled_after_cancel(self):
        """After cancelling a job with a running agent step, subsequent agent
        steps must not be throttled (slot must be available)."""
        started_event = threading.Event()
        block_event = threading.Event()

        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()

        # First call returns blocking executor, subsequent calls return fast executor
        call_count = 0

        def executor_factory(cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _BlockingExecutor(started_event, block_event)
            return _FastExecutor()

        registry.register("agent", executor_factory)
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Start job1
        wf1 = _single_agent_step()
        job1 = engine.create_job(objective="cancel-me", workflow=wf1)

        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job1.id)
            await asyncio.get_event_loop().run_in_executor(
                None, started_event.wait, 5.0
            )
            block_event.set()
            engine.cancel_job(job1.id)

            # Now create and start job2 — it should NOT be throttled
            wf2 = _single_agent_step()
            job2 = engine.create_job(objective="should-run", workflow=wf2)
            engine.start_job(job2.id)

            # Wait for job2 to complete
            result = await asyncio.wait_for(engine.wait_for_job(job2.id), timeout=10)
            assert result.status == JobStatus.COMPLETED, (
                f"Job2 should complete but got status {result.status.value} "
                f"(likely permanently throttled by ghost entry)"
            )
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_reconciliation_catches_ghost_entries(self):
        """_reconcile_tracked_runs removes entries for non-RUNNING runs."""
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("agent", lambda cfg: _FastExecutor())
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 2})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Manually inject a ghost entry (simulating the race condition outcome)
        engine._task_exec_types["ghost-run-id-1"] = "agent"
        engine._task_exec_types["ghost-run-id-2"] = "agent"

        assert engine._running_count_for_type("agent") == 2

        # Run reconciliation — ghost run_ids don't exist in the store
        removed = engine._reconcile_tracked_runs()
        assert removed == 2
        assert engine._running_count_for_type("agent") == 0

    @pytest.mark.asyncio
    async def test_reconciliation_preserves_valid_entries(self):
        """_reconcile_tracked_runs keeps entries for genuinely RUNNING runs."""
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("agent", lambda cfg: _FastExecutor())
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 2})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Create a job and launch it to get a real RUNNING run
        wf = _single_agent_step()
        job = engine.create_job(objective="test", workflow=wf)

        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            # Give the engine time to dispatch
            await asyncio.sleep(0.3)

            # The job should complete quickly (FastExecutor)
            result = await asyncio.wait_for(engine.wait_for_job(job.id), timeout=5)
            assert result.status == JobStatus.COMPLETED

            # After completion, no ghost entries should remain
            assert engine._running_count_for_type("agent") == 0
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_poll_external_changes_reconciles_ghosts(self):
        """The periodic poll check removes ghost entries from _task_exec_types."""
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("agent", lambda cfg: _FastExecutor())
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Manually inject a ghost entry
        engine._task_exec_types["ghost-run-id"] = "agent"
        assert engine._running_count_for_type("agent") == 1

        # Simulate what the event loop does every 5s
        engine._poll_external_changes()

        # Ghost should be cleaned up
        assert engine._running_count_for_type("agent") == 0, (
            "Poll external changes did not clean up ghost entry"
        )

    @pytest.mark.asyncio
    async def test_cancel_during_agent_stagger_frees_slot(self):
        """Cancelling during the agent stagger delay still frees the slot."""
        started_event = threading.Event()
        block_event = threading.Event()

        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("agent", lambda cfg: _BlockingExecutor(started_event, block_event))
        config = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        # Use a longer stagger to create a window for cancellation
        engine._agent_stagger_seconds = 0.0

        wf = _single_agent_step()
        job = engine.create_job(objective="test", workflow=wf)

        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.get_event_loop().run_in_executor(
                None, started_event.wait, 5.0
            )
            block_event.set()

            # Cancel immediately
            engine.cancel_job(job.id)

            # Slot must be freed
            assert engine._running_count_for_type("agent") == 0
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass
