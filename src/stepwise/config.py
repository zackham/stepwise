"""Stepwise configuration: model labels, API keys, model registry.

Config hierarchy (each level overrides the previous):
    1. Defaults (hardcoded)
    2. User level (~/.config/stepwise/config.yaml or config.json)
    3. Project level (.stepwise/config.yaml) — committed to git
    4. Project local (.stepwise/config.local.yaml) — gitignored
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR = Path.home() / ".config" / "stepwise"
CONFIG_FILE = CONFIG_DIR / "config.json"  # legacy, still read
CONFIG_FILE_YAML = CONFIG_DIR / "config.yaml"

LABEL_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9_-]{0,62}$')

DEFAULT_LABELS: dict[str, str] = {
    "fast": "google/gemini-3.1-flash-lite-preview",
    "balanced": "google/gemini-3-flash-preview",
    "strong": "google/gemini-3.1-pro-preview",
}

DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3-flash-preview",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-4.1-nano",
    "openai/gpt-4.1-mini",
    "openai/gpt-5-mini",
    "openai/gpt-5.4",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "deepseek/deepseek-v3.2",
    "x-ai/grok-4.1-fast",
    "meta-llama/llama-4-maverick",
)

DEFAULT_LABEL_NAMES = frozenset(DEFAULT_LABELS.keys())


def validate_label_name(name: str) -> bool:
    """Check if a label name is valid (lowercase, no /, :, @)."""
    return bool(LABEL_NAME_PATTERN.match(name))


def parse_label_value(value: str | dict) -> dict:
    """Normalize label value to dict form."""
    if isinstance(value, str):
        return {"model": value}
    return value


def label_model_id(value: str | dict) -> str:
    """Extract model ID from a label value (string or dict)."""
    return parse_label_value(value)["model"]


@dataclass
class ModelEntry:
    """A model in the registry."""
    id: str
    name: str
    provider: str
    context_length: int | None = None
    max_output_tokens: int | None = None
    prompt_cost: float | None = None       # USD per token (input)
    completion_cost: float | None = None   # USD per token (output)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "provider": self.provider}
        if self.context_length is not None:
            d["context_length"] = self.context_length
        if self.max_output_tokens is not None:
            d["max_output_tokens"] = self.max_output_tokens
        if self.prompt_cost is not None:
            d["prompt_cost"] = self.prompt_cost
        if self.completion_cost is not None:
            d["completion_cost"] = self.completion_cost
        return d

    @staticmethod
    def from_dict(d: dict) -> ModelEntry:
        return ModelEntry(
            id=d["id"], name=d["name"], provider=d["provider"],
            context_length=d.get("context_length"),
            max_output_tokens=d.get("max_output_tokens"),
            prompt_cost=d.get("prompt_cost"),
            completion_cost=d.get("completion_cost"),
        )


@dataclass
class StepwiseConfig:
    """Configuration from a single level or merged result."""
    openrouter_api_key: str | None = None
    anthropic_api_key: str | None = None
    model_registry: list[ModelEntry] = field(default_factory=list)
    default_model: str | None = None
    labels: dict[str, str | dict] = field(default_factory=dict)
    billing: str = "subscription"  # "subscription" | "api_key"

    def resolve_model(self, model_ref: str) -> str:
        """Resolve a model reference (label or concrete ID) to a concrete model ID.

        Lookup-first: check labels (including defaults), then pass through as concrete ID.
        """
        all_labels = {**DEFAULT_LABELS, **self.labels}
        if model_ref in all_labels:
            return label_model_id(all_labels[model_ref])
        return model_ref

    def get_model_entry(self, model_id: str) -> ModelEntry | None:
        """Look up a model entry by ID."""
        for entry in self.model_registry:
            if entry.id == model_id:
                return entry
        return None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        if self.openrouter_api_key is not None:
            d["openrouter_api_key"] = self.openrouter_api_key
        if self.anthropic_api_key is not None:
            d["anthropic_api_key"] = self.anthropic_api_key
        if self.model_registry:
            d["model_registry"] = [m.to_dict() for m in self.model_registry]
        if self.default_model is not None:
            d["default_model"] = self.default_model
        if self.labels:
            d["labels"] = self.labels
        if self.billing != "subscription":
            d["billing"] = self.billing
        return d

    @staticmethod
    def from_dict(d: dict) -> StepwiseConfig:
        labels = dict(d.get("labels", {}))
        registry_raw = d.get("model_registry", [])
        model_registry = []
        for m in registry_raw:
            # Migrate tier → label if present
            tier = m.get("tier")
            if tier and tier not in labels:
                labels[tier] = m["id"]
            model_registry.append(ModelEntry(
                id=m["id"], name=m["name"], provider=m["provider"]
            ))
        return StepwiseConfig(
            openrouter_api_key=d.get("openrouter_api_key"),
            anthropic_api_key=d.get("anthropic_api_key"),
            model_registry=model_registry,
            default_model=d.get("default_model"),
            labels=labels,
            billing=d.get("billing", "subscription"),
        )


# ── Label info (for settings UI) ────────────────────────────────────


@dataclass
class LabelInfo:
    """A label with its resolution and source tracking."""
    name: str
    model: str
    source: str  # "default", "user", "project", "local"
    is_default: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "model": self.model,
            "source": self.source,
            "is_default": self.is_default,
        }


@dataclass
class ConfigWithSources:
    """Merged config plus source tracking for the settings UI."""
    config: StepwiseConfig
    label_info: list[LabelInfo]
    api_key_source: str | None


# ── Config loading ───────────────────────────────────────────────────


def _load_yaml_or_json(yaml_path: Path, json_path: Path | None = None) -> dict:
    """Load config from YAML (preferred) or JSON (legacy fallback)."""
    if yaml_path.exists():
        return yaml.safe_load(yaml_path.read_text()) or {}
    if json_path and json_path.exists():
        return json.loads(json_path.read_text())
    return {}


def _load_user_config() -> StepwiseConfig:
    """Load user-level config from ~/.config/stepwise/."""
    data = _load_yaml_or_json(CONFIG_FILE_YAML, CONFIG_FILE)
    if not data:
        return StepwiseConfig()
    return StepwiseConfig.from_dict(data)


def _load_project_config(project_dir: Path) -> StepwiseConfig:
    """Load project-level config (.stepwise/config.yaml)."""
    path = project_dir / ".stepwise" / "config.yaml"
    if not path.exists():
        return StepwiseConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return StepwiseConfig.from_dict(data)


def _load_project_local_config(project_dir: Path) -> StepwiseConfig:
    """Load project-local config (.stepwise/config.local.yaml)."""
    path = project_dir / ".stepwise" / "config.local.yaml"
    if not path.exists():
        return StepwiseConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return StepwiseConfig.from_dict(data)


def _model_entry_from_id(model_id: str) -> ModelEntry:
    """Create a ModelEntry from a model ID with auto-generated name/provider."""
    provider = model_id.split("/")[0] if "/" in model_id else "custom"
    raw_name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    display_name = raw_name.replace("-", " ").replace("_", " ").title()
    return ModelEntry(id=model_id, name=display_name, provider=provider)


def _ensure_label_models_in_registry(
    registry: list[ModelEntry],
    labels: dict[str, str | dict],
) -> list[ModelEntry]:
    """Ensure every model referenced by a label is present in the registry."""
    existing_ids = {m.id for m in registry}
    for value in labels.values():
        model_id = label_model_id(value)
        if model_id not in existing_ids:
            registry.append(_model_entry_from_id(model_id))
            existing_ids.add(model_id)
    return registry


def _ensure_defaults_in_registry(registry: list[ModelEntry]) -> list[ModelEntry]:
    """Ensure all DEFAULT_MODEL_IDS are present in the registry."""
    existing_ids = {m.id for m in registry}
    for model_id in DEFAULT_MODEL_IDS:
        if model_id not in existing_ids:
            registry.append(_model_entry_from_id(model_id))
            existing_ids.add(model_id)
    return registry


def load_config(project_dir: Path | None = None) -> StepwiseConfig:
    """Load merged config from all levels.

    Args:
        project_dir: Project root directory. If None, only user config is loaded.

    Returns:
        Merged StepwiseConfig with defaults < user < project < local.
    """
    user = _load_user_config()
    project = _load_project_config(project_dir) if project_dir else StepwiseConfig()
    local = _load_project_local_config(project_dir) if project_dir else StepwiseConfig()

    # Merge labels: defaults < user < project < local
    labels: dict[str, str | dict] = dict(DEFAULT_LABELS)
    labels.update(user.labels)
    labels.update(project.labels)
    labels.update(local.labels)

    registry = list(user.model_registry)
    registry = _ensure_label_models_in_registry(registry, labels)
    registry = _ensure_defaults_in_registry(registry)

    # Merge billing: local > project > user > default
    billing = "subscription"
    for level in (user, project, local):
        if level.billing != "subscription":
            billing = level.billing

    return StepwiseConfig(
        openrouter_api_key=(local.openrouter_api_key or project.openrouter_api_key
                            or user.openrouter_api_key),
        anthropic_api_key=(local.anthropic_api_key or project.anthropic_api_key
                           or user.anthropic_api_key),
        model_registry=registry,
        default_model=(local.default_model or project.default_model
                       or user.default_model or "balanced"),
        labels=labels,
        billing=billing,
    )


def load_config_with_sources(project_dir: Path | None = None) -> ConfigWithSources:
    """Load config with source tracking for each label (settings UI)."""
    user = _load_user_config()
    project = _load_project_config(project_dir) if project_dir else StepwiseConfig()
    local = _load_project_local_config(project_dir) if project_dir else StepwiseConfig()

    # Track label sources
    label_sources: dict[str, str] = {}
    merged_labels: dict[str, str | dict] = {}

    for name, value in DEFAULT_LABELS.items():
        merged_labels[name] = value
        label_sources[name] = "default"
    for name, value in user.labels.items():
        merged_labels[name] = value
        label_sources[name] = "user"
    for name, value in project.labels.items():
        merged_labels[name] = value
        label_sources[name] = "project"
    for name, value in local.labels.items():
        merged_labels[name] = value
        label_sources[name] = "local"

    label_info = [
        LabelInfo(
            name=name,
            model=label_model_id(value),
            source=label_sources[name],
            is_default=name in DEFAULT_LABEL_NAMES,
        )
        for name, value in merged_labels.items()
    ]

    api_key_source = None
    if local.openrouter_api_key:
        api_key_source = "local"
    elif project.openrouter_api_key:
        api_key_source = "project"
    elif user.openrouter_api_key:
        api_key_source = "user"

    registry = list(user.model_registry)
    registry = _ensure_label_models_in_registry(registry, merged_labels)
    registry = _ensure_defaults_in_registry(registry)

    config = StepwiseConfig(
        openrouter_api_key=(local.openrouter_api_key or project.openrouter_api_key
                            or user.openrouter_api_key),
        anthropic_api_key=(local.anthropic_api_key or project.anthropic_api_key
                           or user.anthropic_api_key),
        model_registry=registry,
        default_model=(local.default_model or project.default_model
                       or user.default_model or "balanced"),
        labels=merged_labels,
    )

    return ConfigWithSources(config=config, label_info=label_info, api_key_source=api_key_source)


# ── Config saving ────────────────────────────────────────────────────


def save_config(config: StepwiseConfig) -> None:
    """Save config to user level (legacy JSON format for backward compat)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config.to_dict(), indent=2) + "\n")


def save_project_config(project_dir: Path, labels: dict[str, str | dict],
                         default_model: str | None = None) -> None:
    """Save labels and settings to project config (.stepwise/config.yaml)."""
    path = project_dir / ".stepwise" / "config.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    data["labels"] = labels
    if default_model is not None:
        data["default_model"] = default_model
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def save_project_local_config(project_dir: Path, **kwargs: Any) -> None:
    """Save settings to project-local config (.stepwise/config.local.yaml)."""
    path = project_dir / ".stepwise" / "config.local.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    data.update(kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
