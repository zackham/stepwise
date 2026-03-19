"""Stepwise CLI entry point.

Usage:
    stepwise init                          Create .stepwise/ in cwd
    stepwise run <flow> [flags]            Run a flow
    stepwise new <name>                    Create a new flow
    stepwise server start [--detach]       Start server (foreground or background)
    stepwise server stop                   Stop the server
    stepwise server restart                Restart the server
    stepwise server status                 Show server status
    stepwise share <flow> [--author]       Publish a flow to the registry
    stepwise get <target>                  Download a flow (URL, @author:name, or slug)
    stepwise search [query] [--tag]        Search the flow registry
    stepwise info <name>                   Show flow details
    stepwise jobs [flags]                  List jobs
    stepwise status <job-id>               Show job detail
    stepwise cancel <job-id> [--output]     Cancel running job
    stepwise list --suspended [--output]   List suspended steps across jobs
    stepwise wait <job-id>                 Block until job completes or suspends
    stepwise validate <flow>               Validate flow syntax
    stepwise templates                     List templates
    stepwise config get|set [key] [value]  Manage configuration
    stepwise schema <flow>                 Generate JSON tool contract
    stepwise output <job-id> [--step] [--run] Retrieve job/step outputs
    stepwise fulfill <run-id> '<json>'     Satisfy a suspended human step (or --stdin, --wait)
    stepwise agent-help [--update <file>]  Generate agent instructions
    stepwise update                   Upgrade to the latest version
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stepwise.io import IOAdapter, create_adapter
from stepwise.project import (
    DOT_DIR_NAME,
    ProjectNotFoundError,
    StepwiseProject,
    find_project,
    get_bundled_templates_dir,
    init_project,
)


# Exit codes
EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_SUSPENDED = 5
EXIT_PROJECT_ERROR = 4


def _get_version() -> str:
    """Read version from package metadata."""
    try:
        from importlib.metadata import version
        return version("stepwise-run")
    except Exception:
        return "0.0.0"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse version string into comparable tuple."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _fetch_remote_version() -> str | None:
    """Fetch the latest version from GitHub (cached once per day)."""
    import json
    import time
    from pathlib import Path

    cache_dir = Path.home() / ".cache" / "stepwise"
    cache_file = cache_dir / "version-check.json"

    # Check cache first (max age: 24 hours)
    try:
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            if time.time() - data.get("ts", 0) < 86400:
                return data.get("version")
    except Exception:
        pass

    # Fetch pyproject.toml from GitHub to read version
    try:
        import httpx
        resp = httpx.get(
            "https://raw.githubusercontent.com/zackham/stepwise/master/pyproject.toml",
            timeout=5.0,
        )
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.startswith("version"):
                    ver = line.split("=", 1)[1].strip().strip('"').strip("'")
                    # Cache result
                    try:
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        cache_file.write_text(json.dumps({"version": ver, "ts": time.time()}))
                    except Exception:
                        pass
                    return ver
    except Exception:
        pass
    return None


def _fetch_changelog_sections(old_ver: str, new_ver: str) -> str | None:
    """Fetch changelog entries between old_ver and new_ver from GitHub."""
    try:
        import httpx
        resp = httpx.get(
            "https://raw.githubusercontent.com/zackham/stepwise/master/CHANGELOG.md",
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    old_tuple = _parse_version(old_ver)
    new_tuple = _parse_version(new_ver)

    # Parse changelog into version sections
    import re
    sections: list[tuple[str, str]] = []
    current_ver = None
    current_lines: list[str] = []

    for line in resp.text.splitlines():
        match = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if match:
            if current_ver:
                sections.append((current_ver, "\n".join(current_lines)))
            current_ver = match.group(1)
            current_lines = [line]
        elif current_ver:
            current_lines.append(line)

    if current_ver:
        sections.append((current_ver, "\n".join(current_lines)))

    # Filter sections: old_ver < section_ver <= new_ver
    relevant = []
    for ver_str, content in sections:
        ver_tuple = _parse_version(ver_str)
        if old_tuple < ver_tuple <= new_tuple:
            relevant.append(content)

    return "\n\n".join(relevant) if relevant else None


def _check_for_upgrade() -> str | None:
    """Check if an upgrade is available. Returns message string or None."""
    current = _get_version()
    remote = _fetch_remote_version()
    if not remote:
        return None
    if _parse_version(remote) > _parse_version(current):
        return f"Update available: {current} → {remote}  (run: stepwise update)"
    return None


def _find_project_or_exit(args: argparse.Namespace, auto_init: bool = False) -> StepwiseProject:
    """Find project, respecting --project-dir flag.

    If auto_init=True, creates a project in cwd instead of exiting.
    """
    start = Path(args.project_dir) if args.project_dir else None
    try:
        return find_project(start)
    except ProjectNotFoundError:
        if auto_init:
            target = start or Path.cwd()
            return init_project(target)
        _io(args).log("error",
            f"No .stepwise/ found (searched up from {start or Path.cwd()}). "
            f"Run 'stepwise init' to create a project.")
        sys.exit(EXIT_PROJECT_ERROR)


def _project_dir(args: argparse.Namespace) -> Path | None:
    """Return explicit --project-dir or None (let resolve_flow use cwd)."""
    return Path(args.project_dir) if getattr(args, "project_dir", None) else None


def _detect_server_url(args: argparse.Namespace) -> str | None:
    """Detect server URL from flags or project pidfile.

    Priority: --standalone (force off) > --server URL (force on) > auto-detect.
    """
    if getattr(args, "standalone", False):
        return None
    if getattr(args, "server", None):
        return args.server

    # Auto-detect from project
    try:
        project = _find_project_or_exit(args)
        from stepwise.server_detect import detect_server
        return detect_server(project.dot_dir)
    except SystemExit:
        return None


def _try_server(args: argparse.Namespace, fn):
    """Try routing a request through the server API.

    Returns (data, exit_code) on success/API error, or (None, None) for
    fallback to direct SQLite mode (server unavailable).
    """
    server_url = _detect_server_url(args)
    if not server_url:
        return None, None

    from stepwise.api_client import StepwiseClient, StepwiseAPIError

    client = StepwiseClient(server_url)
    try:
        return fn(client), EXIT_SUCCESS
    except StepwiseAPIError as e:
        if e.status == 0:  # connection failed
            print(
                f"Warning: Server at {server_url} unreachable, falling back to direct mode",
                file=sys.stderr,
            )
            return None, None
        return {"status": "error", "error": e.detail}, EXIT_JOB_FAILED


# ── Command handlers ─────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    io = _io(args)
    target = Path(args.project_dir) if args.project_dir else None
    root = (target or Path.cwd()).resolve()

    try:
        project = init_project(target, force=args.force)
        io.log("success", f"Initialized Stepwise project in {project.dot_dir}")
        io.log("info", "Run 'stepwise run <flow.yaml>' to execute a flow.")
    except FileExistsError:
        if args.no_skill:
            io.log("error", f"Project already initialized in {root / DOT_DIR_NAME}. "
                   f"Use --force to reinitialize.")
            return EXIT_USAGE_ERROR
        io.log("info", f"Project already initialized in {root / DOT_DIR_NAME}.")

    # Agent skill installation
    if args.no_skill:
        return EXIT_SUCCESS

    _handle_skill_install(root, args.skill, io)
    return EXIT_SUCCESS


def _handle_skill_install(root: Path, skill_target: str | None, io: IOAdapter) -> None:
    """Detect agent frameworks and install/update the stepwise skill."""
    from stepwise.project import (
        AGENT_FRAMEWORK_DIRS,
        SKILL_NAME,
        detect_agent_skill_locations,
        install_agent_skill,
    )

    detection = detect_agent_skill_locations(root)

    # If explicit --skill target given, just do it
    if skill_target:
        target_dir = root / skill_target
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
        installed = install_agent_skill(target_dir)
        io.log("success", f"Installed agent skill in {installed}")
        return

    # Report symlink detection
    for group in detection.symlinked_groups:
        names = " and ".join(loc.framework_dir for loc in group)
        io.log("info", f"Note: {names} are symlinked (same directory)")

    # Check if already installed and current
    if detection.any_installed and detection.all_current:
        installed_in = [loc for loc in detection.locations if loc.has_skill]
        dirs = ", ".join(loc.framework_dir for loc in installed_in)
        io.log("info", f"Agent skill already up to date in {dirs}")
        return

    # Check if installed but outdated
    outdated = [loc for loc in detection.locations if loc.has_skill and not loc.skill_current]
    if outdated:
        for loc in outdated:
            io.log("info", f"Agent skill in {loc.framework_dir} is outdated, updating...")
            install_agent_skill(loc.path)
            io.log("success", f"Updated {loc.framework_dir}/skills/stepwise/")
        # If all installed locations are now handled, done
        if detection.any_installed:
            return

    # Find candidate dirs to install into (existing framework dirs without skill)
    # Deduplicate by resolved path to handle symlinks
    candidates: list[tuple[str, Path]] = []
    seen_resolved: set[Path] = set()

    for framework_dir, label in AGENT_FRAMEWORK_DIRS:
        candidate = root / framework_dir
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)

        skill_dir = candidate / "skills" / SKILL_NAME
        if not (skill_dir.is_dir() and (skill_dir / "SKILL.md").exists()):
            candidates.append((framework_dir, candidate))

    if not candidates:
        # No framework dirs exist — ask what to create
        _prompt_create_framework_dir(root, io)
        return

    if len(candidates) == 1:
        framework_dir, candidate = candidates[0]
        if io.prompt_confirm(f"Install agent skill in {framework_dir}/skills/stepwise/?"):
            installed = install_agent_skill(candidate)
            io.log("success", f"Installed agent skill in {installed}")
        return

    # Multiple candidates
    choices = [f"{fd}/skills/stepwise/" for fd, _ in candidates] + ["All of the above", "Skip"]
    answer = io.prompt_select("Install agent skill in:", choices)
    if answer == "Skip":
        return
    if answer == "All of the above":
        for framework_dir, candidate in candidates:
            installed = install_agent_skill(candidate)
            io.log("success", f"Installed agent skill in {installed}")
        return
    # Find which candidate was selected
    for i, (framework_dir, candidate) in enumerate(candidates):
        if answer == f"{framework_dir}/skills/stepwise/":
            installed = install_agent_skill(candidate)
            io.log("success", f"Installed agent skill in {installed}")
            return


def _prompt_create_framework_dir(root: Path, io: IOAdapter) -> None:
    """No agent framework dirs exist. Ask user what to create."""
    io.log("info", "No agent framework directory found (.claude/ or .agents/).")
    choices = [
        "Claude Code  (.claude/skills/stepwise/)",
        "Agents       (.agents/skills/stepwise/)",
        "Both",
        "Skip",
    ]
    answer = io.prompt_select("Install agent skill for:", choices)
    if answer.startswith("Skip"):
        return

    from stepwise.project import install_agent_skill

    targets = []
    if answer.startswith("Claude") or answer == "Both":
        targets.append(root / ".claude")
    if answer.startswith("Agents") or answer == "Both":
        targets.append(root / ".agents")

    for target in targets:
        installed = install_agent_skill(target)
        io.log("success", f"Installed agent skill in {installed}")


def cmd_server(args: argparse.Namespace) -> int:
    action = args.action
    if action == "start":
        return _server_start(args)
    elif action == "stop":
        return _server_stop(args)
    elif action == "restart":
        return _server_restart(args)
    elif action == "status":
        return _server_status(args)
    return EXIT_USAGE_ERROR


def _server_start(args: argparse.Namespace) -> int:
    io = _io(args)
    project = _find_project_or_exit(args)

    import os

    # Check if a server is already running for this project
    from stepwise.server_detect import detect_server
    existing_url = detect_server(project.dot_dir)
    if existing_url:
        io.log("info", f"Stepwise is already running at {existing_url}")
        if not args.no_open:
            _open_browser(existing_url)
        return EXIT_SUCCESS

    host = args.host or "127.0.0.1"
    port = args.port or 8340

    if not args.port and not _port_available(host, port):
        port = _find_free_port()
        io.log("warn", f"Port 8340 in use, using {port}")

    if args.detach:
        return _server_start_detached(project, host, port, io, args)

    # Foreground mode
    import uvicorn

    # Set env vars so server.py picks them up in lifespan
    os.environ["STEPWISE_DB"] = str(project.db_path)
    os.environ["STEPWISE_TEMPLATES"] = str(project.templates_dir)
    os.environ["STEPWISE_JOBS_DIR"] = str(project.jobs_dir)
    os.environ["STEPWISE_PROJECT_DIR"] = str(project.root)

    # Resolve web dir now (CLI process can find source tree reliably)
    from stepwise.project import get_web_dir
    os.environ["STEPWISE_WEB_DIR"] = str(get_web_dir())

    io.banner(f"Stepwise v{_get_version()}", f"http://{host}:{port}")

    # Non-blocking upgrade check (fail silently)
    try:
        upgrade_msg = _check_for_upgrade()
        if upgrade_msg:
            io.log("info", f"↑ {upgrade_msg}")
    except Exception:
        pass

    if not args.no_open:
        _open_browser_when_ready(host, port)

    # Write pidfile for CLI server detection
    from stepwise.server_detect import write_pidfile, remove_pidfile
    write_pidfile(project.dot_dir, port)
    try:
        uvicorn.run(
            "stepwise.server:app",
            host=host,
            port=port,
            log_level="error",
        )
    finally:
        remove_pidfile(project.dot_dir)
    return EXIT_SUCCESS


def _server_start_detached(
    project: StepwiseProject,
    host: str,
    port: int,
    io: IOAdapter,
    args: argparse.Namespace,
) -> int:
    import subprocess
    import time

    project.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = project.logs_dir / "server.log"

    # Resolve web dir now (CLI process can find source tree reliably)
    from stepwise.project import get_web_dir
    web_dir = str(get_web_dir())

    cmd = [
        sys.executable, "-m", "stepwise.server_bg",
        "--db", str(project.db_path),
        "--jobs-dir", str(project.jobs_dir),
        "--templates-dir", str(project.templates_dir),
        "--project-dir", str(project.root),
        "--dot-dir", str(project.dot_dir),
        "--port", str(port),
        "--host", host,
        "--log-file", str(log_file),
        "--web-dir", web_dir,
    ]

    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for server to become healthy
    url = f"http://{host}:{port}"
    from stepwise.server_detect import _probe_health
    for _ in range(50):  # up to 5 seconds
        time.sleep(0.1)
        if _probe_health(url, timeout=1.0):
            io.banner(f"Stepwise v{_get_version()}", url)
            io.log("info", f"Log: {log_file}")
            io.log("info", "Run `stepwise server stop` to shut down")
            if not args.no_open:
                _open_browser(url)
            return EXIT_SUCCESS

    io.log("error", f"Server did not start within 5 seconds")
    io.log("info", f"Check log: {log_file}")
    return EXIT_JOB_FAILED


def _server_stop(args: argparse.Namespace) -> int:
    import os
    import signal
    import time

    io = _io(args)
    project = _find_project_or_exit(args)

    from stepwise.server_detect import read_pidfile, remove_pidfile, _pid_alive

    data = read_pidfile(project.dot_dir)
    pid = data.get("pid")

    if not pid or not _pid_alive(pid):
        if pid:
            # Stale pidfile
            remove_pidfile(project.dot_dir)
        io.log("info", "Server is not running")
        return EXIT_SUCCESS

    # Send SIGTERM and wait
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):  # up to 5 seconds
        time.sleep(0.1)
        if not _pid_alive(pid):
            remove_pidfile(project.dot_dir)
            io.log("success", "Server stopped")
            return EXIT_SUCCESS

    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    remove_pidfile(project.dot_dir)
    io.log("warn", f"Server (PID {pid}) did not stop gracefully, sent SIGKILL")
    return EXIT_SUCCESS


def _server_restart(args: argparse.Namespace) -> int:
    _server_stop(args)
    return _server_start(args)


def _server_status(args: argparse.Namespace) -> int:
    io = _io(args)
    project = _find_project_or_exit(args)

    from stepwise.server_detect import detect_server, read_pidfile

    url = detect_server(project.dot_dir)
    if not url:
        io.log("info", "Server is not running")
        return EXIT_SUCCESS

    data = read_pidfile(project.dot_dir)
    pid = data.get("pid", "?")
    port = data.get("port", "?")
    log_file = data.get("log_file")

    # Compute uptime
    uptime_str = ""
    started_at = data.get("started_at")
    if started_at:
        try:
            from datetime import datetime, timezone
            start = datetime.fromisoformat(started_at)
            delta = datetime.now(timezone.utc) - start
            total_secs = int(delta.total_seconds())
            hours, remainder = divmod(total_secs, 3600)
            minutes, secs = divmod(remainder, 60)
            if hours:
                uptime_str = f"{hours}h {minutes}m"
            elif minutes:
                uptime_str = f"{minutes}m {secs}s"
            else:
                uptime_str = f"{secs}s"
        except Exception:
            pass

    parts = [f"PID {pid}", f"port {port}"]
    if uptime_str:
        parts.append(f"uptime {uptime_str}")
    io.log("info", f"Server running at {url} ({', '.join(parts)})")
    if log_file:
        io.log("info", f"Log: {log_file}")
    return EXIT_SUCCESS


def cmd_validate(args: argparse.Namespace) -> int:
    io = _io(args)
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    try:
        wf = load_workflow_yaml(str(flow_path))
        errors = wf.validate()
        if errors:
            io.log("error", f"{flow_path}:")
            for err in errors:
                io.log("info", f"  - {err}")
            return EXIT_JOB_FAILED

        step_count = len(wf.steps)
        loop_count = sum(
            1 for s in wf.steps.values()
            for r in (s.exit_rules or [])
            if r.config.get("action") == "loop"
        )
        parts = [f"{step_count} steps"]
        if loop_count:
            parts.append(f"{loop_count} loops")
        io.log("success", f"{flow_path} ({', '.join(parts)})")
        flow_warnings = wf.warnings()
        for w in flow_warnings:
            if w.startswith("\u2139"):
                io.log("info", f"  {w}")
            else:
                io.log("warn", f"  {w}")

        # Check requirements
        if wf.requires:
            import subprocess
            for req in wf.requires:
                if req.check:
                    try:
                        result = subprocess.run(
                            req.check, shell=True, timeout=5,
                            capture_output=True, text=True,
                        )
                        desc = f" — {req.description}" if req.description else ""
                        if result.returncode == 0:
                            io.log("success", f"  Requirement '{req.name}': OK")
                        else:
                            io.log("warn", f"  ⚠ Requirement '{req.name}': check failed{desc}")
                            if req.install:
                                io.log("info", f"    Install: {req.install}")
                            if req.url:
                                io.log("info", f"    Docs: {req.url}")
                    except subprocess.TimeoutExpired:
                        desc = f" — {req.description}" if req.description else ""
                        io.log("warn", f"  ⚠ Requirement '{req.name}': check timed out{desc}")
                    except Exception:
                        desc = f" — {req.description}" if req.description else ""
                        io.log("warn", f"  ⚠ Requirement '{req.name}': check error{desc}")
                else:
                    io.log("info", f"  ℹ Requirement '{req.name}': no check command")

        return EXIT_SUCCESS
    except YAMLLoadError as e:
        io.log("error", f"{flow_path}:")
        for err in e.errors:
            io.log("info", f"  - {err}")
        return EXIT_JOB_FAILED
    except Exception as e:
        io.log("error", f"{flow_path}: {e}")
        return EXIT_JOB_FAILED


def cmd_diagram(args: argparse.Namespace) -> int:
    """Generate a Graphviz diagram from a flow file."""
    io = _io(args)
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow, parse_registry_ref, resolve_registry_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    # Resolve flow path
    try:
        ref = parse_registry_ref(args.flow)
        if ref:
            author, slug = ref
            flow_path = resolve_registry_flow(author, slug, _project_dir(args))
        else:
            flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    # Load workflow
    try:
        wf = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        io.log("error", f"{flow_path}:")
        for err in e.errors:
            io.log("info", f"  - {err}")
        return EXIT_JOB_FAILED
    except Exception as e:
        io.log("error", f"{flow_path}: {e}")
        return EXIT_JOB_FAILED

    # Build graph
    try:
        import graphviz
    except ImportError:
        io.log("error", "graphviz Python package not installed. Run: uv add graphviz")
        return EXIT_JOB_FAILED

    fmt = args.format
    dot = _build_flow_graph(wf, fmt)

    # Determine output path
    if args.output:
        out_path = args.output
        # Ensure the output path has the correct extension
        if not out_path.endswith(f".{fmt}"):
            out_path = f"{out_path}.{fmt}"
    else:
        name = wf.metadata.name or flow_path.stem
        out_path = f"{name}.{fmt}"

    # Render using pipe() to avoid writing intermediate DOT source files.
    # The old render() approach wrote a bare file (e.g. "council") as the DOT
    # source, which would then be picked up by flow resolution on subsequent
    # runs and parsed as YAML, causing errors.
    try:
        data = dot.pipe()
    except graphviz.backend.ExecutableNotFound:
        io.log("error", "Graphviz 'dot' binary not found. Install Graphviz: brew install graphviz / apt install graphviz")
        return EXIT_JOB_FAILED

    Path(out_path).write_bytes(data)
    io.log("success", out_path)
    return EXIT_SUCCESS


def _executor_subtitle(step) -> str:
    """Compute a human-readable subtitle for a step's executor (mirrors web UI)."""
    exec_type = step.executor.type if step.executor else "script"
    config = step.executor.config if step.executor else {}

    if exec_type == "script":
        cmd = config.get("command", "")
        if not cmd:
            return "script"
        # For python3 -c "..." inline scripts, show outputs instead
        if "python3 -c" in cmd or "python -c" in cmd:
            if step.outputs:
                return f"script -> {', '.join(step.outputs)}"
            return "python script"
        # Strip python3 prefix for .py files, show just the filename
        cmd = cmd.replace("python3 ", "").replace("python ", "")
        return cmd[:36] if len(cmd) <= 36 else cmd[:34] + ".."
    elif exec_type == "llm":
        model = config.get("model", "")
        return f"LLM: {model}" if model else "LLM"
    elif exec_type == "agent":
        model = config.get("model", "")
        parts = ["Agent"]
        if model:
            parts.append(model)
        return " ".join(parts)
    elif exec_type == "human":
        prompt = config.get("prompt", "")
        if prompt:
            return prompt[:36] if len(prompt) <= 36 else prompt[:34] + ".."
        return "human input"
    elif exec_type == "poll":
        return "poll"
    elif exec_type == "mock_llm":
        return "LLM simulation"
    return exec_type


def _build_flow_graph(wf, fmt: str, name: str | None = None):
    """Build a graphviz.Digraph for a WorkflowDefinition."""
    import graphviz

    graph_name = name or wf.metadata.name or "flow"
    dot = graphviz.Digraph(graph_name, format=fmt)
    dot.attr(
        rankdir="TB",
        bgcolor="#0a0a0f",
        fontname="Helvetica",
        fontcolor="white",
        pad="0.5",
        nodesep="0.6",
        ranksep="0.8",
    )
    dot.attr("node", fontname="Helvetica", fontcolor="white", color="#333",
             style="filled", fillcolor="#1a1a2e")
    dot.attr("edge", fontname="Helvetica", fontsize="10")

    _EXECUTOR_SHAPES = {
        "script": "box",
        "human": "parallelogram",
        "llm": "box",
        "agent": "doubleoctagon",
        "poll": "hexagon",
    }
    _EXECUTOR_STYLE = {
        "llm": "filled,rounded",
    }

    # ── Collect $job.* input consumers ──────────────────────────────
    job_input_consumers: dict[str, set[str]] = {}  # step_name -> {field_names}
    for step_name, step in wf.steps.items():
        for inp in step.inputs:
            if inp.any_of_sources:
                for src_step, src_field in inp.any_of_sources:
                    if src_step == "$job":
                        job_input_consumers.setdefault(step_name, set()).add(src_field)
            elif inp.source_step == "$job":
                job_input_consumers.setdefault(step_name, set()).add(inp.source_field)

    all_job_fields: set[str] = set()
    for fields in job_input_consumers.values():
        all_job_fields.update(fields)

    # ── Collect terminal steps and their outputs ────────────────────
    depended_on: set[str] = set()
    for step in wf.steps.values():
        for binding in step.inputs:
            if binding.any_of_sources:
                for src_step, _ in binding.any_of_sources:
                    if src_step != "$job":
                        depended_on.add(src_step)
            elif binding.source_step != "$job":
                depended_on.add(binding.source_step)
        for seq in step.sequencing:
            depended_on.add(seq)
        if step.for_each:
            depended_on.add(step.for_each.source_step)

    terminal_outputs: dict[str, list[str]] = {}  # step_name -> output fields
    for step_name, step in wf.steps.items():
        if step_name not in depended_on and step.outputs:
            terminal_outputs[step_name] = step.outputs

    # ── Add Inputs port node ────────────────────────────────────────
    INPUTS_ID = "__inputs__"
    if all_job_fields:
        fields_label = "<BR/>".join(sorted(all_job_fields))
        input_label = f'<<FONT POINT-SIZE="10">&#x2193; Inputs</FONT><BR/><FONT POINT-SIZE="9" COLOR="#94a3b8">{fields_label}</FONT>>'
        dot.node(INPUTS_ID, label=input_label, shape="box", style="rounded,filled",
                 fillcolor="#1e293b", color="#475569", fontcolor="#94a3b8",
                 margin="0.15,0.1")

    # ── Add step nodes ──────────────────────────────────────────────
    for step_name, step in wf.steps.items():
        exec_type = step.executor.type if step.executor else "script"
        shape = _EXECUTOR_SHAPES.get(exec_type, "box")
        style = _EXECUTOR_STYLE.get(exec_type, "filled")
        subtitle = _executor_subtitle(step)

        # For-each steps: render inline with step count badge
        if step.for_each and step.sub_flow:
            sub_step_count = len(step.sub_flow.steps)
            fe_badge = f"for-each ({sub_step_count} step{'s' if sub_step_count != 1 else ''})"
            label = (
                f'<<B>{step_name}</B>'
                f'<BR/><FONT POINT-SIZE="10" COLOR="#c084fc">'
                f'&#x1F517; {fe_badge}</FONT>>'
            )
            dot.node(step_name, label=label, shape="box", style="filled",
                     fillcolor="#1a1a2e", color="#7c3aed")
        else:
            # Escape HTML entities in subtitle
            safe_subtitle = subtitle.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            label = (
                f'<<B>{step_name}</B>'
                f'<BR/><FONT POINT-SIZE="10" COLOR="#94a3b8">{safe_subtitle}</FONT>>'
            )
            dot.node(step_name, label=label, shape=shape, style=style)

    # ── Add Outputs port node ───────────────────────────────────────
    OUTPUTS_ID = "__outputs__"
    if terminal_outputs:
        all_out_fields: list[str] = []
        for fields in terminal_outputs.values():
            all_out_fields.extend(fields)
        fields_label = "<BR/>".join(all_out_fields)
        output_label = f'<<FONT POINT-SIZE="10">&#x2191; Outputs</FONT><BR/><FONT POINT-SIZE="9" COLOR="#6ee7b7">{fields_label}</FONT>>'
        dot.node(OUTPUTS_ID, label=output_label, shape="box", style="rounded,filled",
                 fillcolor="#022c22", color="#065f46", fontcolor="#6ee7b7",
                 margin="0.15,0.1")

    # ── Edges ───────────────────────────────────────────────────────

    # Edges from Inputs node to consuming steps
    if all_job_fields:
        seen_input_edges: set[str] = set()
        for step_name, fields in job_input_consumers.items():
            edge_key = f"{INPUTS_ID}->{step_name}"
            if edge_key not in seen_input_edges:
                edge_label = ", ".join(sorted(fields))
                dot.edge(INPUTS_ID, step_name, label=edge_label,
                         color="#475569", fontcolor="#94a3b8", style="dashed")
                seen_input_edges.add(edge_key)

    # Step-to-step data flow edges
    for step_name, step in wf.steps.items():
        for inp in step.inputs:
            if inp.any_of_sources:
                for src_step, _src_field in inp.any_of_sources:
                    if src_step != "$job" and src_step in wf.steps:
                        dot.edge(src_step, step_name, label=inp.local_name,
                                 color="#60a5fa", fontcolor="#60a5fa")
            else:
                if inp.source_step != "$job" and inp.source_step in wf.steps:
                    dot.edge(inp.source_step, step_name, label=inp.local_name,
                             color="#60a5fa", fontcolor="#60a5fa")

        # for_each implicit edge from source step
        if step.for_each and step.for_each.source_step in wf.steps:
            dot.edge(step.for_each.source_step, step_name,
                     label=step.for_each.source_field,
                     color="#c084fc", fontcolor="#c084fc")

        # Sequencing edges
        for dep in step.sequencing:
            if dep in wf.steps:
                dot.edge(dep, step_name, style="dashed", color="#666")

    # Edges from terminal steps to Outputs node
    if terminal_outputs:
        for step_name, fields in terminal_outputs.items():
            edge_label = ", ".join(fields)
            dot.edge(step_name, OUTPUTS_ID, label=edge_label,
                     color="#065f46", fontcolor="#6ee7b7", style="dashed")

        # Exit rules
        for rule in step.exit_rules:
            action = rule.config.get("action", "")
            target = rule.config.get("target", "")
            if action == "loop" and target and target in wf.steps:
                dot.edge(step_name, target, label=rule.name, style="dotted",
                         color="#f59e0b", fontcolor="#f59e0b", constraint="false")

        # For-each edge
        if step.for_each:
            src = step.for_each.source_step
            if src in wf.steps:
                dot.edge(src, step_name, label="for each", style="bold",
                         color="#a78bfa", fontcolor="#a78bfa", penwidth="2")

    return dot


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new flow directory with a minimal template."""
    io = _io(args)
    from stepwise.flow_resolution import FLOW_DIR_MARKER, FLOW_NAME_PATTERN

    name = args.name
    if not FLOW_NAME_PATTERN.match(name):
        io.log("error", f"Invalid flow name: '{name}'. Flow names must match [a-zA-Z0-9_-]+")
        return EXIT_USAGE_ERROR

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()
    flows_dir = project_dir / "flows"
    flow_dir = flows_dir / name

    if flow_dir.exists():
        io.log("error", f"Directory already exists: {flow_dir}")
        return EXIT_USAGE_ERROR

    flow_dir.mkdir(parents=True)
    template = (
        f"name: {name}\n"
        f"description: \"\"\n"
        f"\n"
        f"steps:\n"
        f"  hello:\n"
        f"    run: 'echo \"{{\\\"message\\\": \\\"hello from {name}\\\"}}\"'\n"
        f"    outputs: [message]\n"
    )
    (flow_dir / FLOW_DIR_MARKER).write_text(template)

    io.log("success", f"Created flows/{name}/{FLOW_DIR_MARKER}")
    io.log("info", f"Edit: {flow_dir / FLOW_DIR_MARKER}")
    io.log("info", f"Run:  stepwise run {name}")
    return EXIT_SUCCESS


def cmd_templates(args: argparse.Namespace) -> int:
    io = _io(args)
    # Bundled templates
    bundled_dir = get_bundled_templates_dir()
    bundled = []
    if bundled_dir.exists():
        for f in sorted(bundled_dir.iterdir()):
            if f.suffix in (".yaml", ".yml", ".json"):
                bundled.append(f.stem)

    io.log("info", "BUILT-IN:")
    if bundled:
        for name in bundled:
            io.log("info", f"  {name}")
    else:
        io.log("info", "  (none)")

    # Project templates
    try:
        project = find_project(Path(args.project_dir) if args.project_dir else None)
        user_templates = []
        if project.templates_dir.exists():
            for f in sorted(project.templates_dir.iterdir()):
                if f.suffix in (".yaml", ".yml", ".json"):
                    user_templates.append(f.stem)

        io.log("info", "PROJECT:")
        if user_templates:
            for name in user_templates:
                io.log("info", f"  {name}")
        else:
            io.log("info", "  (none — save templates via the web UI)")
    except ProjectNotFoundError:
        io.log("info", "PROJECT:")
        io.log("info", "  (no project — run 'stepwise init' first)")

    return EXIT_SUCCESS


def cmd_config(args: argparse.Namespace) -> int:
    from stepwise.config import load_config, save_config

    action = args.config_action

    if action == "set":
        if not args.key:
            print("Error: config set requires a key", file=sys.stderr)
            return EXIT_USAGE_ERROR

        if args.stdin:
            import getpass
            value = getpass.getpass("Enter value: ")
        elif args.value is not None:
            value = args.value
        else:
            print("Error: config set requires a value or --stdin", file=sys.stderr)
            return EXIT_USAGE_ERROR

        config = load_config()

        if args.key == "openrouter_api_key":
            config.openrouter_api_key = value
        elif args.key == "anthropic_api_key":
            config.anthropic_api_key = value
        elif args.key == "default_model":
            config.default_model = value
        else:
            print(f"Error: Unknown config key '{args.key}'", file=sys.stderr)
            return EXIT_USAGE_ERROR

        save_config(config)
        _io(args).log("success", f"Set {args.key}")
        return EXIT_SUCCESS

    elif action == "get":
        if not args.key:
            print("Error: config get requires a key", file=sys.stderr)
            return EXIT_USAGE_ERROR

        config = load_config()

        if args.key == "openrouter_api_key":
            val = config.openrouter_api_key or ""
        elif args.key == "anthropic_api_key":
            val = config.anthropic_api_key or ""
        elif args.key == "default_model":
            val = config.default_model or ""
        else:
            print(f"Error: Unknown config key '{args.key}'", file=sys.stderr)
            return EXIT_USAGE_ERROR

        if val and not args.unmask and args.key in ("openrouter_api_key", "anthropic_api_key"):
            # Mask all but last 3 chars
            masked = "*" * max(0, len(val) - 3) + val[-3:]
            print(masked)
        else:
            print(val)
        return EXIT_SUCCESS

    elif action == "init":
        # Scaffold config.local.yaml for a flow
        from stepwise.flow_resolution import FlowResolutionError, resolve_flow
        from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

        flow_ref = args.key
        if not flow_ref:
            print("Error: config init requires a flow name or path", file=sys.stderr)
            return EXIT_USAGE_ERROR

        project_dir = _project_dir(args) or Path.cwd()
        try:
            flow_path = resolve_flow(flow_ref, project_dir)
        except FlowResolutionError as e:
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        try:
            wf = load_workflow_yaml(str(flow_path))
        except (YAMLLoadError, Exception) as e:
            print(f"Error loading flow: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        if not wf.config_vars:
            io = _io(args)
            io.log("info", f"Flow '{flow_ref}' has no config: block — nothing to scaffold.")
            return EXIT_SUCCESS

        # Determine output path
        if flow_path.name == "FLOW.yaml":
            config_path = flow_path.parent / "config.local.yaml"
        else:
            stem = flow_path.stem
            if stem.endswith(".flow"):
                stem = stem[:-5]
            config_path = flow_path.parent / f"{stem}.config.local.yaml"

        if config_path.exists():
            print(f"Config file already exists: {config_path}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        # Scaffold YAML with comments
        lines = [
            f"# Configuration for {wf.metadata.name or flow_ref}",
            f"# See: stepwise info {flow_ref}",
            "",
        ]
        for v in wf.config_vars:
            if v.description:
                lines.append(f"# {v.description}" + (f" ({v.type})" if v.type != "str" else ""))
            if v.options:
                lines.append(f"# Options: {', '.join(v.options)}")
            if v.example:
                lines.append(f"# Example: {v.example}")

            if v.default is not None:
                # Has default — comment out
                lines.append(f"# {v.name}: {v.default}")
            elif v.required:
                # Required, no default — leave blank for user
                lines.append(f'{v.name}: ""')
            else:
                lines.append(f"# {v.name}:")
            lines.append("")

        config_path.write_text("\n".join(lines))
        io = _io(args)
        io.log("success", f"Created {config_path}")
        return EXIT_SUCCESS

    else:
        print("Error: config requires 'get', 'set', or 'init' action", file=sys.stderr)
        return EXIT_USAGE_ERROR


def cmd_check(args: argparse.Namespace) -> int:
    """Verify model resolution for every LLM step in a flow."""
    from stepwise.config import load_config, DEFAULT_LABELS, label_model_id
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    import yaml

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()

    try:
        flow_path = resolve_flow(args.flow, project_dir)
    except (FlowResolutionError, Exception) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    with open(flow_path) as f:
        data = yaml.safe_load(f)

    steps = data.get("steps", {})
    cfg = load_config(project_dir)

    # Determine source for each label
    all_labels = {**DEFAULT_LABELS, **cfg.labels}
    label_sources: dict[str, str] = {n: "default" for n in DEFAULT_LABELS}
    # Rough: user labels override default, project overrides user
    for name in cfg.labels:
        if name in DEFAULT_LABELS:
            label_sources[name] = "project"
        else:
            label_sources[name] = "project"

    io = _io(args)
    io.log("info", f"Flow: {flow_path.name}")

    rows = []
    providers_needed: set[str] = set()
    for step_name, step_def in steps.items():
        if not isinstance(step_def, dict):
            continue
        executor = step_def.get("executor", {})
        exec_type = executor.get("type") if isinstance(executor, dict) else executor
        if exec_type != "llm":
            continue

        config_block = executor.get("config", {}) if isinstance(executor, dict) else {}
        model_ref = config_block.get("model") or cfg.default_model or "balanced"
        resolved = cfg.resolve_model(model_ref)

        if model_ref in all_labels:
            source = label_sources.get(model_ref, "default")
            rows.append([step_name, model_ref, resolved, source])
        else:
            rows.append([step_name, resolved, resolved, "pinned"])

        if "/" in resolved:
            providers_needed.add(resolved.split("/")[0])

    if rows:
        io.table(["STEP", "MODEL", "RESOLVED", "SOURCE"], rows)

    # Check API keys
    key_parts = []
    if cfg.openrouter_api_key:
        key_parts.append("openrouter ✓")
    else:
        key_parts.append("openrouter ✗")
    if cfg.anthropic_api_key:
        key_parts.append("anthropic ✓")
    else:
        key_parts.append("anthropic ✗")
    io.log("info", f"Provider keys: {' | '.join(key_parts)}")

    return EXIT_SUCCESS


def cmd_preflight(args: argparse.Namespace) -> int:
    """Combined pre-run check: config + requirements + model resolution."""
    from stepwise.config import load_config, DEFAULT_LABELS
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.runner import load_flow_config
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
    import subprocess
    import yaml

    io = _io(args)
    project_dir = _project_dir(args) or Path.cwd()

    try:
        flow_path = resolve_flow(args.flow, project_dir)
    except FlowResolutionError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    try:
        wf = load_workflow_yaml(str(flow_path))
    except (YAMLLoadError, Exception) as e:
        io.log("error", f"Failed to load flow: {e}")
        return EXIT_USAGE_ERROR

    errors = wf.validate()
    if errors:
        io.log("error", "Validation failed:")
        for err in errors:
            io.log("info", f"  - {err}")
        return EXIT_JOB_FAILED

    io.log("success", f"Flow: {flow_path.name} ({len(wf.steps)} steps)")
    has_issues = False

    # ── Config resolution ────────────────────────────────────────
    job_fields = {
        b.source_field
        for s in wf.steps.values()
        for b in s.inputs
        if b.source_step == "$job"
    }

    if job_fields:
        config = load_flow_config(flow_path, wf)
        # Also apply --var overrides for checking
        var_overrides = {}
        for pair in getattr(args, "var", None) or []:
            if "=" in pair:
                k, v = pair.split("=", 1)
                var_overrides[k] = v
        merged = {**config, **var_overrides}

        from_defaults = sum(1 for v in wf.config_vars if v.default is not None and v.name in job_fields)
        from_local = sum(1 for k in merged if k not in {v.name: v for v in wf.config_vars if v.default is not None} and k in job_fields)
        resolved = sum(1 for f in job_fields if f in merged)
        missing = sorted(job_fields - set(merged.keys()))

        io.log(
            "success" if not missing else "warn",
            f"Config:       {resolved}/{len(job_fields)} variables resolved"
        )
        if missing:
            has_issues = True
            config_map = {v.name: v for v in wf.config_vars}
            for m in missing:
                cv = config_map.get(m)
                desc = f" ({cv.description})" if cv and cv.description else ""
                if cv and cv.sensitive:
                    io.log("info", f"  ✗ {m}{desc} — set STEPWISE_VAR_{m.upper()} or config.local.yaml")
                else:
                    io.log("info", f"  ✗ {m}{desc} — use --var {m}=\"...\" or config.local.yaml")
    else:
        io.log("success", "Config:       no config variables needed")

    # ── Requirements ─────────────────────────────────────────────
    if wf.requires:
        req_ok = 0
        req_fail = 0
        failed_reqs: list[str] = []
        for r in wf.requires:
            if r.check:
                try:
                    result = subprocess.run(
                        r.check, shell=True, timeout=5,
                        capture_output=True, text=True,
                    )
                    if result.returncode == 0:
                        io.log("success", f"  ✓ {r.name}")
                        req_ok += 1
                    else:
                        desc = f" — {r.description}" if r.description else ""
                        io.log("warn", f"  ✗ {r.name}{desc}")
                        if r.install:
                            io.log("info", f"    Install: {r.install}")
                        if r.url:
                            io.log("info", f"    Docs: {r.url}")
                        req_fail += 1
                        failed_reqs.append(r.name)
                except (subprocess.TimeoutExpired, Exception):
                    io.log("warn", f"  ? {r.name}")
                    req_fail += 1
                    failed_reqs.append(r.name)
            else:
                req_ok += 1

        io.log(
            "success" if not req_fail else "warn",
            f"Requirements: {req_ok}/{req_ok + req_fail} met"
        )
        if req_fail:
            has_issues = True
    else:
        io.log("success", "Requirements: none")

    # ── Model resolution ─────────────────────────────────────────
    cfg = load_config(project_dir)
    with open(flow_path) as f:
        data = yaml.safe_load(f)

    steps = data.get("steps", {})
    llm_steps = []
    providers_needed: set[str] = set()
    for step_name, step_def in steps.items():
        if not isinstance(step_def, dict):
            continue
        executor = step_def.get("executor", {})
        exec_type = executor.get("type") if isinstance(executor, dict) else executor
        if exec_type != "llm":
            continue
        config_block = executor.get("config", {}) if isinstance(executor, dict) else {}
        model_ref = config_block.get("model") or cfg.default_model or "balanced"
        resolved = cfg.resolve_model(model_ref)
        llm_steps.append((step_name, model_ref, resolved))
        if "/" in resolved:
            providers_needed.add(resolved.split("/")[0])

    if llm_steps:
        for step_name, model_ref, resolved in llm_steps:
            label = f" ({model_ref})" if model_ref != resolved else ""
            io.log("info", f"  {step_name}: {resolved}{label}")

        # Check provider keys
        key_status = []
        if "openrouter" in str(providers_needed) or not cfg.anthropic_api_key:
            if cfg.openrouter_api_key:
                key_status.append("openrouter ✓")
            else:
                key_status.append("openrouter ✗")
                has_issues = True
        if any(p in ("anthropic",) for p in providers_needed):
            if cfg.anthropic_api_key:
                key_status.append("anthropic ✓")
            else:
                key_status.append("anthropic ✗")
                has_issues = True
        if key_status:
            io.log(
                "success" if all("✓" in s for s in key_status) else "warn",
                f"Models:       {' | '.join(key_status)}"
            )
    else:
        io.log("success", "Models:       no LLM steps")

    # ── Summary ──────────────────────────────────────────────────
    if has_issues:
        io.log("warn", "Preflight:    issues found — resolve before running")
        return EXIT_JOB_FAILED
    else:
        io.log("success", "Preflight:    ready to run")
        return EXIT_SUCCESS


def _try_registry_fetch(flow_ref: str, project_dir: Path, io: IOAdapter) -> Path | None:
    """Fetch a flow from the registry into .stepwise/registry/@author/slug/.

    Returns flow path or None if not found.
    """
    from stepwise.bundle import unpack_bundle
    from stepwise.flow_resolution import parse_registry_ref, registry_flow_dir
    from stepwise.registry_client import fetch_flow, RegistryError

    parsed = parse_registry_ref(flow_ref)
    if not parsed:
        return None
    author_hint, slug = parsed

    try:
        io.log("info", f"Fetching '@{author_hint}:{slug}' from registry...")
        data = fetch_flow(slug)
    except RegistryError:
        return None

    # Use the author from the registry response (authoritative)
    author = data.get("author", author_hint)

    import hashlib
    from datetime import datetime, timezone
    from stepwise.registry_client import get_registry_url

    target_dir = registry_flow_dir(author, slug, project_dir)
    origin = {
        "registry": get_registry_url(),
        "author": author,
        "slug": slug,
        "version": data.get("version", 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(data["yaml"].encode()).hexdigest(),
    }
    flow_path = unpack_bundle(
        target_dir=target_dir,
        yaml_content=data["yaml"],
        files=data.get("files"),
        origin=origin,
    )
    steps = data.get("steps", "?")
    io.log("success", f"Downloaded @{author}:{slug} ({steps} steps)")
    return flow_path


def _run_flow_error(args, io, msg):
    """Report a flow resolution error in the appropriate format."""
    if getattr(args, "wait", False) or getattr(args, "async_mode", False):
        from stepwise.runner import _json_error
        _json_error(2, msg)
    else:
        io.log("error", msg)
    return EXIT_USAGE_ERROR


def cmd_run(args: argparse.Namespace) -> int:
    from stepwise.flow_resolution import (
        FlowResolutionError, parse_registry_ref, resolve_flow, resolve_registry_flow,
    )
    from stepwise.runner import run_flow, parse_vars, load_vars_file, load_flow_config

    io = _io(args)
    project = _find_project_or_exit(args, auto_init=True)
    project_dir = _project_dir(args) or Path.cwd()

    # Resolve flow: @author:name uses registry cache, otherwise local only
    flow_ref = args.flow
    parsed_ref = parse_registry_ref(flow_ref)

    if parsed_ref:
        author, slug = parsed_ref
        # Registry ref — check local cache first, fetch if missing
        try:
            flow_path = resolve_registry_flow(author, slug, project_dir)
        except FlowResolutionError:
            flow_path = _try_registry_fetch(flow_ref, project_dir, io)
            if not flow_path:
                return _run_flow_error(args, io, f"Flow '{flow_ref}' not found in registry")
    else:
        try:
            flow_path = resolve_flow(flow_ref, project_dir)
        except FlowResolutionError as e:
            return _run_flow_error(args, io, str(e))

    # Load flow config defaults + config.local.yaml (lowest priority)
    inputs: dict = {}
    try:
        from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
        _wf = load_workflow_yaml(str(flow_path))
        inputs.update(load_flow_config(flow_path, _wf))
    except Exception:
        pass  # config loading is best-effort; errors surface later during run

    # Parse input variables (shared across all modes) — overrides config
    if args.vars_file:
        try:
            inputs.update(load_vars_file(args.vars_file))
        except (FileNotFoundError, Exception) as e:
            if getattr(args, "wait", False) or getattr(args, "async_mode", False):
                from stepwise.runner import _json_error
                _json_error(2, str(e))
                return EXIT_USAGE_ERROR
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    try:
        inputs.update(parse_vars(args.vars))
    except ValueError as e:
        if getattr(args, "wait", False) or getattr(args, "async_mode", False):
            from stepwise.runner import _json_error
            _json_error(2, str(e))
            return EXIT_USAGE_ERROR
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # --var-file key=path: read file contents as variable value
    if args.var_files:
        for item in args.var_files:
            if "=" not in item:
                msg = f"Invalid --var-file format: '{item}' (expected KEY=PATH)"
                if getattr(args, "wait", False) or getattr(args, "async_mode", False):
                    from stepwise.runner import _json_error
                    _json_error(2, msg)
                    return EXIT_USAGE_ERROR
                print(f"Error: {msg}", file=sys.stderr)
                return EXIT_USAGE_ERROR
            key, fpath = item.split("=", 1)
            try:
                inputs[key] = Path(fpath).read_text()
            except FileNotFoundError:
                msg = f"--var-file path not found: {fpath}"
                if getattr(args, "wait", False) or getattr(args, "async_mode", False):
                    from stepwise.runner import _json_error
                    _json_error(2, msg)
                    return EXIT_USAGE_ERROR
                print(f"Error: {msg}", file=sys.stderr)
                return EXIT_USAGE_ERROR

    # --async mode: fire-and-forget (handles own errors as JSON)
    if getattr(args, "async_mode", False):
        from stepwise.runner import run_async
        notify_context = None
        if getattr(args, "notify_context", None):
            import json as _json
            try:
                notify_context = _json.loads(args.notify_context)
            except _json.JSONDecodeError:
                from stepwise.runner import _json_error
                _json_error(2, f"Invalid --notify-context JSON: {args.notify_context}")
                return EXIT_USAGE_ERROR
        return run_async(
            flow_path=flow_path,
            project=project,
            objective=args.objective,
            inputs=inputs if inputs else None,
            workspace=args.workspace,
            force_local=getattr(args, "local", False),
            notify_url=getattr(args, "notify", None),
            notify_context=notify_context,
            name=getattr(args, "job_name", None),
        )

    # --wait mode: blocking JSON output (handles own errors as JSON)
    if getattr(args, "wait", False):
        from stepwise.runner import run_wait
        return run_wait(
            flow_path=flow_path,
            project=project,
            objective=args.objective,
            inputs=inputs if inputs else None,
            workspace=args.workspace,
            timeout=args.timeout,
            force_local=getattr(args, "local", False),
            name=getattr(args, "job_name", None),
        )

    # Everything below uses stderr for errors
    if not flow_path.exists():
        print(f"Error: File not found: {flow_path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    if args.watch:
        return _run_watch(args, project, flow_path, inputs)  # args.job_name accessed inside

    # Headless mode (default)
    return run_flow(
        flow_path=flow_path,
        project=project,
        objective=args.objective,
        inputs=inputs if inputs else None,
        workspace=args.workspace,
        quiet=args.quiet,
        report=args.report,
        report_output=args.report_output,
        output_json=getattr(args, "output_format", None) == "json",
        force_local=getattr(args, "local", False),
        adapter=_io(args),
        name=getattr(args, "job_name", None),
    )


def _run_watch(
    args: argparse.Namespace,
    project,
    flow_path: Path,
    inputs: dict,
) -> int:
    """--watch mode: start or reuse server, submit job via API."""
    import os
    import uvicorn
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
    from stepwise.server_detect import detect_server, write_pidfile, remove_pidfile

    # Load and validate the flow
    try:
        workflow = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        print(f"Error: {'; '.join(e.errors)}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    errors = workflow.validate()
    if errors:
        print(f"Error: {'; '.join(errors)}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    from stepwise.flow_resolution import flow_display_name
    objective = args.objective or flow_display_name(flow_path)

    job_name = getattr(args, "job_name", None)

    # If a server is already running, submit there and exit
    existing_url = detect_server(project.dot_dir)
    if existing_url:
        return _submit_watch_job(existing_url, workflow, objective, inputs, args, name=job_name)

    # Start a new server, submit the job once it's ready
    if args.port:
        port = args.port
    else:
        port = _find_free_port()

    host = "127.0.0.1"
    server_url = f"http://{host}:{port}"

    os.environ["STEPWISE_DB"] = str(project.db_path)
    os.environ["STEPWISE_TEMPLATES"] = str(project.templates_dir)
    os.environ["STEPWISE_JOBS_DIR"] = str(project.jobs_dir)
    os.environ["STEPWISE_PROJECT_DIR"] = str(project.root)

    print(f"▸ entering flow...")
    print(f"  {server_url}")
    print()
    print(f"  Press Ctrl+C to stop.")

    # Background thread: wait for server ready → submit job → open browser
    _submit_job_when_ready(host, port, server_url, workflow, objective, inputs, args, name=job_name)

    write_pidfile(project.dot_dir, port)
    try:
        uvicorn.run(
            "stepwise.server:app",
            host=host,
            port=port,
            log_level="error",
        )
    finally:
        remove_pidfile(project.dot_dir)
    return EXIT_SUCCESS


def _submit_watch_job(
    server_url: str,
    workflow,
    objective: str,
    inputs: dict,
    args,
    name: str | None = None,
) -> int:
    """Submit a job to a server via REST API, start it, and open the browser."""
    import json
    import urllib.request
    import urllib.error

    body: dict = {
        "objective": objective,
        "workflow": workflow.to_dict(),
        "inputs": inputs if inputs else None,
    }
    if name:
        body["name"] = name
    payload = json.dumps(body).encode()

    try:
        req = urllib.request.Request(
            f"{server_url}/api/jobs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            job_data = json.loads(resp.read())
            job_id = job_data["id"]
    except (urllib.error.URLError, Exception) as e:
        print(f"Error: Failed to create job: {e}", file=sys.stderr)
        return EXIT_JOB_FAILED

    try:
        start_req = urllib.request.Request(
            f"{server_url}/api/jobs/{job_id}/start",
            data=b"",
            method="POST",
        )
        urllib.request.urlopen(start_req, timeout=10)
    except (urllib.error.URLError, Exception) as e:
        print(f"Error: Failed to start job: {e}", file=sys.stderr)
        return EXIT_JOB_FAILED

    job_url = f"{server_url}/jobs/{job_id}"
    print(f"▸ job submitted to running server")
    print(f"  {job_url}")

    if not getattr(args, "no_open", False):
        _open_browser(job_url)

    return EXIT_SUCCESS


def _submit_job_when_ready(
    host: str,
    port: int,
    server_url: str,
    workflow,
    objective: str,
    inputs: dict,
    args,
    name: str | None = None,
) -> None:
    """Background thread: wait for server, submit job via API, open browser."""
    import json
    import socket
    import threading
    import urllib.request

    def _wait_submit_open():
        # Wait for server to accept connections
        for _ in range(50):  # up to 5 seconds
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                import time
                time.sleep(0.1)
        else:
            return  # server never came up

        # Submit the job
        body: dict = {
            "objective": objective,
            "workflow": workflow.to_dict(),
            "inputs": inputs if inputs else None,
        }
        if name:
            body["name"] = name
        payload = json.dumps(body).encode()

        try:
            req = urllib.request.Request(
                f"{server_url}/api/jobs",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                job_data = json.loads(resp.read())
                job_id = job_data["id"]

            start_req = urllib.request.Request(
                f"{server_url}/api/jobs/{job_id}/start",
                data=b"",
                method="POST",
            )
            urllib.request.urlopen(start_req, timeout=10)

            job_url = f"{server_url}/jobs/{job_id}"
        except Exception:
            job_url = server_url  # fallback: open root if job submission fails

        if not getattr(args, "no_open", False):
            _open_browser(job_url)

    t = threading.Thread(target=_wait_submit_open, daemon=True)
    t.start()


def _port_available(host: str, port: int) -> bool:
    """Check if a port is available to bind."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port() -> int:
    """Find a random available port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cmd_jobs(args: argparse.Namespace) -> int:
    # Server routing (JSON mode only)
    if args.output == "json":
        data, code = _try_server(
            args, lambda c: c.jobs(status=args.status)
        )
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    project = _find_project_or_exit(args)

    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        status_filter = None
        if args.status:
            try:
                status_filter = JobStatus(args.status)
            except ValueError:
                valid = ", ".join(s.value for s in JobStatus)
                print(f"Error: Invalid status '{args.status}'. Valid: {valid}", file=sys.stderr)
                return EXIT_USAGE_ERROR

        jobs = store.all_jobs(status=status_filter, top_level_only=True)

        if getattr(args, "filter_name", None):
            pattern = args.filter_name.lower()
            jobs = [j for j in jobs if j.name and pattern in j.name.lower()]

        if not args.all:
            jobs = jobs[-args.limit:]

        if args.output == "json":
            print(json.dumps([_job_summary(j) for j in jobs], indent=2, default=str))
            return EXIT_SUCCESS

        # Table format
        io = _io(args)
        if not jobs:
            io.log("info", "No jobs found.")
            return EXIT_SUCCESS

        rows = []
        for j in jobs:
            runs = store.runs_for_job(j.id)
            completed = sum(1 for r in runs if r.status.value == "completed")
            total = len(j.workflow.steps)
            name = (j.name or "")[:30]
            obj = (j.objective or "")[:23]
            created = _relative_time(j.created_at) if j.created_at else ""
            rows.append([j.id, name, j.status.value, obj, f"{completed}/{total}", created])
        io.table(["ID", "NAME", "STATUS", "OBJECTIVE", "STEPS", "CREATED"], rows)

        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_status(args: argparse.Namespace) -> int:
    # Server routing (JSON mode only)
    if getattr(args, "output", None) == "json":
        data, code = _try_server(args, lambda c: c.status(args.job_id))
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    project = _find_project_or_exit(args)

    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            print(f"Error: Job not found: {args.job_id}", file=sys.stderr)
            return EXIT_JOB_FAILED

        runs = store.runs_for_job(job.id)

        if args.output == "json":
            from stepwise.engine import Engine
            from stepwise.registry_factory import create_default_registry
            engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)
            data = engine.resolved_flow_status(args.job_id)
            print(json.dumps(data, indent=2, default=str))
            return EXIT_SUCCESS

        # Table format
        io = _io(args)
        info = f"Job: {job.id}"
        if job.name:
            info += f"\nName: {job.name}"
        info += f"\nStatus: {job.status.value}\nObjective: {job.objective}"
        if job.created_at:
            info += f"\nCreated: {_relative_time(job.created_at)}"
        io.note(info, title="Job Details")

        # Group runs by step, show latest
        step_runs: dict[str, list] = {}
        for r in runs:
            step_runs.setdefault(r.step_name, []).append(r)

        for step_name in job.workflow.steps:
            step_def = job.workflow.steps[step_name]
            step_r = step_runs.get(step_name, [])
            if step_r:
                latest = step_r[-1]
                status_str = latest.status.value
                duration = None
                cost = None
                if latest.started_at and latest.completed_at:
                    duration = (latest.completed_at - latest.started_at).total_seconds()
                    cost = store.accumulated_cost(latest.id) or None
                io.step_status(step_name, status_str, duration=duration, cost=cost)
            else:
                io.step_status(step_name, "waiting")

        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_cancel(args: argparse.Namespace) -> int:
    # Server routing (JSON mode only)
    if getattr(args, "output", None) == "json":
        data, code = _try_server(args, lambda c: c.cancel(args.job_id))
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.store import SQLiteStore
    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            if getattr(args, "output", None) == "json":
                print(json.dumps({"status": "error", "error": f"Job not found: {args.job_id}"}))
            else:
                print(f"Error: Job not found: {args.job_id}", file=sys.stderr)
            return EXIT_JOB_FAILED

        from stepwise.models import JobStatus, StepRunStatus
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            if getattr(args, "output", None) == "json":
                print(json.dumps({"status": "error", "error": f"Job already {job.status.value}"}))
            else:
                print(f"Error: Job already {job.status.value}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        registry = create_default_registry()
        engine = Engine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)

        # Capture pre-cancel state for JSON output
        if getattr(args, "output", None) == "json":
            runs = store.runs_for_job(args.job_id)
            completed_steps = [r.step_name for r in runs if r.status == StepRunStatus.COMPLETED]
            cancelled_steps = [r.step_name for r in runs
                               if r.status in (StepRunStatus.RUNNING, StepRunStatus.SUSPENDED,
                                               StepRunStatus.DELEGATED)]
            ran_names = {r.step_name for r in runs}
            remaining_steps = []
            for step_name, step_def in job.workflow.steps.items():
                if step_name not in ran_names:
                    info: dict = {"name": step_name, "type": step_def.executor.type}
                    prompt = (step_def.executor.config or {}).get("prompt")
                    if prompt:
                        info["prompt"] = prompt
                    remaining_steps.append(info)

            engine.cancel_job(args.job_id)
            print(json.dumps({
                "job_id": args.job_id,
                "status": "cancelled",
                "completed_steps": completed_steps,
                "cancelled_steps": cancelled_steps,
                "remaining_steps": remaining_steps,
            }, indent=2, default=str))
        else:
            engine.cancel_job(args.job_id)
            _io(args).log("success", f"Cancelled {args.job_id}")

        return EXIT_SUCCESS
    finally:
        store.close()


def _job_summary(job) -> dict:
    """Create a JSON-serializable job summary."""
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status.value,
        "objective": job.objective,
        "steps": len(job.workflow.steps),
        "created_at": str(job.created_at) if job.created_at else None,
    }


def _relative_time(dt) -> str:
    """Format a datetime as a relative time string."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        from datetime import timezone as tz
        dt = dt.replace(tzinfo=tz.utc)
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins} min ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hours ago"
    else:
        days = int(seconds / 86400)
        return f"{days} days ago"


def cmd_list(args: argparse.Namespace) -> int:
    """List suspended steps across all active jobs."""
    # Server routing (JSON mode only)
    if getattr(args, "output", None) == "json":
        data, code = _try_server(
            args,
            lambda c: c.list_suspended(
                since=getattr(args, "since", None),
                flow=getattr(args, "flow", None),
            ),
        )
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    project = _find_project_or_exit(args)

    from stepwise.models import JobStatus, StepRunStatus
    from stepwise.store import SQLiteStore
    from datetime import datetime, timezone

    store = SQLiteStore(str(project.db_path))
    try:
        now = datetime.now(timezone.utc)
        items: list[dict] = []

        # Get all active jobs (running/paused)
        jobs = store.all_jobs(top_level_only=True)
        for job in jobs:
            if job.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
                continue

            # Apply --flow filter
            if getattr(args, "flow", None) and args.flow != job.objective:
                continue

            runs = store.suspended_runs(job.id)
            for run in runs:
                if not run.watch:
                    continue

                suspended_at = run.started_at
                age_seconds = 0
                if suspended_at:
                    if suspended_at.tzinfo is None:
                        suspended_at = suspended_at.replace(tzinfo=timezone.utc)
                    age_seconds = int((now - suspended_at).total_seconds())

                # Apply --since filter
                if getattr(args, "since", None):
                    since_str = args.since
                    # Parse duration like "24h", "7d", "30m"
                    max_age = _parse_duration(since_str)
                    if max_age and age_seconds > max_age:
                        continue

                item = {
                    "job_id": job.id,
                    "flow_name": job.objective,
                    "run_id": run.id,
                    "step_name": run.step_name,
                    "prompt": (run.watch.config or {}).get("prompt", ""),
                    "expected_outputs": run.watch.fulfillment_outputs,
                    "suspended_at": suspended_at.isoformat() if suspended_at else None,
                    "age_seconds": age_seconds,
                    "fulfill_command": f"stepwise fulfill {run.id}",
                }
                if run.watch.output_schema:
                    item["output_schema"] = run.watch.output_schema
                items.append(item)

        if getattr(args, "output", None) == "json":
            print(json.dumps({"suspended_steps": items, "count": len(items)}, indent=2, default=str))
        else:
            io = _io(args)
            if not items:
                io.log("info", "No suspended steps.")
            else:
                rows = []
                for item in items:
                    age = _format_age(item["age_seconds"])
                    rows.append([item["run_id"], item["flow_name"], item["step_name"], age])
                io.table(["RUN ID", "FLOW", "STEP", "AGE"], rows)

        return EXIT_SUCCESS
    finally:
        store.close()


def _parse_duration(s: str) -> int | None:
    """Parse a duration string like '24h', '7d', '30m' into seconds."""
    import re
    m = re.match(r'^(\d+)([hdm])$', s)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2)
    if unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    elif unit == 'm':
        return value * 60
    return None


def _format_age(seconds: int) -> str:
    """Format age in seconds to human-readable."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    else:
        return f"{seconds // 86400}d"


def cmd_get(args: argparse.Namespace) -> int:
    """Download a flow by URL or registry name."""
    from stepwise.bundle import unpack_bundle
    from stepwise.flow_resolution import parse_registry_ref, registry_flow_dir
    from stepwise.registry_client import fetch_flow, get_registry_url, RegistryError

    io = _io(args)
    target = args.target
    if not target:
        io.log("error", "flow get requires a URL or @author:name")
        return EXIT_USAGE_ERROR

    # URL download
    if target.startswith("http://") or target.startswith("https://"):
        return _flow_get_url(target)

    # Registry name lookup — require @author:name format
    parsed = parse_registry_ref(target)
    if parsed:
        author_hint, slug = parsed
    else:
        # Bare name — treat as slug with unknown author
        slug = target
        author_hint = None

    force = getattr(args, "force", False)
    try:
        data = fetch_flow(slug)
    except RegistryError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    bundle_files = data.get("files")
    steps = data.get("steps", "?")
    author = data.get("author", "unknown")
    downloads = data.get("downloads", 0)

    # Install to .stepwise/registry/@author/slug/
    project_dir = Path.cwd()
    target_dir = registry_flow_dir(author, slug, project_dir)

    if target_dir.exists() and not force:
        io.log("error", f"{target_dir} already exists (use --force to overwrite)")
        return EXIT_USAGE_ERROR

    # Build origin metadata
    import hashlib
    from datetime import datetime, timezone

    origin = {
        "registry": get_registry_url(),
        "author": author,
        "slug": slug,
        "version": data.get("version", 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(data["yaml"].encode()).hexdigest(),
    }

    flow_path = unpack_bundle(
        target_dir=target_dir,
        yaml_content=data["yaml"],
        files=bundle_files,
        origin=origin,
    )

    file_count = len(bundle_files) if bundle_files else 0
    file_msg = f" + {file_count} file(s)" if file_count else ""
    io.log("success", f"Downloaded @{author}:{slug}{file_msg} ({steps} steps, {downloads:,} downloads)")
    io.log("info", f"Run: stepwise run @{author}:{slug}")
    return EXIT_SUCCESS


def cmd_share(args: argparse.Namespace) -> int:
    """Publish a flow to the registry."""
    from stepwise.bundle import BundleError, collect_bundle
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.registry_client import publish_flow, update_flow, RegistryError
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    flow_arg = args.flow
    if not flow_arg:
        print("Error: share requires a flow name or file path", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        flow_path = resolve_flow(flow_arg, _project_dir(args))
    except FlowResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # Validate first
    try:
        wf = load_workflow_yaml(str(flow_path))
        errors = wf.validate()
        if errors:
            print(f"Error: Validation failed:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return EXIT_USAGE_ERROR
    except (YAMLLoadError, ValueError) as e:
        print(f"Error: Validation failed: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    io = _io(args)
    yaml_content = flow_path.read_text()
    author = getattr(args, "author", None)
    do_update = getattr(args, "update", False)

    io.log("success", f"Validated {flow_path} ({len(wf.steps)} steps)")

    # Quality checks for sharing
    share_warnings = wf.warnings()
    if share_warnings:
        for w in share_warnings:
            if w.startswith("\u2139"):
                io.log("info", f"  {w}")
            else:
                io.log("warn", f"  {w}")

    # Additional share-specific quality checks
    job_fields = {
        b.source_field
        for s in wf.steps.values()
        for b in s.inputs
        if b.source_step == "$job"
    }
    config_names = {v.name for v in wf.config_vars}

    # Check required config vars have descriptions
    for v in wf.config_vars:
        if v.required and not v.description:
            io.log("warn", f"  ⚠ Config variable '{v.name}' has no description — consumers won't know what to provide")

    # Check requirements have check commands
    for r in wf.requires:
        if not r.check:
            io.log("info", f"  ℹ Requirement '{r.name}' has no check command — consumers can't verify availability")

    # Note zero-config flows
    all_have_defaults = all(
        v.default is not None for v in wf.config_vars
    ) if wf.config_vars else not job_fields
    if all_have_defaults and not wf.requires:
        io.log("success", "  Zero-config flow — runs immediately after install")

    # Collect bundle files if this is a directory flow
    bundle_files: dict[str, str] | None = None
    is_dir_flow = flow_path.name == "FLOW.yaml"
    if is_dir_flow:
        try:
            bundle_files = collect_bundle(flow_path.parent)
        except BundleError as e:
            io.log("error", str(e))
            return EXIT_USAGE_ERROR

        if bundle_files:
            io.log("info", f"Bundling {len(bundle_files)} co-located file(s):")
            for rel_path in sorted(bundle_files):
                size = len(bundle_files[rel_path].encode("utf-8"))
                io.log("info", f"  {rel_path} ({size:,} bytes)")
            if not io.prompt_confirm("Proceed?"):
                io.log("info", "Cancelled.")
                return EXIT_SUCCESS

    try:
        if do_update:
            import yaml as yaml_lib
            data = yaml_lib.safe_load(yaml_content)
            name = data.get("name", flow_path.stem.replace(".flow", ""))
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
            result = update_flow(slug, yaml_content)
            io.log("success", f"Updated: {result.get('url', slug)}")
        else:
            result = publish_flow(yaml_content, author=author, files=bundle_files)
            slug = result.get("slug", "")
            file_msg = f" + {len(bundle_files)} file(s)" if bundle_files else ""
            io.log("info", f"Publishing as \"{result.get('name', '')}\" by {result.get('author', 'anonymous')}...")
            io.log("success", f"Published: {result.get('url', '')}{file_msg}")
            io.log("info", f"Get: stepwise get {slug}")
            if result.get("update_token"):
                io.log("info", "Token saved to ~/.config/stepwise/tokens.json")
    except RegistryError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    return EXIT_SUCCESS


def cmd_search(args: argparse.Namespace) -> int:
    """Search the flow registry."""
    from stepwise.registry_client import search_flows, RegistryError

    query = " ".join(args.query) if args.query else ""
    tag = getattr(args, "tag", None)
    sort = getattr(args, "sort", "downloads")
    output = getattr(args, "output", "table")

    try:
        result = search_flows(query=query, tag=tag, sort=sort)
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    flows = result.get("flows", [])
    io = _io(args)
    if not flows:
        io.log("info", "No flows found.")
        return EXIT_SUCCESS

    if output == "json":
        print(json.dumps(result, indent=2))
        return EXIT_SUCCESS

    # Table output
    rows = []
    for f in flows:
        tags = ", ".join(f.get("tags", []))
        rows.append([
            f["slug"],
            f.get("author", "?"),
            str(f.get("steps", "?")),
            f"{f.get('downloads', 0):,}",
            tags,
        ])
    io.table(["NAME", "AUTHOR", "STEPS", "DOWNLOADS", "TAGS"], rows)

    total = result.get("total", len(flows))
    if total > len(flows):
        io.log("info", f"Showing {len(flows)} of {total} flows")
    return EXIT_SUCCESS


def cmd_info(args: argparse.Namespace) -> int:
    """Show details about a flow (local or registry)."""
    from stepwise.flow_resolution import FlowResolutionError, parse_registry_ref, resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    io = _io(args)
    flow_ref = args.name
    if not flow_ref:
        print("Error: flow info requires a flow name", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # Try local resolution first
    project_dir = _project_dir(args) or Path.cwd()
    local_flow = None
    try:
        local_flow = resolve_flow(flow_ref, project_dir)
    except FlowResolutionError:
        pass

    if local_flow and local_flow.exists():
        try:
            wf = load_workflow_yaml(str(local_flow))
        except (YAMLLoadError, Exception) as e:
            print(f"Error loading flow: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        meta = wf.metadata
        lines = [
            f"Name:        {meta.name or local_flow.stem}",
        ]
        if meta.author:
            lines.append(f"Author:      {meta.author}")
        if meta.version:
            lines.append(f"Version:     {meta.version}")
        if meta.description:
            lines.append(f"Description: {meta.description}")
        if meta.tags:
            lines.append(f"Tags:        {', '.join(meta.tags)}")
        lines.append(f"Steps:       {len(wf.steps)}")
        lines.append(f"File:        {local_flow}")

        # Config variables
        if wf.config_vars:
            lines.append("")
            lines.append("Config variables:")
            for v in wf.config_vars:
                if v.sensitive:
                    req = "required, sensitive" if v.required else f"default: ***, sensitive"
                else:
                    req = "required" if v.required else f"default: {v.default}"
                line = f"  {v.name} ({v.type}, {req})"
                if v.description:
                    line += f" — {v.description}"
                lines.append(line)
                if v.example:
                    lines.append(f"    example: {v.example}")
                if v.sensitive:
                    env_name = f"STEPWISE_VAR_{v.name.upper()}"
                    lines.append(f"    env: {env_name}")

        # Requirements
        req_met = 0
        req_failed: list[str] = []
        if wf.requires:
            lines.append("")
            lines.append("Requirements:")
            for r in wf.requires:
                status = ""
                if r.check:
                    import subprocess
                    try:
                        result = subprocess.run(
                            r.check, shell=True, timeout=5,
                            capture_output=True, text=True,
                        )
                        if result.returncode == 0:
                            status = " ✓"
                            req_met += 1
                        else:
                            status = " ✗"
                            req_failed.append(r.name)
                    except (subprocess.TimeoutExpired, Exception):
                        status = " ?"
                        req_failed.append(r.name)
                else:
                    req_met += 1  # no check = assume OK
                line = f"  {r.name}{status}"
                if r.description:
                    line += f" — {r.description}"
                lines.append(line)
                if r.install:
                    lines.append(f"    Install: {r.install}")
                if r.url:
                    lines.append(f"    Docs: {r.url}")

        # Ready-to-run assessment
        lines.append("")
        required_without_defaults = [
            v for v in wf.config_vars if v.required and v.default is None
        ]
        if req_failed:
            failed_names = ", ".join(req_failed)
            lines.append(f"Status:      Missing requirements ({failed_names})")
        elif required_without_defaults:
            n = len(required_without_defaults)
            lines.append(f"Status:      Needs config ({n} required variable{'s' if n != 1 else ''} without defaults)")
        else:
            parts = []
            if wf.config_vars:
                parts.append("all config has defaults")
            if wf.requires:
                parts.append(f"{req_met}/{len(wf.requires)} requirements met")
            detail = f" ({', '.join(parts)})" if parts else ""
            lines.append(f"Status:      Ready to run{detail}")

        # Readme
        if wf.readme:
            lines.append("")
            lines.append("─" * 40)
            lines.append(wf.readme.rstrip())

        io.note("\n".join(lines), title=meta.name or "Flow Info")
        return EXIT_SUCCESS

    # Fall back to registry lookup
    from stepwise.registry_client import fetch_flow, RegistryError

    try:
        data = fetch_flow(flow_ref)
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    tags = ", ".join(data.get("tags", []))
    executors = data.get("executor_types", [])
    steps = data.get("steps", 0)
    loops = data.get("loops", 0)

    lines = [
        f"Name:        {data.get('name', '?')}",
        f"Author:      {data.get('author', '?')}",
        f"Version:     {data.get('version', '?')}",
        f"Description: {data.get('description', '')}",
        f"Tags:        {tags or '(none)'}",
        f"Downloads:   {data.get('downloads', 0):,}",
        f"Published:   {data.get('created_at', '?')}",
        f"URL:         {data.get('url', '?')}",
        f"Steps:       {steps}",
    ]
    if loops:
        lines.append(f"Loops:       {loops}")
    if executors:
        lines.append(f"Executors:   {', '.join(executors)}")

    io.note("\n".join(lines), title=data.get("name", "Flow Info"))
    return EXIT_SUCCESS


def _flow_get_url(url: str) -> int:
    """Download a flow from a URL."""
    import urllib.request
    import urllib.error

    # Derive filename from URL
    filename = url.rsplit("/", 1)[-1]
    if not filename.endswith((".yaml", ".yml")):
        print(f"Error: URL does not point to a YAML file: {url}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    target = Path(filename)
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        urllib.request.urlretrieve(url, str(target))
        print(f"✓ Downloaded {target}")
        print(f"  Run 'stepwise run {target}' to execute.")
        return EXIT_SUCCESS
    except urllib.error.URLError as e:
        print(f"Error: Failed to download: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR


def cmd_schema(args: argparse.Namespace) -> int:
    """Generate JSON tool contract from a flow file."""
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.schema import generate_schema
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        wf = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        print(f"Error: {'; '.join(e.errors)}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    schema = generate_schema(wf)
    print(json.dumps(schema, indent=2))
    return EXIT_SUCCESS


def cmd_output(args: argparse.Namespace) -> int:
    """Retrieve job outputs after completion."""
    # Server routing (always JSON — route all modes)
    if not getattr(args, "run_id", None):
        # --run mode uses run_id, not job_id — skip server routing for it
        data, code = _try_server(
            args,
            lambda c: c.output(
                args.job_id,
                step=getattr(args, "step", None),
                inputs=getattr(args, "inputs", False),
            ),
        )
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.models import JobStatus
    from stepwise.registry_factory import create_default_registry
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        # --run mode: direct run access by run_id
        if getattr(args, "run_id", None):
            try:
                run = store.load_run(args.run_id)
            except KeyError:
                print(json.dumps({"status": "error", "error": f"Run not found: {args.run_id}"}))
                return EXIT_JOB_FAILED
            artifact = run.result.artifact if run.result else None
            print(json.dumps(artifact, indent=2, default=str))
            return EXIT_SUCCESS

        try:
            engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)
            job = engine.get_job(args.job_id)
        except KeyError:
            print(json.dumps({"status": "error", "error": f"Job not found: {args.job_id}"}))
            return EXIT_JOB_FAILED

        # --step mode: per-step output access
        if getattr(args, "step", None):
            step_names = [s.strip() for s in args.step.split(",")]
            result: dict = {}
            show_inputs = getattr(args, "inputs", False)

            for step_name in step_names:
                if step_name not in job.workflow.steps:
                    result[step_name] = {"_error": f"Step '{step_name}' does not exist in this flow"}
                    continue

                if show_inputs:
                    run = store.latest_run(job.id, step_name)
                    if run and run.inputs is not None:
                        result[step_name] = run.inputs
                    else:
                        result[step_name] = None
                        result[f"{step_name}_status"] = run.status.value if run else "pending"
                else:
                    run = store.latest_completed_run(job.id, step_name)
                    if run and run.result:
                        result[step_name] = run.result.artifact
                    else:
                        result[step_name] = None
                        latest = store.latest_run(job.id, step_name)
                        result[f"{step_name}_status"] = latest.status.value if latest else "pending"

            print(json.dumps(result, indent=2, default=str))
            return EXIT_SUCCESS

        # Default mode: job-level outputs
        result = {"status": job.status.value}

        if job.status == JobStatus.COMPLETED:
            result["outputs"] = engine.terminal_outputs(job.id)
        elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
            result["outputs"] = []
            result["completed_outputs"] = engine.completed_outputs(job.id)
        elif job.status in (JobStatus.RUNNING, JobStatus.PAUSED):
            result["outputs"] = []
            suspended = engine.suspended_step_details(job.id)
            if suspended:
                result["suspended_steps"] = suspended

        if args.scope == "full":
            # Include per-step details
            runs = engine.get_runs(job.id)
            steps: dict = {}
            for run in runs:
                steps[run.step_name] = {
                    "status": run.status.value,
                    "outputs": run.result.artifact if run.result else None,
                }
            result["steps"] = steps
            result["cost_usd"] = round(engine.job_cost(job.id), 4)
            events = engine.get_events(job.id)
            result["event_count"] = len(events)

        print(json.dumps(result, indent=2, default=str))
        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_fulfill(args: argparse.Namespace) -> int:
    """Satisfy a suspended human step from the command line."""
    # Server routing (always JSON)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError

        # Parse payload first (shared with direct path)
        raw_payload = args.payload
        if raw_payload == "-" or (raw_payload is None and getattr(args, "stdin", False)):
            raw_payload = sys.stdin.read().strip()
        elif raw_payload is None:
            print(json.dumps({"status": "error", "error": "No payload provided."}))
            return EXIT_USAGE_ERROR
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}))
            return EXIT_USAGE_ERROR
        if not isinstance(payload, dict):
            print(json.dumps({"status": "error", "error": "Payload must be a JSON object"}))
            return EXIT_USAGE_ERROR

        client = StepwiseClient(server_url)
        try:
            result = client.fulfill(args.run_id, payload)
            if getattr(args, "wait", False):
                # Fulfill succeeded, now wait via API
                job_id = result.get("job_id")
                if job_id:
                    wait_result = client.wait(job_id)
                    print(json.dumps(wait_result, indent=2, default=str))
                    if wait_result.get("status") == "failed":
                        return EXIT_JOB_FAILED
                    return EXIT_SUCCESS
            print(json.dumps(result, indent=2, default=str))
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            if e.status == 0:
                print(
                    f"Warning: Server at {server_url} unreachable, falling back to direct mode",
                    file=sys.stderr,
                )
                # Fall through to direct path
            else:
                print(json.dumps({"status": "error", "error": e.detail}))
                return EXIT_JOB_FAILED

    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)

        # Read payload from stdin or argv
        raw_payload = args.payload
        if raw_payload == "-" or (raw_payload is None and args.stdin):
            raw_payload = sys.stdin.read().strip()
        elif raw_payload is None:
            print(json.dumps({
                "status": "error",
                "error": "No payload provided. Pass JSON as argument or use --stdin.",
            }))
            return EXIT_USAGE_ERROR

        # Parse JSON payload
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "status": "error",
                "error": f"Invalid JSON payload: {e}. "
                         f"Usage: stepwise fulfill {args.run_id} '{{\"field\": \"value\"}}'",
            }))
            return EXIT_USAGE_ERROR

        if not isinstance(payload, dict):
            print(json.dumps({
                "status": "error",
                "error": "Payload must be a JSON object, not " + type(payload).__name__,
            }))
            return EXIT_USAGE_ERROR

        try:
            result = engine.fulfill_watch(args.run_id, payload)
        except (ValueError, KeyError) as e:
            print(json.dumps({"status": "error", "error": str(e)}))
            return EXIT_USAGE_ERROR

        # Idempotent: already fulfilled
        if result is not None:
            if getattr(args, "wait", False):
                # Still enter wait loop on the job even if already fulfilled
                print(json.dumps(result), file=sys.stderr)
                from stepwise.runner import wait_for_job
                return wait_for_job(engine, store, result["job_id"])
            print(json.dumps(result))
            return EXIT_SUCCESS

        # Get the job_id from the run
        run = store.load_run(args.run_id)

        if getattr(args, "wait", False):
            # Enter wait loop on the parent job
            from stepwise.runner import wait_for_job
            return wait_for_job(engine, store, run.job_id)

        print(json.dumps({"status": "fulfilled", "run_id": args.run_id}))
        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until a job reaches terminal state or suspension."""
    # Server routing (always JSON)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError

        client = StepwiseClient(server_url)
        try:
            timeout = getattr(args, "timeout", None)
            result = client.wait(args.job_id, timeout=timeout)
            print(json.dumps(result, indent=2, default=str))
            status = result.get("status", "")
            if status == "failed":
                return EXIT_JOB_FAILED
            if result.get("timeout"):
                return EXIT_CONFIG_ERROR  # exit 3 for timeout in --wait mode
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            if e.status == 0:
                print(
                    f"Warning: Server at {server_url} unreachable, falling back to direct mode",
                    file=sys.stderr,
                )
                # Fall through to direct path
            else:
                print(json.dumps({"status": "error", "error": e.detail}))
                return EXIT_JOB_FAILED

    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.runner import wait_for_job
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            store.load_job(args.job_id)
        except KeyError:
            print(json.dumps({"status": "error", "error": f"Job not found: {args.job_id}"}))
            return EXIT_JOB_FAILED

        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)
        timeout = getattr(args, "timeout", None)
        return wait_for_job(engine, store, args.job_id, timeout=timeout)
    finally:
        store.close()


def cmd_agent_help(args: argparse.Namespace) -> int:
    """Generate agent-readable instructions for available flows."""
    from stepwise.agent_help import generate_agent_help, update_file

    project_dir = Path(args.project_dir) if args.project_dir else Path.cwd()
    flows_dir = Path(args.flows_dir) if args.flows_dir else None
    fmt = getattr(args, "format", "compact")

    # --update implies full format unless explicitly overridden
    if args.update and fmt == "compact":
        fmt = "full"

    content = generate_agent_help(project_dir, flows_dir=flows_dir, fmt=fmt)

    if args.update:
        target = Path(args.update)
        replaced = update_file(target, content)
        if replaced:
            print(f"✓ Updated {target} (replaced existing section)", file=sys.stderr)
        else:
            print(f"✓ Updated {target} (appended new section)", file=sys.stderr)
    else:
        print(content)

    return EXIT_SUCCESS


def _detect_install_method() -> str:
    """Detect how stepwise was installed: 'uv', 'pipx', or 'pip'."""
    import shutil
    import subprocess

    # Check the executable path for signatures
    exe = shutil.which("stepwise") or ""
    if "/uv/" in exe:
        return "uv"
    if "/pipx/" in exe:
        return "pipx"

    # Probe tool lists
    for tool, cmd in [("uv", ["uv", "tool", "list"]), ("pipx", ["pipx", "list", "--short"])]:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and "stepwise-run" in out.stdout:
                return tool
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return "pip"


def cmd_self_update(args: argparse.Namespace) -> int:
    """Upgrade stepwise to the latest version."""
    import subprocess

    old_version = _get_version()
    method = _detect_install_method()

    GIT_URL = "stepwise-run@git+https://github.com/zackham/stepwise.git"

    upgrade_cmds = {
        "uv": ["uv", "tool", "install", "--force", GIT_URL],
        "pipx": ["pipx", "install", "--force", "git+https://github.com/zackham/stepwise.git"],
        "pip": [sys.executable, "-m", "pip", "install", "--upgrade", GIT_URL],
    }

    io = _io(args)
    cmd = upgrade_cmds[method]
    io.log("info", f"Upgrading via {method}...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Upgrade failed (exit {result.returncode}):", file=sys.stderr)
        stderr = result.stderr.strip()
        if stderr:
            print(stderr, file=sys.stderr)
        if method == "pip":
            print(
                "\nTip: if pip is blocked by your OS, install with:\n"
                "  curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh",
                file=sys.stderr,
            )
        return EXIT_JOB_FAILED

    # Get the new version from a fresh subprocess (avoids stale importlib cache)
    ver_result = subprocess.run(
        ["stepwise", "--version"], capture_output=True, text=True
    )
    new_version = ver_result.stdout.strip().removeprefix("stepwise ") if ver_result.returncode == 0 else "unknown"

    # Invalidate version check cache so serve doesn't show stale upgrade notice
    try:
        import json
        from pathlib import Path
        cache_file = Path.home() / ".cache" / "stepwise" / "version-check.json"
        if cache_file.exists():
            cache_file.unlink()
    except Exception:
        pass

    # Also update acpx if npm is available
    import shutil as _shutil
    if _shutil.which("npm"):
        acpx_result = subprocess.run(
            ["npm", "install", "-g", "acpx"],
            capture_output=True, text=True,
        )
        if acpx_result.returncode == 0:
            io.log("info", "Updated acpx (agent protocol CLI).")
        else:
            io.log("warn", "Failed to update acpx — agent/LLM steps may not work.")
    elif not _shutil.which("acpx"):
        io.log("warn", "acpx not found. Install Node.js and run: npm install -g acpx")

    if new_version == old_version:
        io.log("success", f"Already up to date (v{old_version}).")
        return EXIT_SUCCESS

    io.log("success", f"Updated: v{old_version} → v{new_version}")

    # Show what changed
    changelog = _fetch_changelog_sections(old_version, new_version)
    if changelog:
        io.note(changelog, title="What's new")
    elif old_version != new_version:
        io.log("info", "Run `stepwise changelog` or see CHANGELOG.md for details.")

    return EXIT_SUCCESS


def _open_browser(url: str) -> None:
    """Open URL in default browser (best-effort, non-blocking)."""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _open_browser_when_ready(host: str, port: int) -> None:
    """Wait for server to accept connections, then open browser."""
    import socket
    import threading

    def _wait_and_open():
        url = f"http://{host}:{port}"
        for _ in range(50):  # up to 5 seconds
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    _open_browser(url)
                    return
            except OSError:
                import time
                time.sleep(0.1)

    threading.Thread(target=_wait_and_open, daemon=True).start()


# ── Parser ───────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stepwise",
        description="Enter the flow state. Portable orchestration for agents and humans.",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-essential output")
    parser.add_argument("--project-dir", help="Use specified project directory instead of cwd")
    parser.add_argument("--standalone", action="store_true",
                        help="Force direct SQLite mode (skip server detection)")
    parser.add_argument("--server", metavar="URL",
                        help="Force API mode with specified server URL")

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Create .stepwise/ in current directory")
    p_init.add_argument("--force", action="store_true", help="Reinitialize existing project")
    p_init.add_argument("--no-skill", action="store_true",
                        help="Skip agent skill installation")
    p_init.add_argument("--skill", metavar="DIR",
                        help="Install agent skill to specific directory (e.g., .claude or .agents)")

    # server
    p_server = sub.add_parser("server", help="Manage the Stepwise server")
    p_server.add_argument("action", choices=["start", "stop", "restart", "status"])
    p_server.add_argument("--port", type=int, help="Port (default: 8340)")
    p_server.add_argument("--host", help="Bind address (default: 127.0.0.1)")
    p_server.add_argument("--detach", "-d", action="store_true", help="Run in background")
    p_server.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    # run
    p_run = sub.add_parser("run", help="Run a flow")
    p_run.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_run.add_argument("--watch", action="store_true", help="Ephemeral server + browser UI")
    p_run.add_argument("--wait", action="store_true", help="Block until completion, JSON output on stdout")
    p_run.add_argument("--async", action="store_true", dest="async_mode",
                       help="Fire-and-forget, returns job_id immediately")
    p_run.add_argument("--output", choices=["json"], dest="output_format",
                       help="Output format (currently only json)")
    p_run.add_argument("--timeout", type=int, help="Timeout in seconds (for --wait)")
    p_run.add_argument("--var", action="append", dest="vars", metavar="KEY=VALUE",
                       help="Pass input variable (repeatable)")
    p_run.add_argument("--var-file", action="append", dest="var_files", metavar="KEY=PATH",
                       help="Pass input variable from file contents (repeatable)")
    p_run.add_argument("--vars-file", help="Load variables from YAML/JSON file")
    p_run.add_argument("--port", type=int, help="Override port (for --watch)")
    p_run.add_argument("--objective", help="Set job objective (defaults to flow name)")
    p_run.add_argument("--name", dest="job_name", help="Human-friendly job name")
    p_run.add_argument("--workspace", help="Override workspace directory")
    p_run.add_argument("--report", action="store_true", help="Generate HTML report after completion")
    p_run.add_argument("--report-output", help="Report output path (default: <flow>-report.html)")
    p_run.add_argument("--no-open", action="store_true", help="Don't auto-open browser (for --watch)")
    p_run.add_argument("--local", action="store_true", help="Force local execution (skip server delegation)")
    p_run.add_argument("--notify", metavar="URL", help="Webhook URL for job event notifications")
    p_run.add_argument("--notify-context", metavar="JSON", dest="notify_context",
                       help="JSON context to include in webhook payloads")

    # check
    p_check = sub.add_parser("check", help="Verify model resolution for a flow")
    p_check.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # validate
    p_validate = sub.add_parser("validate", help="Validate a flow file")
    p_validate.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # preflight
    p_preflight = sub.add_parser("preflight", help="Pre-run check: config + requirements + models")
    p_preflight.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_preflight.add_argument("--var", action="append", help="Variable override (key=value)")

    # diagram
    p_diagram = sub.add_parser("diagram", help="Generate a diagram from a flow file")
    p_diagram.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_diagram.add_argument("-o", "--output", help="Output file path (default: <name>.<format>)")
    p_diagram.add_argument("-f", "--format", choices=["svg", "png", "pdf"], default="svg",
                           help="Output format (default: svg)")

    # templates
    sub.add_parser("templates", help="List available templates")

    # config
    p_config = sub.add_parser("config", help="Manage configuration")
    p_config.add_argument("config_action", choices=["get", "set", "init"], help="Action")
    p_config.add_argument("key", nargs="?", help="Config key or flow name (for init)")
    p_config.add_argument("value", nargs="?", help="Config value (for set)")
    p_config.add_argument("--stdin", action="store_true", help="Read value from stdin")
    p_config.add_argument("--unmask", action="store_true", help="Show full values")

    # new
    p_new = sub.add_parser("new", help="Create a new flow")
    p_new.add_argument("name", help="Flow name (alphanumeric, hyphens, underscores)")

    # share
    p_share = sub.add_parser("share", help="Publish a flow to the registry")
    p_share.add_argument("flow", nargs="?", help="Flow name or path to share")
    p_share.add_argument("--author", help="Author name (default: from git config)")
    p_share.add_argument("--update", action="store_true", help="Update existing flow")

    # get
    p_get = sub.add_parser("get", help="Download a flow")
    p_get.add_argument("target", help="URL, @author:name, or flow slug")
    p_get.add_argument("--force", action="store_true", help="Overwrite existing file")

    # search
    p_search = sub.add_parser("search", help="Search the flow registry")
    p_search.add_argument("query", nargs="*", help="Search query")
    p_search.add_argument("--tag", help="Filter by tag")
    p_search.add_argument("--sort", choices=["downloads", "newest", "name"], default="downloads")
    p_search.add_argument("--output", choices=["table", "json"], default="table")

    # info
    p_info = sub.add_parser("info", help="Show flow details")
    p_info.add_argument("name", help="Flow name")

    # jobs
    p_jobs = sub.add_parser("jobs", help="List jobs")
    p_jobs.add_argument("--output", choices=["table", "json"], default="table")
    p_jobs.add_argument("--limit", type=int, default=20)
    p_jobs.add_argument("--all", action="store_true")
    p_jobs.add_argument("--status", help="Filter by status")
    p_jobs.add_argument("--name", dest="filter_name", help="Filter by name (substring match)")

    # status
    p_status = sub.add_parser("status", help="Show job detail")
    p_status.add_argument("job_id", help="Job ID")
    p_status.add_argument("--output", choices=["table", "json"], default="table")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a running job")
    p_cancel.add_argument("job_id", help="Job ID")
    p_cancel.add_argument("--output", choices=["table", "json"], default="table",
                          help="Output format")

    # schema
    p_schema = sub.add_parser("schema", help="Generate JSON tool contract from a flow file")
    p_schema.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # output
    p_output = sub.add_parser("output", help="Retrieve job outputs")
    p_output.add_argument("job_id", nargs="?", default=None, help="Job ID")
    p_output.add_argument("--scope", choices=["default", "full"], default="default",
                          help="Output scope (default: terminal outputs only)")
    p_output.add_argument("--step", help="Comma-separated step names to retrieve")
    p_output.add_argument("--inputs", action="store_true",
                          help="Return step inputs instead of outputs (use with --step)")
    p_output.add_argument("--run", dest="run_id", help="Retrieve output by run ID directly")

    # list
    p_list = sub.add_parser("list", help="List suspended steps or other items")
    p_list.add_argument("--suspended", action="store_true",
                        help="Show suspended steps across all active jobs")
    p_list.add_argument("--output", choices=["table", "json"], default="table")
    p_list.add_argument("--since", help="Filter by age (e.g., 24h, 7d, 30m)")
    p_list.add_argument("--flow", help="Filter by flow name")

    # wait
    p_wait = sub.add_parser("wait", help="Block until job completes or suspends")
    p_wait.add_argument("job_id", help="Job ID to wait on")
    p_wait.add_argument("--timeout", type=int, help="Timeout in seconds")

    # fulfill
    p_fulfill = sub.add_parser("fulfill", help="Satisfy a suspended human step")
    p_fulfill.add_argument("run_id", help="Run ID of the suspended step")
    p_fulfill.add_argument("payload", nargs="?", default=None,
                           help="JSON payload with field values (use --stdin or '-' to read from stdin)")
    p_fulfill.add_argument("--stdin", action="store_true",
                           help="Read JSON payload from stdin")
    p_fulfill.add_argument("--wait", action="store_true",
                           help="After fulfilling, block until next suspension or completion")

    # agent-help
    p_agent_help = sub.add_parser("agent-help", help="Generate agent instructions for available flows")
    p_agent_help.add_argument("--update", metavar="FILE",
                              help="Update a file in-place between markers (uses 'full' format)")
    p_agent_help.add_argument("--flows-dir", metavar="DIR",
                              help="Override flow discovery directory")
    p_agent_help.add_argument("--format", choices=["compact", "json", "full"],
                              default="compact",
                              help="Output format: compact (default), json, or full")

    # update
    sub.add_parser("update", help="Upgrade stepwise to the latest version")

    # welcome
    sub.add_parser("welcome", help="Try the interactive welcome demo")

    return parser


def cmd_welcome(args: argparse.Namespace) -> int:
    """Interactive welcome demo prompt."""
    io = _io(args)
    io.log("info", "Welcome to Stepwise!")
    io.log("info", "The welcome flow walks you through a simulated dev workflow:")
    io.log("info", "  plan → implement (parallel) → test → review → deploy")
    print()

    choice = io.prompt_select(
        "How would you like to try it?",
        choices=[
            "Browser (stepwise run @stepwise:welcome --watch)",
            "Terminal (stepwise run @stepwise:welcome)",
            "Skip for now",
        ],
    )

    if choice.startswith("Skip"):
        io.log("info", "No problem! Run 'stepwise new my-flow' to create your own flow.")
        return EXIT_SUCCESS

    import os
    watch = "--watch" if choice.startswith("Browser") else ""
    cmd = f"stepwise run @stepwise:welcome {watch}".strip()
    io.log("info", f"Running: {cmd}")
    print()
    os.execvp("stepwise", cmd.split())
    return EXIT_SUCCESS  # unreachable after exec


def _io(args: argparse.Namespace) -> IOAdapter:
    """Get the IOAdapter from args (created in main())."""
    return getattr(args, "_adapter", create_adapter())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Create adapter early, attach to args for all commands
    args._adapter = create_adapter(
        quiet=getattr(args, "quiet", False),
    )

    if args.version:
        print(f"stepwise {_get_version()}")
        return EXIT_SUCCESS

    if not args.command:
        parser.print_help()
        return EXIT_USAGE_ERROR

    handlers = {
        "init": cmd_init,
        "server": cmd_server,
        "run": cmd_run,
        "new": cmd_new,
        "validate": cmd_validate,
        "preflight": cmd_preflight,
        "diagram": cmd_diagram,
        "templates": cmd_templates,
        "config": cmd_config,
        "check": cmd_check,
        "share": cmd_share,
        "get": cmd_get,
        "search": cmd_search,
        "info": cmd_info,
        "jobs": cmd_jobs,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "list": cmd_list,
        "wait": cmd_wait,
        "schema": cmd_schema,
        "output": cmd_output,
        "fulfill": cmd_fulfill,
        "agent-help": cmd_agent_help,
        "update": cmd_self_update,
        "welcome": cmd_welcome,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return EXIT_USAGE_ERROR


def cli_main() -> None:
    """Entry point for console_scripts."""
    sys.exit(main())


if __name__ == "__main__":
    cli_main()
