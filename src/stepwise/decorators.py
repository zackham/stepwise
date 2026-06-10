"""Executor decorators: TimeoutDecorator, RetryDecorator, FallbackDecorator."""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Any

from stepwise.executors import (
    Executor,
    ExecutionContext,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import HandoffEnvelope, Sidecar, _now

logger = logging.getLogger("stepwise.decorators")


class TimeoutDecorator(Executor):
    """Enforces a wall-clock limit on the wrapped executor's start().

    The inner executor runs on a daemon worker thread. If it does not
    finish within the configured limit, the recorded subprocess (pid/pgid
    reported via context.state_update_fn) is killed, the inner executor's
    cancel() is invoked best-effort, and a failed ExecutorResult is
    returned (executor_state={"failed": True, ...,
    "error_category": "timeout"}).

    Limitation: Python threads cannot be force-killed, so if the inner
    executor is blocked on something that ignores the process kill (e.g.
    a wedged network call), the worker thread may linger until that call
    returns. The step run still fails immediately regardless.

    A limit of 0 (or negative) disables enforcement; the result is only
    annotated with timeout metadata.
    """

    def __init__(self, executor: Executor, config: dict) -> None:
        self._executor = executor
        self._limit_minutes = config.get("minutes", 30)

    def _timeout_meta(self, triggered: bool, elapsed_minutes: float) -> dict:
        return {
            "timeout": {
                "limit_minutes": self._limit_minutes,
                "triggered": triggered,
                "elapsed_minutes": round(elapsed_minutes, 3),
            }
        }

    def _annotate(self, result: ExecutorResult, elapsed_minutes: float) -> ExecutorResult:
        """Attach timeout metadata to a completed (non-timed-out) result."""
        timeout_meta = self._timeout_meta(
            triggered=elapsed_minutes >= self._limit_minutes,
            elapsed_minutes=elapsed_minutes,
        )
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

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        # Set timeout on context
        context.timeout_minutes = self._limit_minutes
        limit_seconds = float(self._limit_minutes) * 60.0

        # Capture the latest executor state (pid/pgid/...) so we can kill
        # the underlying process and pass state to cancel() on timeout.
        captured_state: dict = {}
        original_update = context.state_update_fn

        def _capturing_update(state: dict) -> None:
            if state:
                captured_state.update(state)
            if original_update is not None:
                original_update(state)

        context.state_update_fn = _capturing_update

        start_time = time.monotonic()

        if limit_seconds <= 0:
            # Enforcement disabled — run inline, annotate only.
            result = self._executor.start(inputs, context)
            elapsed_minutes = (time.monotonic() - start_time) / 60.0
            return self._annotate(result, elapsed_minutes)

        result_box: list[ExecutorResult] = []
        error_box: list[BaseException] = []

        def _run_inner() -> None:
            try:
                result_box.append(self._executor.start(inputs, context))
            except BaseException as e:  # noqa: BLE001 — re-raised on the caller thread
                error_box.append(e)

        worker = threading.Thread(
            target=_run_inner,
            name=f"stepwise-timeout-{context.step_name}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=limit_seconds)
        elapsed_minutes = (time.monotonic() - start_time) / 60.0

        if worker.is_alive():
            # Timed out — kill the recorded process tree and cancel
            # best-effort. The worker thread cannot be force-killed and
            # may linger until the inner start() returns; its eventual
            # result is discarded.
            logger.warning(
                "Step '%s' timed out after %.1f minutes (limit %s) — killing executor",
                context.step_name, elapsed_minutes, self._limit_minutes,
            )
            pid = captured_state.get("pid")
            pgid = captured_state.get("pgid")
            if pid or pgid:
                try:
                    from stepwise.process_lifecycle import kill_run_process

                    kill_run_process(
                        pid=pid,
                        pgid=pgid,
                        grace_seconds=2,
                        step_name=context.step_name,
                    )
                except Exception:
                    logger.warning(
                        "Failed to kill timed-out process for step '%s'",
                        context.step_name, exc_info=True,
                    )
            try:
                self._executor.cancel(captured_state)
            except Exception:
                logger.warning(
                    "cancel() failed for timed-out step '%s'",
                    context.step_name, exc_info=True,
                )

            error = f"Step timed out after {self._limit_minutes} minute(s)"
            timeout_meta = self._timeout_meta(triggered=True, elapsed_minutes=elapsed_minutes)
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path or "",
                    timestamp=_now(),
                    executor_meta={"failed": True, "error": error, **timeout_meta},
                ),
                executor_state={
                    "failed": True,
                    "error": error,
                    "error_category": "timeout",
                },
            )

        if error_box:
            raise error_box[0]
        return self._annotate(result_box[0], elapsed_minutes)

    def check_status(self, state: dict) -> ExecutorStatus:
        return self._executor.check_status(state)

    def cancel(self, state: dict) -> None:
        self._executor.cancel(state)


TRANSIENT_ERROR_CATEGORIES = {"infra_failure", "timeout"}


class RetryDecorator(Executor):
    """Retries on failure. Checks context.idempotency before retrying.

    Config keys:
        max_retries: int (default 5) — number of retry attempts after initial failure
        backoff: "none" | "exponential" — backoff strategy
        backoff_base: float (default 0.01) — base delay in seconds for exponential backoff
        transient_only: bool (default False) — when True, only retry if
            executor_state.error_category is in the transient set (infra_failure, timeout)
    """

    def __init__(self, executor: Executor, config: dict) -> None:
        self._executor = executor
        self._max_retries = config.get("max_retries", 5)
        self._backoff = config.get("backoff", "none")
        self._backoff_base = config.get("backoff_base", 0.01)
        self._transient_only = config.get("transient_only", False)

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

            # Transient-only filtering: if enabled, only retry transient errors
            if self._transient_only and result.executor_state:
                category = result.executor_state.get("error_category", "")
                if category not in TRANSIENT_ERROR_CATEGORIES:
                    # Non-transient error — fail immediately, no retry
                    logger.info(
                        "Non-transient error for step '%s' (category=%s), not retrying: %s",
                        context.step_name, category, error_msg,
                    )
                    retry_meta = {
                        "retry": {
                            "attempts": attempt_num + 1,
                            "reasons": [error_msg],
                        }
                    }
                    if result.envelope:
                        result.envelope.executor_meta.update(retry_meta)
                    return result

            attempts.append(error_msg)
            last_result = result

            # Backoff before next retry (not after final attempt)
            if attempt_num < self._max_retries and self._backoff == "exponential":
                delay = self._backoff_base * (2 ** attempt_num)
                logger.info(
                    "Transient retry %d/%d for step '%s' after %.1fs delay (error: %s)",
                    attempt_num + 1, self._max_retries, context.step_name, delay, error_msg,
                )
                time.sleep(delay)

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
