"""Tests for configurable agent permissions."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from stepwise.agent import AcpxBackend
from stepwise.config import StepwiseConfig, load_config
from stepwise.yaml_loader import load_workflow_yaml


# ── Config parsing ────────────────────────────────────────────────


class TestConfigAgentPermissions:
    def test_default_is_approve_all(self):
        config = StepwiseConfig()
        assert config.agent_permissions == "approve_all"

    def test_from_dict(self):
        config = StepwiseConfig.from_dict({"agent_permissions": "deny"})
        assert config.agent_permissions == "deny"

    def test_from_dict_default(self):
        config = StepwiseConfig.from_dict({})
        assert config.agent_permissions == "approve_all"

    def test_to_dict_default_omitted(self):
        config = StepwiseConfig()
        assert "agent_permissions" not in config.to_dict()

    def test_to_dict_non_default_included(self):
        config = StepwiseConfig(agent_permissions="prompt")
        assert config.to_dict()["agent_permissions"] == "prompt"

    def test_roundtrip(self):
        for perm in ("approve_all", "prompt", "deny"):
            config = StepwiseConfig(agent_permissions=perm)
            restored = StepwiseConfig.from_dict(config.to_dict())
            assert restored.agent_permissions == perm

    def test_load_config_merges_permissions(self, tmp_path):
        """Project-level config overrides default."""
        project_dir = tmp_path / "project"
        stepwise_dir = project_dir / ".stepwise"
        stepwise_dir.mkdir(parents=True)
        (stepwise_dir / "config.yaml").write_text(
            yaml.dump({"agent_permissions": "deny"})
        )
        # Patch user config to return default
        with patch("stepwise.config._load_user_config", return_value=StepwiseConfig()):
            config = load_config(project_dir)
        assert config.agent_permissions == "deny"

    def test_load_config_local_overrides_project(self, tmp_path):
        """Local config overrides project config."""
        project_dir = tmp_path / "project"
        stepwise_dir = project_dir / ".stepwise"
        stepwise_dir.mkdir(parents=True)
        (stepwise_dir / "config.yaml").write_text(
            yaml.dump({"agent_permissions": "deny"})
        )
        (stepwise_dir / "config.local.yaml").write_text(
            yaml.dump({"agent_permissions": "prompt"})
        )
        with patch("stepwise.config._load_user_config", return_value=StepwiseConfig()):
            config = load_config(project_dir)
        assert config.agent_permissions == "prompt"


# ── YAML step-level parsing ──────────────────────────────────────


class TestYAMLPermissions:
    def test_permissions_in_agent_step(self):
        yaml_str = """\
name: test-flow
steps:
  do-work:
    executor: agent
    prompt: "Do something"
    permissions: prompt
    outputs: [result]
"""
        wf = load_workflow_yaml(yaml_str)
        assert wf.steps["do-work"].executor.config["permissions"] == "prompt"

    def test_no_permissions_field(self):
        yaml_str = """\
name: test-flow
steps:
  do-work:
    executor: agent
    prompt: "Do something"
    outputs: [result]
"""
        wf = load_workflow_yaml(yaml_str)
        assert "permissions" not in wf.steps["do-work"].executor.config


# ── AcpxBackend flag selection ───────────────────────────────────


class TestAcpxBackendPermissions:
    def test_default_permissions_stored(self):
        backend = AcpxBackend(default_permissions="prompt")
        assert backend.default_permissions == "prompt"

    def test_default_is_approve_all(self):
        backend = AcpxBackend()
        assert backend.default_permissions == "approve_all"

    def _build_args(self, backend, config):
        """Extract the acpx command args that spawn() would build.

        Simulates the permission flag logic without actually spawning a process.
        """
        permissions = config.get("permissions") or backend.default_permissions
        args = [backend.acpx_path, "--format", "json", "--cwd", "/tmp"]
        if permissions == "approve_all":
            args.append("--approve-all")
        elif permissions == "deny":
            args.append("--deny-all")
        # "prompt" → no flag
        return args

    def test_approve_all_flag(self):
        backend = AcpxBackend(default_permissions="approve_all")
        args = self._build_args(backend, {})
        assert "--approve-all" in args
        assert "--deny-all" not in args

    def test_prompt_no_flag(self):
        backend = AcpxBackend(default_permissions="prompt")
        args = self._build_args(backend, {})
        assert "--approve-all" not in args
        assert "--deny-all" not in args

    def test_deny_flag(self):
        backend = AcpxBackend(default_permissions="deny")
        args = self._build_args(backend, {})
        assert "--deny-all" in args
        assert "--approve-all" not in args

    def test_step_level_overrides_default(self):
        backend = AcpxBackend(default_permissions="approve_all")
        args = self._build_args(backend, {"permissions": "deny"})
        assert "--deny-all" in args
        assert "--approve-all" not in args

    def test_step_level_prompt_overrides_approve_all(self):
        backend = AcpxBackend(default_permissions="approve_all")
        args = self._build_args(backend, {"permissions": "prompt"})
        assert "--approve-all" not in args
        assert "--deny-all" not in args
