"""Stepwise configuration: model registry, API keys, settings."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".config" / "stepwise"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class ModelEntry:
    """A model in the registry."""
    id: str
    name: str
    provider: str
    tier: str | None = None  # "fast", "balanced", "strong", or None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "provider": self.provider}
        if self.tier:
            d["tier"] = self.tier
        return d

    @staticmethod
    def from_dict(d: dict) -> ModelEntry:
        return ModelEntry(
            id=d["id"],
            name=d["name"],
            provider=d["provider"],
            tier=d.get("tier"),
        )


@dataclass
class StepwiseConfig:
    """Runtime configuration for Stepwise."""
    openrouter_api_key: str | None = None
    anthropic_api_key: str | None = None
    model_registry: list[ModelEntry] = field(default_factory=list)
    default_model: str | None = None

    def resolve_model(self, model_ref: str) -> str:
        """Resolve a model reference to a concrete model ID.

        If model_ref contains '/', it's already a concrete ID — returned as-is.
        Otherwise, treat it as a tier alias and find the first matching model.
        """
        if "/" in model_ref:
            return model_ref
        # Tier alias lookup
        for entry in self.model_registry:
            if entry.tier == model_ref:
                return entry.id
        raise ValueError(
            f"No model found for tier '{model_ref}'. "
            f"Available tiers: {sorted(set(e.tier for e in self.model_registry if e.tier))}"
        )

    def get_model_entry(self, model_id: str) -> ModelEntry | None:
        """Look up a model entry by ID."""
        for entry in self.model_registry:
            if entry.id == model_id:
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "openrouter_api_key": self.openrouter_api_key,
            "anthropic_api_key": self.anthropic_api_key,
            "model_registry": [m.to_dict() for m in self.model_registry],
            "default_model": self.default_model,
        }

    @staticmethod
    def from_dict(d: dict) -> StepwiseConfig:
        return StepwiseConfig(
            openrouter_api_key=d.get("openrouter_api_key"),
            anthropic_api_key=d.get("anthropic_api_key"),
            model_registry=[ModelEntry.from_dict(m) for m in d.get("model_registry", [])],
            default_model=d.get("default_model"),
        )


def load_config() -> StepwiseConfig:
    """Load config from disk, or return defaults if no config file."""
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text())
        return StepwiseConfig.from_dict(data)
    return StepwiseConfig()


def save_config(config: StepwiseConfig) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
