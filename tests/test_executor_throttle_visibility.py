"""Visibility for per-executor-type dispatch throttling.

Regression: the `_executor_at_capacity` gate would silently hold back ready
steps, which manifested as a P2 "silent 35-minute wait" bug. A
`fast-implement` job occupied the only agent slot and a research job's
`codebase-explore` step sat throttled with no feedback in logs or UI.

This file pins the visibility contract:
- A `step.throttled` event is emitted on the first throttle of a
  (job, step).
- Throttling is logged at INFO, not DEBUG.

Coverage of the actual *gating* lives in `test_executor_concurrency.py` —
this file is about observability only.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from stepwise.config import StepwiseConfig
from stepwise.engine import AsyncEngine
from stepwise.events import STEP_THROTTLED
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
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import run_job


class _SlowExecutor(Executor):
    """Executor that sleeps to force concurrent steps to queue."""

    def __init__(self, sleep_seconds: float = 0.3):
        self._sleep_seconds = sleep_seconds

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        time.sleep(self._sleep_seconds)
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
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


def _make_throttled_engine(limit: int = 1, sleep: float = 0.3) -> AsyncEngine:
    store = SQLiteStore(":memory:")
    registry = ExecutorRegistry()
    registry.register("callable", lambda cfg, s=sleep: _SlowExecutor(s))
    config = StepwiseConfig(max_concurrent_by_executor={"callable": limit})
    engine = AsyncEngine(store=store, registry=registry, config=config)
    engine._agent_stagger_seconds = 0.0
    return engine


def _parallel_steps(count: int) -> WorkflowDefinition:
    steps = {
        f"step-{i}": StepDefinition(
            name=f"step-{i}",
            executor=ExecutorRef(type="callable", config={}),
            outputs=["result"],
        )
        for i in range(count)
    }
    return WorkflowDefinition(steps=steps)


class TestThrottleVisibility:
    @pytest.mark.asyncio
    async def test_step_throttled_event_emitted_on_first_throttle(self):
        """Dispatching 3 steps against a limit of 1 emits STEP_THROTTLED."""
        engine = _make_throttled_engine(limit=1, sleep=0.25)
        wf = _parallel_steps(3)
        job = engine.create_job(objective="test", workflow=wf)

        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED

        events = engine.store.load_events(job.id)
        throttled = [e for e in events if e.type == STEP_THROTTLED]
        # At least one step was throttled on the first dispatch pass (the
        # dispatch loop walks all ready steps; with limit=1, 2 of the 3
        # steps are throttled on that first pass).
        assert len(throttled) >= 1, (
            f"expected at least 1 step.throttled event, got {len(throttled)}: "
            f"{[e.type for e in events]}"
        )
        # Payload shape: step name, executor_type, running count, limit.
        first = throttled[0]
        assert "step" in first.data
        assert first.data.get("executor_type") == "callable"
        assert first.data.get("limit") == 1
        assert first.data.get("running") >= 1

    @pytest.mark.asyncio
    async def test_throttle_logged_at_info_level(self, caplog):
        """Throttle messages should hit INFO logs (operators need to see them)."""
        engine = _make_throttled_engine(limit=1, sleep=0.2)
        wf = _parallel_steps(2)
        job = engine.create_job(objective="test", workflow=wf)

        with caplog.at_level(logging.INFO, logger="stepwise.async_engine"):
            result = await run_job(engine, job.id, timeout=15)

        assert result.status == JobStatus.COMPLETED
        throttle_logs = [
            r for r in caplog.records
            if r.levelno == logging.INFO and "throttled" in r.getMessage()
        ]
        assert throttle_logs, (
            "expected at least one INFO-level throttle log message, got none"
        )

    @pytest.mark.asyncio
    async def test_no_duplicate_throttle_event_while_still_throttled(self):
        """Re-dispatches of the same throttled job must not fan out events.

        Each poll cycle re-walks `ready` and would otherwise emit a fresh
        STEP_THROTTLED on every cycle. We dedupe by checking
        `_throttled_jobs` membership before emitting."""
        engine = _make_throttled_engine(limit=1, sleep=0.5)
        wf = _parallel_steps(3)
        job = engine.create_job(objective="test", workflow=wf)

        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED

        events = engine.store.load_events(job.id)
        throttled = [e for e in events if e.type == STEP_THROTTLED]
        # Upper bound: at most one throttled event per *transition* into
        # the throttled state. With 3 steps and limit=1, the job enters
        # the throttled state at most twice (step-1 dispatch blocked,
        # step-2 dispatch blocked) as earlier steps complete. Without
        # dedupe we'd see N events per poll cycle, easily 10+.
        assert len(throttled) <= 3, (
            f"expected ≤3 throttled events (one per state transition), "
            f"got {len(throttled)}"
        )
