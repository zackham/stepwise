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
    "x-ai/grok-4.20-beta",
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
    default_agent: str | None = None  # "claude" | "codex" | etc.
    notify_url: str | None = None
    notify_context: dict = field(default_factory=dict)
    max_concurrent_jobs: int = 10
    max_concurrent_agents: int = 3
    max_concurrent_by_executor: dict[str, int] = field(default_factory=dict)
    # Per-agent-NAME concurrency caps (claude, codex, aloop, ...).
    # Distinct from max_concurrent_by_executor["agent"] which is a
    # single cap across ALL agents regardless of which agent. Use this
    # when you want "max 3 claude running at once even if there are
    # codex + aloop slots free" — e.g. to protect a rate-limited
    # subscription.
    # 0 or missing = no per-agent cap (only the type-level cap applies).
    max_concurrent_by_agent: dict[str, int] = field(default_factory=dict)
    agent_permissions: str = "approve_all"  # "approve_all" | "prompt" | "deny"
    agent_containment: str | None = None  # "cloud-hypervisor" | None
    agent_process_ttl: int = 0  # seconds; 0 = disabled (no limit). Safety net for zombie processes.

    def resolve_model(self, model_ref: str) -> str:
        """Resolve a model reference (label or concrete ID) to a concrete model ID.

        Lookup-first: check labels (including defaults), then pass through as concrete ID.
        """
        all_labels = {**DEFAULT_LABELS, **self.labels}
        if model_ref in all_labels:
            return label_model_id(all_labels[model_ref])
        return model_ref

    def resolved_executor_limits(self) -> dict[str, int]:
        """Effective per-executor-type concurrency limits.

        max_concurrent_agents seeds the agent limit; max_concurrent_by_executor overlays.
        """
        limits: dict[str, int] = {}
        if self.max_concurrent_agents > 0:
            limits["agent"] = self.max_concurrent_agents
        limits.update(self.max_concurrent_by_executor)
        return {k: v for k, v in limits.items() if v > 0}

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
        if self.default_agent is not None:
            d["default_agent"] = self.default_agent
        if self.notify_url is not None:
            d["notify_url"] = self.notify_url
        if self.notify_context:
            d["notify_context"] = self.notify_context
        if self.max_concurrent_jobs != 10:
            d["max_concurrent_jobs"] = self.max_concurrent_jobs
        if self.max_concurrent_agents != 3:
            d["max_concurrent_agents"] = self.max_concurrent_agents
        if self.max_concurrent_by_executor:
            d["max_concurrent_by_executor"] = self.max_concurrent_by_executor
        if self.max_concurrent_by_agent:
            d["max_concurrent_by_agent"] = self.max_concurrent_by_agent
        if self.agent_permissions != "approve_all":
            d["agent_permissions"] = self.agent_permissions
        if self.agent_containment:
            d["agent_containment"] = self.agent_containment
        if self.agent_process_ttl != 0:
            d["agent_process_ttl"] = self.agent_process_ttl
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
        raw_limits = d.get("max_concurrent_by_executor", {})
        if not isinstance(raw_limits, dict):
            raw_limits = {}
        validated_limits = {}
        for k, v in raw_limits.items():
            if isinstance(v, int) and v >= 0:
                validated_limits[str(k)] = v

        raw_agent_limits = d.get("max_concurrent_by_agent", {})
        if not isinstance(raw_agent_limits, dict):
            raw_agent_limits = {}
        validated_agent_limits: dict[str, int] = {}
        for k, v in raw_agent_limits.items():
            if isinstance(v, int) and v > 0:
                validated_agent_limits[str(k)] = v

        return StepwiseConfig(
            openrouter_api_key=d.get("openrouter_api_key"),
            anthropic_api_key=d.get("anthropic_api_key"),
            model_registry=model_registry,
            default_model=d.get("default_model"),
            labels=labels,
            billing=d.get("billing", "subscription"),
            default_agent=d.get("default_agent"),
            notify_url=d.get("notify_url"),
            notify_context=d.get("notify_context", {}),
            max_concurrent_jobs=d.get("max_concurrent_jobs", 10),
            max_concurrent_agents=d.get("max_concurrent_agents", 3),
            max_concurrent_by_executor=validated_limits,
            max_concurrent_by_agent=validated_agent_limits,
            agent_permissions=d.get("agent_permissions", "approve_all"),
            agent_containment=d.get("agent_containment"),
            agent_process_ttl=d.get("agent_process_ttl", 0),
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


def _load_user_agents(project_dir: Path | None = None) -> None:
    """Load user-defined agents from all config levels and register them."""
    from stepwise.agent_registry import load_user_agents_from_config, set_user_agents

    merged: dict = {}
    # User level
    user_data = _load_yaml_or_json(CONFIG_FILE_YAML, CONFIG_FILE)
    merged.update(load_user_agents_from_config(user_data))
    # Project + local levels
    if project_dir:
        proj_path = project_dir / ".stepwise" / "config.yaml"
        if proj_path.exists():
            proj_data = yaml.safe_load(proj_path.read_text()) or {}
            merged.update(load_user_agents_from_config(proj_data))
        local_path = project_dir / ".stepwise" / "config.local.yaml"
        if local_path.exists():
            local_data = yaml.safe_load(local_path.read_text()) or {}
            merged.update(load_user_agents_from_config(local_data))
    set_user_agents(merged)


def load_config(project_dir: Path | None = None) -> StepwiseConfig:
    """Load merged config from all levels.

    Args:
        project_dir: Project root directory. If None, only user config is loaded.

    Returns:
        Merged StepwiseConfig with defaults < user < project < local.
    """
    # Load user-defined agents into the agent registry
    _load_user_agents(project_dir)

    user = _load_user_config()
    project = _load_project_config(project_dir) if project_dir else StepwiseConfig()
    local = _load_project_local_config(project_dir) if project_dir else StepwiseConfig()

    # Merge labels: defaults < user < project < local
    labels: dict[str, str | dict] = dict(DEFAULT_LABELS)
    labels.update(user.labels)
    labels.update(project.labels)
    labels.update(local.labels)

    # Merge executor limits: user < project < local
    executor_limits: dict[str, int] = {}
    executor_limits.update(user.max_concurrent_by_executor)
    executor_limits.update(project.max_concurrent_by_executor)
    executor_limits.update(local.max_concurrent_by_executor)

    # Merge per-agent-name limits: user < project < local
    agent_limits: dict[str, int] = {}
    agent_limits.update(user.max_concurrent_by_agent)
    agent_limits.update(project.max_concurrent_by_agent)
    agent_limits.update(local.max_concurrent_by_agent)

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
        default_agent=(local.default_agent or project.default_agent
                       or user.default_agent or "claude"),
        notify_url=(local.notify_url or project.notify_url or user.notify_url),
        notify_context=(local.notify_context or project.notify_context
                        or user.notify_context),
        max_concurrent_jobs=next(
            (l.max_concurrent_jobs for l in (local, project, user)
             if l.max_concurrent_jobs != 10),
            10,
        ),
        max_concurrent_agents=next(
            (l.max_concurrent_agents for l in (local, project, user)
             if l.max_concurrent_agents != 3),
            3,
        ),
        max_concurrent_by_executor=executor_limits,
        max_concurrent_by_agent=agent_limits,
        agent_permissions=next(
            (l.agent_permissions for l in (local, project, user)
             if l.agent_permissions != "approve_all"),
            "approve_all",
        ),
        agent_process_ttl=next(
            (l.agent_process_ttl for l in (local, project, user)
             if l.agent_process_ttl != 0),
            0,
        ),
        agent_containment=(local.agent_containment or project.agent_containment
                           or user.agent_containment),
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

    billing = "subscription"
    for level in (user, project, local):
        if level.billing != "subscription":
            billing = level.billing

    config = StepwiseConfig(
        openrouter_api_key=(local.openrouter_api_key or project.openrouter_api_key
                            or user.openrouter_api_key),
        anthropic_api_key=(local.anthropic_api_key or project.anthropic_api_key
                           or user.anthropic_api_key),
        model_registry=registry,
        default_model=(local.default_model or project.default_model
                       or user.default_model or "balanced"),
        labels=merged_labels,
        billing=billing,
        default_agent=(local.default_agent or project.default_agent
                       or user.default_agent or "claude"),
        notify_url=(local.notify_url or project.notify_url or user.notify_url),
        notify_context=(local.notify_context or project.notify_context
                        or user.notify_context),
        agent_containment=(local.agent_containment or project.agent_containment
                           or user.agent_containment),
        max_concurrent_by_agent={
            **user.max_concurrent_by_agent,
            **project.max_concurrent_by_agent,
            **local.max_concurrent_by_agent,
        },
    )

    return ConfigWithSources(config=config, label_info=label_info, api_key_source=api_key_source)


# ── Config saving ────────────────────────────────────────────────────


def save_config(config: StepwiseConfig) -> None:
    """Save config to user level (legacy JSON format for backward compat)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config.to_dict(), indent=2) + "\n")


def save_project_config(project_dir: Path, labels: dict[str, str | dict],
                         default_model: str | None = None,
                         default_agent: str | None = None) -> None:
    """Save labels and settings to project config (.stepwise/config.yaml)."""
    path = project_dir / ".stepwise" / "config.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    data["labels"] = labels
    if default_model is not None:
        data["default_model"] = default_model
    if default_agent is not None:
        data["default_agent"] = default_agent
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


def save_agents_to_local_config(project_dir: Path, agents_data: dict) -> None:
    """Update only the ``agents:`` key in project-local config.

    Reads ``.stepwise/config.local.yaml``, replaces just the ``agents``
    section with *agents_data*, and writes back without clobbering other
    settings.
    """
    path = project_dir / ".stepwise" / "config.local.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    data["agents"] = agents_data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
