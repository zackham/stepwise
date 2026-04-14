"""Host-toolchain isolation for `_build_agent_env`.

P0 regression: agent subprocesses inherited the stepwise server's full
environment, including `VIRTUAL_ENV`, `PYTHONPATH`, `CONDA_PREFIX`, npm
config, etc. An agent running `pip install`, `npm install`, or similar
would then mutate the HOST's tool installation — corrupting the server's
own dependencies.

`_build_agent_env` now strips known host-toolchain variables and filters
PATH segments that live inside the host venv / conda prefix. A per-step
opt-out (`inherit_host_toolchain: true`) is available for the rare case
where an agent genuinely needs the same environment as the server.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from stepwise.agent import (
    _HOST_TOOLCHAIN_VARS_TO_STRIP,
    _build_agent_env,
    _filter_path,
)
from stepwise.executors import ExecutionContext


def _ctx(workspace: str) -> ExecutionContext:
    return ExecutionContext(
        job_id="job-test-123",
        step_name="test-step",
        attempt=1,
        workspace_path=workspace,
        idempotency="test-step-1",
    )


class TestHostToolchainStripped:
    def test_virtual_env_stripped(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/srv/stepwise/.venv")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "VIRTUAL_ENV" not in env

    def test_python_path_stripped(self, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/srv/stepwise/src:/srv/other")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "PYTHONPATH" not in env

    def test_conda_vars_stripped(self, monkeypatch):
        monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/server")
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "server")
        monkeypatch.setenv("CONDA_SHLVL", "1")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "CONDA_PREFIX" not in env
        assert "CONDA_DEFAULT_ENV" not in env
        assert "CONDA_SHLVL" not in env

    def test_npm_config_vars_stripped(self, monkeypatch):
        monkeypatch.setenv("npm_config_prefix", "/srv/stepwise/node_modules")
        monkeypatch.setenv("npm_config_cache", "/srv/stepwise/.npm")
        monkeypatch.setenv("NPM_CONFIG_PREFIX", "/srv/stepwise/node_modules")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "npm_config_prefix" not in env
        assert "npm_config_cache" not in env
        assert "NPM_CONFIG_PREFIX" not in env

    def test_unrelated_vars_preserved(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret-1234")
        monkeypatch.setenv("NODE_ENV", "production")
        monkeypatch.setenv("HOME", "/home/user")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert env.get("MY_API_KEY") == "secret-1234"
        assert env.get("NODE_ENV") == "production"
        assert "HOME" in env

    def test_stepwise_vars_still_injected(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/srv/stepwise/.venv")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert env.get("STEPWISE_STEP_NAME") == "test-step"
        assert env.get("STEPWISE_ATTEMPT") == "1"
        assert "STEPWISE_STEP_IO" in env


class TestPathFiltering:
    def test_venv_bin_removed_from_path(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/srv/stepwise/.venv")
        monkeypatch.setenv(
            "PATH",
            "/srv/stepwise/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        )
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "/srv/stepwise/.venv/bin" not in env["PATH"].split(":")
        assert "/usr/bin" in env["PATH"].split(":")

    def test_conda_prefix_segments_removed_from_path(self):
        filtered = _filter_path(
            "/opt/conda/envs/server/bin:/opt/conda/bin:/usr/bin",
            host_venv=None,
            host_conda="/opt/conda/envs/server",
        )
        segments = filtered.split(":")
        assert "/opt/conda/envs/server/bin" not in segments
        assert "/opt/conda/bin" in segments  # outside the conda prefix
        assert "/usr/bin" in segments

    def test_filter_path_with_empty_segments(self):
        filtered = _filter_path(
            ":/usr/bin::/usr/local/bin:",
            host_venv=None,
            host_conda=None,
        )
        segments = filtered.split(":")
        assert segments == ["/usr/bin", "/usr/local/bin"]

    def test_filter_path_nothing_to_strip(self):
        filtered = _filter_path(
            "/usr/local/bin:/usr/bin:/bin",
            host_venv="/srv/stepwise/.venv",
            host_conda=None,
        )
        assert filtered == "/usr/local/bin:/usr/bin:/bin"


class TestOptOut:
    def test_inherit_host_toolchain_keeps_virtual_env(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/srv/stepwise/.venv")
        monkeypatch.setenv("PYTHONPATH", "/srv/stepwise/src")
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={"inherit_host_toolchain": True},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert env.get("VIRTUAL_ENV") == "/srv/stepwise/.venv"
        assert env.get("PYTHONPATH") == "/srv/stepwise/src"

    def test_inherit_host_toolchain_keeps_venv_path_segments(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/srv/stepwise/.venv")
        monkeypatch.setenv(
            "PATH",
            "/srv/stepwise/.venv/bin:/usr/bin",
        )
        workspace = tempfile.mkdtemp()
        env = _build_agent_env(
            config={"inherit_host_toolchain": True},
            context=_ctx(workspace),
            step_io=Path(workspace) / ".stepwise" / "step-io",
            working_dir=workspace,
        )
        assert "/srv/stepwise/.venv/bin" in env["PATH"].split(":")


class TestHostToolchainVarsConstant:
    def test_constant_has_python_vars(self):
        assert "VIRTUAL_ENV" in _HOST_TOOLCHAIN_VARS_TO_STRIP
        assert "PYTHONPATH" in _HOST_TOOLCHAIN_VARS_TO_STRIP

    def test_constant_has_conda_vars(self):
        assert "CONDA_PREFIX" in _HOST_TOOLCHAIN_VARS_TO_STRIP

    def test_constant_has_node_vars(self):
        assert "NODE_PATH" in _HOST_TOOLCHAIN_VARS_TO_STRIP
