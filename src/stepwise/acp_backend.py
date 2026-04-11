"""ACPBackend: native ACP agent backend using stdio transport.

Native ACP agent backend using stdio transport. Manages process
lifecycle via ResourceLifecycleManager with backward-looking reuse.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stepwise.acp_client import ACPClient
from stepwise.acp_ndjson import extract_cost, extract_session_id, read_last_error
from stepwise.acp_transport import AcpError, JsonRpcTransport
from stepwise.agent import AgentProcess, AgentStatus
from stepwise.agent_registry import ResolvedAgentConfig, resolve_config
from stepwise.executors import ExecutionContext
from stepwise.lifecycle import ResourceLifecycleManager

logger = logging.getLogger("stepwise.acp_backend")


@dataclass
class ACPProcess:
    """A long-lived ACP server process hosting one or more sessions."""

    config: ResolvedAgentConfig
    process: subprocess.Popen
    transport: JsonRpcTransport
    client: ACPClient
    capabilities: dict = field(default_factory=dict)
    pid_file: Path | None = None


class ACPBackend:
    """Agent backend using native ACP over stdio.

    Native ACP agent backend using stdio transport. Manages process
    lifecycle via ResourceLifecycleManager with backward-looking reuse.

    Optionally wraps process spawning with a containment backend
    (cloud-hypervisor, etc.) for hardware-isolated execution.
    """

    def __init__(
        self,
        default_permissions: str = "approve_all",
        containment: Any | None = None,
    ):
        self.default_permissions = default_permissions
        self.containment = containment  # ContainmentBackend or None
        self.lifecycle: ResourceLifecycleManager[ACPProcess] = ResourceLifecycleManager(
            is_eq=self._config_eq,
            factory=self._spawn_process,
            teardown=self._kill_process,
        )

    @staticmethod
    def _config_eq(a: ResolvedAgentConfig, b: ResolvedAgentConfig) -> bool:
        """Two configs can share a process if agent, model, tools, paths, and containment match."""
        return (
            a.name == b.name
            and a.model == b.model
            and a.tools == b.tools
            and a.allowed_paths == b.allowed_paths
            and a.containment == b.containment
        )

    def _spawn_process(self, config: ResolvedAgentConfig) -> ACPProcess:
        """Spawn ACP server subprocess, do handshake.

        If a containment backend is configured, the process is spawned
        inside a hardware-isolated environment (microVM). The stdio
        streams are bridged via vsock, transparent to the ACP protocol.
        """
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")
        }
        env.update(config.env_vars)

        # Use containment spawn context if available
        if self.containment and getattr(config, "containment", None):
            from stepwise.containment.backend import ContainmentConfig

            containment_config = ContainmentConfig(
                mode=config.containment,
                tools=config.tools,
                allowed_paths=config.allowed_paths,
                working_dir=config.allowed_paths[0] if config.allowed_paths else ".",
            )
            spawn_ctx = self.containment.get_spawn_context(containment_config)
            cwd = config.allowed_paths[0] if config.allowed_paths else "."
            proc = spawn_ctx.spawn(config.command, env, cwd)
        else:
            proc = subprocess.Popen(
                config.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )

        transport = JsonRpcTransport(proc)
        transport.start()
        client = ACPClient(transport)

        # ACP handshake
        try:
            caps = client.initialize()
        except Exception as exc:
            # Kill process on handshake failure
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            raise RuntimeError(
                f"ACP handshake failed for agent {config.name!r}: {exc}"
            ) from exc

        # Write PID file for crash recovery
        pid_file = Path(".stepwise") / "pids" / f"acp-{proc.pid}.json"
        try:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(
                json.dumps({"pid": proc.pid, "agent": config.name})
            )
        except OSError:
            pid_file = None

        logger.info(
            "Spawned ACP process pid=%d agent=%s cmd=%s",
            proc.pid,
            config.name,
            config.command[:3],
        )

        return ACPProcess(
            config=config,
            process=proc,
            transport=transport,
            client=client,
            capabilities=caps,
            pid_file=pid_file,
        )

    @staticmethod
    def _kill_process(acp_proc: ACPProcess) -> None:
        """Gracefully terminate an ACP process."""
        try:
            acp_proc.transport.close()
        except Exception:
            pass
        try:
            pgid = os.getpgid(acp_proc.process.pid)
            os.killpg(pgid, signal.SIGTERM)
            acp_proc.process.wait(timeout=5)
        except Exception:
            try:
                pgid = os.getpgid(acp_proc.process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
        if acp_proc.pid_file and acp_proc.pid_file.exists():
            try:
                acp_proc.pid_file.unlink()
            except OSError:
                pass

    # ── AgentBackend protocol ──────────────────────────────────

    def spawn(
        self, prompt: str, config: dict, context: ExecutionContext,
    ) -> AgentProcess:
        """Acquire ACP process, create/load session, send prompt."""
        t0 = time.monotonic()
        step_id = f"{context.step_name}@{context.job_id or 'local'}"
        logger.info("[%s] acp spawn started", step_id)

        # Resolve agent config
        agent_name = config.get("agent", "claude")
        skip_keys = {
            "agent", "prompt", "permissions", "working_dir",
            "output_fields", "output_path", "output_mode",
            "_session_name", "_session_uuid", "_fork_from_session_id",
        }
        step_overrides = {k: v for k, v in config.items() if k not in skip_keys}
        working_dir = str(
            Path(config.get("working_dir", context.workspace_path)).resolve()
        )
        resolved = resolve_config(agent_name, step_overrides, working_dir)

        # Acquire or reuse ACP process
        session_name = config.get("_session_name")
        managed = self.lifecycle.acquire(resolved, session_name)
        acp_proc = managed.resource

        # Create or continue session
        fork_from = config.get("_fork_from_session_id")
        session_uuid = config.get("_session_uuid")

        if fork_from:
            session_id = acp_proc.client.fork_session(fork_from, working_dir)
        elif session_uuid:
            acp_proc.client.load_session(session_uuid, working_dir)
            session_id = session_uuid
        else:
            session_id = acp_proc.client.new_session(working_dir, session_name)

        # Execute ACP method calls (mode, model overrides)
        for method_name, value in resolved.acp_calls:
            if method_name == "set_session_mode":
                acp_proc.client.set_session_mode(session_id, str(value))
            elif method_name == "set_session_model":
                acp_proc.client.set_session_model(session_id, str(value))

        # Set up output file
        step_io = (
            Path(working_dir)
            / ".stepwise"
            / "step-io"
            / (context.job_id or "local")
        )
        step_io.mkdir(parents=True, exist_ok=True)
        output_path = step_io / f"{context.step_name}-{context.attempt}.output.jsonl"

        # Write prompt file (for debugging/replay)
        prompt_file = step_io / f"{context.step_name}-{context.attempt}.prompt.md"
        prompt_file.write_text(prompt)

        # Build env vars for the step (STEPWISE_* vars)
        proc_env = acp_proc.process.environ if hasattr(acp_proc.process, "environ") else {}
        env_updates = {
            "STEPWISE_STEP_NAME": context.step_name,
            "STEPWISE_ATTEMPT": str(context.attempt),
            "STEPWISE_STEP_IO": str(step_io),
        }
        output_fields = config.get("output_fields")
        if output_fields:
            output_filename = config.get("output_path") or f"{context.step_name}-output.json"
            env_updates["STEPWISE_OUTPUT_FILE"] = str(
                (Path(working_dir) / output_filename).resolve()
            )

        # Send prompt (blocking — runs in thread pool via AsyncEngine)
        try:
            acp_proc.client.prompt(
                session_id,
                prompt,
                output_path=str(output_path),
            )
        except AcpError as exc:
            logger.warning("[%s] ACP prompt error: %s", step_id, exc)
        except Exception as exc:
            logger.warning("[%s] Prompt failed: %s", step_id, exc)

        elapsed = time.monotonic() - t0
        logger.info(
            "[%s] acp spawn done pid=%d session=%s (%.1fs)",
            step_id,
            acp_proc.process.pid,
            session_id,
            elapsed,
        )

        return AgentProcess(
            pid=acp_proc.process.pid,
            pgid=os.getpgid(acp_proc.process.pid),
            output_path=str(output_path),
            working_dir=working_dir,
            session_id=session_id,
            session_name=session_name,
            capture_transcript=False,
            agent=agent_name,
        )

    def wait(
        self, process: AgentProcess, on_usage_limit: Any = None,
    ) -> AgentStatus:
        """Extract status from the output file.

        The prompt already completed in spawn() (ACP is request/response),
        so this just reads the result from the NDJSON output.
        """
        session_id = extract_session_id(process.output_path, result_only=True)
        cost = extract_cost(process.output_path)
        error = read_last_error(process.output_path)

        if error:
            return AgentStatus(
                state="failed",
                exit_code=-1,
                session_id=session_id or process.session_id,
                error=error,
                cost_usd=cost,
            )

        return AgentStatus(
            state="completed",
            exit_code=0,
            session_id=session_id or process.session_id,
            cost_usd=cost,
        )

    def check(self, process: AgentProcess) -> AgentStatus:
        """Non-blocking status check.

        Since prompt blocks in spawn(), check output file for completion.
        """
        if not Path(process.output_path).exists():
            return AgentStatus(
                state="running",
                session_id=process.session_id,
            )

        session_id = extract_session_id(process.output_path, result_only=True)
        cost = extract_cost(process.output_path)

        # Check if we have a completed result (stopReason in output)
        try:
            with open(process.output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        result = data.get("result", {})
                        if isinstance(result, dict) and "stopReason" in result:
                            error = read_last_error(process.output_path)
                            if error:
                                return AgentStatus(
                                    state="failed",
                                    exit_code=-1,
                                    session_id=session_id or process.session_id,
                                    error=error,
                                    cost_usd=cost,
                                )
                            return AgentStatus(
                                state="completed",
                                exit_code=0,
                                session_id=session_id or process.session_id,
                                cost_usd=cost,
                            )
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass

        return AgentStatus(
            state="running",
            session_id=session_id or process.session_id,
            cost_usd=cost,
        )

    def cancel(self, process: AgentProcess) -> None:
        """Cancel in-flight prompt."""
        # Find the ACP process hosting this session
        if process.session_id:
            for managed in self.lifecycle.active:
                acp_proc = managed.resource
                try:
                    acp_proc.client.cancel(process.session_id)
                    return
                except Exception:
                    pass

        # Fallback: SIGTERM on process group
        pgid = process.pgid or process.pid
        if pgid:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    @property
    def supports_resume(self) -> bool:
        return True

    def cleanup(self) -> None:
        """Release all managed processes and containment environments (job end)."""
        self.lifecycle.release_all()
        if self.containment:
            self.containment.release_all()
