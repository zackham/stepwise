"""Fetch, cache, and search OpenRouter model catalog."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from stepwise.config import ModelEntry

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL = 24 * 3600  # 24 hours

# In-memory cache
_cache: list[dict[str, Any]] = []
_cache_ts: float = 0.0


@dataclass
class OpenRouterModel:
    """Parsed model from OpenRouter API."""
    id: str
    name: str
    provider: str
    context_length: int
    max_output_tokens: int | None
    prompt_cost: float | None      # USD per token
    completion_cost: float | None   # USD per token

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "context_length": self.context_length,
            "max_output_tokens": self.max_output_tokens,
            "prompt_cost": self.prompt_cost,
            "completion_cost": self.completion_cost,
        }

    def to_model_entry(self) -> ModelEntry:
        return ModelEntry(
            id=self.id,
            name=self.name,
            provider=self.provider,
            context_length=self.context_length,
            max_output_tokens=self.max_output_tokens,
            prompt_cost=self.prompt_cost,
            completion_cost=self.completion_cost,
        )


def _parse_model(raw: dict[str, Any]) -> OpenRouterModel:
    """Parse a single model from the OpenRouter API response."""
    model_id = raw["id"]
    provider = model_id.split("/")[0] if "/" in model_id else "unknown"
    pricing = raw.get("pricing", {})
    top_provider = raw.get("top_provider", {}) or {}

    def _parse_cost(val: str | None) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    return OpenRouterModel(
        id=model_id,
        name=raw.get("name", model_id),
        provider=provider,
        context_length=raw.get("context_length", 0),
        max_output_tokens=top_provider.get("max_completion_tokens"),
        prompt_cost=_parse_cost(pricing.get("prompt")),
        completion_cost=_parse_cost(pricing.get("completion")),
    )


def _fetch_models() -> list[dict[str, Any]]:
    """Fetch model list from OpenRouter (no auth required)."""
    resp = httpx.get(OPENROUTER_MODELS_URL, timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_openrouter_models(force_refresh: bool = False) -> list[OpenRouterModel]:
    """Get cached OpenRouter model list, refreshing if stale."""
    global _cache, _cache_ts

    if not force_refresh and _cache and (time.time() - _cache_ts) < CACHE_TTL:
        return [_parse_model(m) for m in _cache]

    try:
        _cache = _fetch_models()
        _cache_ts = time.time()
    except Exception:
        # Return stale cache on failure
        if _cache:
            return [_parse_model(m) for m in _cache]
        raise

    return [_parse_model(m) for m in _cache]


def search_openrouter_models(query: str, limit: int = 30) -> list[OpenRouterModel]:
    """Search OpenRouter models by name or ID."""
    models = get_openrouter_models()
    if not query:
        return models[:limit]

    q = query.lower()
    scored: list[tuple[int, OpenRouterModel]] = []
    for m in models:
        # Score: exact ID prefix > name contains > ID contains
        if m.id.lower().startswith(q):
            scored.append((0, m))
        elif q in m.name.lower():
            scored.append((1, m))
        elif q in m.id.lower():
            scored.append((2, m))

    scored.sort(key=lambda x: x[0])
    return [m for _, m in scored[:limit]]


def enrich_registry(registry: list[ModelEntry]) -> list[ModelEntry]:
    """Enrich registry model entries with metadata from OpenRouter cache.

    Only fills in missing fields — doesn't overwrite existing data.
    """
    try:
        or_models = get_openrouter_models()
    except Exception:
        return registry  # Can't enrich without cache

    or_map = {m.id: m for m in or_models}
    enriched = []
    for entry in registry:
        orm = or_map.get(entry.id)
        if orm:
            enriched.append(ModelEntry(
                id=entry.id,
                name=orm.name if entry.name == entry.id else entry.name,
                provider=entry.provider,
                context_length=entry.context_length or orm.context_length,
                max_output_tokens=entry.max_output_tokens or orm.max_output_tokens,
                prompt_cost=entry.prompt_cost or orm.prompt_cost,
                completion_cost=entry.completion_cost or orm.completion_cost,
            ))
        else:
            enriched.append(entry)
    return enriched
