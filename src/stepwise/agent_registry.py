"""Agent registry: config model, builtins, and resolution for ACP agents.

Each agent has a command (subprocess argv), config keys (with delivery
mechanism: CLI flag, env var, or ACP method call), and capabilities.

Override chain: flow step value > config default > error if required and missing.

This module has NO dependencies on agent.py, claude_direct.py, engine.py,
or any existing executor code.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass
class ConfigKey:
    """A single config key for an agent.

    Exactly one delivery mechanism should be set: flag, env, or acp.
    """
    flag: str | None = None
    env: str | None = None
    acp: str | None = None
    default: Any = None
    required: bool = False

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        if self.flag is not None:
            d["flag"] = self.flag
        if self.env is not None:
            d["env"] = self.env
        if self.acp is not None:
            d["acp"] = self.acp
        if self.default is not None:
            d["default"] = self.default
        if self.required:
            d["required"] = True
        return d

    @staticmethod
    def from_dict(d: dict) -> ConfigKey:
        return ConfigKey(
            flag=d.get("flag"),
            env=d.get("env"),
            acp=d.get("acp"),
            default=d.get("default"),
            required=d.get("required", False),
        )


@dataclass
class AgentCapabilities:
    """What this agent supports."""
    fork: bool = False
    resume: bool = False
    sessions: bool = True
    modes: bool = False
    multi_session: bool = True

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        if self.fork:
            d["fork"] = True
        if self.resume:
            d["resume"] = True
        if not self.sessions:
            d["sessions"] = False
        if self.modes:
            d["modes"] = True
        if not self.multi_session:
            d["multi_session"] = False
        return d

    @staticmethod
    def from_dict(d: dict) -> AgentCapabilities:
        return AgentCapabilities(
            fork=d.get("fork", False),
            resume=d.get("resume", False),
            sessions=d.get("sessions", True),
            modes=d.get("modes", False),
            multi_session=d.get("multi_session", True),
        )


@dataclass
class AgentConfig:
    """Configuration for an ACP agent."""
    name: str
    command: list[str]
    config: dict[str, ConfigKey] = field(default_factory=dict)
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
        }
        if self.config:
            d["config"] = {k: v.to_dict() for k, v in self.config.items()}
        caps = self.capabilities.to_dict()
        if caps:
            d["capabilities"] = caps
        return d

    @staticmethod
    def from_dict(d: dict) -> AgentConfig:
        config_raw = d.get("config", {})
        config = {k: ConfigKey.from_dict(v) if isinstance(v, dict) else ConfigKey(default=v)
                  for k, v in config_raw.items()}
        caps_raw = d.get("capabilities", {})
        return AgentConfig(
            name=d["name"],
            command=d.get("command", []),
            config=config,
            capabilities=AgentCapabilities.from_dict(caps_raw) if isinstance(caps_raw, dict) else AgentCapabilities(),
        )


@dataclass
class ResolvedAgentConfig:
    """Fully resolved config ready for subprocess spawn."""
    name: str
    command: list[str]
    env_vars: dict[str, str] = field(default_factory=dict)
    acp_calls: list[tuple[str, Any]] = field(default_factory=list)
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    model: str | None = None
    tools: list[str] | None = None
    allowed_paths: list[str] | None = None


# ── Builtin Agents ──────────────────────────────────────────────────


BUILTIN_AGENTS: dict[str, AgentConfig] = {
    "claude": AgentConfig(
        name="claude",
        command=["npx", "@agentclientprotocol/claude-agent-acp"],
        config={
            "model": ConfigKey(flag="--model", default="opus"),
            "max_turns": ConfigKey(flag="--max-turns"),
            "tools": ConfigKey(
                flag="--allowedTools",
                default=["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
            ),
            "allowed_paths": ConfigKey(
                flag="--allowedPaths",
                default=["${working_dir}"],
            ),
            "api_key": ConfigKey(
                env="ANTHROPIC_API_KEY",
                default="${ANTHROPIC_API_KEY}",
                required=True,
            ),
        },
        capabilities=AgentCapabilities(fork=True, resume=True),
    ),
    "aloop": AgentConfig(
        name="aloop",
        command=["aloop", "serve"],
        config={
            "model": ConfigKey(flag="--model", default="minimax-m2.5"),
            "mode": ConfigKey(acp="set_session_mode"),
            "tools": ConfigKey(
                flag="--tools",
                default=["read_file", "write_file", "edit_file", "bash", "load_skill"],
            ),
            "allowed_paths": ConfigKey(
                env="ALOOP_ALLOWED_PATHS",
                default=["${working_dir}"],
            ),
            "api_key": ConfigKey(
                env="OPENROUTER_API_KEY",
                default="${OPENROUTER_API_KEY}",
                required=True,
            ),
        },
    ),
    "codex": AgentConfig(
        name="codex",
        command=["npx", "@zed-industries/codex-acp"],
        config={
            "model": ConfigKey(flag="--model"),
            "sandbox": ConfigKey(flag="--sandbox", default="workspace-write"),
            "tools": ConfigKey(flag="--tools"),
            "allowed_paths": ConfigKey(
                flag="--allowed-paths",
                default=["${working_dir}"],
            ),
        },
    ),
}


# ── User-defined agents from settings ───────────────────────────────

_user_agents: dict[str, AgentConfig] = {}


def load_user_agents_from_config(config_data: dict) -> dict[str, AgentConfig]:
    """Parse user-defined agents from a stepwise config dict.

    Expected format in config YAML::

        agents:
          my-agent:
            command: ["my-agent", "serve"]
            config:
              model:
                flag: "--model"
                default: "gpt-4"
              api_key:
                env: "MY_API_KEY"
                required: true
            capabilities:
              fork: true
    """
    agents: dict[str, AgentConfig] = {}
    raw = config_data.get("agents", {})
    if not isinstance(raw, dict):
        return agents
    for name, agent_data in raw.items():
        if not isinstance(agent_data, dict):
            continue
        agent_data.setdefault("name", name)
        agents[name] = AgentConfig.from_dict(agent_data)
    return agents


def set_user_agents(agents: dict[str, AgentConfig]) -> None:
    """Set user-defined agents (called during config loading)."""
    global _user_agents
    _user_agents = dict(agents)


def _get_all_agents() -> dict[str, AgentConfig]:
    """Return merged agent registry: builtins + user-defined (user wins)."""
    merged = dict(BUILTIN_AGENTS)
    merged.update(_user_agents)
    return merged


# ── Public API ──────────────────────────────────────────────────────


def get_agent(name: str) -> AgentConfig:
    """Look up agent by name. Checks builtins then user config.

    Raises ValueError if not found, with a helpful message listing known agents.
    """
    all_agents = _get_all_agents()
    if name in all_agents:
        return deepcopy(all_agents[name])
    known = sorted(all_agents.keys())
    raise ValueError(
        f"Unknown agent {name!r}. Known agents: {', '.join(known)}"
    )


def list_agents() -> list[str]:
    """List all known agent names (builtins + user config)."""
    return sorted(_get_all_agents().keys())


def _expand_env_refs(value: Any, working_dir: str) -> Any:
    """Expand ${ENV_VAR} and ${working_dir} references in a value."""
    if isinstance(value, str):
        def _replacer(m: re.Match) -> str:
            var_name = m.group(1)
            if var_name == "working_dir":
                return working_dir
            return os.environ.get(var_name, "")
        return _ENV_VAR_RE.sub(_replacer, value)
    if isinstance(value, list):
        return [_expand_env_refs(item, working_dir) for item in value]
    return value


def resolve_config(
    agent_name: str,
    step_overrides: dict | None = None,
    working_dir: str = ".",
) -> ResolvedAgentConfig:
    """Resolve agent config from registry + step overrides.

    Returns the fully resolved config with all env var references expanded
    and the final command + env vars + ACP method calls computed.
    """
    agent = get_agent(agent_name)
    overrides = step_overrides or {}

    command = list(agent.command)
    env_vars: dict[str, str] = {}
    acp_calls: list[tuple[str, Any]] = []

    # Track special fields for grouping
    resolved_model: str | None = None
    resolved_tools: list[str] | None = None
    resolved_allowed_paths: list[str] | None = None

    for key_name, config_key in agent.config.items():
        # Determine raw value: step override > default
        if key_name in overrides:
            raw_value = overrides[key_name]
        elif config_key.default is not None:
            raw_value = deepcopy(config_key.default)
        else:
            if config_key.required:
                raise ValueError(
                    f"Agent {agent_name!r}: required config key {key_name!r} "
                    f"has no value (no step override, no default)"
                )
            continue  # optional with no value — skip

        # Expand env var references
        value = _expand_env_refs(raw_value, working_dir)

        # Route to delivery mechanism
        if config_key.flag:
            if isinstance(value, list):
                command.extend([config_key.flag, ",".join(str(v) for v in value)])
            else:
                command.extend([config_key.flag, str(value)])
        elif config_key.env:
            env_vars[config_key.env] = str(value) if not isinstance(value, str) else value
        elif config_key.acp:
            acp_calls.append((config_key.acp, value))

        # Track grouping fields
        if key_name == "model":
            resolved_model = str(value) if value is not None else None
        elif key_name == "tools":
            resolved_tools = value if isinstance(value, list) else [str(value)] if value else None
        elif key_name == "allowed_paths":
            resolved_allowed_paths = value if isinstance(value, list) else [str(value)] if value else None

    return ResolvedAgentConfig(
        name=agent_name,
        command=command,
        env_vars=env_vars,
        acp_calls=acp_calls,
        capabilities=deepcopy(agent.capabilities),
        model=resolved_model,
        tools=resolved_tools,
        allowed_paths=resolved_allowed_paths,
    )
