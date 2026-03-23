"""OpenRouter API client for LLM calls."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from stepwise.llm_client import LLMResponse

logger = logging.getLogger("stepwise.openrouter")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterError(Exception):
    """Error from OpenRouter API with response details."""

    def __init__(
        self, message: str, status_code: int, response_body: str, model: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.model = model


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

        # Normalize model name: strip whitespace that can appear after $variable
        # interpolation (e.g. "  google/gemini-2.5-pro  " → "google/gemini-2.5-pro").
        # Slashes in model IDs (provider/model) are valid and must be preserved.
        model = model.strip()
        if not model:
            raise ValueError("model name is empty — check that $variable references resolved correctly")

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            # Use tool_choice "required" rather than the named-function form
            # {"type": "function", "function": {"name": "..."}} — the named form
            # causes 400 errors from some providers (e.g. Gemini) via OpenRouter
            # because they don't support pinning a specific function.  "required"
            # forces the model to call *some* tool, which is sufficient since we
            # only ever expose the single step_output function.
            payload["tool_choice"] = "required"

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

        if resp.status_code >= 400:
            # Log the full error response body — OpenRouter includes specific
            # error messages (invalid model, context length exceeded, etc.)
            # that raise_for_status() doesn't surface in the exception message.
            error_body = resp.text
            prompt_len = sum(len(m.get("content", "")) for m in messages)
            logger.error(
                "OpenRouter %d for model=%s prompt_len=%d tools=%s: %s",
                resp.status_code, model, prompt_len,
                "yes" if tools else "no", error_body,
            )
            raise OpenRouterError(
                f"OpenRouter {resp.status_code} for model={model}: {error_body}",
                status_code=resp.status_code,
                response_body=error_body,
                model=model,
            )

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
