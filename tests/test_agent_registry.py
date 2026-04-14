"""Tests for stepwise.agent_registry — agent config model, builtins, and resolution."""

import os

import pytest

from stepwise.agent_registry import (
    BUILTIN_AGENTS,
    AgentCapabilities,
    AgentConfig,
    ConfigKey,
    ResolvedAgentConfig,
    get_agent,
    list_agents,
    load_user_agents_from_config,
    resolve_config,
    set_user_agents,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_user_agents():
    """Reset user-defined agents after each test."""
    yield
    set_user_agents({})


# ── Builtin Lookup ────────────────────────────────────────────────────


class TestBuiltinLookup:
    def test_get_claude(self):
        agent = get_agent("claude")
        assert agent.name == "claude"
        assert "npx" in agent.command[0]
        assert "model" in agent.config
        assert agent.config["api_key"].required is True

    def test_get_aloop(self):
        agent = get_agent("aloop")
        assert agent.name == "aloop"
        assert agent.command == ["aloop", "serve"]
        assert agent.config["model"].default == "minimax-m2.5"

    def test_get_codex(self):
        agent = get_agent("codex")
        assert agent.name == "codex"
        assert "codex-acp" in agent.command[-1]
        assert agent.config["sandbox"].default == "workspace-write"

    def test_unknown_agent_raises(self):
        with pytest.raises(ValueError, match="Unknown agent 'nonexistent'"):
            get_agent("nonexistent")

    def test_unknown_agent_lists_known(self):
        with pytest.raises(ValueError, match="claude"):
            get_agent("nonexistent")

    def test_get_agent_returns_copy(self):
        a1 = get_agent("claude")
        a2 = get_agent("claude")
        a1.command.append("--extra")
        assert "--extra" not in a2.command


class TestListAgents:
    def test_lists_builtins(self):
        agents = list_agents()
        assert "claude" in agents
        assert "aloop" in agents
        assert "codex" in agents

    def test_sorted(self):
        agents = list_agents()
        assert agents == sorted(agents)

    def test_includes_user_agents(self):
        set_user_agents({
            "custom": AgentConfig(name="custom", command=["custom-agent"]),
        })
        agents = list_agents()
        assert "custom" in agents


# ── Config Resolution: Flag Delivery ──────────────────────────────────


class TestFlagDelivery:
    def test_flag_appends_to_command(self):
        resolved = resolve_config("claude", {"model": "sonnet"}, "/tmp/work")
        assert "--model" in resolved.command
        idx = resolved.command.index("--model")
        assert resolved.command[idx + 1] == "sonnet"

    def test_flag_with_list_joins_comma(self):
        resolved = resolve_config("claude", {"tools": ["Read", "Write"]}, "/tmp/work")
        assert "--allowedTools" in resolved.command
        idx = resolved.command.index("--allowedTools")
        assert resolved.command[idx + 1] == "Read,Write"

    def test_default_flag_value_used(self, monkeypatch):
        # codex no longer carries real CLI flags (its adapter takes only
        # `-c key=value`), so the generic "does the default flag value
        # get serialized?" check moves to claude, which still has a real
        # flag surface (--model opus is the registered default).
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert "--model" in resolved.command
        idx = resolved.command.index("--model")
        assert resolved.command[idx + 1] == "opus"


# ── Config Resolution: Env Delivery ───────────────────────────────────


class TestEnvDelivery:
    def test_env_adds_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert "ANTHROPIC_API_KEY" in resolved.env_vars
        assert resolved.env_vars["ANTHROPIC_API_KEY"] == "sk-test-123"

    def test_env_step_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-default")
        resolved = resolve_config("claude", {"api_key": "sk-override"}, "/tmp/work")
        assert resolved.env_vars["ANTHROPIC_API_KEY"] == "sk-override"


# ── Config Resolution: ACP Delivery ──────────────────────────────────


class TestAcpDelivery:
    def test_acp_adds_to_calls(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
        resolved = resolve_config("aloop", {"mode": "creative"}, "/tmp/work")
        assert ("set_session_mode", "creative") in resolved.acp_calls

    def test_acp_no_override_no_default_skipped(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
        resolved = resolve_config("aloop", {}, "/tmp/work")
        # mode has no default, so it should not appear in acp_calls
        method_names = [name for name, _ in resolved.acp_calls]
        assert "set_session_mode" not in method_names


# ── Config Resolution: Defaults ───────────────────────────────────────


class TestDefaults:
    def test_default_used_when_no_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert "--model" in resolved.command
        idx = resolved.command.index("--model")
        assert resolved.command[idx + 1] == "opus"

    def test_step_override_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {"model": "sonnet"}, "/tmp/work")
        idx = resolved.command.index("--model")
        assert resolved.command[idx + 1] == "sonnet"


# ── Config Resolution: Env Var Expansion ──────────────────────────────


class TestEnvVarExpansion:
    def test_env_var_expanded(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert resolved.env_vars["ANTHROPIC_API_KEY"] == "sk-real-key"

    def test_missing_env_var_expands_to_empty(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # claude has api_key required, but the default is ${ANTHROPIC_API_KEY}
        # which expands to "" — this still satisfies "has a value"
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert resolved.env_vars["ANTHROPIC_API_KEY"] == ""

    def test_working_dir_expanded(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {}, "/my/project")
        assert "--allowedPaths" in resolved.command
        idx = resolved.command.index("--allowedPaths")
        assert "/my/project" in resolved.command[idx + 1]


# ── Config Resolution: Required Validation ────────────────────────────


class TestRequiredValidation:
    def test_required_missing_raises(self):
        set_user_agents({
            "strict": AgentConfig(
                name="strict",
                command=["strict-agent"],
                config={
                    "token": ConfigKey(env="TOKEN", required=True),
                },
            ),
        })
        with pytest.raises(ValueError, match="required config key 'token'"):
            resolve_config("strict", {}, "/tmp/work")


# ── Config Resolution: List Values ────────────────────────────────────


class TestListValues:
    def test_list_default_for_flag(self, monkeypatch):
        # claude still uses --allowedPaths (codex's --allowed-paths went
        # away when codex-acp's flag surface collapsed to `-c key=value`).
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert "--allowedPaths" in resolved.command
        idx = resolved.command.index("--allowedPaths")
        assert "/tmp/work" in resolved.command[idx + 1]

    def test_list_override_for_flag(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {"allowed_paths": ["/a", "/b"]}, "/tmp/work")
        idx = resolved.command.index("--allowedPaths")
        assert resolved.command[idx + 1] == "/a,/b"


# ── Config Resolution: Optional Keys ─────────────────────────────────


class TestOptionalKeys:
    def test_no_default_no_override_not_required_skipped(self):
        resolved = resolve_config("codex", {}, "/tmp/work")
        # "model" has no default on codex, should not appear
        assert "--model" not in resolved.command

    def test_tools_no_default_no_override_skipped(self):
        resolved = resolve_config("codex", {}, "/tmp/work")
        # "tools" has no default on codex, should not appear
        assert "--tools" not in resolved.command

    def test_aloop_tools_not_passed_as_flag(self):
        # `aloop serve` accepts only --model / --provider. The registry's
        # tools default is kept for containment-config grouping, but no
        # CLI flag delivery — otherwise the adapter dies on `unrecognized
        # arguments: --tools ...` at handshake.
        resolved = resolve_config("aloop", {}, "/tmp/work")
        assert "--tools" not in resolved.command
        # But tools still track in the resolved config so VMs with
        # matching tool sets can share.
        assert resolved.tools == [
            "read_file", "write_file", "edit_file", "bash", "load_skill",
        ]

    def test_codex_has_no_legacy_flags(self):
        # codex-acp's only argv surface is `-c key=value` + `--help`.
        # Passing --model / --sandbox / --tools / --allowed-paths makes
        # the adapter die at handshake with "unexpected argument". The
        # registry defaults still feed `ResolvedAgentConfig` for
        # containment grouping but do not get serialized into argv.
        resolved = resolve_config("codex", {}, "/tmp/work")
        assert "--model" not in resolved.command
        assert "--sandbox" not in resolved.command
        assert "--tools" not in resolved.command
        assert "--allowed-paths" not in resolved.command
        # Grouping defaults still populate.
        assert resolved.allowed_paths == ["/tmp/work"]


# ── Grouping Fields ───────────────────────────────────────────────────


class TestGroupingFields:
    def test_model_tracked(self):
        resolved = resolve_config("codex", {"model": "gpt-4"}, "/tmp/work")
        assert resolved.model == "gpt-4"

    def test_tools_tracked(self):
        resolved = resolve_config("codex", {"tools": ["read", "write"]}, "/tmp/work")
        assert resolved.tools == ["read", "write"]

    def test_allowed_paths_tracked(self):
        resolved = resolve_config("codex", {}, "/tmp/work")
        assert resolved.allowed_paths == ["/tmp/work"]

    def test_model_none_when_not_provided(self):
        resolved = resolve_config("codex", {}, "/tmp/work")
        assert resolved.model is None


# ── Independent Resolution ────────────────────────────────────────────


class TestIndependentResolution:
    def test_resolving_claude_does_not_affect_aloop(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
        r1 = resolve_config("claude", {"model": "haiku"}, "/tmp/a")
        r2 = resolve_config("aloop", {"model": "gpt-5"}, "/tmp/b")
        assert r1.name == "claude"
        assert r2.name == "aloop"
        assert r1.model == "haiku"
        assert r2.model == "gpt-5"


# ── Capabilities ──────────────────────────────────────────────────────


class TestCapabilities:
    def test_claude_has_fork_and_resume(self):
        agent = get_agent("claude")
        assert agent.capabilities.fork is True
        assert agent.capabilities.resume is True

    def test_codex_defaults(self):
        agent = get_agent("codex")
        assert agent.capabilities.fork is False
        assert agent.capabilities.sessions is True

    def test_capabilities_in_resolved(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        resolved = resolve_config("claude", {}, "/tmp/work")
        assert resolved.capabilities.fork is True


# ── User-Defined Agents ──────────────────────────────────────────────


class TestUserDefinedAgents:
    def test_load_from_config_data(self):
        data = {
            "agents": {
                "my-agent": {
                    "command": ["my-agent", "serve"],
                    "config": {
                        "model": {"flag": "--model", "default": "gpt-4"},
                        "api_key": {"env": "MY_API_KEY", "required": True},
                    },
                    "capabilities": {"fork": True},
                },
            },
        }
        agents = load_user_agents_from_config(data)
        assert "my-agent" in agents
        agent = agents["my-agent"]
        assert agent.name == "my-agent"
        assert agent.command == ["my-agent", "serve"]
        assert agent.config["model"].flag == "--model"
        assert agent.config["api_key"].required is True
        assert agent.capabilities.fork is True

    def test_user_agent_accessible_via_get(self):
        set_user_agents({
            "custom": AgentConfig(
                name="custom",
                command=["custom-agent"],
                config={"port": ConfigKey(flag="--port", default="8080")},
            ),
        })
        agent = get_agent("custom")
        assert agent.name == "custom"

    def test_user_agent_overrides_builtin(self):
        set_user_agents({
            "claude": AgentConfig(
                name="claude",
                command=["my-claude-wrapper"],
                config={},
            ),
        })
        agent = get_agent("claude")
        assert agent.command == ["my-claude-wrapper"]

    def test_user_agent_resolvable(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_KEY", "key123")
        set_user_agents({
            "custom": AgentConfig(
                name="custom",
                command=["custom-agent"],
                config={
                    "api_key": ConfigKey(env="CUSTOM_KEY", default="${CUSTOM_KEY}", required=True),
                },
            ),
        })
        resolved = resolve_config("custom", {}, "/tmp/work")
        assert resolved.env_vars["CUSTOM_KEY"] == "key123"

    def test_empty_agents_section(self):
        agents = load_user_agents_from_config({"agents": {}})
        assert agents == {}

    def test_no_agents_section(self):
        agents = load_user_agents_from_config({"labels": {"fast": "gpt-4"}})
        assert agents == {}

    def test_invalid_agents_section(self):
        agents = load_user_agents_from_config({"agents": "not-a-dict"})
        assert agents == {}


# ── Serialization ─────────────────────────────────────────────────────


class TestSerialization:
    def test_config_key_roundtrip(self):
        key = ConfigKey(flag="--model", default="gpt-4", required=True)
        d = key.to_dict()
        restored = ConfigKey.from_dict(d)
        assert restored.flag == "--model"
        assert restored.default == "gpt-4"
        assert restored.required is True

    def test_agent_config_roundtrip(self):
        agent = get_agent("claude")
        d = agent.to_dict()
        restored = AgentConfig.from_dict(d)
        assert restored.name == "claude"
        assert restored.command == agent.command
        assert restored.config["model"].flag == "--model"
        assert restored.capabilities.fork is True

    def test_capabilities_roundtrip(self):
        caps = AgentCapabilities(fork=True, resume=True, modes=True)
        d = caps.to_dict()
        restored = AgentCapabilities.from_dict(d)
        assert restored.fork is True
        assert restored.resume is True
        assert restored.modes is True
        assert restored.sessions is True  # default
