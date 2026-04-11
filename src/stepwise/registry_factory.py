"""Shared executor registration, used by both server.py and runner.py.

This prevents the server and headless runner from having duplicate executor
registration code that drifts out of sync.
"""

from __future__ import annotations

import logging
import os

from stepwise.acp_backend import ACPBackend
from stepwise.agent import AgentExecutor
from pathlib import Path
from stepwise.config import StepwiseConfig, load_config
from stepwise.executors import (
    ExecutorRegistry,
    ExternalExecutor,
    MockLLMExecutor,
    PollExecutor,
    ScriptExecutor,
)

logger = logging.getLogger("stepwise.registry")


def create_default_registry(config: StepwiseConfig | None = None) -> ExecutorRegistry:
    """Register all built-in executors. Shared by server and headless runner.

    Args:
        config: Optional config. If None, loads from disk.

    Returns:
        ExecutorRegistry with all built-in executor types registered.
    """
    if config is None:
        config = load_config()

    registry = ExecutorRegistry()

    registry.register("script", lambda cfg: ScriptExecutor(
        command=cfg.get("command", "echo '{}'"),
        working_dir=cfg.get("working_dir"),
        flow_dir=cfg.get("flow_dir"),
    ))

    registry.register("external", lambda cfg: ExternalExecutor(
        prompt=cfg.get("prompt", "Awaiting external input"),
    ))

    registry.register("poll", lambda cfg: PollExecutor(
        check_command=cfg.get("check_command", "echo"),
        interval_seconds=cfg.get("interval_seconds", 60),
        prompt=cfg.get("prompt", ""),
    ))

    registry.register("mock_llm", lambda cfg: MockLLMExecutor(
        failure_rate=cfg.get("failure_rate", 0.0),
        partial_rate=cfg.get("partial_rate", 0.0),
        latency_range=tuple(cfg.get("latency_range", [0.0, 0.0])),
        responses=cfg.get("responses"),
    ))

    # Containment backend (if configured)
    containment_backend = None
    if getattr(config, "agent_containment", None) == "cloud-hypervisor":
        try:
            from stepwise.containment.cloud_hypervisor import CloudHypervisorBackend
            containment_backend = CloudHypervisorBackend()
            logger.info("Containment enabled: cloud-hypervisor")
        except Exception as exc:
            logger.warning("Failed to initialize cloud-hypervisor containment: %s", exc)

    # Agent executor (native ACP)
    acp_backend = ACPBackend(
        default_permissions=config.agent_permissions,
        containment=containment_backend,
    )
    registry.register("agent", lambda cfg: AgentExecutor(
        backend=acp_backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        output_path=cfg.get("output_path"),
        _user_set_output_mode=("output_mode" in cfg),
        **{k: v for k, v in cfg.items()
           if k not in ("prompt", "output_mode", "output_path")},
    ))

    # LLM executor — OpenRouter if key configured, else CLI fallback via native ACP
    llm_client = None
    llm_backend = None  # "openrouter" | "cli" | None

    if config.openrouter_api_key:
        try:
            from stepwise.openrouter import OpenRouterClient
            llm_client = OpenRouterClient(api_key=config.openrouter_api_key)
            llm_backend = "openrouter"
        except ImportError:
            pass  # httpx should always be available
    else:
        from stepwise.cli_llm_client import CliLLMClient, detect_cli_backend
        cli_info = detect_cli_backend()
        if cli_info:
            (agent,) = cli_info
            llm_client = CliLLMClient(agent=agent)
            llm_backend = "cli"

    if llm_client:
        def _create_llm_executor(cfg: dict, _client=llm_client):
            from stepwise.executors import LLMExecutor
            model_ref = cfg.get("model") or config.default_model or "anthropic/claude-sonnet-4-20250514"
            # Strip whitespace — can appear after $variable interpolation leaves
            # leading/trailing spaces when the YAML value was e.g. "  $model_id  ".
            model_ref = model_ref.strip()
            # Detect unresolved $variable references — interpolation should have
            # run before the factory is called, so a bare $var here means the
            # variable name was misspelled or the input binding is missing.
            if model_ref.startswith("$"):
                raise ValueError(
                    f"LLM step model '{model_ref}' looks like an unresolved variable. "
                    "Check that the input binding for this variable is defined and the "
                    "source step has completed."
                )
            model_id = config.resolve_model(model_ref)
            kwargs: dict = {}
            if cfg.get("system"):
                kwargs["system"] = cfg["system"]
            if cfg.get("temperature") is not None:
                kwargs["temperature"] = cfg["temperature"]
            if cfg.get("max_tokens") is not None:
                kwargs["max_tokens"] = cfg["max_tokens"]
            if cfg.get("loop_prompt"):
                kwargs["loop_prompt"] = cfg["loop_prompt"]
            executor = LLMExecutor(
                client=_client,
                model=model_id,
                prompt=cfg.get("prompt", ""),
                **kwargs,
            )
            if cfg.get("output_fields"):
                executor._output_fields = cfg["output_fields"]
            return executor

        registry.register("llm", _create_llm_executor)

    if llm_backend == "openrouter":
        logger.debug("LLM executor: OpenRouter")
    elif llm_backend == "cli":
        logger.debug("LLM executor: CLI fallback (agent=%s)", agent)
    else:
        logger.debug("LLM executor: not available (no OpenRouter key or CLI)")

    registry.llm_backend = llm_backend

    return registry
