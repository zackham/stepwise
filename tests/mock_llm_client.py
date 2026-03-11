"""Mock LLM client for testing. Configurable responses, error modes, cost simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stepwise.llm_client import LLMResponse


@dataclass
class MockCall:
    """Record of a single call to the mock client."""
    model: str
    messages: list[dict[str, str]]
    tools: list[dict] | None
    temperature: float
    max_tokens: int


class MockLLMClient:
    """Test double for LLMClient protocol.

    Modes:
    - Default: returns configured response (tool_call or content)
    - Error: raises exception on call
    - Sequence: returns different responses per call
    """

    def __init__(
        self,
        *,
        # Default response fields
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        model: str = "mock/test-model",
        cost_usd: float | None = 0.001,
        latency_ms: int = 50,
        # Error simulation
        error: Exception | None = None,
        # Sequence mode: list of LLMResponses to return in order
        responses: list[LLMResponse] | None = None,
    ) -> None:
        self._default_content = content
        self._default_tool_calls = tool_calls
        self._default_usage = usage or {"prompt_tokens": 100, "completion_tokens": 50}
        self._default_model = model
        self._default_cost = cost_usd
        self._default_latency = latency_ms
        self._error = error
        self._responses = responses
        self._call_index = 0
        self.calls: list[MockCall] = []

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Record the call and return configured response."""
        self.calls.append(MockCall(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        ))

        if self._error is not None:
            raise self._error

        if self._responses is not None:
            if self._call_index >= len(self._responses):
                raise RuntimeError(
                    f"MockLLMClient exhausted: {self._call_index + 1} calls "
                    f"but only {len(self._responses)} responses configured"
                )
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp

        return LLMResponse(
            content=self._default_content,
            tool_calls=self._default_tool_calls,
            usage=self._default_usage,
            model=self._default_model,
            cost_usd=self._default_cost,
            latency_ms=self._default_latency,
        )

    @staticmethod
    def tool_call_response(
        arguments: dict[str, str],
        *,
        model: str = "mock/test-model",
        cost_usd: float = 0.001,
    ) -> LLMResponse:
        """Convenience: build an LLMResponse with a step_output tool call."""
        return LLMResponse(
            content=None,
            tool_calls=[{"name": "step_output", "arguments": arguments}],
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            model=model,
            cost_usd=cost_usd,
            latency_ms=50,
        )

    @staticmethod
    def content_response(
        content: str,
        *,
        model: str = "mock/test-model",
        cost_usd: float = 0.001,
    ) -> LLMResponse:
        """Convenience: build an LLMResponse with plain content."""
        return LLMResponse(
            content=content,
            tool_calls=None,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            model=model,
            cost_usd=cost_usd,
            latency_ms=50,
        )
