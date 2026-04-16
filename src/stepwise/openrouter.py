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
    """Makes chat completion calls via OpenRouter
    using Server-Sent Events streaming.

    All calls stream with `stream: true`. This prevents idle-connection
    timeouts on slow providers (Kimi K2.5 via OpenRouter used to die on
    long responses with the old blocking POST path). Token counts and
    cost arrive in the final chunk via `stream_options.include_usage`
    (OpenAI spec) and `usage: {include: true}` (OpenRouter-specific
    cost enrichment). The public interface still returns a fully
    accumulated `LLMResponse` — streaming is an implementation detail.
    """

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
        """Send a chat completion request (streaming) and return the
        fully-accumulated response. Blocks until the stream is complete."""
        start = time.monotonic()

        # Normalize model name: strip whitespace that can appear after $variable
        # interpolation (e.g. "  google/gemini-2.5-pro  " → "google/gemini-2.5-pro").
        # Slashes in model IDs (provider/model) are valid and must be preserved.
        model = model.strip()
        if not model:
            raise ValueError("model name is empty — check that $variable references resolved correctly")

        api_url = f"{self.base_url}/chat/completions"
        api_key = self.api_key
        effective_model = model

        # Support provider routing via suffix: "model-id:provider/tag"
        # e.g. "moonshotai/kimi-k2.5:moonshotai/int4" routes to that specific provider
        provider_order = None
        if ":" in effective_model:
            parts = effective_model.split(":", 1)
            if "/" in parts[0]:
                effective_model, provider_order = parts[0], [parts[1]]

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "usage": {"include": True},
        }
        if provider_order:
            payload["provider"] = {"order": provider_order}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "required"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://stepwise.local",
            "X-Title": "Stepwise",
        }

        try:
            with httpx.stream(
                "POST",
                api_url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    # Long total timeout since some flows take minutes,
                    # but a tight read timeout so a stalled provider is
                    # detected quickly. With streaming every delta
                    # resets the read clock, so this bounds INTRA-chunk
                    # silence, not total run length.
                    connect=30.0, read=120.0, write=30.0, pool=30.0,
                ),
            ) as resp:
                if resp.status_code >= 400:
                    error_body = resp.read().decode("utf-8", errors="replace")
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

                parsed = _accumulate_sse_stream(resp.iter_lines())
        except httpx.TimeoutException as e:
            raise OpenRouterError(
                f"OpenRouter timeout for model={model}: {e}",
                status_code=0,
                response_body=str(e),
                model=model,
            ) from e

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Some models (reasoning models, Kimi variants) stream their
        # output via `reasoning_content` or `reasoning` deltas rather
        # than `content`. Fall back so the parser downstream has
        # something to work with.
        content = parsed["content"] or parsed["reasoning"] or None
        usage = parsed["usage"]
        cost = usage.get("cost") if usage else None
        if cost is not None:
            cost = float(cost)

        return LLMResponse(
            content=content,
            tool_calls=parsed["tool_calls"] or None,
            usage={
                "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
                "completion_tokens": (usage or {}).get("completion_tokens", 0),
            },
            model=parsed["model"] or model,
            cost_usd=cost,
            latency_ms=elapsed_ms,
            raw_response=parsed["raw_last_chunk"],
        )


def _accumulate_sse_stream(lines) -> dict[str, Any]:
    """Parse an OpenRouter/OpenAI-compatible SSE chat completion stream.

    Takes an iterable of raw lines (strings from httpx's iter_lines()
    or equivalents). Accumulates content, reasoning, tool calls, and
    usage/cost, returning a dict:

        {
            "content": str,                     # joined content deltas
            "reasoning": str,                   # joined reasoning deltas
            "tool_calls": list[dict],           # [{"name","arguments"}]
            "usage": dict | None,               # final usage object or None
            "model": str | None,                # echoed model name
            "raw_last_chunk": dict | None,      # final non-[DONE] chunk
        }

    Tool call deltas arrive indexed — calls with the same index
    across chunks are concatenated on `function.arguments` and parsed
    as JSON at the end.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # index → {"id": str, "name": str, "args_str": str}
    tool_call_bufs: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    model_echo: str | None = None
    raw_last: dict[str, Any] | None = None

    for raw in lines:
        if raw is None:
            continue
        line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        line = line.strip()
        if not line or line.startswith(":"):
            continue  # SSE keepalive / comment
        if not line.startswith("data:"):
            continue
        data_str = line[5:].lstrip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        raw_last = chunk

        # Errors can be embedded in stream (provider overload, etc.)
        if isinstance(chunk, dict) and chunk.get("error"):
            err = chunk["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise OpenRouterError(
                f"OpenRouter stream error: {err_msg}",
                status_code=(err.get("code", 0) if isinstance(err, dict) else 0),
                response_body=json.dumps(chunk),
                model=chunk.get("model", ""),
            )

        if "model" in chunk:
            model_echo = chunk["model"]

        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                content_parts.append(delta["content"])
            # Reasoning-model fields (Kimi K2.5 variants, others)
            if isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]:
                reasoning_parts.append(delta["reasoning_content"])
            if isinstance(delta.get("reasoning"), str) and delta["reasoning"]:
                reasoning_parts.append(delta["reasoning"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                buf = tool_call_bufs.setdefault(
                    idx, {"id": "", "name": "", "args_str": ""},
                )
                if tc.get("id"):
                    buf["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    buf["name"] = fn["name"]
                if "arguments" in fn and fn["arguments"]:
                    buf["args_str"] += fn["arguments"]

        # Usage arrives in a late chunk (sometimes the last one, sometimes
        # a dedicated trailing chunk after finish_reason). OR enriches it
        # with `cost` when `usage: {include: true}` is in the request.
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]

    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(tool_call_bufs):
        buf = tool_call_bufs[idx]
        if not buf["name"]:
            continue
        try:
            args = json.loads(buf["args_str"] or "{}")
        except (json.JSONDecodeError, ValueError):
            args = {}
        tool_calls.append({"name": buf["name"], "arguments": args})

    return {
        "content": "".join(content_parts),
        "reasoning": "".join(reasoning_parts),
        "tool_calls": tool_calls,
        "usage": usage,
        "model": model_echo,
        "raw_last_chunk": raw_last,
    }
