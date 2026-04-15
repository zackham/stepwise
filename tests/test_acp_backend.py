"""Tests for stepwise.acp_backend — ACPBackend integration tests.

Uses the mock ACP server from Phase 0 as the agent subprocess.
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

from stepwise.acp_backend import ACPBackend, ACPProcess
from stepwise.agent import AgentProcess, AgentStatus
from stepwise.agent_registry import (
    AgentConfig,
    ConfigKey,
    ResolvedAgentConfig,
    set_user_agents,
)
from stepwise.executors import ExecutionContext

MOCK_SERVER = [sys.executable, "tests/mock_acp_server.py"]


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_agents():
    """Register a 'mock' agent that points to our mock server."""
    set_user_agents({
        "mock": AgentConfig(
            name="mock",
            command=MOCK_SERVER,
            config={},
        ),
    })
    yield
    set_user_agents({})


@pytest.fixture
def backend():
    b = ACPBackend()
    yield b
    b.cleanup()


@pytest.fixture
def context(tmp_path):
    return ExecutionContext(
        job_id="job-test-123",
        step_name="test-step",
        attempt=1,
        workspace_path=str(tmp_path),
        idempotency="test-idemp",
    )


def _make_config(agent: str = "mock", **overrides) -> dict:
    return {"agent": agent, **overrides}


# ── spawn() ───────────────────────────────────────────────────────────


class TestSpawn:
    def test_spawn_creates_process_and_session(self, backend, context):
        result = backend.spawn("Hello", _make_config(), context)
        assert isinstance(result, AgentProcess)
        assert result.pid > 0
        assert result.session_id
        assert result.output_path
        assert result.agent == "mock"

    def test_spawn_with_named_session(self, backend, context):
        config = _make_config(_session_name="step-job-test-1")
        result = backend.spawn("Hello", config, context)
        assert result.session_name == "step-job-test-1"

    def test_spawn_writes_output_file(self, backend, context):
        result = backend.spawn("Hello from test", _make_config(), context)
        # Output file should exist and contain NDJSON
        assert os.path.exists(result.output_path)
        with open(result.output_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) >= 1

        # Should have parseable NDJSON
        for line in lines:
            json.loads(line)  # Should not raise

    def test_spawn_writes_prompt_file(self, backend, context):
        backend.spawn("Test prompt content", _make_config(), context)
        prompt_file = (
            context.workspace_path + "/.stepwise/step-io/job-test-123/"
            + "test-step-1.prompt.md"
        )
        assert os.path.exists(prompt_file)
        with open(prompt_file) as f:
            assert f.read() == "Test prompt content"


class TestProcessReuse:
    def test_reuses_process_for_same_config(self, backend, context):
        r1 = backend.spawn("First", _make_config(), context)

        context2 = ExecutionContext(
            job_id="job-test-123",
            step_name="step-b",
            attempt=1,
            workspace_path=context.workspace_path,
            idempotency="test-idemp-2",
        )
        r2 = backend.spawn("Second", _make_config(), context2)

        # Same PID means same process was reused
        assert r1.pid == r2.pid
        assert len(backend.lifecycle.active) == 1

    def test_creates_new_process_for_different_config(self, backend, context):
        # Register two different mock agents
        set_user_agents({
            "mock": AgentConfig(name="mock", command=MOCK_SERVER, config={}),
            "mock2": AgentConfig(name="mock2", command=MOCK_SERVER, config={}),
        })

        r1 = backend.spawn("First", _make_config("mock"), context)

        context2 = ExecutionContext(
            job_id="job-test-123",
            step_name="step-b",
            attempt=1,
            workspace_path=context.workspace_path,
            idempotency="test-idemp-2",
        )
        r2 = backend.spawn("Second", _make_config("mock2"), context2)

        # Different agents = different processes
        assert r1.pid != r2.pid
        assert len(backend.lifecycle.active) == 2


# ── wait() ────────────────────────────────────────────────────────────


class TestWait:
    def test_wait_returns_completed(self, backend, context):
        process = backend.spawn("Hello", _make_config(), context)
        status = backend.wait(process)
        assert isinstance(status, AgentStatus)
        assert status.state == "completed"
        assert status.exit_code == 0

    def test_wait_extracts_cost(self, backend, context):
        process = backend.spawn("Hello", _make_config(), context)
        status = backend.wait(process)
        # Mock server emits a usage_update with cost
        assert status.cost_usd is not None
        assert status.cost_usd > 0


# ── check() ───────────────────────────────────────────────────────────


class TestCheck:
    def test_check_completed(self, backend, context):
        process = backend.spawn("Hello", _make_config(), context)
        status = backend.check(process)
        # After spawn completes, check should see completed
        assert status.state == "completed"

    def test_check_nonexistent_output(self, backend, context):
        fake = AgentProcess(
            pid=1, pgid=1,
            output_path="/nonexistent/output.jsonl",
            working_dir="/tmp",
        )
        status = backend.check(fake)
        assert status.state == "running"


# ── cancel() ──────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_does_not_crash(self, backend, context):
        process = backend.spawn("Hello", _make_config(), context)
        # Cancel after completion should not raise
        backend.cancel(process)


# ── cleanup() ─────────────────────────────────────────────────────────


class TestCleanup:
    def test_cleanup_kills_all_processes(self, backend, context):
        backend.spawn("Hello", _make_config(), context)
        assert len(backend.lifecycle.active) == 1

        backend.cleanup()
        assert len(backend.lifecycle.active) == 0

    def test_pid_files_cleaned_up(self, backend, context, tmp_path):
        process = backend.spawn("Hello", _make_config(), context)
        # Check for PID file
        pid_dir = tmp_path / ".stepwise" / "pids"
        # PID files are created in CWD, which is the test runner's CWD
        # Just verify cleanup doesn't crash
        backend.cleanup()


# ── Lazy containment backend init ─────────────────────────────────────


class TestLazyContainmentInit:
    """Regression pin for the bug caught in the Tier 3 containment staircase
    (2026-04-14): flow-YAML `containment: cloud-hypervisor` silently no-op'd
    because ACPBackend.containment was only set from stepwise-level config.
    Agents ran on the host regardless, and Tier 1 + 2 "passed" without any
    real VM being spun up.
    """

    def test_no_containment_requested_no_backend_created(self):
        b = ACPBackend()
        assert b.containment is None

    def test_ensure_creates_cloud_hypervisor_backend(self, monkeypatch):
        b = ACPBackend()
        assert b.containment is None

        # Stub the import so we don't actually try to talk to vmmd.
        import stepwise.containment.cloud_hypervisor as ch_module

        class StubBackend:
            pass

            def __init__(self):
                self.instantiated = True

        monkeypatch.setattr(ch_module, "CloudHypervisorBackend", StubBackend)

        result = b._ensure_containment_backend("cloud-hypervisor")
        assert result is b.containment
        assert isinstance(result, StubBackend)

        # Second call should return the cached backend, not re-instantiate.
        result2 = b._ensure_containment_backend("cloud-hypervisor")
        assert result2 is result

    def test_ensure_rejects_unknown_mode(self):
        b = ACPBackend()
        with pytest.raises(RuntimeError, match="Unknown containment mode"):
            b._ensure_containment_backend("docker-desktop")


# ── NDJSON output compatibility ───────────────────────────────────────


class TestNdjsonOutput:
    def test_output_parseable_by_acp_ndjson(self, backend, context):
        """Output file can be read by acp_ndjson module."""
        from stepwise.acp_ndjson import extract_cost, extract_final_text

        process = backend.spawn("Test message", _make_config(), context)

        text = extract_final_text(process.output_path)
        assert "Test message" in text  # Mock echoes the prompt

        cost = extract_cost(process.output_path)
        assert cost is not None


# ── supports_resume ───────────────────────────────────────────────────


class TestSupportsResume:
    def test_supports_resume(self, backend):
        assert backend.supports_resume is True


# ── aloop credentials resolution for containment ──────────────────────


class TestResolveAloopOpenrouterKey:
    """_resolve_aloop_openrouter_key reads host ~/.aloop/credentials.json
    into env when OPENROUTER_API_KEY isn't already set. This is needed
    because aloop inside a VM can't see the host credentials file."""

    def test_reads_key_from_credentials_file(self, tmp_path, monkeypatch):
        from stepwise.acp_backend import _resolve_aloop_openrouter_key

        creds_dir = tmp_path / ".aloop"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text(
            json.dumps({"api_key": "sk-or-v1-test-xyz"})
        )
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env: dict[str, str] = {}
        _resolve_aloop_openrouter_key(env)
        assert env.get("OPENROUTER_API_KEY") == "sk-or-v1-test-xyz"

    def test_respects_env_precedence(self, tmp_path, monkeypatch):
        """If OPENROUTER_API_KEY is already in env, don't overwrite."""
        from stepwise.acp_backend import _resolve_aloop_openrouter_key

        creds_dir = tmp_path / ".aloop"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text(
            json.dumps({"api_key": "file-key"})
        )
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env = {"OPENROUTER_API_KEY": "env-key"}
        _resolve_aloop_openrouter_key(env)
        assert env["OPENROUTER_API_KEY"] == "env-key"

    def test_noop_when_file_missing(self, tmp_path, monkeypatch):
        from stepwise.acp_backend import _resolve_aloop_openrouter_key
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env: dict[str, str] = {}
        _resolve_aloop_openrouter_key(env)
        assert "OPENROUTER_API_KEY" not in env

    def test_noop_on_bad_json(self, tmp_path, monkeypatch):
        from stepwise.acp_backend import _resolve_aloop_openrouter_key

        creds_dir = tmp_path / ".aloop"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text("not valid json {")
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env: dict[str, str] = {}
        _resolve_aloop_openrouter_key(env)
        assert "OPENROUTER_API_KEY" not in env

    def test_noop_when_key_empty(self, tmp_path, monkeypatch):
        """Credentials file exists but api_key is empty string — don't set."""
        from stepwise.acp_backend import _resolve_aloop_openrouter_key

        creds_dir = tmp_path / ".aloop"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text(json.dumps({"api_key": ""}))
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env: dict[str, str] = {}
        _resolve_aloop_openrouter_key(env)
        assert "OPENROUTER_API_KEY" not in env

    def test_noop_when_key_missing(self, tmp_path, monkeypatch):
        """Credentials file exists but has no api_key field."""
        from stepwise.acp_backend import _resolve_aloop_openrouter_key

        creds_dir = tmp_path / ".aloop"
        creds_dir.mkdir()
        (creds_dir / "credentials.json").write_text(json.dumps({"other": "x"}))
        monkeypatch.setattr("stepwise.acp_backend.Path.home", lambda: tmp_path)

        env: dict[str, str] = {}
        _resolve_aloop_openrouter_key(env)
        assert "OPENROUTER_API_KEY" not in env
