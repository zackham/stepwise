"""Tests for Stepwise configuration: model labels, resolution, persistence, hierarchy."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from stepwise.config import (
    DEFAULT_LABELS,
    DEFAULT_LABEL_NAMES,
    LabelInfo,
    ModelEntry,
    StepwiseConfig,
    load_config,
    load_config_with_sources,
    save_config,
    save_project_config,
    save_project_local_config,
    validate_label_name,
    parse_label_value,
    label_model_id,
    CONFIG_FILE,
    CONFIG_FILE_YAML,
)


# ── Label validation ────────────────────────────────────────────────


class TestLabelValidation:
    def test_valid_names(self):
        assert validate_label_name("fast")
        assert validate_label_name("balanced")
        assert validate_label_name("my-model")
        assert validate_label_name("code_gen")
        assert validate_label_name("a123")

    def test_invalid_names(self):
        assert not validate_label_name("")
        assert not validate_label_name("Fast")  # uppercase
        assert not validate_label_name("my/model")  # slash
        assert not validate_label_name("my:model")  # colon
        assert not validate_label_name("my@model")  # at
        assert not validate_label_name("123abc")  # starts with digit
        assert not validate_label_name("-abc")  # starts with hyphen


class TestParseLabel:
    def test_string_shorthand(self):
        assert parse_label_value("google/gemini-3-flash-preview") == {"model": "google/gemini-3-flash-preview"}

    def test_dict_form(self):
        d = {"model": "google/gemini-3-flash-preview"}
        assert parse_label_value(d) == d

    def test_label_model_id(self):
        assert label_model_id("google/gemini-3-flash-preview") == "google/gemini-3-flash-preview"
        assert label_model_id({"model": "openai/gpt-4o"}) == "openai/gpt-4o"


# ── ModelEntry ───────────────────────────────────────────────────────


class TestModelEntry:
    def test_round_trip(self):
        entry = ModelEntry(id="openai/gpt-4o", name="GPT-4o", provider="openai")
        d = entry.to_dict()
        restored = ModelEntry.from_dict(d)
        assert restored.id == "openai/gpt-4o"
        assert restored.name == "GPT-4o"

    def test_no_tier_field(self):
        entry = ModelEntry(id="x/model", name="Model", provider="x")
        d = entry.to_dict()
        assert "tier" not in d


# ── StepwiseConfig ───────────────────────────────────────────────────


class TestStepwiseConfig:
    def _config_with_labels(self):
        return StepwiseConfig(
            openrouter_api_key="sk-test",
            model_registry=[
                ModelEntry("google/gemini-3-flash-preview", "Claude Sonnet", "anthropic"),
                ModelEntry("openai/gpt-4o", "GPT-4o", "openai"),
            ],
            default_model="balanced",
            default_agent="codex",
            labels={
                "code": "google/gemini-3-flash-preview",
            },
        )

    def test_resolve_default_label(self):
        cfg = StepwiseConfig()
        assert cfg.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview"
        assert cfg.resolve_model("balanced") == "google/gemini-3-flash-preview"
        assert cfg.resolve_model("strong") == "google/gemini-3.1-pro-preview"

    def test_resolve_custom_label(self):
        cfg = self._config_with_labels()
        assert cfg.resolve_model("code") == "google/gemini-3-flash-preview"

    def test_resolve_concrete_id(self):
        cfg = self._config_with_labels()
        assert cfg.resolve_model("anthropic/claude-opus-4") == "anthropic/claude-opus-4"

    def test_resolve_unknown_passes_through(self):
        """Unknown strings pass through (error surfaces at provider)."""
        cfg = StepwiseConfig()
        assert cfg.resolve_model("unknown-model") == "unknown-model"

    def test_resolve_label_overrides_default(self):
        cfg = StepwiseConfig(labels={"fast": "openai/gpt-4o-mini"})
        assert cfg.resolve_model("fast") == "openai/gpt-4o-mini"

    def test_resolve_ollama_style(self):
        """Ollama-style IDs without '/' pass through when not a label."""
        cfg = StepwiseConfig()
        assert cfg.resolve_model("llama3") == "llama3"

    def test_get_model_entry(self):
        cfg = self._config_with_labels()
        entry = cfg.get_model_entry("openai/gpt-4o")
        assert entry is not None
        assert entry.name == "GPT-4o"

    def test_get_model_entry_missing(self):
        cfg = self._config_with_labels()
        assert cfg.get_model_entry("nonexistent/model") is None

    def test_round_trip(self):
        cfg = self._config_with_labels()
        d = cfg.to_dict()
        restored = StepwiseConfig.from_dict(d)
        assert restored.openrouter_api_key == "sk-test"
        assert len(restored.model_registry) == 2
        assert restored.default_model == "balanced"
        assert restored.default_agent == "codex"
        assert restored.labels == {"code": "google/gemini-3-flash-preview"}

    def test_empty_config(self):
        cfg = StepwiseConfig()
        assert cfg.openrouter_api_key is None
        assert cfg.model_registry == []
        assert cfg.default_model is None
        assert cfg.labels == {}

    def test_migration_from_tier_format(self):
        """Old config with tier fields should be migrated to labels."""
        old_data = {
            "openrouter_api_key": "sk-test",
            "model_registry": [
                {"id": "google/gemini-3-flash-preview", "name": "Sonnet", "provider": "anthropic", "tier": "fast"},
                {"id": "openai/gpt-4o", "name": "GPT-4o", "provider": "openai", "tier": "balanced"},
            ],
        }
        cfg = StepwiseConfig.from_dict(old_data)
        assert cfg.labels == {"fast": "google/gemini-3-flash-preview", "balanced": "openai/gpt-4o"}
        assert cfg.resolve_model("fast") == "google/gemini-3-flash-preview"

    def test_to_dict_omits_none(self):
        cfg = StepwiseConfig()
        d = cfg.to_dict()
        assert "openrouter_api_key" not in d
        assert "model_registry" not in d


# ── Config hierarchy ─────────────────────────────────────────────────


class TestConfigHierarchy:
    def test_defaults_present(self, tmp_path):
        """Default labels are present even with empty configs."""
        with patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "config.yaml"), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config()
        assert cfg.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview"
        assert cfg.resolve_model("balanced") == "google/gemini-3-flash-preview"
        assert cfg.resolve_model("strong") == "google/gemini-3.1-pro-preview"
        assert cfg.default_model == "balanced"
        assert cfg.default_agent == "claude"

    def test_user_overrides_defaults(self, tmp_path):
        """User-level labels override defaults."""
        user_yaml = tmp_path / "config.yaml"
        user_yaml.write_text(yaml.dump({"labels": {"fast": "openai/gpt-4o-mini"}}))
        with patch("stepwise.config.CONFIG_FILE_YAML", user_yaml), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config()
        assert cfg.resolve_model("fast") == "openai/gpt-4o-mini"
        assert cfg.resolve_model("balanced") == "google/gemini-3-flash-preview"  # still default

    def test_project_overrides_user(self, tmp_path):
        """Project-level labels override user-level."""
        user_yaml = tmp_path / "config.yaml"
        user_yaml.write_text(yaml.dump({"labels": {"fast": "openai/gpt-4o-mini"}}))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot = project_dir / ".stepwise"
        dot.mkdir()
        (dot / "config.yaml").write_text(yaml.dump({"labels": {"fast": "google/gemini-3.1-flash-lite-preview-8b"}}))

        with patch("stepwise.config.CONFIG_FILE_YAML", user_yaml), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config(project_dir)
        assert cfg.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview-8b"

    def test_local_overrides_project(self, tmp_path):
        """Project-local labels override project."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot = project_dir / ".stepwise"
        dot.mkdir()
        (dot / "config.yaml").write_text(yaml.dump({"labels": {"fast": "model-a"}}))
        (dot / "config.local.yaml").write_text(yaml.dump({"labels": {"fast": "model-b"}}))

        with patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "config.yaml"), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config(project_dir)
        assert cfg.resolve_model("fast") == "model-b"

    def test_custom_labels_merge(self, tmp_path):
        """Custom labels from project merge with defaults."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot = project_dir / ".stepwise"
        dot.mkdir()
        (dot / "config.yaml").write_text(yaml.dump({"labels": {"code": "google/gemini-3-flash-preview"}}))

        with patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "config.yaml"), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config(project_dir)
        # Defaults still work
        assert cfg.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview"
        # Custom label works
        assert cfg.resolve_model("code") == "google/gemini-3-flash-preview"

    def test_api_key_cascade(self, tmp_path):
        """API keys cascade: local > project > user."""
        user_yaml = tmp_path / "config.yaml"
        user_yaml.write_text(yaml.dump({"openrouter_api_key": "user-key"}))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot = project_dir / ".stepwise"
        dot.mkdir()
        (dot / "config.yaml").write_text(yaml.dump({}))
        (dot / "config.local.yaml").write_text(yaml.dump({"openrouter_api_key": "local-key"}))

        with patch("stepwise.config.CONFIG_FILE_YAML", user_yaml), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config(project_dir)
        assert cfg.openrouter_api_key == "local-key"


# ── Config with sources ──────────────────────────────────────────────


class TestConfigWithSources:
    def test_label_sources(self, tmp_path):
        user_yaml = tmp_path / "config.yaml"
        user_yaml.write_text(yaml.dump({"labels": {"fast": "user-fast-model"}}))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot = project_dir / ".stepwise"
        dot.mkdir()
        (dot / "config.yaml").write_text(yaml.dump({
            "labels": {"code": "google/gemini-3-flash-preview"}
        }))
        (dot / "config.local.yaml").write_text(yaml.dump({}))

        with patch("stepwise.config.CONFIG_FILE_YAML", user_yaml), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cs = load_config_with_sources(project_dir)

        by_name = {li.name: li for li in cs.label_info}
        assert by_name["fast"].source == "user"
        assert by_name["fast"].model == "user-fast-model"
        assert by_name["balanced"].source == "default"
        assert by_name["strong"].source == "default"
        assert by_name["code"].source == "project"
        assert by_name["code"].is_default is False
        assert by_name["fast"].is_default is True

    def test_api_key_source(self, tmp_path):
        user_yaml = tmp_path / "config.yaml"
        user_yaml.write_text(yaml.dump({"openrouter_api_key": "sk-user"}))

        with patch("stepwise.config.CONFIG_FILE_YAML", user_yaml), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cs = load_config_with_sources()
        assert cs.api_key_source == "user"


# ── Persistence ──────────────────────────────────────────────────────


class TestConfigPersistence:
    def test_save_and_load_json(self, tmp_path):
        """Legacy JSON format save/load."""
        config_file = tmp_path / "config.json"
        cfg = StepwiseConfig(
            openrouter_api_key="sk-test-123",
            model_registry=[
                ModelEntry("test/model", "Test", "test"),
            ],
            default_model="fast",
            labels={"custom": "test/model"},
        )

        with patch("stepwise.config.CONFIG_FILE", config_file), \
             patch("stepwise.config.CONFIG_DIR", tmp_path), \
             patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "config.yaml"):
            save_config(cfg)
            loaded = load_config()

        assert loaded.openrouter_api_key == "sk-test-123"
        # Registry includes user model + auto-seeded default label models
        user_model_ids = {m.id for m in loaded.model_registry}
        assert "test/model" in user_model_ids
        assert "google/gemini-3.1-flash-lite-preview" in user_model_ids  # auto-seeded from "fast" label
        # loaded via load_config() includes defaults
        assert loaded.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview"
        assert loaded.resolve_model("custom") == "test/model"

    def test_load_missing_file(self, tmp_path):
        with patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "nonexistent.yaml"), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "nonexistent.json"):
            cfg = load_config()
        assert cfg.openrouter_api_key is None
        # Defaults still work
        assert cfg.resolve_model("fast") == "google/gemini-3.1-flash-lite-preview"

    def test_yaml_preferred_over_json(self, tmp_path):
        """YAML config takes precedence over JSON."""
        (tmp_path / "config.json").write_text(json.dumps({
            "openrouter_api_key": "json-key",
        }))
        (tmp_path / "config.yaml").write_text(yaml.dump({
            "openrouter_api_key": "yaml-key",
        }))
        with patch("stepwise.config.CONFIG_FILE_YAML", tmp_path / "config.yaml"), \
             patch("stepwise.config.CONFIG_FILE", tmp_path / "config.json"):
            cfg = load_config()
        assert cfg.openrouter_api_key == "yaml-key"


class TestProjectConfig:
    def test_save_and_load_project_config(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".stepwise").mkdir()

        save_project_config(
            project_dir,
            {"code": "google/gemini-3-flash-preview"},
            "balanced",
            "codex",
        )

        path = project_dir / ".stepwise" / "config.yaml"
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["labels"]["code"] == "google/gemini-3-flash-preview"
        assert data["default_model"] == "balanced"
        assert data["default_agent"] == "codex"

    def test_save_project_local_config(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".stepwise").mkdir()

        save_project_local_config(project_dir, openrouter_api_key="sk-local")

        path = project_dir / ".stepwise" / "config.local.yaml"
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["openrouter_api_key"] == "sk-local"
