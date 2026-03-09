"""Tests for ScriptExecutor, HumanExecutor, MockLLMExecutor, and decorators."""

import asyncio

import pytest

from stepwise.decorators import (
    FallbackDecorator,
    NotificationDecorator,
    RetryDecorator,
    TimeoutDecorator,
)
from stepwise.events import EventBus, EventType
from stepwise.executors import (
    ExecutorResult,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
)
from stepwise.models import StepRun


def _make_sr(**kwargs):
    defaults = {"job_id": "j1", "step_name": "test_step"}
    defaults.update(kwargs)
    return StepRun.create(**defaults)


# ── ScriptExecutor ───────────────────────────────────────────────────


class TestScriptExecutor:
    async def test_callable_success(self, script_executor):
        sr = _make_sr(inputs={"x": 5})
        result = await script_executor.execute(
            sr, {"callable": lambda inputs: {"doubled": inputs["x"] * 2}}
        )
        assert result.success
        assert result.outputs["doubled"] == 10

    async def test_callable_returns_non_dict(self, script_executor):
        sr = _make_sr()
        result = await script_executor.execute(
            sr, {"callable": lambda inputs: 42}
        )
        assert result.success
        assert result.outputs["result"] == 42

    async def test_callable_exception(self, script_executor):
        def fail(inputs):
            raise ValueError("boom")

        sr = _make_sr()
        result = await script_executor.execute(sr, {"callable": fail})
        assert not result.success
        assert "boom" in result.error

    async def test_async_callable(self, script_executor):
        async def async_fn(inputs):
            return {"async": True}

        sr = _make_sr()
        result = await script_executor.execute(sr, {"callable": async_fn})
        assert result.success
        assert result.outputs["async"] is True

    async def test_command_success(self, script_executor):
        sr = _make_sr()
        result = await script_executor.execute(sr, {"command": "echo hello"})
        assert result.success
        assert result.outputs["stdout"] == "hello"
        assert result.outputs["return_code"] == 0

    async def test_command_failure(self, script_executor):
        sr = _make_sr()
        result = await script_executor.execute(sr, {"command": "exit 1"})
        assert not result.success
        assert result.outputs["return_code"] == 1

    async def test_command_with_input_substitution(self, script_executor):
        sr = _make_sr(inputs={"name": "world"})
        result = await script_executor.execute(
            sr, {"command": "echo {name}"}
        )
        assert result.success
        assert "world" in result.outputs["stdout"]

    async def test_no_config(self, script_executor):
        sr = _make_sr()
        result = await script_executor.execute(sr, {})
        assert not result.success
        assert "no 'command' or 'callable'" in result.error


# ── HumanExecutor ────────────────────────────────────────────────────


class TestHumanExecutor:
    async def test_complete(self, human_executor):
        sr = _make_sr()

        async def resolve():
            await asyncio.sleep(0.01)
            human_executor.complete(sr.id, {"approved": True})

        asyncio.create_task(resolve())
        result = await human_executor.execute(sr, {"prompt": "Approve?"})
        assert result.success
        assert result.outputs["approved"] is True

    async def test_fail(self, human_executor):
        sr = _make_sr()

        async def resolve():
            await asyncio.sleep(0.01)
            human_executor.fail(sr.id, "Rejected by reviewer")

        asyncio.create_task(resolve())
        result = await human_executor.execute(sr, {"prompt": "Approve?"})
        assert not result.success
        assert "Rejected" in result.error

    async def test_pending_ids(self, human_executor):
        sr = _make_sr()

        async def resolve():
            await asyncio.sleep(0.02)
            assert sr.id in human_executor.pending_ids
            human_executor.complete(sr.id, {})

        asyncio.create_task(resolve())
        await human_executor.execute(sr, {})
        assert sr.id not in human_executor.pending_ids


# ── MockLLMExecutor ──────────────────────────────────────────────────


class TestMockLLMExecutor:
    async def test_config_response(self, mock_llm):
        sr = _make_sr()
        result = await mock_llm.execute(sr, {"response": "hello from LLM"})
        assert result.success
        assert result.outputs["response"] == "hello from LLM"

    async def test_config_response_dict(self, mock_llm):
        sr = _make_sr()
        result = await mock_llm.execute(
            sr, {"response": {"answer": 42, "confidence": 0.9}}
        )
        assert result.success
        assert result.outputs["answer"] == 42

    async def test_instance_responses(self):
        llm = MockLLMExecutor(responses={"my_step": "custom"})
        sr = _make_sr(step_name="my_step")
        result = await llm.execute(sr, {})
        assert result.outputs["response"] == "custom"

    async def test_default_response(self):
        llm = MockLLMExecutor(responses={"default": "fallback"})
        sr = _make_sr(step_name="unknown_step")
        result = await llm.execute(sr, {})
        assert result.outputs["response"] == "fallback"

    async def test_response_fn(self, mock_llm):
        sr = _make_sr(inputs={"question": "2+2?"})
        result = await mock_llm.execute(
            sr,
            {"response_fn": lambda inputs, config: f"Answer: {inputs['question']}"},
        )
        assert result.success
        assert "2+2?" in result.outputs["response"]

    async def test_response_fn_exception(self, mock_llm):
        sr = _make_sr()
        result = await mock_llm.execute(
            sr,
            {"response_fn": lambda i, c: 1 / 0},
        )
        assert not result.success
        assert "division by zero" in result.error


# ── TimeoutDecorator ─────────────────────────────────────────────────


class TestTimeoutDecorator:
    async def test_completes_within_timeout(self, script_executor):
        wrapped = TimeoutDecorator(script_executor, timeout_seconds=5.0)
        sr = _make_sr()
        result = await wrapped.execute(
            sr, {"callable": lambda i: {"ok": True}}
        )
        assert result.success

    async def test_times_out(self):
        class SlowExecutor:
            async def execute(self, sr, config):
                await asyncio.sleep(10)
                return ExecutorResult(outputs={"done": True})

        wrapped = TimeoutDecorator(SlowExecutor(), timeout_seconds=0.05)
        sr = _make_sr()
        result = await wrapped.execute(sr, {})
        assert not result.success
        assert "timed out" in result.error


# ── RetryDecorator ───────────────────────────────────────────────────


class TestRetryDecorator:
    async def test_succeeds_on_retry(self):
        call_count = 0

        class FlakyExecutor:
            async def execute(self, sr, config):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return ExecutorResult(error=f"fail {call_count}")
                return ExecutorResult(outputs={"ok": True})

        wrapped = RetryDecorator(FlakyExecutor(), max_retries=4)
        sr = _make_sr()
        result = await wrapped.execute(sr, {})
        assert result.success
        assert call_count == 3

    async def test_exhausts_retries(self):
        class AlwaysFail:
            async def execute(self, sr, config):
                return ExecutorResult(error="always fails")

        wrapped = RetryDecorator(AlwaysFail(), max_retries=2)
        sr = _make_sr()
        result = await wrapped.execute(sr, {})
        assert not result.success
        assert sr.attempt == 3  # 1 original + 2 retries

    async def test_no_retry_on_success(self, script_executor):
        call_count = 0
        original_execute = script_executor.execute

        async def counting_execute(sr, config):
            nonlocal call_count
            call_count += 1
            return await original_execute(sr, config)

        script_executor.execute = counting_execute
        wrapped = RetryDecorator(script_executor, max_retries=3)
        sr = _make_sr()
        result = await wrapped.execute(sr, {"callable": lambda i: {"ok": True}})
        assert result.success
        assert call_count == 1


# ── NotificationDecorator ────────────────────────────────────────────


class TestNotificationDecorator:
    async def test_emits_start_and_complete(self, script_executor, event_bus):
        wrapped = NotificationDecorator(script_executor, event_bus)
        sr = _make_sr()
        result = await wrapped.execute(
            sr, {"callable": lambda i: {"ok": True}}
        )
        assert result.success

        types = [e.event_type for e in event_bus.history]
        assert EventType.STEP_STARTED in types
        assert EventType.STEP_COMPLETED in types

    async def test_emits_start_and_fail(self, event_bus):
        class FailExec:
            async def execute(self, sr, config):
                return ExecutorResult(error="oops")

        wrapped = NotificationDecorator(FailExec(), event_bus)
        sr = _make_sr()
        result = await wrapped.execute(sr, {})
        assert not result.success

        types = [e.event_type for e in event_bus.history]
        assert EventType.STEP_STARTED in types
        assert EventType.STEP_FAILED in types


# ── FallbackDecorator ────────────────────────────────────────────────


class TestFallbackDecorator:
    async def test_primary_succeeds(self, script_executor, mock_llm):
        wrapped = FallbackDecorator(script_executor, mock_llm)
        sr = _make_sr()
        result = await wrapped.execute(
            sr, {"callable": lambda i: {"primary": True}}
        )
        assert result.success
        assert result.outputs.get("primary") is True

    async def test_fallback_used(self, mock_llm):
        class FailExec:
            async def execute(self, sr, config):
                return ExecutorResult(error="primary failed")

        wrapped = FallbackDecorator(FailExec(), mock_llm)
        sr = _make_sr()
        result = await wrapped.execute(
            sr, {"response": "fallback response"}
        )
        assert result.success
        assert result.outputs["response"] == "fallback response"

    async def test_both_fail(self):
        class Fail1:
            async def execute(self, sr, config):
                return ExecutorResult(error="fail 1")

        class Fail2:
            async def execute(self, sr, config):
                return ExecutorResult(error="fail 2")

        wrapped = FallbackDecorator(Fail1(), Fail2())
        sr = _make_sr()
        result = await wrapped.execute(sr, {})
        assert not result.success
        assert "fail 2" in result.error
