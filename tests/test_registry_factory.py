"""Tests for stepwise.registry_factory — shared executor registration."""

import pytest
from unittest.mock import patch, MagicMock

from stepwise.config import StepwiseConfig
from stepwise.executors import ExecutorRegistry
from stepwise.registry_factory import create_default_registry


class TestCreateDefaultRegistry:
    """create_default_registry() registers all built-in executor types."""

    def test_registers_core_types(self):
        """Core types (script, human, mock_llm, delegating, agent) always present."""
        registry = create_default_registry(StepwiseConfig())
        assert "script" in registry._factories
        assert "human" in registry._factories
        assert "mock_llm" in registry._factories
        assert "delegating" in registry._factories
        assert "agent" in registry._factories

    def test_skips_llm_without_api_key(self):
        """LLM executor not registered when no API key."""
        registry = create_default_registry(StepwiseConfig(openrouter_api_key=None))
        assert "llm" not in registry._factories

    def test_registers_llm_with_api_key(self):
        """LLM executor registered when API key is present."""
        config = StepwiseConfig(openrouter_api_key="sk-test-key")
        with patch("stepwise.openrouter.OpenRouterClient"):
            registry = create_default_registry(config)
        assert "llm" in registry._factories

    def test_output_matches_server_types(self):
        """Registry has same executor types as server.py registration."""
        # The expected types that server.py registers
        expected_types = {"script", "human", "mock_llm", "delegating", "agent"}
        config = StepwiseConfig()
        registry = create_default_registry(config)
        actual_types = set(registry._factories.keys())
        # Without API key, llm is excluded from both
        assert expected_types == actual_types

    def test_loads_config_from_disk_if_none(self):
        """When config=None, loads from disk."""
        with patch("stepwise.registry_factory.load_config", return_value=StepwiseConfig()) as mock_load:
            registry = create_default_registry(None)
            mock_load.assert_called_once()
        assert "script" in registry._factories

    def test_script_executor_created(self):
        """Script factory produces a ScriptExecutor."""
        from stepwise.executors import ScriptExecutor
        from stepwise.models import ExecutorRef
        registry = create_default_registry(StepwiseConfig())
        ref = ExecutorRef(type="script", config={"command": "echo test"})
        executor = registry.create(ref)
        assert isinstance(executor, ScriptExecutor)

    def test_human_executor_created(self):
        """Human factory produces a HumanExecutor."""
        from stepwise.executors import HumanExecutor
        from stepwise.models import ExecutorRef
        registry = create_default_registry(StepwiseConfig())
        ref = ExecutorRef(type="human", config={"prompt": "Review this"})
        executor = registry.create(ref)
        assert isinstance(executor, HumanExecutor)
