"""Tests for OpenRouterClient: request format, model names, error handling.

Post-2026-04-15: the client uses streaming SSE via `httpx.stream(...)`
instead of a blocking `httpx.post(...)`. Mock helpers build a fake
streaming context-manager that yields SSE lines derived from a
non-streaming response body, so each test can declare the expected
terminal state and the helper converts it into the chunk format the
parser sees.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from stepwise.openrouter import OpenRouterClient, OpenRouterError


# ── SSE stream mock helpers ──────────────────────────────────────────


def _body_to_sse_lines(body: dict) -> list[str]:
    """Convert a non-streaming response body into an equivalent SSE
    line sequence that the openrouter client's parser accumulates
    back into the same LLMResponse.

    Body shape:
        {
          "choices": [{"message": {"content": ..., "tool_calls": ...}}],
          "usage": {...},
          "model": "...",
        }

    Emits one `data: {delta}` line for the content (if any), one per
    tool_call, a finish chunk, and a final usage chunk with the
    echoed model field. Ends with `data: [DONE]`.
    """
    lines: list[str] = []
    model = body.get("model", "")
    choices = body.get("choices") or []

    # Content chunk (if any)
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content:
            lines.append("data: " + json.dumps({
                "model": model,
                "choices": [{"index": 0, "delta": {"content": content}}],
            }))
        # Tool-call chunks — one per tool call, args as a single fragment.
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            lines.append("data: " + json.dumps({
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": i,
                            "id": tc.get("id", f"tc-{i}"),
                            "function": {
                                "name": fn.get("name"),
                                "arguments": fn.get("arguments", ""),
                            },
                        }],
                    },
                }],
            }))

    # Finish reason chunk
    lines.append("data: " + json.dumps({
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }))
    # Usage chunk (OpenAI spec: separate trailing chunk with usage)
    if "usage" in body:
        lines.append("data: " + json.dumps({
            "model": model,
            "choices": [],
            "usage": body["usage"],
        }))
    lines.append("data: [DONE]")
    return lines


def _mock_stream(
    *,
    status_code: int = 200,
    body: dict | None = None,
    text: str | None = None,
    sse_lines: list[str] | None = None,
):
    """Build a context-manager mock for httpx.stream(...).

    Passes status_code and iter_lines() to the client. For error
    responses (status_code >= 400), provides read() returning the
    error body. For 200 responses, iter_lines() yields SSE lines
    derived from `body` (or the explicit `sse_lines` override).
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}

    if status_code >= 400:
        err_body = text if text is not None else (
            json.dumps(body) if body is not None else ""
        )
        resp.read = MagicMock(return_value=err_body.encode("utf-8"))
        resp.iter_lines = MagicMock(return_value=iter([]))
    else:
        if sse_lines is None:
            sse_lines = _body_to_sse_lines(body or {})
        resp.iter_lines = MagicMock(return_value=iter(sse_lines))
        resp.read = MagicMock(return_value=b"")

    @contextmanager
    def _cm(*args, **kwargs):
        yield resp

    return _cm


def _success_body(model: str = "google/gemini-2.5-pro") -> dict:
    """Minimal successful OpenRouter chat completion response body."""
    return {
        "choices": [{"message": {"content": "hello", "tool_calls": None}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": model,
    }


class _PayloadCapture:
    """Captures the `json=` kwarg passed to httpx.stream(...) so tests
    can assert request shape without re-implementing the full mock."""

    def __init__(self, body: dict | None = None, status_code: int = 200,
                 text: str | None = None, sse_lines: list[str] | None = None):
        self.captured_payload: dict = {}
        self.captured_headers: dict = {}
        self._body = body
        self._status_code = status_code
        self._text = text
        self._sse_lines = sse_lines

    def __call__(self, method, url, *, json=None, headers=None, timeout=None, **kw):
        if json is not None:
            self.captured_payload.clear()
            self.captured_payload.update(json)
        if headers is not None:
            self.captured_headers.clear()
            self.captured_headers.update(headers)
        cm_factory = _mock_stream(
            status_code=self._status_code,
            body=self._body,
            text=self._text,
            sse_lines=self._sse_lines,
        )
        return cm_factory()


class TestModelNameHandling:
    """Model names with slashes and whitespace are handled correctly."""

    def test_slash_in_model_name_sent_verbatim(self):
        """Model names like google/gemini-2.5-pro must be sent as-is in the payload."""
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body(model="google/gemini-2.5-pro"))

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert cap.captured_payload["model"] == "google/gemini-2.5-pro"

    def test_model_name_whitespace_stripped(self):
        """Leading/trailing whitespace on model name (e.g. from $variable interpolation) is stripped."""
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body(model="anthropic/claude-sonnet-4"))

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="  anthropic/claude-sonnet-4  ",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert cap.captured_payload["model"] == "anthropic/claude-sonnet-4"

    def test_empty_model_raises_value_error(self):
        """An empty model string (unresolved variable) raises ValueError before any HTTP call."""
        client = OpenRouterClient(api_key="sk-test")

        with pytest.raises(ValueError, match="model name is empty"):
            client.chat_completion(
                model="",
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_whitespace_only_model_raises_value_error(self):
        """A whitespace-only model string raises ValueError."""
        client = OpenRouterClient(api_key="sk-test")

        with pytest.raises(ValueError, match="model name is empty"):
            client.chat_completion(
                model="   ",
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_provider_slash_model_format_preserved(self):
        """Various provider/model formats with slashes are preserved correctly.

        Note: `moonshotai/kimi-k2.5` may be direct-provider remapped if
        `~/.config/vita/moonshot.json` exists (see _resolve_direct_provider).
        We skip that case in this test — it has dedicated coverage below.
        """
        import os
        from pathlib import Path
        moonshot_config = Path(os.path.expanduser("~/.config/vita/moonshot.json"))
        has_direct_moonshot = moonshot_config.exists()

        client = OpenRouterClient(api_key="sk-test")

        for model_name in [
            "openai/gpt-4.1-mini",
            "anthropic/claude-sonnet-4-20250514",
            "google/gemini-2.5-flash-preview-05-20",
            "deepseek/deepseek-r1",
            "meta-llama/llama-4-maverick",
            "moonshotai/kimi-k2.5",
        ]:
            if model_name == "moonshotai/kimi-k2.5" and has_direct_moonshot:
                # Direct-provider routing rewrites the model; skip the
                # preservation assertion for that case.
                continue
            cap = _PayloadCapture(body=_success_body(model=model_name))
            with patch("httpx.stream", side_effect=cap):
                client.chat_completion(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                )
            assert cap.captured_payload["model"] == model_name, (
                f"model name mutated for {model_name}"
            )


class TestStreamingPayload:
    """Streaming mode sends the expected flags."""

    def test_stream_flag_true(self):
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body())

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert cap.captured_payload["stream"] is True
        assert cap.captured_payload["stream_options"] == {"include_usage": True}

    def test_usage_include_flag_sent_to_openrouter(self):
        """The OpenRouter-specific `usage: {include: true}` flag is set
        so BYOK users get cost in the response."""
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body())

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert cap.captured_payload["usage"] == {"include": True}


class TestToolChoice:
    """tool_choice format is compatible with all OpenRouter models."""

    def test_no_tools_no_tool_choice(self):
        """When no tools are provided, tool_choice is not sent."""
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body())

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
            )

        assert "tool_choice" not in cap.captured_payload
        assert "tools" not in cap.captured_payload

    def test_with_tools_uses_required(self):
        """When tools are provided, tool_choice is set to 'required' (not named-function form)."""
        client = OpenRouterClient(api_key="sk-test")

        tool_response_body = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "function": {
                            "name": "step_output",
                            "arguments": '{"label": "positive"}',
                        }
                    }],
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "google/gemini-2.5-pro",
        }
        cap = _PayloadCapture(body=tool_response_body)

        tools = [{
            "type": "function",
            "function": {
                "name": "step_output",
                "description": "Output",
                "parameters": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
            },
        }]

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "classify"}],
                tools=tools,
            )

        assert cap.captured_payload["tool_choice"] == "required"
        assert cap.captured_payload["tool_choice"] != {"type": "function", "function": {"name": "step_output"}}

    def test_tools_present_in_payload(self):
        """Tools are included in the payload when provided."""
        client = OpenRouterClient(api_key="sk-test")

        tool_response_body = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"name": "step_output", "arguments": "{}"}}],
                }
            }],
            "usage": {},
            "model": "anthropic/claude-sonnet-4",
        }
        cap = _PayloadCapture(body=tool_response_body)

        tools = [{"type": "function", "function": {"name": "step_output", "description": "Out", "parameters": {"type": "object", "properties": {}, "required": []}}}]

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="anthropic/claude-sonnet-4",
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
            )

        assert "tools" in cap.captured_payload
        assert cap.captured_payload["tools"] == tools


class TestErrorHandling:
    """400 and other HTTP errors raise OpenRouterError with response body."""

    def test_400_raises_openrouter_error(self):
        """400 from OpenRouter raises OpenRouterError with status_code and response_body."""
        client = OpenRouterClient(api_key="sk-test")
        error_body = '{"error": {"message": "Invalid model: imaginary/model-xyz", "code": 400}}'
        cap = _PayloadCapture(status_code=400, text=error_body)

        with pytest.raises(OpenRouterError) as exc_info:
            with patch("httpx.stream", side_effect=cap):
                client.chat_completion(
                    model="imaginary/model-xyz",
                    messages=[{"role": "user", "content": "hi"}],
                )

        err = exc_info.value
        assert err.status_code == 400
        assert "imaginary/model-xyz" in err.response_body
        assert err.model == "imaginary/model-xyz"
        assert "400" in str(err)
        assert "imaginary/model-xyz" in str(err)

    def test_401_raises_openrouter_error(self):
        """401 from OpenRouter raises OpenRouterError."""
        client = OpenRouterClient(api_key="invalid-key")
        error_body = '{"error": {"message": "No auth credentials found", "code": 401}}'
        cap = _PayloadCapture(status_code=401, text=error_body)

        with pytest.raises(OpenRouterError) as exc_info:
            with patch("httpx.stream", side_effect=cap):
                client.chat_completion(
                    model="anthropic/claude-sonnet-4",
                    messages=[{"role": "user", "content": "hi"}],
                )

        assert exc_info.value.status_code == 401

    def test_200_returns_llm_response(self):
        """200 returns parsed LLMResponse."""
        client = OpenRouterClient(api_key="sk-test")
        cap = _PayloadCapture(body=_success_body(model="google/gemini-2.5-pro"))

        with patch("httpx.stream", side_effect=cap):
            resp = client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert resp.content == "hello"
        assert resp.model == "google/gemini-2.5-pro"


class TestRequestHeaders:
    """Required headers are sent with every request."""

    def test_authorization_header(self):
        """Authorization header uses Bearer token."""
        client = OpenRouterClient(api_key="sk-my-api-key")
        cap = _PayloadCapture(body=_success_body())

        with patch("httpx.stream", side_effect=cap):
            client.chat_completion(
                model="anthropic/claude-sonnet-4",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert cap.captured_headers["Authorization"] == "Bearer sk-my-api-key"
        assert cap.captured_headers["Content-Type"] == "application/json"


# ── Streaming accumulator unit tests ─────────────────────────────────


class TestStreamAccumulator:
    """_accumulate_sse_stream parses the deltas correctly."""

    def test_content_accumulation(self):
        from stepwise.openrouter import _accumulate_sse_stream
        lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": " world"}}]}',
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
            'data: {"usage": {"prompt_tokens": 4, "completion_tokens": 2, "cost": 0.0001}}',
            'data: [DONE]',
        ]
        result = _accumulate_sse_stream(iter(lines))
        assert result["content"] == "Hello world"
        assert result["usage"]["cost"] == 0.0001
        assert result["tool_calls"] == []

    def test_tool_call_delta_accumulation(self):
        """Tool call arguments arrive as fragments; the parser must
        concatenate them by index and JSON-parse the final string."""
        from stepwise.openrouter import _accumulate_sse_stream
        lines = [
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "tc-1", "function": {"name": "step_output", "arguments": "{\\"labe"}}]}}]}',
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "l\\": \\"yes\\"}"}}]}}]}',
            'data: [DONE]',
        ]
        result = _accumulate_sse_stream(iter(lines))
        assert result["tool_calls"] == [
            {"name": "step_output", "arguments": {"label": "yes"}}
        ]

    def test_keepalive_and_comments_skipped(self):
        """SSE comment lines (`:heartbeat`) and empty lines are skipped."""
        from stepwise.openrouter import _accumulate_sse_stream
        lines = [
            ":keepalive",
            "",
            'data: {"choices": [{"delta": {"content": "ok"}}]}',
            "",
            'data: [DONE]',
        ]
        result = _accumulate_sse_stream(iter(lines))
        assert result["content"] == "ok"

    def test_reasoning_field_fallback(self):
        """Reasoning models stream via `reasoning_content` — accumulator
        captures it so the caller can fall back when `content` is empty."""
        from stepwise.openrouter import _accumulate_sse_stream
        lines = [
            'data: {"choices": [{"delta": {"reasoning_content": "thinking..."}}]}',
            'data: {"choices": [{"delta": {"reasoning_content": " done"}}]}',
            'data: [DONE]',
        ]
        result = _accumulate_sse_stream(iter(lines))
        assert result["content"] == ""
        assert result["reasoning"] == "thinking... done"

    def test_stream_error_raises(self):
        """An error embedded in the stream raises OpenRouterError."""
        from stepwise.openrouter import _accumulate_sse_stream
        lines = [
            'data: {"error": {"message": "overloaded", "code": 529}}',
        ]
        with pytest.raises(OpenRouterError) as exc:
            _accumulate_sse_stream(iter(lines))
        assert "overloaded" in str(exc.value)
