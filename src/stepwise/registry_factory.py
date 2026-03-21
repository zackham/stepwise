"""Shared executor registration, used by both server.py and runner.py.

This prevents the server and headless runner from having duplicate executor
registration code that drifts out of sync.
"""

from __future__ import annotations

import logging
import os

from stepwise.agent import AgentExecutor, AcpxBackend
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

    # Backwards-compatible alias
    registry.register("human", lambda cfg: ExternalExecutor(
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

    # Agent executor (ACP via acpx)
    # Config default_agent > env var > "claude"
    default_agent = (config.default_agent
                     or os.environ.get("STEPWISE_DEFAULT_AGENT")
                     or "claude")
    import shutil
    acpx_env = os.environ.get("ACPX_PATH")
    acpx_path = acpx_env or shutil.which("acpx") or "acpx"
    acpx_backend = AcpxBackend(
        acpx_path=acpx_path,
        default_agent=default_agent,
    )
    registry.register("agent", lambda cfg: AgentExecutor(
        backend=acpx_backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        output_path=cfg.get("output_path"),
        **{k: v for k, v in cfg.items()
           if k not in ("prompt", "output_mode", "output_path")},
    ))

    # LLM executor — OpenRouter if key configured, else CLI fallback via acpx
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
            acpx_path, agent = cli_info
            llm_client = CliLLMClient(acpx_path=acpx_path, agent=agent)
            llm_backend = "cli"

    if llm_client:
        def _create_llm_executor(cfg: dict, _client=llm_client):
            from stepwise.executors import LLMExecutor
            model_ref = cfg.get("model") or config.default_model or "anthropic/claude-sonnet-4-20250514"
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
        logger.debug("LLM executor: CLI fallback (%s via %s)", agent, acpx_path)
    else:
        logger.debug("LLM executor: not available (no OpenRouter key or CLI)")

    registry.llm_backend = llm_backend

    return registry
