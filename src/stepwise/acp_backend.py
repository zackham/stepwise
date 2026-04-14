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
from stepwise.containment.acp_bridge import BridgeClient, BridgeError, translate_path
from stepwise.executors import ExecutionContext
from stepwise.lifecycle import ResourceLifecycleManager

# Containment strategy: when enabled, ALL agents run inside the
# microVM via VMSpawnContext.spawn. The previous host-adapter + bridge
# split (pre-2026-04-14) relied on claude-agent-acp / codex-acp
# delegating fs/terminal calls via ACP, which they don't actually do —
# their Read/Write/Bash tools run in-process, so a host-spawned
# adapter defeats containment. See
# data/reports/2026-04-14-containment-tier-3-findings.md.

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
    bridge_client: BridgeClient | None = None


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
        # Containment can come from two places:
        #   1. Eagerly injected at registry setup via stepwise-level
        #      `agent_containment` config (registry_factory.py).
        #   2. Lazily created in `_spawn_process` when a step's resolved
        #      config carries `containment: cloud-hypervisor` (either via
        #      flow-level propagation in yaml_loader, or a step-level
        #      override, or a CLI `--containment` flag).
        # The second path used to silently no-op — flow YAMLs asked for
        # containment and spawned on the host anyway. Now we detect it
        # and initialize a real backend on demand.
        self.containment = containment  # ContainmentBackend or None
        self.lifecycle: ResourceLifecycleManager[ACPProcess] = ResourceLifecycleManager(
            is_eq=self._config_eq,
            factory=self._spawn_process,
            teardown=self._kill_process,
            is_alive=self._is_alive,
        )

    def _ensure_containment_backend(self, mode: str) -> Any:
        """Lazily create a containment backend for the requested mode.

        Called from `_spawn_process` when a step requests containment but
        `self.containment` is None. The resulting backend is cached on
        `self.containment` so subsequent steps in the same process reuse
        it (and its VM pool).
        """
        if self.containment is not None:
            return self.containment
        if mode == "cloud-hypervisor":
            from stepwise.containment.cloud_hypervisor import CloudHypervisorBackend

            logger.info(
                "Lazily initializing cloud-hypervisor containment backend "
                "(flow requested containment but stepwise config has no "
                "agent_containment global default)"
            )
            self.containment = CloudHypervisorBackend()
            return self.containment
        raise RuntimeError(
            f"Unknown containment mode {mode!r} — expected 'cloud-hypervisor'"
        )

    @staticmethod
    def _register_client_handlers(
        transport: JsonRpcTransport,
        bridge_client: BridgeClient | None = None,
    ) -> None:
        """Register handlers for ACP client-side requests.

        The ACP agent (server) sends requests to the client for:
        - Permission approval (session/request_permission)
        - File I/O (fs/read_text_file, fs/write_text_file)
        - Terminal/shell execution (terminal/create, terminal/output, etc.)

        Without these, the agent hangs waiting for responses that never come.

        When `bridge_client` is provided, fs/terminal requests are proxied
        through the in-VM ACP bridge instead of executing on the host. This
        is the containment path for claude/codex: the adapter runs on the
        host (needs API keys + network) but all sandbox-relevant operations
        execute inside the microVM. Host-absolute paths under the bridge's
        `host_workdir` are rewritten to the VM's `/mnt/workspace` mount so
        both sides see the same virtiofs-backed bytes.
        """
        terminals: dict[str, subprocess.Popen] = {}

        def handle_request_permission(params: dict) -> dict:
            # Auto-approve: pick the allow option from the request's options
            options = params.get("options", [])
            for opt in options:
                if opt.get("optionId") in ("allow_once", "allow_always"):
                    return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
            if options:
                return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
            return {"outcome": {"outcome": "cancelled"}}

        def _rewrite(params: dict) -> dict:
            if not bridge_client or "path" not in params:
                return params
            rewritten = dict(params)
            rewritten["path"] = translate_path(params["path"], bridge_client.host_workdir)
            return rewritten

        def handle_read_text_file(params: dict) -> dict:
            file_path = params.get("path", "")
            if bridge_client:
                try:
                    return bridge_client.call("fs/read_text_file", _rewrite(params))
                except BridgeError as exc:
                    raise RuntimeError(f"Cannot read {file_path}: {exc}")
            try:
                content = Path(file_path).read_text()
                return {"content": content}
            except Exception as exc:
                raise RuntimeError(f"Cannot read {file_path}: {exc}")

        def handle_write_text_file(params: dict) -> dict:
            file_path = params.get("path", "")
            content = params.get("content", "")
            if bridge_client:
                try:
                    return bridge_client.call("fs/write_text_file", _rewrite(params))
                except BridgeError as exc:
                    raise RuntimeError(f"Cannot write {file_path}: {exc}")
            try:
                p = Path(file_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                return {}
            except Exception as exc:
                raise RuntimeError(f"Cannot write {file_path}: {exc}")

        def handle_terminal_create(params: dict) -> dict:
            if bridge_client:
                try:
                    return bridge_client.call("terminal/create", params)
                except BridgeError as exc:
                    raise RuntimeError(f"Terminal create failed: {exc}")
            from uuid import uuid4
            cmd = params.get("command", "")
            cwd = params.get("cwd")
            terminal_id = str(uuid4())
            try:
                proc = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=cwd,
                )
                terminals[terminal_id] = proc
                return {"terminalId": terminal_id, "pid": proc.pid}
            except Exception as exc:
                raise RuntimeError(f"Terminal create failed: {exc}")

        def handle_terminal_output(params: dict) -> dict:
            if bridge_client:
                try:
                    return bridge_client.call("terminal/output", params)
                except BridgeError as exc:
                    raise RuntimeError(f"Terminal output failed: {exc}")
            import select as _select
            terminal_id = params.get("terminalId", "")
            proc = terminals.get(terminal_id)
            if not proc or not proc.stdout:
                return {"output": "", "isEof": True}
            ready, _, _ = _select.select([proc.stdout], [], [], 0.1)
            if ready:
                chunk = proc.stdout.read(65536) if proc.stdout.readable() else ""
                is_eof = chunk == "" and proc.poll() is not None
                return {"output": chunk or "", "isEof": is_eof}
            return {"output": "", "isEof": proc.poll() is not None}

        def handle_terminal_wait(params: dict) -> dict:
            if bridge_client:
                try:
                    return bridge_client.call("terminal/wait_for_exit", params)
                except BridgeError as exc:
                    raise RuntimeError(f"Terminal wait failed: {exc}")
            terminal_id = params.get("terminalId", "")
            timeout_ms = params.get("timeoutMs", 30000)
            proc = terminals.get(terminal_id)
            if not proc:
                return {"exitCode": -1}
            try:
                proc.wait(timeout=timeout_ms / 1000)
                remaining = proc.stdout.read() if proc.stdout else ""
                return {"exitCode": proc.returncode, "output": remaining}
            except subprocess.TimeoutExpired:
                return {"exitCode": None, "timedOut": True}

        def handle_terminal_kill(params: dict) -> dict:
            if bridge_client:
                try:
                    return bridge_client.call("terminal/kill", params)
                except BridgeError as exc:
                    raise RuntimeError(f"Terminal kill failed: {exc}")
            terminal_id = params.get("terminalId", "")
            proc = terminals.get(terminal_id)
            if proc:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return {}

        def handle_terminal_release(params: dict) -> dict:
            if bridge_client:
                try:
                    return bridge_client.call("terminal/release", params)
                except BridgeError as exc:
                    raise RuntimeError(f"Terminal release failed: {exc}")
            terminal_id = params.get("terminalId", "")
            proc = terminals.pop(terminal_id, None)
            if proc and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return {}

        transport.on_request("session/request_permission", handle_request_permission)
        transport.on_request("fs/read_text_file", handle_read_text_file)
        transport.on_request("fs/write_text_file", handle_write_text_file)
        transport.on_request("terminal/create", handle_terminal_create)
        transport.on_request("terminal/output", handle_terminal_output)
        transport.on_request("terminal/wait_for_exit", handle_terminal_wait)
        transport.on_request("terminal/kill", handle_terminal_kill)
        transport.on_request("terminal/release", handle_terminal_release)

    @staticmethod
    def _is_alive(acp_proc: ACPProcess) -> bool:
        """Check if the ACP subprocess is still running."""
        return acp_proc.process.poll() is None

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

        Containment strategy (post-2026-04-14 rewrite):
        All agents — aloop, claude, codex — run inside the microVM
        when containment is enabled. The pre-rewrite bridge model
        (host adapter + in-VM bridge for fs/terminal proxy) turned
        out to be ineffective: claude-agent-acp and codex-acp run
        their Read/Write/Bash tools internally on the host process
        rather than delegating via ACP. Putting the adapter itself
        in the VM is the only way to contain its tool subprocesses.

        For claude/codex to work inside the VM, we mount the host's
        ~/.claude and ~/.codex dirs via per-agent virtiofs shares so
        OAuth creds are readable. The VM also needs network egress —
        vmmd sets up a tap + NAT per-VM; see vmmd.py _setup_network.

        The BridgeClient machinery still exists in the codebase but
        is no longer exercised in the default path. Kept for future
        ACP adapters that DO delegate fs/terminal requests (aloop
        itself delegates nothing; its tools are internal-in-VM).
        """
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CLAUDECODE", "STEPWISE_OUTPUT_FILE")
        }
        env.update(config.env_vars)

        bridge_client: BridgeClient | None = None

        # Lazy-init if the step requested containment and we don't yet
        # have a backend. This unblocks flow-YAML-level `containment:`
        # without requiring the user to ALSO set `agent_containment` in
        # stepwise config — the ergonomic bug caught in Tier 3 of the
        # containment staircase (2026-04-14).
        requested_mode = getattr(config, "containment", None)
        if requested_mode and not self.containment:
            self._ensure_containment_backend(requested_mode)

        if self.containment and requested_mode:
            from stepwise.containment.backend import ContainmentConfig

            # Override HOME-related env for in-VM execution. The VM
            # rootfs has no /home/zack — npx tries to mkdir ~/.npm/_logs
            # and dies with ENOENT. virtiofs mounts the host credential
            # dirs at /root/.claude and /root/.codex, so HOME=/root
            # points the adapter at its OAuth + session storage.
            env["HOME"] = "/root"
            env["USER"] = "root"
            env["LOGNAME"] = "root"
            # Alpine's default PATH omits /usr/local/bin, but that's
            # where `npm install -g` drops bin stubs for our pre-baked
            # claude-agent-acp + codex-acp. Override PATH in the VM
            # env so adapters are reachable without a full npx round-
            # trip through the registry.
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            # npm needs a writable cache. The VM rootfs is mounted
            # read-only (cloud-hypervisor sector-0 protection), so the
            # default ~/.npm cache dir lives on ro fs and EROFSs.
            # /tmp is a fresh tmpfs per boot. Do NOT override
            # npm_config_prefix — the rootfs pre-installs the ACP
            # adapters at /usr/local (the default prefix), and pointing
            # npx at a different prefix makes it re-download everything
            # on each spawn.
            env["npm_config_cache"] = "/tmp/.npm-cache"
            # XDG_* and VIRTUAL_ENV pointing at host paths are also
            # traps inside the VM. Drop them and let the process use
            # defaults rooted at /root. Also drop EMPTY auth env vars
            # — claude-agent-acp treats `ANTHROPIC_API_KEY=""` as an
            # explicit external-API-key auth selection and rejects
            # the mounted OAuth credential file with "Invalid API key
            # · Fix external API key". Unsetting lets it fall through
            # to the credentials file at /root/.claude/.credentials.json
            # (virtiofs-mounted from host ~/.claude).
            for k in list(env):
                if k.startswith("XDG_") or k in ("VIRTUAL_ENV", "PYTHONPATH"):
                    env.pop(k, None)
                elif k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") and not env[k]:
                    env.pop(k, None)

            # Per-agent credential mounts. claude and codex adapters
            # read OAuth from their dotfile dirs; we mount those into
            # the VM read-write (the adapter may write session files
            # to ~/.claude/projects/...). aloop uses OPENROUTER_API_KEY
            # env var, no mount needed.
            auth_mounts: list[dict] = []
            if config.name == "claude":
                claude_home = Path.home() / ".claude"
                if claude_home.is_dir():
                    auth_mounts.append(
                        {"tag": "claude_home", "path": str(claude_home)}
                    )
            elif config.name == "codex":
                codex_home = Path.home() / ".codex"
                if codex_home.is_dir():
                    auth_mounts.append(
                        {"tag": "codex_home", "path": str(codex_home)}
                    )

            containment_config = ContainmentConfig(
                mode=config.containment,
                tools=config.tools,
                allowed_paths=config.allowed_paths,
                working_dir=config.allowed_paths[0] if config.allowed_paths else ".",
            )
            # Pass auth_mounts via an attribute the cloud-hypervisor
            # backend will thread into vmmd.boot's config. This is
            # per-agent-instance state that doesn't belong in the
            # VM-grouping config_key (grouping is on tools/paths).
            containment_config._host_auth_mounts = auth_mounts  # type: ignore[attr-defined]
            spawn_ctx = self.containment.get_spawn_context(containment_config)
            # Inside the VM, the host workspace lives at /mnt/workspace
            # (virtiofs mount from guest-agent's init.sh). Passing the
            # host path as cwd would make guest-agent os.makedirs a
            # fresh empty dir inside the VM rootfs, which then breaks
            # adapters that expect workspace files or a writable HOME
            # under that path. Diagnosed during Tier 1 in-VM smoke
            # (2026-04-14).
            proc = spawn_ctx.spawn(config.command, env, "/mnt/workspace")
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

        # Register client-side request handlers that the ACP agent expects.
        # Without these, the agent hangs when it sends tool execution requests.
        self._register_client_handlers(transport, bridge_client=bridge_client)

        transport.start()
        client = ACPClient(transport)

        # ACP handshake
        try:
            caps = client.initialize()
        except Exception as exc:
            # Capture stderr for diagnostics before killing
            stderr_text = ""
            try:
                import os as _os
                _os.set_blocking(proc.stderr.fileno(), False)
                stderr_text = (proc.stderr.read() or "").strip()
            except Exception:
                pass
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            if bridge_client is not None:
                try:
                    bridge_client.close()
                except Exception:
                    pass
            detail = f": {stderr_text}" if stderr_text else ""
            raise RuntimeError(
                f"ACP handshake failed for agent {config.name!r}: {exc}{detail}"
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
            bridge_client=bridge_client,
        )

    @staticmethod
    def _kill_process(acp_proc: ACPProcess) -> None:
        """Gracefully terminate an ACP process."""
        if acp_proc.bridge_client is not None:
            try:
                acp_proc.bridge_client.close()
            except Exception:
                pass
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

        # When the adapter is running INSIDE a containment VM, its
        # filesystem only has `/mnt/workspace` (the virtiofs projection
        # of the host working_dir). Passing the host path to
        # session/new or session/load makes the adapter try to
        # chdir or stat a path that doesn't exist in the guest and
        # crash/timeout. The session stays logically tied to the host
        # workspace (virtiofs bytes are identical), we're just naming
        # the in-VM cwd the adapter uses. session storage under
        # ~/.claude/projects/... still encodes /mnt/workspace as the
        # project key — that's stable per VM config, so subsequent
        # spawns for the same host workspace reuse the session file.
        adapter_cwd = working_dir
        if resolved.containment:
            adapter_cwd = "/mnt/workspace"

        # Create or continue session
        fork_from = config.get("_fork_from_session_id")
        session_uuid = config.get("_session_uuid")

        logger.info(
            "[%s] session setup: name=%s uuid=%s fork_from=%s managed_id=%s active_count=%d",
            step_id, session_name, session_uuid, fork_from,
            id(acp_proc), len(self.lifecycle.active),
        )

        if fork_from:
            session_id = acp_proc.client.fork_session(fork_from, adapter_cwd)
        elif session_uuid:
            # Use the session's original working_dir for loading — sessions are
            # stored per-project by Claude Code, so a session created under one
            # working_dir won't be found if loaded from a different one. For
            # containment, the stored project key is /mnt/workspace (since
            # that's what new_session saw when creating the session).
            load_dir = config.get("_session_working_dir") or adapter_cwd
            if resolved.containment and load_dir == working_dir:
                load_dir = "/mnt/workspace"
            try:
                logger.info("[%s] loading session %s from disk (load_dir=%s)", step_id, session_uuid[:8], load_dir)
                acp_proc.client.load_session(session_uuid, load_dir)
                session_id = session_uuid
            except Exception as exc:
                logger.warning(
                    "[%s] load_session failed (%s), creating new session",
                    step_id, exc,
                )
                session_id = acp_proc.client.new_session(adapter_cwd, session_name)
        else:
            session_id = acp_proc.client.new_session(adapter_cwd, session_name)

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
        # Include session_name in file paths to prevent cross-contamination
        # when multiple agent steps run concurrently with different sessions.
        session_suffix = f"-{session_name}" if session_name else ""
        output_path = step_io / f"{context.step_name}{session_suffix}-{context.attempt}.output.jsonl"

        # Write prompt file (for debugging/replay)
        prompt_file = step_io / f"{context.step_name}{session_suffix}-{context.attempt}.prompt.md"
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

        # Publish output_path to DB BEFORE blocking so the stream monitor can
        # start tailing while the agent is still running. When the adapter
        # is running inside a containment VM, `process.pid` is the guest
        # PID — `os.getpgid` can't look it up on the host and raises
        # ProcessLookupError. Fall back to 0 (sentinel "pgid unknown").
        try:
            pgid = os.getpgid(acp_proc.process.pid)
        except (ProcessLookupError, OSError):
            pgid = 0
        if context.state_update_fn:
            context.state_update_fn({
                "pid": acp_proc.process.pid,
                "pgid": pgid,
                "output_path": str(output_path),
                "working_dir": working_dir,
                "session_id": session_id,
                "session_name": session_name,
                "agent": agent_name,
            })

        # Send prompt (blocking — runs in thread pool via AsyncEngine)
        prompt_error: str | None = None
        try:
            acp_proc.client.prompt(
                session_id,
                prompt,
                output_path=str(output_path),
            )
        except AcpError as exc:
            prompt_error = f"ACP error: {exc}"
            logger.warning("[%s] ACP prompt error: %s", step_id, exc)
        except Exception as exc:
            prompt_error = f"Prompt failed: {exc}"
            logger.warning("[%s] Prompt failed: %s", step_id, exc)

        # Capture any stderr the ACP process emitted during the prompt
        try:
            import os as _os
            _os.set_blocking(acp_proc.process.stderr.fileno(), False)
            stderr = (acp_proc.process.stderr.read() or "").strip()
            if stderr:
                logger.warning("[%s] Agent stderr: %s", step_id, stderr[:500])
                # Also write stderr to step-io for post-mortem debugging
                stderr_path = step_io / f"{context.step_name}{session_suffix}-{context.attempt}.output.jsonl.stderr"
                stderr_path.write_text(stderr)
        except Exception:
            pass

        elapsed = time.monotonic() - t0
        logger.info(
            "[%s] acp spawn done pid=%d session=%s (%.1fs)",
            step_id,
            acp_proc.process.pid,
            session_id,
            elapsed,
        )

        # pgid unknown for VM-running adapters (pid is guest-side).
        try:
            ap_pgid = os.getpgid(acp_proc.process.pid)
        except (ProcessLookupError, OSError):
            ap_pgid = 0
        process = AgentProcess(
            pid=acp_proc.process.pid,
            pgid=ap_pgid,
            output_path=str(output_path),
            working_dir=working_dir,
            session_id=session_id,
            session_name=session_name,
            capture_transcript=False,
            agent=agent_name,
        )
        process.prompt_error = prompt_error
        return process

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

        # Check for prompt-level transport errors (agent killed, connection lost)
        prompt_error = process.prompt_error

        if error:
            return AgentStatus(
                state="failed",
                exit_code=-1,
                session_id=session_id or process.session_id,
                error=error,
                cost_usd=cost,
            )

        if prompt_error:
            return AgentStatus(
                state="failed",
                exit_code=-1,
                session_id=session_id or process.session_id,
                error=prompt_error,
                cost_usd=cost,
            )

        # Verify the output has a completed result (stopReason).
        # Without this, a killed agent would be falsely marked completed.
        has_stop_reason = False
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
                            has_stop_reason = True
                            break
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass

        if not has_stop_reason:
            return AgentStatus(
                state="failed",
                exit_code=-1,
                session_id=session_id or process.session_id,
                error="Agent terminated without completing (no stopReason in output)",
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
