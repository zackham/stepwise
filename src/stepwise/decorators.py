"""Executor decorators: TimeoutDecorator, RetryDecorator, FallbackDecorator."""

from __future__ import annotations

import signal
import time
from typing import Any

from stepwise.executors import (
    Executor,
    ExecutionContext,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import HandoffEnvelope, Sidecar, _now


class TimeoutDecorator(Executor):
    """Cancels after N minutes."""

    def __init__(self, executor: Executor, config: dict) -> None:
        self._executor = executor
        self._limit_minutes = config.get("minutes", 30)

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Set timeout on context
        context.timeout_minutes = self._limit_minutes

        start_time = time.monotonic()
        result = self._executor.start(inputs, context)
        elapsed = time.monotonic() - start_time
        elapsed_minutes = elapsed / 60.0

        # Build timeout metadata
        timeout_meta = {
            "timeout": {
                "limit_minutes": self._limit_minutes,
                "triggered": elapsed_minutes >= self._limit_minutes,
                "elapsed_minutes": round(elapsed_minutes, 3),
            }
        }

        if result.envelope:
            result.envelope.executor_meta.update(timeout_meta)
        elif result.type == "data" and not result.envelope:
            # If somehow no envelope, create one
            result.envelope = HandoffEnvelope(
                artifact={},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
                executor_meta=timeout_meta,
            )

        return result

    def check_status(self, state: dict) -> ExecutorStatus:
        return self._executor.check_status(state)

    def cancel(self, state: dict) -> None:
        self._executor.cancel(state)


class RetryDecorator(Executor):
    """Retries on failure. Checks context.idempotency before retrying."""

    def __init__(self, executor: Executor, config: dict) -> None:
        self._executor = executor
        self._max_retries = config.get("max_retries", 2)
        self._backoff = config.get("backoff", "none")

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        if context.idempotency == "non_retriable":
            # Don't retry non-retriable steps
            return self._executor.start(inputs, context)

        attempts: list[str] = []
        last_result: ExecutorResult | None = None

        for attempt_num in range(1 + self._max_retries):
            result = self._executor.start(inputs, context)

            # Check if it's a real failure (executor_state has failed flag or envelope has failed meta)
            is_failure = False
            if result.executor_state and result.executor_state.get("failed"):
                is_failure = True
            elif result.envelope and result.envelope.executor_meta.get("failed"):
                is_failure = True

            if not is_failure:
                # Success — add retry metadata
                retry_meta = {
                    "retry": {
                        "attempts": attempt_num + 1,
                        "reasons": attempts,
                    }
                }
                if result.envelope:
                    result.envelope.executor_meta.update(retry_meta)
                return result

            error_msg = ""
            if result.executor_state:
                error_msg = result.executor_state.get("error", "unknown")
            attempts.append(error_msg)
            last_result = result

            # Backoff
            if attempt_num < self._max_retries and self._backoff == "exponential":
                time.sleep(0.01 * (2 ** attempt_num))  # Very short for testing

        # All retries exhausted
        retry_meta = {
            "retry": {
                "attempts": 1 + self._max_retries,
                "reasons": attempts,
            }
        }
        if last_result and last_result.envelope:
            last_result.envelope.executor_meta.update(retry_meta)
        return last_result  # type: ignore[return-value]

    def check_status(self, state: dict) -> ExecutorStatus:
        return self._executor.check_status(state)

    def cancel(self, state: dict) -> None:
        self._executor.cancel(state)


class FallbackDecorator(Executor):
    """Tries primary executor, falls back to secondary on failure."""

    def __init__(self, primary: Executor, fallback: Executor, config: dict) -> None:
        self._primary = primary
        self._fallback = fallback
        self._config = config

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        result = self._primary.start(inputs, context)

        is_failure = False
        if result.executor_state and result.executor_state.get("failed"):
            is_failure = True
        elif result.envelope and result.envelope.executor_meta.get("failed"):
            is_failure = True

        if not is_failure:
            return result

        primary_error = ""
        if result.executor_state:
            primary_error = result.executor_state.get("error", "unknown")

        # Try fallback
        fallback_result = self._fallback.start(inputs, context)
        fallback_meta = {
            "fallback": {
                "primary_failed": True,
                "reason": primary_error,
            }
        }
        if fallback_result.envelope:
            fallback_result.envelope.executor_meta.update(fallback_meta)
        return fallback_result

    def check_status(self, state: dict) -> ExecutorStatus:
        return self._primary.check_status(state)

    def cancel(self, state: dict) -> None:
        self._primary.cancel(state)
