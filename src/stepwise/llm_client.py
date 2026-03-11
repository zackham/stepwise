"""LLM client protocol and response types for dependency injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Normalized response from an LLM API call."""
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] = field(default_factory=dict)  # prompt_tokens, completion_tokens
    model: str = ""
    cost_usd: float | None = None
    latency_ms: int = 0


class LLMClient(Protocol):
    """Protocol for LLM API clients. Implementations: OpenRouterClient, MockLLMClient."""

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request and return the response."""
        ...
