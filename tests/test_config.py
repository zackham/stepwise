"""Tests for Stepwise configuration: model registry, tier resolution, persistence."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.config import (
    ModelEntry,
    StepwiseConfig,
    load_config,
    save_config,
    CONFIG_FILE,
)


# ── ModelEntry ───────────────────────────────────────────────────────


class TestModelEntry:
    def test_round_trip(self):
        entry = ModelEntry(id="openai/gpt-4o", name="GPT-4o", provider="openai", tier="balanced")
        d = entry.to_dict()
        restored = ModelEntry.from_dict(d)
        assert restored.id == "openai/gpt-4o"
        assert restored.tier == "balanced"

    def test_no_tier(self):
        entry = ModelEntry(id="x/model", name="Model", provider="x")
        d = entry.to_dict()
        assert "tier" not in d
        restored = ModelEntry.from_dict(d)
        assert restored.tier is None


# ── StepwiseConfig ───────────────────────────────────────────────────


class TestStepwiseConfig:
    def _config_with_models(self):
        return StepwiseConfig(
            openrouter_api_key="sk-test",
            model_registry=[
                ModelEntry("anthropic/claude-sonnet-4", "Claude Sonnet", "anthropic", "fast"),
                ModelEntry("openai/gpt-4o", "GPT-4o", "openai", "balanced"),
                ModelEntry("anthropic/claude-opus-4", "Claude Opus", "anthropic", "strong"),
            ],
            default_model="balanced",
        )

    def test_resolve_concrete_id(self):
        cfg = self._config_with_models()
        assert cfg.resolve_model("anthropic/claude-opus-4") == "anthropic/claude-opus-4"

    def test_resolve_tier_alias(self):
        cfg = self._config_with_models()
        assert cfg.resolve_model("fast") == "anthropic/claude-sonnet-4"
        assert cfg.resolve_model("balanced") == "openai/gpt-4o"
        assert cfg.resolve_model("strong") == "anthropic/claude-opus-4"

    def test_resolve_unknown_tier_raises(self):
        cfg = self._config_with_models()
        with pytest.raises(ValueError, match="No model found for tier 'turbo'"):
            cfg.resolve_model("turbo")

    def test_resolve_first_match_wins(self):
        """If multiple models share a tier, the first one wins."""
        cfg = StepwiseConfig(model_registry=[
            ModelEntry("a/model-1", "M1", "a", "fast"),
            ModelEntry("b/model-2", "M2", "b", "fast"),
        ])
        assert cfg.resolve_model("fast") == "a/model-1"

    def test_get_model_entry(self):
        cfg = self._config_with_models()
        entry = cfg.get_model_entry("openai/gpt-4o")
        assert entry is not None
        assert entry.name == "GPT-4o"

    def test_get_model_entry_missing(self):
        cfg = self._config_with_models()
        assert cfg.get_model_entry("nonexistent/model") is None

    def test_round_trip(self):
        cfg = self._config_with_models()
        d = cfg.to_dict()
        restored = StepwiseConfig.from_dict(d)
        assert restored.openrouter_api_key == "sk-test"
        assert len(restored.model_registry) == 3
        assert restored.default_model == "balanced"

    def test_empty_config(self):
        cfg = StepwiseConfig()
        assert cfg.openrouter_api_key is None
        assert cfg.model_registry == []
        assert cfg.default_model is None

    def test_resolve_empty_registry_raises(self):
        cfg = StepwiseConfig()
        with pytest.raises(ValueError):
            cfg.resolve_model("fast")


# ── Persistence ──────────────────────────────────────────────────────


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        config_file = tmp_path / "config.json"
        cfg = StepwiseConfig(
            openrouter_api_key="sk-test-123",
            model_registry=[
                ModelEntry("test/model", "Test", "test", "fast"),
            ],
            default_model="fast",
        )

        with patch("stepwise.config.CONFIG_FILE", config_file), \
             patch("stepwise.config.CONFIG_DIR", tmp_path):
            save_config(cfg)
            loaded = load_config()

        assert loaded.openrouter_api_key == "sk-test-123"
        assert len(loaded.model_registry) == 1
        assert loaded.model_registry[0].id == "test/model"

    def test_load_missing_file(self, tmp_path):
        config_file = tmp_path / "nonexistent" / "config.json"
        with patch("stepwise.config.CONFIG_FILE", config_file):
            cfg = load_config()
        assert cfg.openrouter_api_key is None
        assert cfg.model_registry == []

    def test_save_creates_directory(self, tmp_path):
        config_dir = tmp_path / "new" / "nested"
        config_file = config_dir / "config.json"

        with patch("stepwise.config.CONFIG_FILE", config_file), \
             patch("stepwise.config.CONFIG_DIR", config_dir):
            save_config(StepwiseConfig(openrouter_api_key="sk-new"))

        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["openrouter_api_key"] == "sk-new"
