"""Tests for ScriptExecutor, HumanExecutor, MockLLMExecutor, and decorators.

Covers required test cases 15, 16, 24.
"""

import json
import os
import tempfile

import pytest

from stepwise.decorators import (
    FallbackDecorator,
    RetryDecorator,
    TimeoutDecorator,
)
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
)
from stepwise.models import (
    HandoffEnvelope,
    Sidecar,
    WatchSpec,
    _now,
)


def _ctx(step_name="test", attempt=1, workspace=None):
    return ExecutionContext(
        job_id="job-test",
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or tempfile.mkdtemp(),
        idempotency="idempotent",
    )


# ── ScriptExecutor ────────────────────────────────────────────────────


class TestScriptExecutor:
    def test_success_json_output(self):
        ctx = _ctx()
        executor = ScriptExecutor(command='echo \'{"result": "hello"}\'')
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact["result"] == "hello"

    def test_success_non_json_output(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="echo 'plain text'")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact["stdout"] == "plain text"

    def test_failure_exit_code(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="exit 1")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.executor_state["failed"] is True

    def test_inputs_written_to_file(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="cat $JOB_ENGINE_INPUTS")
        inputs = {"key": "value", "num": 42}
        result = executor.start(inputs, ctx)
        assert result.type == "data"
        # The command reads the input file (JSON) and prints it to stdout.
        # ScriptExecutor parses valid JSON stdout directly into artifact.
        assert result.envelope.artifact["key"] == "value"
        assert result.envelope.artifact["num"] == 42

    def test_empty_stdout(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="true")  # exit 0, no output
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact.get("stdout", "") == ""

    # ── Test 24: Poll watch protocol ──────────────────────────────────

    def test_watch_signaling(self):
        """If output JSON contains _watch key, step suspends."""
        ctx = _ctx()
        watch_output = json.dumps({
            "_watch": {
                "mode": "poll",
                "config": {"check_command": "echo done", "interval_seconds": 5},
                "fulfillment_outputs": ["status"],
            }
        })
        executor = ScriptExecutor(command=f"echo '{watch_output}'")
        result = executor.start({}, ctx)
        assert result.type == "watch"
        assert result.watch.mode == "poll"
        assert result.watch.config["interval_seconds"] == 5


# ── HumanExecutor ─────────────────────────────────────────────────────


class TestHumanExecutor:
    def test_immediately_returns_watch(self):
        executor = HumanExecutor(prompt="Approve?")
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.type == "watch"
        assert result.watch.mode == "human"
        assert result.watch.config["prompt"] == "Approve?"

    def test_check_status_running(self):
        executor = HumanExecutor(prompt="test")
        status = executor.check_status({})
        assert status.state == "running"


# ── Test 16: MockLLMExecutor ─────────────────────────────────────────


class TestMockLLMExecutor:
    def test_success_default(self):
        executor = MockLLMExecutor()
        ctx = _ctx(step_name="analyze")
        result = executor.start({"data": "test"}, ctx)
        assert result.type == "data"
        assert result.envelope is not None

    def test_with_preconfigured_responses(self):
        executor = MockLLMExecutor(responses={
            "analyze": {"plan": "do stuff", "confidence": 0.9},
        })
        ctx = _ctx(step_name="analyze")
        result = executor.start({}, ctx)
        assert result.envelope.artifact["plan"] == "do stuff"
        assert result.envelope.artifact["confidence"] == 0.9

    def test_failure_mode(self):
        executor = MockLLMExecutor(failure_rate=1.0)  # always fail
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.executor_state.get("failed") is True

    def test_partial_mode(self):
        executor = MockLLMExecutor(partial_rate=1.0)  # always partial
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.envelope.executor_meta.get("partial") is True

    def test_callable_response(self):
        executor = MockLLMExecutor(responses={
            "transform": lambda inputs: {"doubled": inputs.get("n", 0) * 2},
        })
        ctx = _ctx(step_name="transform")
        result = executor.start({"n": 5}, ctx)
        assert result.envelope.artifact["doubled"] == 10


# ── Test 15: Decorator composition ───────────────────────────────────


class TestDecoratorComposition:
    def test_timeout_retry_script(self):
        """timeout(retry(script)) — decorators compose."""
        # Inner: script that succeeds
        inner = ScriptExecutor(command='echo \'{"result": "ok"}\'')

        # Wrap with retry
        retried = RetryDecorator(inner, {"max_retries": 2})

        # Wrap with timeout
        timed = TimeoutDecorator(retried, {"minutes": 5})

        ctx = _ctx()
        result = timed.start({}, ctx)

        assert result.type == "data"
        assert result.envelope.artifact["result"] == "ok"
        # Check decorator metadata is present
        assert "timeout" in result.envelope.executor_meta
        assert result.envelope.executor_meta["timeout"]["triggered"] is False

    def test_retry_on_failure(self):
        """RetryDecorator retries failed executor."""
        call_count = {"n": 0}

        class FailOnceExecutor(Executor):
            def start(self, inputs, context):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return ExecutorResult(
                        type="data",
                        envelope=HandoffEnvelope(
                            artifact={},
                            sidecar=Sidecar(),
                            workspace="",
                            timestamp=_now(),
                            executor_meta={"failed": True},
                        ),
                        executor_state={"failed": True, "error": "first fail"},
                    )
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "success"},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        retried = RetryDecorator(FailOnceExecutor(), {"max_retries": 2})
        ctx = _ctx()
        result = retried.start({}, ctx)

        assert result.type == "data"
        assert result.envelope.artifact["result"] == "success"
        assert call_count["n"] == 2
        assert result.envelope.executor_meta["retry"]["attempts"] == 2

    def test_retry_respects_non_retriable(self):
        """RetryDecorator doesn't retry non-retriable steps."""
        call_count = {"n": 0}

        class AlwaysFailExecutor(Executor):
            def start(self, inputs, context):
                call_count["n"] += 1
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                        executor_meta={"failed": True},
                    ),
                    executor_state={"failed": True, "error": "fail"},
                )

            def check_status(self, state):
                return ExecutorStatus(state="failed")

            def cancel(self, state):
                pass

        retried = RetryDecorator(AlwaysFailExecutor(), {"max_retries": 3})
        ctx = _ctx()
        ctx.idempotency = "non_retriable"
        result = retried.start({}, ctx)

        # Should only be called once since it's non-retriable
        assert call_count["n"] == 1

    def test_fallback_decorator(self):
        class FailExecutor(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                        executor_meta={"failed": True},
                    ),
                    executor_state={"failed": True, "error": "primary failed"},
                )

            def check_status(self, state):
                return ExecutorStatus(state="failed")

            def cancel(self, state):
                pass

        class SuccessExecutor(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "from_fallback"},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        fb = FallbackDecorator(FailExecutor(), SuccessExecutor(), {})
        ctx = _ctx()
        result = fb.start({}, ctx)

        assert result.envelope.artifact["result"] == "from_fallback"
        assert result.envelope.executor_meta["fallback"]["primary_failed"] is True
