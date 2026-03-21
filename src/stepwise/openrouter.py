"""OpenRouter API client for LLM calls."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from stepwise.llm_client import LLMResponse

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    """Makes chat completion calls via OpenRouter."""

    def __init__(self, api_key: str, base_url: str = OPENROUTER_BASE) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request to OpenRouter."""
        start = time.monotonic()

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = {"type": "function", "function": {"name": "step_output"}}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://stepwise.local",
            "X-Title": "Stepwise",
        }

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=900.0,
        )
        resp.raise_for_status()

        data = resp.json()
        elapsed_ms = int((time.monotonic() - start) * 1000)

        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})

        # Extract cost from response body (preferred) or header (legacy)
        cost: float | None = None
        body_cost = usage.get("cost")
        if body_cost is not None:
            cost = float(body_cost)
        else:
            cost_header = resp.headers.get("x-openrouter-cost")
            if cost_header:
                cost = float(cost_header)

        # Parse tool calls
        tool_calls = None
        if choice.get("tool_calls"):
            tool_calls = []
            for tc in choice["tool_calls"]:
                tool_calls.append({
                    "name": tc["function"]["name"],
                    "arguments": json.loads(tc["function"]["arguments"]),
                })

        return LLMResponse(
            content=choice.get("content"),
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            model=data.get("model", model),
            cost_usd=cost,
            latency_ms=elapsed_ms,
        )
