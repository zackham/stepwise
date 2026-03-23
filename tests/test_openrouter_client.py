"""Tests for OpenRouterClient: request format, model names, error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from stepwise.openrouter import OpenRouterClient, OpenRouterError


def _mock_response(
    *,
    status_code: int = 200,
    body: dict | None = None,
    text: str | None = None,
) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    if body is not None:
        resp.json.return_value = body
        resp.text = json.dumps(body)
    elif text is not None:
        resp.text = text
        resp.json.side_effect = ValueError("not json")
    else:
        resp.text = ""
    resp.headers = {}
    return resp


def _success_body(model: str = "google/gemini-2.5-pro") -> dict:
    """Minimal successful OpenRouter chat completion response body."""
    return {
        "choices": [{"message": {"content": "hello", "tool_calls": None}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": model,
    }


class TestModelNameHandling:
    """Model names with slashes and whitespace are handled correctly."""

    def test_slash_in_model_name_sent_verbatim(self):
        """Model names like google/gemini-2.5-pro must be sent as-is in the payload."""
        client = OpenRouterClient(api_key="sk-test")
        captured_payload = {}

        def fake_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _mock_response(body=_success_body(model="google/gemini-2.5-pro"))

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert captured_payload["model"] == "google/gemini-2.5-pro"

    def test_model_name_whitespace_stripped(self):
        """Leading/trailing whitespace on model name (e.g. from $variable interpolation) is stripped."""
        client = OpenRouterClient(api_key="sk-test")
        captured_payload = {}

        def fake_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _mock_response(body=_success_body(model="anthropic/claude-sonnet-4"))

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="  anthropic/claude-sonnet-4  ",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert captured_payload["model"] == "anthropic/claude-sonnet-4"

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
        """Various provider/model formats with slashes are preserved correctly."""
        client = OpenRouterClient(api_key="sk-test")

        for model_name in [
            "openai/gpt-4.1-mini",
            "anthropic/claude-sonnet-4-20250514",
            "google/gemini-2.5-flash-preview-05-20",
            "deepseek/deepseek-r1",
            "meta-llama/llama-4-maverick",
            "moonshotai/kimi-k2.5",
        ]:
            captured_payload = {}

            def fake_post(url, json, headers, timeout, _m=model_name):
                captured_payload.update(json)
                return _mock_response(body=_success_body(model=_m))

            with patch("httpx.post", side_effect=fake_post):
                client.chat_completion(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                )

            assert captured_payload["model"] == model_name, f"model name mutated for {model_name}"


class TestToolChoice:
    """tool_choice format is compatible with all OpenRouter models."""

    def test_no_tools_no_tool_choice(self):
        """When no tools are provided, tool_choice is not sent."""
        client = OpenRouterClient(api_key="sk-test")
        captured_payload = {}

        def fake_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _mock_response(body=_success_body())

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
            )

        assert "tool_choice" not in captured_payload
        assert "tools" not in captured_payload

    def test_with_tools_uses_required(self):
        """When tools are provided, tool_choice is set to 'required' (not named-function form).

        The named-function form {"type": "function", "function": {"name": "..."}} causes
        400 errors from some providers (e.g. Gemini) via OpenRouter.  'required' forces
        any tool use without specifying which function, and is universally supported.
        """
        client = OpenRouterClient(api_key="sk-test")
        captured_payload = {}

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

        def fake_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _mock_response(body=tool_response_body)

        tools = [{
            "type": "function",
            "function": {
                "name": "step_output",
                "description": "Output",
                "parameters": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
            },
        }]

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "classify"}],
                tools=tools,
            )

        assert captured_payload["tool_choice"] == "required"
        # Verify the named-function form is NOT used
        assert captured_payload["tool_choice"] != {"type": "function", "function": {"name": "step_output"}}

    def test_tools_present_in_payload(self):
        """Tools are included in the payload when provided."""
        client = OpenRouterClient(api_key="sk-test")
        captured_payload = {}

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

        def fake_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _mock_response(body=tool_response_body)

        tools = [{"type": "function", "function": {"name": "step_output", "description": "Out", "parameters": {"type": "object", "properties": {}, "required": []}}}]

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="anthropic/claude-sonnet-4",
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
            )

        assert "tools" in captured_payload
        assert captured_payload["tools"] == tools


class TestErrorHandling:
    """400 and other HTTP errors raise OpenRouterError with response body."""

    def test_400_raises_openrouter_error(self):
        """400 from OpenRouter raises OpenRouterError with status_code and response_body."""
        client = OpenRouterClient(api_key="sk-test")
        error_body = '{"error": {"message": "Invalid model: imaginary/model-xyz", "code": 400}}'

        def fake_post(url, json, headers, timeout):
            return _mock_response(status_code=400, text=error_body)

        with pytest.raises(OpenRouterError) as exc_info:
            with patch("httpx.post", side_effect=fake_post):
                client.chat_completion(
                    model="imaginary/model-xyz",
                    messages=[{"role": "user", "content": "hi"}],
                )

        err = exc_info.value
        assert err.status_code == 400
        assert "imaginary/model-xyz" in err.response_body
        assert err.model == "imaginary/model-xyz"
        # The error message includes both status and body
        assert "400" in str(err)
        assert "imaginary/model-xyz" in str(err)

    def test_401_raises_openrouter_error(self):
        """401 from OpenRouter raises OpenRouterError."""
        client = OpenRouterClient(api_key="invalid-key")
        error_body = '{"error": {"message": "No auth credentials found", "code": 401}}'

        def fake_post(url, json, headers, timeout):
            return _mock_response(status_code=401, text=error_body)

        with pytest.raises(OpenRouterError) as exc_info:
            with patch("httpx.post", side_effect=fake_post):
                client.chat_completion(
                    model="anthropic/claude-sonnet-4",
                    messages=[{"role": "user", "content": "hi"}],
                )

        assert exc_info.value.status_code == 401

    def test_200_returns_llm_response(self):
        """200 returns parsed LLMResponse."""
        client = OpenRouterClient(api_key="sk-test")

        def fake_post(url, json, headers, timeout):
            return _mock_response(body=_success_body(model="google/gemini-2.5-pro"))

        with patch("httpx.post", side_effect=fake_post):
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
        captured_headers = {}

        def fake_post(url, json, headers, timeout):
            captured_headers.update(headers)
            return _mock_response(body=_success_body())

        with patch("httpx.post", side_effect=fake_post):
            client.chat_completion(
                model="anthropic/claude-sonnet-4",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert captured_headers["Authorization"] == "Bearer sk-my-api-key"
        assert captured_headers["Content-Type"] == "application/json"
