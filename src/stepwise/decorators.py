"""Executor decorators: TimeoutDecorator, RetryDecorator, NotificationDecorator, FallbackDecorator."""

from __future__ import annotations

import asyncio
from typing import Any

from stepwise.events import Event, EventBus, EventType
from stepwise.executors import Executor, ExecutorResult
from stepwise.models import StepRun


class TimeoutDecorator(Executor):
    """Wraps an executor with a timeout."""

    def __init__(self, executor: Executor, timeout_seconds: float) -> None:
        self._executor = executor
        self._timeout = timeout_seconds

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        try:
            return await asyncio.wait_for(
                self._executor.execute(step_run, config),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return ExecutorResult(
                error=f"Step timed out after {self._timeout}s"
            )


class RetryDecorator(Executor):
    """Wraps an executor with retry logic."""

    def __init__(
        self,
        executor: Executor,
        max_retries: int,
        delay_seconds: float = 0,
    ) -> None:
        self._executor = executor
        self._max_retries = max_retries
        self._delay = delay_seconds

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        last_result: ExecutorResult | None = None
        total_attempts = 1 + self._max_retries

        for attempt in range(1, total_attempts + 1):
            step_run.attempt = attempt
            result = await self._executor.execute(step_run, config)
            if result.success:
                return result
            last_result = result
            if attempt < total_attempts and self._delay > 0:
                await asyncio.sleep(self._delay)

        return last_result  # type: ignore[return-value]


class NotificationDecorator(Executor):
    """Emits events on step start, completion, and failure."""

    def __init__(self, executor: Executor, event_bus: EventBus) -> None:
        self._executor = executor
        self._event_bus = event_bus

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        await self._event_bus.emit(
            Event.create(
                step_run.job_id,
                EventType.STEP_STARTED,
                step_run.step_name,
            )
        )

        result = await self._executor.execute(step_run, config)

        if result.success:
            await self._event_bus.emit(
                Event.create(
                    step_run.job_id,
                    EventType.STEP_COMPLETED,
                    step_run.step_name,
                    data={"outputs": result.outputs},
                )
            )
        else:
            await self._event_bus.emit(
                Event.create(
                    step_run.job_id,
                    EventType.STEP_FAILED,
                    step_run.step_name,
                    data={"error": result.error},
                )
            )

        return result


class FallbackDecorator(Executor):
    """Tries a primary executor, falls back to a secondary on failure."""

    def __init__(self, primary: Executor, fallback: Executor) -> None:
        self._primary = primary
        self._fallback = fallback

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        result = await self._primary.execute(step_run, config)
        if result.success:
            return result
        return await self._fallback.execute(step_run, config)
