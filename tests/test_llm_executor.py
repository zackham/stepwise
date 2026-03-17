"""Tests for LLMExecutor: prompt rendering, output parsing, error handling, structured output."""

import json
import tempfile

import pytest

from stepwise.executors import ExecutionContext, LLMExecutor
from stepwise.llm_client import LLMResponse
from tests.mock_llm_client import MockLLMClient


def _ctx(step_name="test", attempt=1, workspace=None, injected_context=None):
    return ExecutionContext(
        job_id="job-test",
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or tempfile.mkdtemp(),
        idempotency="idempotent",
        injected_context=injected_context,
    )


def _executor(client, output_fields=None, **kwargs):
    """Build an LLMExecutor with common defaults."""
    ex = LLMExecutor(
        client=client,
        model=kwargs.get("model", "test/model"),
        prompt=kwargs.get("prompt", "Classify: $text"),
        system=kwargs.get("system", None),
        temperature=kwargs.get("temperature", 0.0),
        max_tokens=kwargs.get("max_tokens", 4096),
    )
    if output_fields:
        ex._output_fields = output_fields
    return ex


# ── Prompt Rendering ─────────────────────────────────────────────────


class TestPromptRendering:
    def test_template_substitution(self):
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "positive"}}
        ])
        ex = _executor(client, prompt="Classify: $text", output_fields=["label"])
        ex.start({"text": "Great product!"}, _ctx())

        assert len(client.calls) == 1
        user_msg = client.calls[0].messages[-1]["content"]
        assert "Great product!" in user_msg
        assert "$text" not in user_msg

    def test_missing_variable_left_as_is(self):
        """safe_substitute leaves unknown variables in place."""
        client = MockLLMClient(content='{"out": "ok"}')
        ex = _executor(client, prompt="Hello $name, process $unknown_var", output_fields=["out"])
        ex.start({"name": "world"}, _ctx())

        user_msg = client.calls[0].messages[-1]["content"]
        assert "Hello world" in user_msg
        assert "$unknown_var" in user_msg

    def test_system_message(self):
        client = MockLLMClient(content='{"x": "1"}')
        ex = _executor(client, system="You are a classifier.", output_fields=["x"])
        ex.start({}, _ctx())

        msgs = client.calls[0].messages
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a classifier."
        assert msgs[1]["role"] == "user"

    def test_no_system_message(self):
        client = MockLLMClient(content='{"x": "1"}')
        ex = _executor(client, output_fields=["x"])
        ex.start({}, _ctx())

        msgs = client.calls[0].messages
        assert msgs[0]["role"] == "user"
        assert len(msgs) == 1

    def test_injected_context_appended(self):
        client = MockLLMClient(content='{"x": "1"}')
        ex = _executor(client, output_fields=["x"])
        ctx = _ctx(injected_context=["Previous step said: hello", "Other info"])
        ex.start({}, ctx)

        user_msg = client.calls[0].messages[-1]["content"]
        assert "Previous step said: hello" in user_msg
        assert "Other info" in user_msg

    def test_non_string_inputs_converted(self):
        client = MockLLMClient(content='{"r": "ok"}')
        ex = _executor(client, prompt="Count is $count, flag is $flag", output_fields=["r"])
        ex.start({"count": 42, "flag": True}, _ctx())

        user_msg = client.calls[0].messages[-1]["content"]
        assert "Count is 42" in user_msg
        assert "flag is True" in user_msg


# ── Output Tool Schema ───────────────────────────────────────────────


class TestOutputToolSchema:
    def test_tool_schema_built(self):
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "pos", "confidence": "0.9"}}
        ])
        ex = _executor(client, output_fields=["label", "confidence"])
        ex.start({}, _ctx())

        tools = client.calls[0].tools
        assert tools is not None
        assert len(tools) == 1
        fn = tools[0]["function"]
        assert fn["name"] == "step_output"
        assert "label" in fn["parameters"]["properties"]
        assert "confidence" in fn["parameters"]["properties"]
        assert fn["parameters"]["required"] == ["label", "confidence"]

    def test_no_tools_without_output_fields(self):
        client = MockLLMClient(content="just text")
        ex = _executor(client)  # no output_fields
        ex.start({}, _ctx())

        assert client.calls[0].tools is None


# ── Output Parsing ───────────────────────────────────────────────────


class TestOutputParsing:
    def test_tool_call_parsing(self):
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "negative", "reason": "bad"}}
        ])
        ex = _executor(client, output_fields=["label", "reason"])
        result = ex.start({}, _ctx())

        assert result.type == "data"
        assert result.envelope.artifact["label"] == "negative"
        assert result.envelope.artifact["reason"] == "bad"
        assert result.envelope.executor_meta["parse_method"] == "tool_call"

    def test_json_content_parsing(self):
        client = MockLLMClient(content='{"label": "positive", "score": "0.95"}')
        ex = _executor(client, output_fields=["label", "score"])
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["label"] == "positive"
        assert result.envelope.executor_meta["parse_method"] == "json_content"

    def test_json_with_code_fences(self):
        client = MockLLMClient(content='```json\n{"label": "neutral"}\n```')
        ex = _executor(client, output_fields=["label"])
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["label"] == "neutral"
        assert result.envelope.executor_meta["parse_method"] == "json_content"

    def test_single_field_shortcut(self):
        """Single output field + plain content = wrap in field name."""
        client = MockLLMClient(content="This is a great summary of the topic.")
        ex = _executor(client, output_fields=["summary"])
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["summary"] == "This is a great summary of the topic."
        assert result.envelope.executor_meta["parse_method"] == "single_field"

    def test_single_field_prefers_content_over_tool_call(self):
        """For single-field steps, content takes priority over tool call.

        Some models (e.g. GPT-5.4) put full responses in content and brief
        summaries in tool call args. Content preference avoids truncation.
        """
        client = MockLLMClient(
            content='{"label": "from_content"}',
            tool_calls=[{"name": "step_output", "arguments": {"label": "from_tool"}}],
        )
        ex = _executor(client, output_fields=["label"])
        result = ex.start({}, _ctx())

        # Content is JSON with the expected field — parsed as json_content
        assert result.envelope.artifact["label"] == "from_content"
        assert result.envelope.executor_meta["parse_method"] == "json_content"

    def test_multi_field_tool_call_takes_priority_over_content(self):
        """For multi-field steps, tool_call still takes priority when not truncated."""
        client = MockLLMClient(
            content='{"label": "from_content", "score": "3"}',
            tool_calls=[{"name": "step_output", "arguments": {"label": "from_tool", "score": "5"}}],
        )
        ex = _executor(client, output_fields=["label", "score"])
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["label"] == "from_tool"
        assert result.envelope.executor_meta["parse_method"] == "tool_call"

    def test_no_output_fields_returns_content_as_json(self):
        """Without declared outputs, JSON content is still parsed."""
        client = MockLLMClient(content='{"answer": "42"}')
        ex = _executor(client)  # no output_fields
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["answer"] == "42"

    def test_no_output_fields_unparseable_content_fails(self):
        """Without declared outputs, non-JSON content fails to parse."""
        client = MockLLMClient(content="just some text")
        ex = _executor(client)  # no output_fields
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "parse" in result.executor_state["error"].lower()

    def test_wrong_tool_call_name_falls_through(self):
        """Tool call with wrong name falls through to content parsing."""
        client = MockLLMClient(
            content='{"label": "from_content"}',
            tool_calls=[{"name": "other_tool", "arguments": {"x": "1"}}],
        )
        ex = _executor(client, output_fields=["label"])
        result = ex.start({}, _ctx())

        assert result.envelope.artifact["label"] == "from_content"
        assert result.envelope.executor_meta["parse_method"] == "json_content"


# ── Missing Output Fields ────────────────────────────────────────────


class TestMissingFields:
    def test_missing_fields_fails(self):
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "ok"}}
        ])
        ex = _executor(client, output_fields=["label", "confidence", "reason"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "confidence" in result.executor_state["error"]
        assert "reason" in result.executor_state["error"]
        # Partial artifact is still available for debugging
        assert result.envelope.artifact["label"] == "ok"

    def test_all_fields_present_succeeds(self):
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"a": "1", "b": "2", "c": "3"}}
        ])
        ex = _executor(client, output_fields=["a", "b", "c"])
        result = ex.start({}, _ctx())

        assert result.executor_state is None  # no failure state
        assert result.envelope.artifact == {"a": "1", "b": "2", "c": "3"}


# ── Error Handling ───────────────────────────────────────────────────


class TestErrorHandling:
    def test_api_exception(self):
        client = MockLLMClient(error=ConnectionError("OpenRouter unreachable"))
        ex = _executor(client, output_fields=["x"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "unreachable" in result.executor_state["error"].lower()
        assert result.envelope.executor_meta["failed"] is True
        assert result.envelope.executor_meta["model"] == "test/model"
        # Prompt is preserved for debugging
        assert "prompt" in result.envelope.executor_meta

    def test_http_error(self):
        client = MockLLMClient(error=RuntimeError("HTTP 429: Rate limited"))
        ex = _executor(client, output_fields=["x"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "429" in result.executor_state["error"]

    def test_malformed_json_content(self):
        client = MockLLMClient(content="{broken json")
        ex = _executor(client, output_fields=["label", "score"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "parse" in result.executor_state["error"].lower()

    def test_empty_response(self):
        client = MockLLMClient(content=None, tool_calls=None)
        ex = _executor(client, output_fields=["label", "score"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True


# ── Executor Meta ────────────────────────────────────────────────────


class TestExecutorMeta:
    def test_success_meta(self):
        client = MockLLMClient(
            tool_calls=[{"name": "step_output", "arguments": {"out": "val"}}],
            cost_usd=0.0042,
            model="anthropic/claude-sonnet-4",
        )
        ex = _executor(client, output_fields=["out"])
        result = ex.start({}, _ctx())

        meta = result.envelope.executor_meta
        assert meta["model"] == "anthropic/claude-sonnet-4"
        assert meta["cost_usd"] == 0.0042
        assert meta["parse_method"] == "tool_call"
        assert "usage" in meta
        assert "prompt" in meta
        assert "latency_ms" in meta

    def test_failure_meta_includes_debugging_info(self):
        client = MockLLMClient(content="not parseable", tool_calls=None)
        ex = _executor(client, output_fields=["a", "b"])
        result = ex.start({"text": "hello"}, _ctx())

        meta = result.envelope.executor_meta
        assert meta["failed"] is True
        assert "raw_content" in meta
        assert meta["raw_content"] == "not parseable"
        assert "prompt" in meta


# ── Check Status / Cancel ────────────────────────────────────────────


class TestCheckStatusCancel:
    def test_check_status_empty_is_running(self):
        """Empty state means executor hasn't set state yet (synchronous call in progress)."""
        client = MockLLMClient()
        ex = _executor(client)
        assert ex.check_status({}).state == "running"
        assert ex.check_status(None).state == "running"

    def test_check_status_completed(self):
        client = MockLLMClient()
        ex = _executor(client)
        assert ex.check_status({"completed": True}).state == "completed"

    def test_check_status_failed(self):
        client = MockLLMClient()
        ex = _executor(client)
        status = ex.check_status({"failed": True, "error": "boom"})
        assert status.state == "failed"
        assert status.message == "boom"

    def test_cancel_is_noop(self):
        client = MockLLMClient()
        ex = _executor(client)
        ex.cancel({})  # should not raise


# ── MockLLMClient Behavior ───────────────────────────────────────────


class TestMockLLMClient:
    def test_call_recording(self):
        client = MockLLMClient(content='{"x": "1"}')
        ex = _executor(client, output_fields=["x"], model="my/model")
        ex.start({"text": "hello"}, _ctx())

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call.model == "my/model"
        assert call.temperature == 0.0

    def test_sequence_mode(self):
        r1 = MockLLMClient.tool_call_response({"label": "first"})
        r2 = MockLLMClient.tool_call_response({"label": "second"})
        client = MockLLMClient(responses=[r1, r2])

        ex = _executor(client, output_fields=["label"])
        res1 = ex.start({}, _ctx())
        res2 = ex.start({}, _ctx())

        assert res1.envelope.artifact["label"] == "first"
        assert res2.envelope.artifact["label"] == "second"

    def test_sequence_exhausted_raises(self):
        client = MockLLMClient(responses=[
            MockLLMClient.content_response('{"x": "1"}'),
        ])
        ex = _executor(client, output_fields=["x"])
        ex.start({}, _ctx())  # first call ok

        # Second call to the same client raises (exhausted responses)
        ex2 = _executor(client, output_fields=["x"])
        result = ex2.start({}, _ctx())
        assert result.executor_state["failed"] is True  # exception caught by executor

    def test_error_mode(self):
        client = MockLLMClient(error=TimeoutError("deadline exceeded"))
        ex = _executor(client, output_fields=["x"])
        result = ex.start({}, _ctx())

        assert result.executor_state["failed"] is True
        assert "deadline exceeded" in result.executor_state["error"]
