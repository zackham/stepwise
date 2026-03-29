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
    stepwise wait <job-id> [...]           Block until job(s) complete or suspend
    stepwise wait --all <id1> <id2> ...    Wait for all jobs
    stepwise wait --any <id1> <id2> ...    Wait for first job
    stepwise validate <flow>               Validate flow syntax
    stepwise flows                         List flows in this project
    stepwise templates                     List templates
    stepwise config get|set [key] [value]  Manage configuration
    stepwise schema <flow>                 Generate JSON tool contract
    stepwise tail <job-id>                 Stream live events for a job
    stepwise logs <job-id>                 Show full event history
    stepwise output <job-id> [step] [--step] [--run] Retrieve job/step outputs
    stepwise fulfill <run-id> '<json>'     Satisfy a suspended external step (or --stdin, --wait)
    stepwise help "question"               Ask a question about Stepwise
    stepwise agent-help [--update <file>]  Generate agent instructions
    stepwise extensions [list] [--refresh] List discovered extensions
    stepwise login                          Log in to the Stepwise registry
    stepwise logout                         Log out of the Stepwise registry
    stepwise update                        Upgrade to the latest version
    stepwise uninstall [--yes] [--force]   Remove stepwise from this project
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


# ── Event formatting (shared by tail + logs) ─────────────────────────


def _format_event_line(envelope: dict) -> str:
    """Format a single event envelope (WS or REST format) as a human-readable line."""
    from datetime import datetime

    ts_raw = envelope.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        ts = "??:??:??"

    event_type = envelope.get("event") or envelope.get("type", "unknown")
    step = envelope.get("step") or envelope.get("data", {}).get("step", "")
    data = envelope.get("data", {})

    # Build detail string based on event type
    if event_type == "step.completed":
        detail = f"(attempt {data.get('attempt', 1)})"
        if data.get("from_cache"):
            detail += ", from_cache"
    elif event_type == "step.suspended":
        detail = data.get("prompt", "Awaiting external input")
    elif event_type == "step.failed":
        detail = data.get("error", "")[:80]
    elif event_type == "step.delegated":
        detail = "delegated to sub-flow"
    elif event_type == "exit.resolved":
        action = data.get("action", "")
        target = data.get("target", "")
        detail = f"{action} → {target}" if target else action
    elif event_type == "loop.iteration":
        detail = f"attempt {data.get('attempt', '?')}"
    elif event_type == "job.failed":
        detail = data.get("reason", "")
    else:
        detail = ""

    return f"[{ts}] {event_type:<18s} {step:<16s} {detail}".rstrip()


def _format_job_header(job_data: dict) -> str:
    """Format a job summary header for the logs command."""
    from datetime import datetime

    job_id = job_data.get("id", "unknown")
    status = job_data.get("status", "unknown")
    name = job_data.get("name") or job_data.get("objective", "")

    created_raw = job_data.get("created_at", "")
    completed_raw = job_data.get("completed_at", "")

    try:
        created = datetime.fromisoformat(created_raw)
    except (ValueError, TypeError):
        created = None

    try:
        completed = datetime.fromisoformat(completed_raw)
    except (ValueError, TypeError):
        completed = None

    if created and completed:
        dur_secs = (completed - created).total_seconds()
    elif created:
        from datetime import datetime as dt, timezone
        dur_secs = (dt.now(timezone.utc) - created).total_seconds()
    else:
        dur_secs = 0

    mins = int(dur_secs) // 60
    secs = int(dur_secs) % 60
    dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    lines = [
        f"Job: {job_id} ({name})" if name else f"Job: {job_id}",
        f"Status: {status}",
        f"Duration: {dur_str}",
        "",
    ]
    return "\n".join(lines)


# ── Command handlers ─────────────────────────────────────────────────


async def _tail_ws(ws_url: str) -> int:
    """Connect to the event stream WebSocket and print events until job terminates."""
    import websockets

    try:
        async with websockets.connect(ws_url) as ws:
            async for raw in ws:
                envelope = json.loads(raw)

                # Boundary frame after replay
                if envelope.get("type") == "sys.replay.complete":
                    print("--- replay complete ---", flush=True)
                    continue

                print(_format_event_line(envelope), flush=True)

                event_type = envelope.get("event", "")
                if event_type in ("job.completed", "job.failed", "job.paused"):
                    return EXIT_JOB_FAILED if event_type == "job.failed" else EXIT_SUCCESS
    except websockets.exceptions.ConnectionClosed:
        return EXIT_JOB_FAILED
    except (ConnectionRefusedError, OSError) as e:
        print(f"Error: Could not connect to server: {e}", file=sys.stderr)
        return EXIT_JOB_FAILED

    return EXIT_SUCCESS


def cmd_tail(args: argparse.Namespace) -> int:
    """Stream live events for a job via WebSocket."""
    import asyncio

    server_url = _detect_server_url(args)
    if not server_url:
        print(
            "stepwise tail requires a running server. Start one with: stepwise server start",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    # Build WebSocket URL for event stream
    ws_url = server_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/api/v1/events/stream?job_id={args.job_id}&since_job_start=true"

    try:
        return asyncio.run(_tail_ws(ws_url))
    except KeyboardInterrupt:
        return 130


def cmd_logs(args: argparse.Namespace) -> int:
    """Show full event history for a job."""
    # Server path
    data, code = _try_server(args, lambda c: {
        "job": c.status(args.job_id),
        "events": c.events(args.job_id),
    })
    if code is not None:
        if code != EXIT_SUCCESS:
            print(json.dumps(data, indent=2, default=str))
            return code
        job_data = data["job"]
        events = data["events"]
        print(_format_job_header(job_data))
        for ev in events:
            print(_format_event_line(ev))
        return EXIT_SUCCESS

    # Direct SQLite fallback
    project = _find_project_or_exit(args)

    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            print(f"Error: Job not found: {args.job_id}", file=sys.stderr)
            return EXIT_JOB_FAILED

        events = store.load_events(args.job_id)

        # Compute completed_at from last event timestamp (Job has no completed_at)
        completed_at = ""
        if events and job.status.value in ("completed", "failed", "cancelled"):
            completed_at = events[-1].timestamp.isoformat()

        # Build header dict from Job fields
        header = {
            "id": job.id,
            "status": job.status.value,
            "name": job.name or job.objective,
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "completed_at": completed_at,
        }
        print(_format_job_header(header))
        for ev in events:
            print(_format_event_line(ev.to_dict()))
        return EXIT_SUCCESS
    finally:
        store.close()


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
    elif action == "log":
        return _server_log(args)
    return EXIT_USAGE_ERROR


def _server_start(args: argparse.Namespace) -> int:
    io = _io(args)
    project = _find_project_or_exit(args)

    import os

    # Check if a server is already running for this project
    from stepwise.server_detect import detect_server, detect_any_server
    existing_url = detect_server(project.dot_dir)
    if existing_url:
        io.log("info", f"Stepwise is already running at {existing_url}")
        if not args.no_open:
            _open_browser(existing_url)
        return EXIT_SUCCESS

    # Warn about servers running in other project directories
    others = detect_any_server()
    for s in others:
        if str(Path(s["project_path"]).resolve()) != str(project.root.resolve()):
            io.log("warn", f"Another server active: {s['url']} ({s['project_path']})")

    host = args.host or "0.0.0.0"
    port = args.port or 8340

    if not args.port and not _port_available(host, port):
        port = _find_free_port()
        io.log("warn", f"Port 8340 in use, using {port}")

    # Default to detached mode — spawns server in its own session, required
    # for acpx agent sessions when started from Claude Code.
    if not getattr(args, 'no_detach', False):
        return _server_start_detached(project, host, port, io, args)

    # Foreground mode (--no-detach)
    import uvicorn

    # Set env vars so server.py picks them up in lifespan
    os.environ["STEPWISE_DB"] = str(project.db_path)
    os.environ["STEPWISE_TEMPLATES"] = str(project.templates_dir)
    os.environ["STEPWISE_JOBS_DIR"] = str(project.jobs_dir)
    os.environ["STEPWISE_PROJECT_DIR"] = str(project.root)
    os.environ["STEPWISE_PORT"] = str(port)

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
    log_file = str(project.logs_dir / "server.log")
    write_pidfile(project.dot_dir, port, log_file=log_file)
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

    # start_new_session gives the server its own clean session. Required for
    # acpx agent sessions to work when started from Claude Code or similar
    # tools that create sessions per command.
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


def _stop_server_for_project(dot_dir: Path, io: IOAdapter) -> bool:
    """Stop a running server for the given project directory.

    Returns True if a server was stopped, False if none was running.
    """
    import os
    import signal
    import time

    from stepwise.server_detect import read_pidfile, remove_pidfile, _pid_alive

    data = read_pidfile(dot_dir)
    pid = data.get("pid")

    if not pid or not _pid_alive(pid):
        if pid:
            # Stale pidfile
            remove_pidfile(dot_dir)
        return False

    # Send SIGTERM and wait
    io.log("info", f"Stopping server (PID {pid})...")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):  # up to 5 seconds
        time.sleep(0.1)
        if not _pid_alive(pid):
            remove_pidfile(dot_dir)
            io.log("success", "Server stopped")
            return True

    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    remove_pidfile(dot_dir)
    io.log("warn", f"Server (PID {pid}) did not stop gracefully, sent SIGKILL")
    return True


def _server_stop(args: argparse.Namespace) -> int:
    io = _io(args)
    project = _find_project_or_exit(args)

    stopped = _stop_server_for_project(project.dot_dir, io)
    if not stopped:
        io.log("info", "Server is not running")
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


def _server_log(args: argparse.Namespace) -> int:
    """Print or stream the server log file."""
    import time

    io = _io(args)
    project = _find_project_or_exit(args)

    log_file = project.logs_dir / "server.log"

    if not log_file.exists():
        io.log("info", f"No server log found at {log_file}")
        io.log("info", "Start the server with `stepwise server start` to create a log")
        return EXIT_SUCCESS

    lines = getattr(args, "lines", 50)
    follow = getattr(args, "follow", False)

    # Read the last N lines from the file
    def _tail(path: Path, n: int) -> list[str]:
        with path.open("r", errors="replace") as fh:
            all_lines = fh.readlines()
        return all_lines[-n:] if n > 0 else all_lines

    tail_lines = _tail(log_file, lines)
    for line in tail_lines:
        print(line, end="")

    if not follow:
        return EXIT_SUCCESS

    # Follow mode: poll for new content
    try:
        with log_file.open("r", errors="replace") as fh:
            fh.seek(0, 2)  # Seek to end
            while True:
                chunk = fh.read()
                if chunk:
                    print(chunk, end="", flush=True)
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        pass

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

        # Auto-fix mode
        if getattr(args, "fix", False):
            fixes = wf.fixable_warnings()
            if not fixes:
                io.log("info", "Nothing to fix.")
            else:
                from stepwise.yaml_loader import apply_fixes
                updated_yaml = apply_fixes(str(flow_path), fixes)
                flow_path.write_text(updated_yaml)

                for fix in fixes:
                    if fix["fix"] == "add_max_iterations":
                        io.log("success", f"  Fixed: step '{fix['step']}' rule '{fix['rule_name']}' → max_iterations: {fix['value']}")

                # Re-validate to show clean state
                wf2 = load_workflow_yaml(str(flow_path))
                remaining = wf2.warnings()
                if remaining:
                    for w in remaining:
                        if w.startswith("\u2139"):
                            io.log("info", f"  {w}")
                        else:
                            io.log("warn", f"  {w}")
                else:
                    io.log("success", "All warnings resolved.")

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


def cmd_test_fixture(args: argparse.Namespace) -> int:
    """Generate a pytest test harness for a flow."""
    io = _io(args)
    from pathlib import Path

    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.test_gen import generate_test_fixture
    from stepwise.yaml_loader import YAMLLoadError, load_workflow_yaml

    try:
        flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    try:
        wf = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        io.log("warn", f"Flow has validation errors: {e.errors}")
        return EXIT_JOB_FAILED

    flow_name = wf.metadata.name if wf.metadata and wf.metadata.name else flow_path.stem
    code = generate_test_fixture(wf, flow_name)

    if getattr(args, "output", None):
        Path(args.output).write_text(code)
        io.log("success", f"Wrote test fixture to {args.output}")
    else:
        print(code)

    return EXIT_SUCCESS


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
    elif exec_type == "external":
        prompt = config.get("prompt", "")
        if prompt:
            return prompt[:36] if len(prompt) <= 36 else prompt[:34] + ".."
        return "external input"
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
        "external": "parallelogram",
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
        for seq in step.after:
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

        # After edges
        for dep in step.after:
            if dep in wf.steps:
                dot.edge(dep, step_name, style="dashed", color="#666")

        # Loop-back edges from exit rules
        for rule in step.exit_rules:
            action = rule.config.get("action", "")
            target = rule.config.get("target", "")
            if action == "loop" and target and target in wf.steps:
                dot.edge(step_name, target, label=f"↺ {rule.name}",
                         color="#f59e0b", fontcolor="#f59e0b",
                         penwidth="2.5", style="bold",
                         arrowhead="normal", arrowsize="1.2",
                         constraint="false")

    # Edges from terminal steps to Outputs node
    if terminal_outputs:
        for step_name, fields in terminal_outputs.items():
            edge_label = ", ".join(fields)
            dot.edge(step_name, OUTPUTS_ID, label=edge_label,
                     color="#065f46", fontcolor="#6ee7b7", style="dashed")

    return dot


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new flow directory with a minimal template."""
    io = _io(args)
    from stepwise.flow_resolution import FLOW_DIR_MARKER, FLOW_NAME_PATTERN

    name = args.name
    if not FLOW_NAME_PATTERN.match(name):
        io.log("error", f"Invalid flow name: '{name}'. Flow names must match [a-zA-Z0-9_.+-]+")
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
        f'description: ""\n'
        f"\n"
        f"# A 3-step workflow: gather data → analyze with LLM → format results\n"
        f"# Run with: stepwise run {name} --input topic=\"your topic\"\n"
        f"\n"
        f"config:\n"
        f"  topic:\n"
        f"    type: str\n"
        f"    description: The topic to research\n"
        f"    required: true\n"
        f"    example: machine learning\n"
        f"\n"
        f"steps:\n"
        f"  gather-info:\n"
        f"    # Script steps use 'run:' — stdout must be valid JSON\n"
        f"    run: |\n"
        f"      echo '{{\"topic\": \"'\"$topic\"'\", \"timestamp\": \"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'\"}}'\n"
        f"    inputs:\n"
        f"      topic: $job.topic\n"
        f"    outputs: [topic, timestamp]\n"
        f"\n"
        f"  analyze:\n"
        f"    # LLM steps use 'executor: llm' with a prompt template\n"
        f"    executor: llm\n"
        f"    prompt: |\n"
        f"      Briefly summarize what \"$topic\" is in 2-3 sentences.\n"
        f"      Gathered at: $timestamp\n"
        f"    inputs:\n"
        f"      topic: gather-info.topic\n"
        f"      timestamp: gather-info.timestamp\n"
        f"    outputs: [summary]\n"
        f"\n"
        f"  format-report:\n"
        f"    # This step depends on the LLM output\n"
        f"    run: |\n"
        f"      echo '{{\"report\": \"Topic: '\"$topic\"'\\nSummary: '\"$summary\"'\"}}'\n"
        f"    inputs:\n"
        f"      topic: gather-info.topic\n"
        f"      summary: analyze.summary\n"
        f"    outputs: [report]\n"
    )
    (flow_dir / FLOW_DIR_MARKER).write_text(template)

    io.log("success", f"Created flows/{name}/{FLOW_DIR_MARKER}")
    io.log("info", f"Edit: {flow_dir / FLOW_DIR_MARKER}")
    io.log("info", f"Run:  stepwise run {name} --input topic=\"your topic\"")
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


def cmd_flows(args: argparse.Namespace) -> int:
    """List available flows in the current project."""
    import yaml as _yaml

    io = _io(args)

    # Determine the project root (for flows/ directory lookup)
    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()

    found: list[dict] = []

    # 1. Scan flows/<name>/FLOW.yaml
    flows_dir = project_dir / "flows"
    if flows_dir.is_dir():
        for entry in sorted(flows_dir.iterdir()):
            if entry.is_dir():
                flow_yaml = entry / "FLOW.yaml"
                if not flow_yaml.exists():
                    flow_yaml = entry / "FLOW.yml"
                if flow_yaml.exists():
                    try:
                        raw = _yaml.safe_load(flow_yaml.read_text()) or {}
                    except Exception:
                        raw = {}
                    name = raw.get("name") or entry.name
                    desc = raw.get("description", "")
                    tags = raw.get("tags") or []
                    steps = raw.get("steps") or {}
                    found.append({
                        "name": name,
                        "description": desc,
                        "steps": len(steps),
                        "tags": tags,
                    })

    # 2. Scan *.flow.yaml in the project root
    for flow_file in sorted(project_dir.glob("*.flow.yaml")):
        try:
            raw = _yaml.safe_load(flow_file.read_text()) or {}
        except Exception:
            raw = {}
        name = raw.get("name") or flow_file.stem.replace(".flow", "")
        desc = raw.get("description", "")
        tags = raw.get("tags") or []
        steps = raw.get("steps") or {}
        found.append({
            "name": name,
            "description": desc,
            "steps": len(steps),
            "tags": tags,
        })

    # Sort alphabetically by name
    found.sort(key=lambda f: f["name"].lower())

    if not found:
        io.log("info", "No flows found. Create one with: stepwise new <name>")
        return EXIT_SUCCESS

    rows = []
    for f in found:
        tags_str = ", ".join(f["tags"]) if f["tags"] else ""
        desc = (f["description"] or "")[:50]
        rows.append([f["name"], desc, str(f["steps"]), tags_str])

    io.table(["NAME", "DESCRIPTION", "STEPS", "TAGS"], rows)
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
        elif args.key == "notify_url":
            from stepwise.config import save_project_local_config
            project_dir = _project_dir(args) or Path.cwd()
            save_project_local_config(project_dir, notify_url=value)
            _io(args).log("success", f"Set {args.key} in project config")
            return EXIT_SUCCESS
        elif args.key == "notify_context":
            import json as _json
            try:
                ctx = _json.loads(value)
            except _json.JSONDecodeError:
                print(f"Error: notify_context must be valid JSON", file=sys.stderr)
                return EXIT_USAGE_ERROR
            from stepwise.config import save_project_local_config
            project_dir = _project_dir(args) or Path.cwd()
            save_project_local_config(project_dir, notify_context=ctx)
            _io(args).log("success", f"Set {args.key} in project config")
            return EXIT_SUCCESS
        elif args.key.startswith("max_concurrent_by_executor"):
            from stepwise.config import save_project_local_config
            project_dir = _project_dir(args) or Path.cwd()
            if "." in args.key:
                _, exec_type = args.key.split(".", 1)
                try:
                    limit = int(value)
                except ValueError:
                    print(f"Error: value must be an integer, got '{value}'", file=sys.stderr)
                    return EXIT_USAGE_ERROR
                if limit < 0:
                    print("Error: limit must be non-negative (0 = unlimited)", file=sys.stderr)
                    return EXIT_USAGE_ERROR
                config = load_config(project_dir)
                limits = dict(config.max_concurrent_by_executor)
                if limit == 0:
                    limits.pop(exec_type, None)
                else:
                    limits[exec_type] = limit
                save_project_local_config(project_dir, max_concurrent_by_executor=limits)
            else:
                import json as _json
                try:
                    limits = _json.loads(value)
                except _json.JSONDecodeError:
                    print("Error: value must be JSON dict, e.g. '{\"agent\": 1}'", file=sys.stderr)
                    return EXIT_USAGE_ERROR
                if not isinstance(limits, dict) or not all(isinstance(v, int) and v >= 0 for v in limits.values()):
                    print("Error: each value must be a non-negative integer", file=sys.stderr)
                    return EXIT_USAGE_ERROR
                save_project_local_config(project_dir, max_concurrent_by_executor=limits)
            _io(args).log("success", f"Set {args.key} in project config")
            return EXIT_SUCCESS
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

        project_dir = _project_dir(args) or Path.cwd()
        config = load_config(project_dir)

        if args.key == "openrouter_api_key":
            val = config.openrouter_api_key or ""
        elif args.key == "anthropic_api_key":
            val = config.anthropic_api_key or ""
        elif args.key == "default_model":
            val = config.default_model or ""
        elif args.key == "notify_url":
            val = config.notify_url or ""
        elif args.key == "notify_context":
            import json as _json
            print(_json.dumps(config.notify_context) if config.notify_context else "{}")
            return EXIT_SUCCESS
        elif args.key.startswith("max_concurrent_by_executor"):
            limits = config.resolved_executor_limits()
            if "." in args.key:
                _, exec_type = args.key.split(".", 1)
                print(limits.get(exec_type, "unlimited"))
            else:
                if limits:
                    for t, v in sorted(limits.items()):
                        print(f"  {t}: {v}")
                else:
                    print("  (no limits set)")
            return EXIT_SUCCESS
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
    """Validate flow structure and model resolution."""
    from stepwise.config import load_config_with_sources
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()

    try:
        flow_path = resolve_flow(args.flow, project_dir)
    except (FlowResolutionError, Exception) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    io = _io(args)

    # Phase 1: Structural validation
    try:
        wf = load_workflow_yaml(str(flow_path))
        errors = wf.validate()
        if errors:
            io.log("error", f"Validation failed: {flow_path.name}")
            for err in errors:
                io.log("info", f"  ✗ {err}")
            return EXIT_JOB_FAILED

        step_count = len(wf.steps)
        loop_count = sum(
            1 for s in wf.steps.values()
            for r in s.exit_rules
            if r.config.get("action") == "loop"
        )
        parts = [f"{step_count} steps"]
        if loop_count:
            parts.append(f"{loop_count} loops")
        io.log("success", f"Structure OK ({', '.join(parts)})")

        flow_warnings = wf.warnings()
        for w in flow_warnings:
            if w.startswith("\u2139"):
                io.log("info", f"  {w}")
            else:
                io.log("warn", f"  {w}")
    except YAMLLoadError as e:
        io.log("error", f"{flow_path.name}:")
        for err in e.errors:
            io.log("info", f"  - {err}")
        return EXIT_JOB_FAILED
    except Exception as e:
        io.log("error", f"{flow_path}: {e}")
        return EXIT_JOB_FAILED

    # Phase 2: Model resolution
    cws = load_config_with_sources(project_dir)
    cfg = cws.config

    all_labels = dict(cfg.labels)
    label_sources: dict[str, str] = {
        li.name: li.source for li in cws.label_info
    }

    rows = []
    for step_name, step_def in wf.steps.items():
        if step_def.executor.type not in ("llm", "agent"):
            continue

        model_ref = step_def.executor.config.get("model") or cfg.default_model or "balanced"
        resolved = cfg.resolve_model(model_ref)

        if model_ref in all_labels:
            source = label_sources.get(model_ref, "default")
            rows.append([step_name, model_ref, resolved, source])
        else:
            rows.append([step_name, resolved, resolved, "pinned"])

    if rows:
        io.table(["STEP", "MODEL", "RESOLVED", "SOURCE"], rows)

    # Check API keys
    key_parts = []
    if cfg.openrouter_api_key:
        key_parts.append("openrouter \u2713")
    else:
        key_parts.append("openrouter \u2717")
    if cfg.anthropic_api_key:
        key_parts.append("anthropic \u2713")
    else:
        key_parts.append("anthropic \u2717")
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
        # Also apply --input overrides for checking
        input_overrides = {}
        for pair in getattr(args, "inputs", None) or []:
            if "=" in pair:
                k, v = pair.split("=", 1)
                input_overrides[k] = v
        merged = {**config, **input_overrides}

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
                    io.log("info", f"  ✗ {m}{desc} — use --input {m}=\"...\" or config.local.yaml")
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
        config_block = executor.get("config", {}) if isinstance(executor, dict) else step_def.get("config", {})
        model_ref = config_block.get("model") or step_def.get("model") or cfg.default_model or "balanced"
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


def parse_meta_flags(meta_args: list[str]) -> dict:
    """Parse --meta KEY=VALUE flags into metadata dict with sys/app namespaces."""
    result: dict = {"sys": {}, "app": {}}
    for arg in meta_args:
        if "=" not in arg:
            print(f"Error: Invalid --meta format: '{arg}' (expected KEY=VALUE)", file=sys.stderr)
            raise SystemExit(EXIT_USAGE_ERROR)
        key, value = arg.split("=", 1)
        parts = key.split(".")
        if len(parts) < 2 or parts[0] not in ("sys", "app"):
            print(f"Error: --meta key must start with 'sys.' or 'app.': '{key}'", file=sys.stderr)
            raise SystemExit(EXIT_USAGE_ERROR)
        namespace = parts[0]
        leaf_key = ".".join(parts[1:])
        result[namespace][leaf_key] = value
    return result


def cmd_run(args: argparse.Namespace) -> int:
    from stepwise.flow_resolution import (
        FlowResolutionError, parse_registry_ref, resolve_flow, resolve_registry_flow,
    )
    from stepwise.runner import run_flow, parse_inputs, load_vars_file, load_flow_config

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
        inputs.update(parse_inputs(args.inputs))
    except ValueError as e:
        if getattr(args, "wait", False) or getattr(args, "async_mode", False):
            from stepwise.runner import _json_error
            _json_error(2, str(e))
            return EXIT_USAGE_ERROR
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # Parse metadata flags
    metadata = parse_meta_flags(args.meta) if args.meta else None

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
            metadata=metadata,
        )

    # --wait mode: blocking JSON output (handles own errors as JSON)
    if getattr(args, "wait", False):
        from stepwise.runner import run_wait
        wait_notify_context = None
        if getattr(args, "notify_context", None):
            import json as _json
            try:
                wait_notify_context = _json.loads(args.notify_context)
            except _json.JSONDecodeError:
                from stepwise.runner import _json_error
                _json_error(2, f"Invalid --notify-context JSON: {args.notify_context}")
                return EXIT_USAGE_ERROR
        return run_wait(
            flow_path=flow_path,
            project=project,
            objective=args.objective,
            inputs=inputs if inputs else None,
            workspace=args.workspace,
            force_local=getattr(args, "local", False),
            notify_url=getattr(args, "notify", None),
            notify_context=wait_notify_context,
            name=getattr(args, "job_name", None),
            metadata=metadata,
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
        rerun_steps=getattr(args, "rerun_steps", None),
        metadata=metadata,
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

    project_label = ""
    try:
        with urllib.request.urlopen(f"{server_url}/api/health", timeout=2) as hr:
            hp = json.loads(hr.read()).get("project_path")
            if hp:
                home = str(Path.home())
                project_label = f" ({hp.replace(home, '~', 1) if hp.startswith(home) else hp})"
    except Exception:
        pass

    job_url = f"{server_url}/jobs/{job_id}"
    print(f"▸ job submitted to running server{project_label}")
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

        meta_filters = None
        if getattr(args, "meta", None):
            meta_filters = {}
            for item in args.meta:
                if "=" not in item:
                    print(f"Error: Invalid --meta filter: '{item}' (expected KEY=VALUE)", file=sys.stderr)
                    return EXIT_USAGE_ERROR
                key, value = item.split("=", 1)
                meta_filters[key] = value

        include_archived = getattr(args, "archived", False)
        jobs = store.all_jobs(status=status_filter, top_level_only=True, meta_filters=meta_filters, include_archived=include_archived)

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

        # Show metadata if non-default
        if job.metadata != {"sys": {}, "app": {}}:
            io.note(json.dumps(job.metadata, indent=2), title="Metadata")

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
        "depends_on": job.depends_on,
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


def cmd_archive(args: argparse.Namespace) -> int:
    """Archive completed/failed/cancelled jobs."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)
    TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            body: dict = {}
            if getattr(args, "job_ids", None):
                body["job_ids"] = args.job_ids
            if getattr(args, "status", None):
                body["status"] = args.status
            if getattr(args, "group", None):
                body["group"] = args.group
            result = client._request("POST", "/api/jobs/archive", json=body)
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                count = result.get("count", 0)
                io.log("success", f"Archived {count} job(s)")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        to_archive: list = []

        if getattr(args, "job_ids", None):
            for jid in args.job_ids:
                try:
                    job = store.load_job(jid)
                except KeyError:
                    io.log("error", f"Job not found: {jid}")
                    return EXIT_JOB_FAILED
                if job.status not in TERMINAL:
                    io.log("error", f"Can only archive terminal jobs (job {jid} is {job.status.value})")
                    return EXIT_USAGE_ERROR
                to_archive.append(job)
        elif getattr(args, "status", None):
            try:
                status = JobStatus(args.status)
            except ValueError:
                io.log("error", f"Invalid status: {args.status}")
                return EXIT_USAGE_ERROR
            if status not in TERMINAL:
                io.log("error", f"Can only archive terminal statuses (completed, failed, cancelled)")
                return EXIT_USAGE_ERROR
            to_archive = store.all_jobs(status=status, top_level_only=True)
        elif getattr(args, "group", None):
            all_jobs = store.all_jobs(top_level_only=True)
            to_archive = [j for j in all_jobs if j.job_group == args.group and j.status in TERMINAL]
        else:
            io.log("error", "Specify job IDs, --status, or --group")
            return EXIT_USAGE_ERROR

        for job in to_archive:
            store.archive_job(job.id)

        if args.output == "json":
            print(json.dumps({"count": len(to_archive), "archived": [j.id for j in to_archive]}))
        else:
            io.log("success", f"Archived {len(to_archive)} job(s)")
        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_unarchive(args: argparse.Namespace) -> int:
    """Restore archived jobs."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client._request("POST", "/api/jobs/unarchive", json={"job_ids": args.job_ids})
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                count = result.get("count", 0)
                io.log("success", f"Unarchived {count} job(s)")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        restored = []
        for jid in args.job_ids:
            try:
                job = store.load_job(jid)
            except KeyError:
                io.log("error", f"Job not found: {jid}")
                return EXIT_JOB_FAILED
            if job.status != JobStatus.ARCHIVED:
                io.log("error", f"Job {jid} is not archived (status: {job.status.value})")
                return EXIT_USAGE_ERROR
            store.unarchive_job(jid)
            restored.append(jid)

        if args.output == "json":
            print(json.dumps({"count": len(restored), "unarchived": restored}))
        else:
            io.log("success", f"Unarchived {len(restored)} job(s)")
        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_rm(args: argparse.Namespace) -> int:
    """Permanently delete jobs."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)
    ACTIVE = {JobStatus.RUNNING, JobStatus.PENDING}

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            body: dict = {}
            if getattr(args, "job_ids", None):
                body["job_ids"] = args.job_ids
            if getattr(args, "status", None):
                body["status"] = args.status
            if getattr(args, "group", None):
                body["group"] = args.group
            if getattr(args, "archived", False):
                body["archived"] = True
            result = client._request("POST", "/api/jobs/bulk-delete", json=body)
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                count = result.get("count", 0)
                io.log("success", f"Deleted {count} job(s)")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        to_delete: list = []

        if getattr(args, "job_ids", None):
            for jid in args.job_ids:
                try:
                    job = store.load_job(jid)
                except KeyError:
                    io.log("error", f"Job not found: {jid}")
                    return EXIT_JOB_FAILED
                if job.status in ACTIVE:
                    io.log("error", f"Cannot delete active job {jid} (status: {job.status.value}). Cancel it first.")
                    return EXIT_USAGE_ERROR
                to_delete.append(job)
        elif getattr(args, "archived", False):
            to_delete = store.all_jobs(status=JobStatus.ARCHIVED, top_level_only=True)
        elif getattr(args, "status", None):
            try:
                status = JobStatus(args.status)
            except ValueError:
                io.log("error", f"Invalid status: {args.status}")
                return EXIT_USAGE_ERROR
            if status in ACTIVE:
                io.log("error", f"Cannot bulk-delete active jobs. Cancel them first.")
                return EXIT_USAGE_ERROR
            to_delete = store.all_jobs(status=status, top_level_only=True)
        elif getattr(args, "group", None):
            all_jobs = store.all_jobs(top_level_only=True, include_archived=True)
            to_delete = [j for j in all_jobs if j.job_group == args.group and j.status not in ACTIVE]
        else:
            io.log("error", "Specify job IDs, --status, --group, or --archived")
            return EXIT_USAGE_ERROR

        if len(to_delete) > 1 and not getattr(args, "force", False):
            io.log("warning", f"About to permanently delete {len(to_delete)} job(s). Use --force to skip confirmation.")
            return EXIT_USAGE_ERROR

        for job in to_delete:
            store.delete_job(job.id)

        if args.output == "json":
            print(json.dumps({"count": len(to_delete), "deleted": [j.id for j in to_delete]}))
        else:
            io.log("success", f"Deleted {len(to_delete)} job(s)")
        return EXIT_SUCCESS
    finally:
        store.close()


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
    from stepwise.models import parse_duration
    return parse_duration(s)


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


def cmd_login(args: argparse.Namespace) -> int:
    """Log in to the Stepwise registry via GitHub Device Flow."""
    import time

    from stepwise.registry_client import (
        RegistryError,
        get_registry_url,
        initiate_device_flow,
        load_auth,
        poll_device_flow,
        save_auth,
        verify_auth,
    )

    io = _io(args)

    # Check existing auth
    auth = load_auth()
    if auth and auth.get("auth_token"):
        try:
            verify_auth(auth["auth_token"])
            io.log("success", f"Already logged in as @{auth.get('github_username', '?')}")
            return EXIT_SUCCESS
        except RegistryError:
            pass  # Token invalid, proceed to re-login

    # Initiate Device Flow
    try:
        flow = initiate_device_flow()
    except RegistryError as e:
        io.log("error", f"Failed to start login: {e}")
        return EXIT_USAGE_ERROR

    device_code = flow["device_code"]
    user_code = flow["user_code"]
    verification_uri = flow["verification_uri"]
    interval = flow.get("interval", 5)

    io.log("info", f"Visit {verification_uri} and enter code: {user_code}")

    # Poll loop
    try:
        while True:
            time.sleep(interval)
            try:
                result = poll_device_flow(device_code)
            except RegistryError as e:
                io.log("error", f"Login failed: {e}")
                return EXIT_USAGE_ERROR

            if "auth_token" in result:
                registry_url = get_registry_url()
                save_auth(result["auth_token"], result["github_username"], registry_url)
                io.log("success", f"Logged in as @{result['github_username']}. You can now publish flows with `stepwise share`.")
                return EXIT_SUCCESS

            error = result.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error in ("expired_token", "access_denied"):
                io.log("error", f"Login failed: {error.replace('_', ' ')}")
                return EXIT_USAGE_ERROR
            else:
                io.log("error", f"Unexpected response: {error or result}")
                return EXIT_USAGE_ERROR
    except KeyboardInterrupt:
        io.log("info", "\nLogin cancelled.")
        return EXIT_USAGE_ERROR


def cmd_logout(args: argparse.Namespace) -> int:
    """Log out of the Stepwise registry."""
    from stepwise.registry_client import clear_auth, load_auth

    io = _io(args)

    if load_auth() is None:
        io.log("info", "Not logged in.")
        return EXIT_SUCCESS

    clear_auth()
    io.log("success", "Logged out.")
    return EXIT_SUCCESS


def cmd_share(args: argparse.Namespace) -> int:
    """Publish a flow to the registry."""
    from stepwise.bundle import BundleError, collect_bundle
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.registry_client import load_auth, publish_flow, update_flow, RegistryError
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

    # Load auth credentials
    auth = load_auth()
    auth_token = auth["auth_token"] if auth else None

    if not do_update and not auth_token:
        io.log("error", "You need to log in first. Run `stepwise login`.")
        return EXIT_USAGE_ERROR

    try:
        if do_update:
            import yaml as yaml_lib
            data = yaml_lib.safe_load(yaml_content)
            name = data.get("name", flow_path.stem.replace(".flow", ""))
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
            result = update_flow(slug, yaml_content, auth_token=auth_token)
            io.log("success", f"Updated: {result.get('url', slug)}")
        else:
            result = publish_flow(yaml_content, author=author, files=bundle_files, auth_token=auth_token)
            slug = result.get("slug", "")
            file_msg = f" + {len(bundle_files)} file(s)" if bundle_files else ""
            io.log("info", f"Publishing as \"{result.get('name', '')}\" by {result.get('author', 'anonymous')}...")
            io.log("success", f"Published: {result.get('url', '')}{file_msg}")
            io.log("info", f"Get: stepwise get {slug}")
            if result.get("update_token"):
                io.log("info", "Token saved to ~/.config/stepwise/tokens.json")
    except RegistryError as e:
        io.log("error", str(e))
        if e.status_code == 401:
            io.log("info", "Your session may have expired. Run `stepwise login` to re-authenticate.")
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
    from stepwise.schema import generate_input_schema, generate_schema
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

    if getattr(args, "input_schema", False):
        schema = generate_input_schema(wf)
    else:
        schema = generate_schema(wf)
    print(json.dumps(schema, indent=2))
    return EXIT_SUCCESS


def cmd_output(args: argparse.Namespace) -> int:
    """Retrieve job outputs after completion."""
    # Positional step_name → --step (if --step not already set)
    raw_output = False
    if getattr(args, "step_name", None) and not getattr(args, "step", None):
        args.step = args.step_name
        raw_output = True

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
            # For positional step access via server, unwrap to raw artifact
            if raw_output and isinstance(data, dict) and args.step in data:
                data = data[args.step]
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

            # Positional step: unwrap to raw artifact
            if raw_output:
                step_val = result.get(args.step)
                if isinstance(step_val, dict) and "_error" in step_val:
                    print(json.dumps(step_val, indent=2, default=str))
                    return EXIT_JOB_FAILED
                print(json.dumps(step_val, indent=2, default=str))
                return EXIT_SUCCESS

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
    """Satisfy a suspended external step from the command line."""
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


def _wait_single(args: argparse.Namespace, job_id: str) -> int:
    """Single-job wait — backward-compatible path (original cmd_wait logic)."""
    project = _find_project_or_exit(args)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.runner import wait_for_job_id
        return wait_for_job_id(server_url, job_id, project_dir=project.dot_dir)

    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.runner import wait_for_job
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            store.load_job(job_id)
        except KeyError:
            print(json.dumps({"status": "error", "error": f"Job not found: {job_id}"}))
            return EXIT_JOB_FAILED

        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)
        return wait_for_job(engine, store, job_id, project_dir=project.dot_dir)
    finally:
        store.close()


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until job(s) reach terminal state or suspension."""
    job_ids = args.job_ids
    wait_mode = getattr(args, "wait_mode", None)

    # Multiple jobs without --all/--any is ambiguous
    if len(job_ids) > 1 and not wait_mode:
        print(json.dumps({"status": "error",
                          "error": "Multiple job IDs require --all or --any flag"}))
        return EXIT_USAGE_ERROR

    # Single job without flags → backward-compatible single-job path
    if len(job_ids) == 1 and not wait_mode:
        return _wait_single(args, job_ids[0])

    # Multi-job path (or single job with explicit --all/--any)
    project = _find_project_or_exit(args)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.runner import wait_for_job_ids
        return wait_for_job_ids(server_url, job_ids, wait_mode, project_dir=project.dot_dir)

    # Local (no-server) multi-job wait
    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.runner import wait_for_jobs
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir),
                        project_dir=project.dot_dir)
        return wait_for_jobs(engine, store, job_ids, wait_mode, project_dir=project.dot_dir)
    finally:
        store.close()


def _get_doc_description(path: Path) -> str:
    """Extract one-line description from a markdown file."""
    try:
        text = path.read_text()
    except OSError:
        return ""
    lines = text.split("\n")
    past_heading = False
    for line in lines:
        stripped = line.strip()
        if not past_heading:
            if stripped.startswith("# "):
                past_heading = True
            continue
        if not stripped:
            continue
        # Skip blockquotes, sub-headings, metadata lines, and horizontal rules
        if stripped.startswith(">") or stripped.startswith("#") or stripped.startswith("**") or stripped.startswith("---"):
            continue
        # Truncate long descriptions
        if len(stripped) > 80:
            return stripped[:77] + "..."
        return stripped
    return ""


def _list_doc_topics(docs_dir: Path) -> None:
    """List available doc topics with descriptions."""
    topics = []
    for md_file in sorted(docs_dir.rglob("*.md")):
        stem = md_file.stem
        desc = _get_doc_description(md_file)
        topics.append((stem, desc))

    if not topics:
        print("No documentation files found.")
        return

    print("Available documentation topics:\n")
    max_name = max(len(t[0]) for t in topics)
    for name, desc in topics:
        print(f"  {name:<{max_name}}  {desc}")
    print(f"\nUse 'stepwise docs <topic>' to read a specific topic.")


def _search_doc_content(candidates: list, query: str) -> list:
    """Search doc file content for query string, return matching sections."""
    query_lower = query.lower()
    results = []
    for md_file in candidates:
        text = md_file.read_text()
        if query_lower not in text.lower():
            continue
        # Split into sections by markdown headers
        sections = []
        current_title = md_file.stem
        current_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("#"):
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines)))
                current_title = line.lstrip("#").strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_title, "\n".join(current_lines)))
        for title, body in sections:
            if query_lower in body.lower():
                results.append((md_file.stem, title, body))
    return results


def cmd_docs(args: argparse.Namespace) -> int:
    """Print reference documentation."""
    from stepwise.project import get_docs_dir

    docs_dir = get_docs_dir()
    if docs_dir is None:
        print("Documentation not found.", file=sys.stderr)
        return EXIT_USAGE_ERROR

    topic = getattr(args, "topic", None)

    if not topic:
        _list_doc_topics(docs_dir)
        return EXIT_SUCCESS

    # Collect all markdown files
    candidates = list(docs_dir.rglob("*.md"))

    # Try exact stem match
    match = None
    for c in candidates:
        if c.stem == topic:
            match = c
            break

    # Try partial match
    if match is None:
        for c in candidates:
            if topic in c.stem:
                match = c
                break

    # Content search fallback: find sections containing the query
    if match is None:
        results = _search_doc_content(candidates, topic)
        if results:
            print(f"No exact topic match for '{topic}'. Showing matching sections:\n")
            for file_stem, section_title, section_text in results:
                print(f"── {file_stem} > {section_title} ──\n")
                print(section_text.strip())
                print()
            return EXIT_SUCCESS
        print(f"No documentation found for '{topic}'.", file=sys.stderr)
        print("Run 'stepwise docs' to see available topics.", file=sys.stderr)
        return EXIT_USAGE_ERROR

    print(match.read_text())
    return EXIT_SUCCESS


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


def cmd_help(args: argparse.Namespace) -> int:
    """Interactive help assistant powered by LLM."""
    import os

    import httpx

    question = " ".join(args.question) if args.question else ""
    if not question.strip():
        print("Usage: stepwise help \"your question here\"")
        print("Example: stepwise help \"how do I create a flow?\"")
        return EXIT_USAGE_ERROR

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Try loading from stepwise config
        from stepwise.config import load_config

        cfg = load_config()
        api_key = cfg.openrouter_api_key or ""

    if not api_key:
        print("Error: No OpenRouter API key found.", file=sys.stderr)
        print("Set OPENROUTER_API_KEY or run: stepwise config set openrouter_api_key <key>", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # Gather context
    context_parts: list[str] = []

    # 1. Docs: quickstart + concepts (first 2000 chars each)
    from stepwise.project import get_docs_dir

    docs_dir = get_docs_dir()
    if docs_dir:
        for name in ("quickstart", "concepts"):
            doc_path = docs_dir / f"{name}.md"
            if doc_path.exists():
                text = doc_path.read_text()[:2000]
                context_parts.append(f"## {name}.md\n{text}")

    # 2. Agent-help output (flow catalog)
    try:
        from stepwise.agent_help import generate_agent_help

        project_dir = Path(args.project_dir) if args.project_dir else Path.cwd()
        agent_help = generate_agent_help(project_dir, fmt="compact")
        context_parts.append(f"## Agent Instructions\n{agent_help}")
    except Exception:
        pass

    # 3. List of available flows
    try:
        from stepwise.flow_resolution import discover_flows

        project_dir = Path(args.project_dir) if args.project_dir else Path.cwd()
        flows = discover_flows(project_dir)
        if flows:
            flow_names = [f.name for f in flows]
            context_parts.append(f"## Available Flows\n{', '.join(flow_names)}")
    except Exception:
        pass

    context = "\n\n".join(context_parts)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant for Stepwise, a portable workflow orchestration tool "
                "for agents and humans. Answer the user's question using the provided context. "
                "Be concise and practical. Include CLI examples when relevant."
            ),
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}",
        },
    ]

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "anthropic/claude-sonnet-4-6",
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 2048,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        print(answer)
        return EXIT_SUCCESS
    except httpx.HTTPStatusError as exc:
        print(f"Error: OpenRouter API returned {exc.response.status_code}", file=sys.stderr)
        return EXIT_JOB_FAILED
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_JOB_FAILED


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
    from pathlib import Path

    # Block update if running from editable install (dev mode)
    try:
        import importlib.metadata
        dist = importlib.metadata.distribution("stepwise-run")
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            import json as _json
            data = _json.loads(direct_url_text)
            if data.get("dir_info", {}).get("editable"):
                io = _io(args)
                io.log("warn", "Running from editable install — 'stepwise update' is disabled.")
                io.log("info", "To update, pull the latest source: cd ~/work/stepwise && git pull")
                return 0
    except Exception:
        pass

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


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove stepwise from this project."""
    import shutil

    io = _io(args)
    removed: list[str] = []

    # --- Find project ---
    start = Path(args.project_dir) if args.project_dir else None
    try:
        project = find_project(start)
    except ProjectNotFoundError:
        project = None

    if project is None and not getattr(args, "cli", False):
        io.log("info", "No .stepwise/ project found. Nothing to remove.")
        return EXIT_SUCCESS

    if project is not None:
        # --- Stop running server ---
        try:
            _stop_server_for_project(project.dot_dir, io)
        except Exception:
            pass  # No server running or pidfile missing — fine

        # --- Check for active jobs ---
        try:
            from stepwise.models import JobStatus
            from stepwise.store import SQLiteStore

            store = SQLiteStore(str(project.db_path))
            active = store.active_jobs()
            # Also check paused (suspended) jobs
            paused_rows = store._conn.execute(
                "SELECT * FROM jobs WHERE status = ?", (JobStatus.PAUSED.value,)
            ).fetchall()
            paused = [store._row_to_job(r) for r in paused_rows]
            in_flight = active + paused

            if in_flight and not getattr(args, "force", False):
                io.log("warn", f"Found {len(in_flight)} active/paused job(s):")
                for job in in_flight:
                    io.log("warn", f"  {job.id[:12]}  {job.status.value:10s}  {job.objective or ''}")
                io.log("error", "Aborting. Pass --force to remove anyway.")
                return EXIT_USAGE_ERROR
        except (OSError, Exception):
            if not getattr(args, "force", False):
                io.log("warn", "Could not check for active jobs (DB may be locked). Use --force to proceed.")
                return EXIT_USAGE_ERROR

        # --- Confirm and remove .stepwise/ ---
        yes = getattr(args, "yes", False)
        if not yes:
            if not io.prompt_confirm(f"Remove {project.dot_dir} and all job data?", default=False):
                io.log("info", "Aborted.")
                return EXIT_SUCCESS

        try:
            shutil.rmtree(project.dot_dir)
            removed.append(str(project.dot_dir))
            io.log("success", f"Removed {project.dot_dir}")
        except OSError as e:
            io.log("error", f"Failed to remove {project.dot_dir}: {e}")

        # --- Optionally remove flows/ ---
        flows_dir = project.root / "flows"
        if flows_dir.is_dir():
            remove_flows = getattr(args, "remove_flows", False)
            if not remove_flows and not yes:
                remove_flows = io.prompt_confirm(f"Also remove {flows_dir}?", default=False)
            if remove_flows:
                try:
                    shutil.rmtree(flows_dir)
                    removed.append(str(flows_dir))
                    io.log("success", f"Removed {flows_dir}")
                except OSError as e:
                    io.log("error", f"Failed to remove {flows_dir}: {e}")

        # --- Clean .gitignore ---
        gitignore = project.root / ".gitignore"
        if gitignore.exists():
            try:
                content = gitignore.read_text()
                stepwise_entries = {f"{DOT_DIR_NAME}/", "config.local.yaml", "*.config.local.yaml"}
                lines = content.splitlines(keepends=True)
                cleaned = [l for l in lines if l.strip() not in stepwise_entries]
                new_content = "".join(cleaned)
                # Remove trailing blank lines that may be left over
                new_content = new_content.rstrip("\n") + "\n" if new_content.strip() else ""
                if new_content != content:
                    gitignore.write_text(new_content)
                    io.log("info", "Cleaned stepwise entries from .gitignore")
            except OSError:
                pass

    # --- Optionally uninstall CLI tool ---
    if getattr(args, "cli", False):
        import subprocess

        method = _detect_install_method()
        uninstall_cmds = {
            "uv": ["uv", "tool", "uninstall", "stepwise-run"],
            "pipx": ["pipx", "uninstall", "stepwise-run"],
            "pip": [sys.executable, "-m", "pip", "uninstall", "-y", "stepwise-run"],
        }
        cmd = uninstall_cmds[method]
        io.log("info", f"Uninstalling CLI via {method}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            removed.append(f"stepwise CLI ({method})")
            io.log("success", "CLI tool uninstalled.")
        else:
            io.log("error", f"CLI uninstall failed: {result.stderr.strip()}")
            io.log("info", f"You can uninstall manually: {' '.join(cmd)}")

    # --- Summary ---
    if removed:
        io.log("success", "Uninstall complete. Removed:")
        for item in removed:
            io.log("info", f"  {item}")
    elif project is None:
        pass  # already printed "nothing to remove"
    else:
        io.log("info", "Nothing was removed.")

    io.log("info", "Thanks for using Stepwise!")
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
    p_server.add_argument("action", choices=["start", "stop", "restart", "status", "log"])
    p_server.add_argument("--port", type=int, help="Port (default: 8340)")
    p_server.add_argument("--host", help="Bind address (default: 0.0.0.0)")
    p_server.add_argument("--detach", "-d", action="store_true", help="Run in background (default)")
    p_server.add_argument("--no-detach", action="store_true", help="Run in foreground")
    p_server.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    p_server.add_argument("--lines", "-n", type=int, default=50,
                          help="Number of lines to show (default: 50; log action only)")
    p_server.add_argument("--follow", "-f", action="store_true",
                          help="Stream new log lines as they are written (log action only)")

    # run
    p_run = sub.add_parser("run", help="Run a flow")
    p_run.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_run.add_argument("--watch", action="store_true", help="Ephemeral server + browser UI")
    p_run.add_argument("--wait", action="store_true", help="Block until completion, JSON output on stdout")
    p_run.add_argument("--async", action="store_true", dest="async_mode",
                       help="Fire-and-forget, returns job_id immediately")
    p_run.add_argument("--output", choices=["json"], dest="output_format",
                       help="Output format (currently only json)")
    p_run.add_argument("--input", action="append", dest="inputs", metavar="KEY=VALUE",
                       help="Pass input variable (repeatable). Use KEY=@path to read from file.")
    p_run.add_argument("--var", action="append", dest="inputs", metavar="KEY=VALUE",
                       help=argparse.SUPPRESS)  # deprecated alias for --input
    p_run.add_argument("--vars-file", help="Load variables from YAML/JSON file")
    p_run.add_argument("--port", type=int, help="Override port (for --watch)")
    p_run.add_argument("--objective", help="Set job objective (defaults to flow name)")
    p_run.add_argument("--name", dest="job_name", help="Human-friendly job name")
    p_run.add_argument("--workspace", help="Override workspace directory")
    p_run.add_argument("--report", action="store_true", help="Generate HTML report after completion")
    p_run.add_argument("--report-output", help="Report output path (default: <flow>-report.html)")
    p_run.add_argument("--no-open", action="store_true", help="Don't auto-open browser (for --watch)")
    p_run.add_argument("--local", action="store_true", help="Force local execution (skip server delegation)")
    p_run.add_argument("--rerun", action="append", dest="rerun_steps", metavar="STEP",
                       help="Bypass cache for this step (repeatable)")
    p_run.add_argument("--notify", metavar="URL", help="Webhook URL for job event notifications")
    p_run.add_argument("--notify-context", metavar="JSON", dest="notify_context",
                       help="JSON context to include in webhook payloads")
    p_run.add_argument("--meta", action="append", default=[], dest="meta",
                       metavar="KEY=VALUE",
                       help="Set job metadata (dot notation: sys.origin=cli, app.project=foo)")

    # check
    p_check = sub.add_parser("check", help="Validate flow structure and model resolution")
    p_check.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # validate
    p_validate = sub.add_parser("validate", help="Validate a flow file")
    p_validate.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_validate.add_argument("--fix", action="store_true", help="Auto-fix fixable warnings")

    # test-fixture
    p_tf = sub.add_parser("test-fixture", help="Generate a pytest test harness for a flow")
    p_tf.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_tf.add_argument("-o", "--output", help="Output file path (default: stdout)")

    # preflight
    p_preflight = sub.add_parser("preflight", help="Pre-run check: config + requirements + models")
    p_preflight.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_preflight.add_argument("--input", action="append", dest="inputs",
                             help="Variable override (key=value)")
    p_preflight.add_argument("--var", action="append", dest="inputs",
                             help=argparse.SUPPRESS)  # deprecated alias for --input

    # diagram
    p_diagram = sub.add_parser("diagram", help="Generate a diagram from a flow file")
    p_diagram.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_diagram.add_argument("-o", "--output", help="Output file path (default: <name>.<format>)")
    p_diagram.add_argument("-f", "--format", choices=["svg", "png", "pdf"], default="svg",
                           help="Output format (default: svg)")

    # templates
    sub.add_parser("templates", help="List available templates")

    # flows
    sub.add_parser("flows", help="List flows in this project")

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
    p_jobs.add_argument("--archived", action="store_true", help="Include archived jobs")
    p_jobs.add_argument("--status", help="Filter by status")
    p_jobs.add_argument("--name", dest="filter_name", help="Filter by name (substring match)")
    p_jobs.add_argument("--meta", action="append", default=[], dest="meta",
                       metavar="KEY=VALUE",
                       help="Filter by metadata (e.g. sys.origin=cli)")

    # status
    p_status = sub.add_parser("status", help="Show job detail")
    p_status.add_argument("job_id", help="Job ID")
    p_status.add_argument("--output", choices=["table", "json"], default="table")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a running job")
    p_cancel.add_argument("job_id", help="Job ID")
    p_cancel.add_argument("--output", choices=["table", "json"], default="table",
                          help="Output format")

    # archive
    p_archive = sub.add_parser("archive", help="Archive completed/failed/cancelled jobs")
    p_archive.add_argument("job_ids", nargs="*", metavar="JOB_ID", help="Job ID(s) to archive")
    p_archive.add_argument("--status", help="Archive all jobs with this status (completed, failed, cancelled)")
    p_archive.add_argument("--group", "-g", help="Archive all terminal jobs in this group")
    p_archive.add_argument("--output", choices=["table", "json"], default="table")

    # unarchive
    p_unarchive = sub.add_parser("unarchive", help="Restore archived jobs")
    p_unarchive.add_argument("job_ids", nargs="+", metavar="JOB_ID", help="Job ID(s) to unarchive")
    p_unarchive.add_argument("--output", choices=["table", "json"], default="table")

    # rm
    p_rm = sub.add_parser("rm", help="Permanently delete jobs")
    p_rm.add_argument("job_ids", nargs="*", metavar="JOB_ID", help="Job ID(s) to delete")
    p_rm.add_argument("--status", help="Delete all jobs with this status")
    p_rm.add_argument("--group", "-g", help="Delete all jobs in this group")
    p_rm.add_argument("--archived", action="store_true", help="Delete all archived jobs")
    p_rm.add_argument("--force", "-f", action="store_true", help="Skip confirmation for bulk deletes")
    p_rm.add_argument("--output", choices=["table", "json"], default="table")

    # schema
    p_schema = sub.add_parser("schema", help="Generate JSON tool contract from a flow file")
    p_schema.add_argument("flow", help="Flow name or path to .flow.yaml file")
    p_schema.add_argument("--input-schema", action="store_true",
                          help="Output JSON Schema for flow inputs instead of tool contract")

    # tail
    p_tail = sub.add_parser("tail", help="Stream live events for a job")
    p_tail.add_argument("job_id", help="Job ID to tail")

    # logs
    p_logs = sub.add_parser("logs", help="Show full event history for a job")
    p_logs.add_argument("job_id", help="Job ID")

    # output
    p_output = sub.add_parser("output", help="Retrieve job outputs")
    p_output.add_argument("job_id", nargs="?", default=None, help="Job ID")
    p_output.add_argument("step_name", nargs="?", default=None,
                          help="Step name (positional shorthand for --step)")
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
    p_wait = sub.add_parser("wait", help="Block until job(s) complete or suspend")
    p_wait.add_argument("job_ids", nargs="+", metavar="JOB_ID", help="Job ID(s) to wait on")
    wait_mode = p_wait.add_mutually_exclusive_group()
    wait_mode.add_argument("--all", dest="wait_mode", action="store_const", const="all",
                           help="Wait for all jobs to reach terminal state")
    wait_mode.add_argument("--any", dest="wait_mode", action="store_const", const="any",
                           help="Wait for first job to reach terminal state")

    # fulfill
    p_fulfill = sub.add_parser("fulfill", help="Satisfy a suspended external step")
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

    # extensions
    p_extensions = sub.add_parser(
        "extensions",
        aliases=["extension"],
        help="List discovered extensions (stepwise-* executables on PATH)",
    )
    ext_sub = p_extensions.add_subparsers(dest="ext_action")
    p_ext_list = ext_sub.add_parser("list", help="List discovered extensions")
    p_ext_list.add_argument("--refresh", action="store_true",
                            help="Bypass cache and force a fresh scan")
    # Default action for bare `stepwise extensions` is also list
    p_extensions.add_argument("--refresh", action="store_true",
                              help="Bypass cache and force a fresh scan")

    # docs
    p_docs = sub.add_parser("docs", help="Print reference documentation")
    p_docs.add_argument("topic", nargs="?", help="Documentation topic (e.g., patterns, cli, executors)")

    # cache
    p_cache = sub.add_parser("cache", help="Manage step result cache")
    cache_sub = p_cache.add_subparsers(dest="cache_action")
    p_cache_clear = cache_sub.add_parser("clear", help="Clear cached results")
    p_cache_clear.add_argument("--flow", help="Filter by flow name")
    p_cache_clear.add_argument("--step", help="Filter by step name")
    cache_sub.add_parser("stats", help="Show cache statistics")
    p_cache_debug = cache_sub.add_parser("debug", help="Show cache key for a step")
    p_cache_debug.add_argument("flow", help="Flow file path")
    p_cache_debug.add_argument("step", help="Step name")
    p_cache_debug.add_argument("--input", action="append", dest="inputs", metavar="KEY=VALUE",
                               help="Input variable (repeatable)")
    p_cache_debug.add_argument("--var", action="append", dest="inputs", metavar="KEY=VALUE",
                               help=argparse.SUPPRESS)  # deprecated alias for --input

    # job (staging & orchestration)
    p_job = sub.add_parser("job", help="Job staging and orchestration")
    job_sub = p_job.add_subparsers(dest="job_command")

    # job create
    p_job_create = job_sub.add_parser("create", help="Create a staged job")
    p_job_create.add_argument("flow", help="Flow name or path")
    p_job_create.add_argument("--input", "-i", action="append", default=[], dest="inputs",
                              metavar="KEY=VALUE", help="Input parameter")
    p_job_create.add_argument("--group", "-g", help="Group label")
    p_job_create.add_argument("--name", help="Job name")
    p_job_create.add_argument("--approve", action="store_true",
                              help="Require approval before job can run")
    p_job_create.add_argument("--output", choices=["table", "json"], default="table")

    # job show
    p_job_show = job_sub.add_parser("show", help="Show staged/pending jobs")
    p_job_show.add_argument("job_id", nargs="?", help="Job ID (omit for listing)")
    p_job_show.add_argument("--group", "-g", help="Filter by group")
    p_job_show.add_argument("--all", action="store_true", help="Include running/paused jobs")
    p_job_show.add_argument("--output", choices=["table", "json"], default="table")

    # job run
    p_job_run = job_sub.add_parser("run", help="Run staged jobs")
    p_job_run.add_argument("job_id", nargs="?", help="Run a single staged job")
    p_job_run.add_argument("--group", "-g", help="Run all staged jobs in group")
    p_job_run.add_argument("--output", choices=["table", "json"], default="table")

    # job dep
    p_job_dep = job_sub.add_parser("dep", help="Manage dependencies between jobs")
    p_job_dep.add_argument("job_id", help="Job that should wait")
    p_job_dep.add_argument("--after", help="Job to wait for (add dep)")
    p_job_dep.add_argument("--rm", help="Remove dependency on this job")
    p_job_dep.add_argument("--output", choices=["table", "json"], default="table")

    # job approve
    p_job_approve = job_sub.add_parser("approve", help="Approve a job awaiting approval")
    p_job_approve.add_argument("job_id", help="Job ID to approve")
    p_job_approve.add_argument("--output", choices=["table", "json"], default="table")

    # job cancel
    p_job_cancel = job_sub.add_parser("cancel", help="Cancel a staged/pending/running job")
    p_job_cancel.add_argument("job_id", help="Job ID")
    p_job_cancel.add_argument("--output", choices=["table", "json"], default="table")

    # job rm
    p_job_rm = job_sub.add_parser("rm", help="Remove a staged job")
    p_job_rm.add_argument("job_id", help="Job ID")
    p_job_rm.add_argument("--output", choices=["table", "json"], default="table")

    # login
    sub.add_parser("login", help="Log in to the Stepwise registry via GitHub")

    # logout
    sub.add_parser("logout", help="Log out of the Stepwise registry")

    # update
    sub.add_parser("update", help="Upgrade stepwise to the latest version")

    # version
    sub.add_parser("version", help="Print version and exit")

    # welcome
    sub.add_parser("welcome", help="Try the interactive welcome demo")

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Remove stepwise from this project")
    p_uninstall.add_argument("--yes", "-y", action="store_true",
                              help="Skip confirmation prompts")
    p_uninstall.add_argument("--force", action="store_true",
                              help="Proceed even with active/paused jobs")
    p_uninstall.add_argument("--remove-flows", action="store_true",
                              help="Also remove flows/ directory")
    p_uninstall.add_argument("--cli", action="store_true",
                              help="Also uninstall the stepwise CLI tool")

    return parser


def cmd_extensions(args: argparse.Namespace) -> int:
    """List discovered stepwise extensions."""
    from stepwise.extensions import scan_extensions

    # Determine dot_dir for caching (optional — non-fatal if no project found)
    dot_dir = None
    try:
        project = _find_project_or_exit(args)
        dot_dir = project.dot_dir
    except SystemExit:
        pass

    # --refresh can come from bare `stepwise extensions` or `stepwise extensions list`
    refresh = getattr(args, "refresh", False)

    extensions = scan_extensions(dot_dir=dot_dir, refresh=refresh)

    if not extensions:
        print("No extensions found.")
        print()
        print("Extensions are executables named 'stepwise-<name>' on your PATH.")
        print("Example: create a script called 'stepwise-telegram' to add 'stepwise telegram'.")
        return EXIT_SUCCESS

    # Column widths
    name_w = max(len("NAME"), max(len(e.name) for e in extensions))
    ver_w = max(len("VERSION"), max(len(e.version or "-") for e in extensions))
    desc_w = max(len("DESCRIPTION"), max(len(e.description or "-") for e in extensions))

    header = (
        f"{'NAME':<{name_w}}  {'VERSION':<{ver_w}}  {'DESCRIPTION':<{desc_w}}  PATH"
    )
    separator = "-" * len(header)
    print(header)
    print(separator)
    for ext in extensions:
        ver = ext.version or "-"
        desc = ext.description or "-"
        print(f"{ext.name:<{name_w}}  {ver:<{ver_w}}  {desc:<{desc_w}}  {ext.path}")

    return EXIT_SUCCESS


def _get_store_or_client(args):
    """Return (store, client) — exactly one is non-None.

    Uses detect_server(). If server running, return StepwiseClient for API
    calls. If not, open store directly.
    """
    project = _find_project_or_exit(args)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient
        return None, StepwiseClient(server_url), project
    from stepwise.store import SQLiteStore
    return SQLiteStore(str(project.db_path)), None, project


def _cmd_job_create(args) -> int:
    """Create a staged job from a flow."""
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.runner import parse_inputs
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
    from stepwise.models import Job, JobStatus, WorkflowDefinition, _now, _gen_id

    io = _io(args)
    project = _find_project_or_exit(args)
    project_dir = _project_dir(args) or Path.cwd()

    # Resolve flow
    try:
        flow_path = resolve_flow(args.flow, project_dir)
    except FlowResolutionError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    # Parse workflow
    try:
        workflow = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        io.log("error", f"Invalid flow: {'; '.join(e.errors)}")
        return EXIT_USAGE_ERROR

    # Parse inputs
    try:
        inputs = parse_inputs(args.inputs)
    except ValueError as e:
        io.log("error", str(e))
        return EXIT_USAGE_ERROR

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            group = getattr(args, "group", None)
            initial_status = "awaiting_approval" if getattr(args, "approve", False) else "staged"
            result = client.create_job(
                objective=getattr(args, "name", None) or workflow.metadata.name or args.flow,
                workflow=workflow.to_dict(),
                inputs=inputs or None,
                name=getattr(args, "name", None),
                status=initial_status,
                job_group=group,
            )
            job_id = result.get("id")
            if args.output == "json":
                print(json.dumps({"id": job_id, "status": initial_status,
                                  "job_group": group}))
            else:
                io.log("success", f"Created {initial_status} job {job_id}")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    from stepwise.store import SQLiteStore
    store = SQLiteStore(str(project.db_path))
    try:
        now = _now()
        initial_status = JobStatus.AWAITING_APPROVAL if getattr(args, "approve", False) else JobStatus.STAGED
        job = Job(
            id=_gen_id("job"),
            objective=getattr(args, "name", None) or workflow.metadata.name or args.flow,
            name=getattr(args, "name", None),
            workflow=workflow,
            status=initial_status,
            inputs=inputs,
            job_group=getattr(args, "group", None),
            created_at=now,
            updated_at=now,
        )

        # Validate $job_ref inputs and auto-add dependencies
        ref_job_ids = []
        for key, value in job.inputs.items():
            if isinstance(value, dict) and "$job_ref" in value:
                ref_id = value["$job_ref"]
                try:
                    store.load_job(ref_id)
                except KeyError:
                    io.log("error", f"Referenced job not found: {ref_id} (from input '{key}')")
                    return EXIT_USAGE_ERROR
                ref_job_ids.append(ref_id)

        store.save_job(job)

        # Auto-add dependency edges for referenced jobs
        for ref_id in ref_job_ids:
            if store.would_create_cycle(job.id, ref_id):
                store.delete_job(job.id)
                io.log("error", f"Adding dependency on {ref_id} would create a cycle")
                return EXIT_USAGE_ERROR
            store.add_job_dependency(job.id, ref_id)

        if args.output == "json":
            print(json.dumps({"id": job.id, "status": initial_status.value,
                              "job_group": job.job_group}))
        else:
            io.log("success", f"Created {initial_status.value} job {job.id}")
        return EXIT_SUCCESS
    finally:
        store.close()


def _cmd_job_show(args) -> int:
    """Show staged/pending jobs or detail for a single job."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)
    project = _find_project_or_exit(args)

    # Server delegation for JSON
    if args.output == "json":
        data, code = _try_server(
            args, lambda c: c._request("GET", f"/api/jobs/{args.job_id}") if args.job_id
            else c.jobs(status=None)
        )
        if code is not None:
            print(json.dumps(data, indent=2, default=str))
            return code

    store = SQLiteStore(str(project.db_path))
    try:
        if args.job_id:
            # Single job detail
            try:
                job = store.load_job(args.job_id)
            except KeyError:
                io.log("error", f"Job not found: {args.job_id}")
                return EXIT_JOB_FAILED
            deps = job.depends_on
            dep_details = []
            for dep_id in deps:
                try:
                    dep_job = store.load_job(dep_id)
                    dep_details.append(f"  {dep_id} ({dep_job.status.value})")
                except KeyError:
                    dep_details.append(f"  {dep_id} (not found)")

            if args.output == "json":
                print(json.dumps({
                    "id": job.id, "name": job.name, "status": job.status.value,
                    "objective": job.objective, "job_group": job.job_group,
                    "depends_on": deps, "inputs": job.inputs,
                    "steps": len(job.workflow.steps),
                    "created_at": str(job.created_at),
                }, indent=2, default=str))
            else:
                info = f"Job: {job.id}"
                if job.name:
                    info += f"\nName: {job.name}"
                info += f"\nStatus: {job.status.value}"
                if job.job_group:
                    info += f"\nGroup: {job.job_group}"
                info += f"\nObjective: {job.objective}"
                info += f"\nSteps: {len(job.workflow.steps)}"
                if deps:
                    info += f"\nDepends on:\n" + "\n".join(dep_details)
                if job.inputs:
                    info += f"\nInputs: {json.dumps(job.inputs, default=str)}"
                io.note(info, title="Job Details")
            return EXIT_SUCCESS
        else:
            # Listing mode
            statuses = [JobStatus.AWAITING_APPROVAL, JobStatus.STAGED, JobStatus.PENDING]
            if args.all:
                statuses.extend([JobStatus.RUNNING, JobStatus.PAUSED])

            all_jobs = []
            for status in statuses:
                all_jobs.extend(store.all_jobs(status=status, top_level_only=True))

            if getattr(args, "group", None):
                all_jobs = [j for j in all_jobs if j.job_group == args.group]

            if args.output == "json":
                print(json.dumps([_job_summary(j) for j in all_jobs], indent=2, default=str))
                return EXIT_SUCCESS

            if not all_jobs:
                io.log("info", "No staged/pending jobs found.")
                return EXIT_SUCCESS

            rows = []
            for j in all_jobs:
                name = (j.name or "")[:30]
                group = (j.job_group or "")[:15]
                created = _relative_time(j.created_at) if j.created_at else ""
                rows.append([j.id, name, j.status.value, group, created])
            io.table(["ID", "NAME", "STATUS", "GROUP", "CREATED"], rows)
            return EXIT_SUCCESS
    finally:
        store.close()


def _cmd_job_run(args) -> int:
    """Transition staged jobs to PENDING."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)

    if not args.job_id and not getattr(args, "group", None):
        io.log("error", "Specify a job ID or --group")
        return EXIT_USAGE_ERROR

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            if args.job_id:
                result = client._request("POST", f"/api/jobs/{args.job_id}/run")
            else:
                result = client._request("POST", "/api/jobs/run-group", {"group": args.group})
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                if args.job_id:
                    io.log("success", f"Job {args.job_id} is now PENDING")
                else:
                    count = result.get("count", 0)
                    io.log("success", f"Transitioned {count} job(s) in group '{args.group}' to PENDING")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        if args.job_id:
            try:
                store.transition_job_to_pending(args.job_id)
            except (KeyError, ValueError) as e:
                io.log("error", str(e))
                return EXIT_JOB_FAILED
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "status": "pending"}))
            else:
                io.log("success", f"Job {args.job_id} is now PENDING")
        else:
            job_ids = store.transition_group_to_pending(args.group)
            # Check for cross-group unmet deps
            cross_group_count = 0
            for jid in job_ids:
                deps = store.get_job_dependencies(jid)
                for dep_id in deps:
                    try:
                        dep_job = store.load_job(dep_id)
                        if dep_job.job_group != args.group and dep_job.status != JobStatus.COMPLETED:
                            cross_group_count += 1
                            break
                    except KeyError:
                        cross_group_count += 1
                        break
            if args.output == "json":
                print(json.dumps({"group": args.group, "count": len(job_ids),
                                  "job_ids": job_ids}))
            else:
                io.log("success", f"Transitioned {len(job_ids)} job(s) in group '{args.group}' to PENDING")
                if cross_group_count:
                    io.log("info", f"{cross_group_count} job(s) have unmet dependencies outside this group")
        return EXIT_SUCCESS
    finally:
        store.close()


def _cmd_job_approve(args) -> int:
    """Approve a job awaiting approval."""
    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client.approve(args.job_id)
            if args.output == "json":
                print(json.dumps(result))
            else:
                io.log("success", f"Job {args.job_id} approved — now PENDING")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    from stepwise.store import SQLiteStore
    store = SQLiteStore(str(project.db_path))
    try:
        store.transition_job_to_approved(args.job_id)
        if args.output == "json":
            print(json.dumps({"status": "approved", "job_id": args.job_id}))
        else:
            io.log("success", f"Job {args.job_id} approved — now PENDING")
        return EXIT_SUCCESS
    except (KeyError, ValueError) as e:
        io.log("error", str(e))
        return EXIT_JOB_FAILED
    finally:
        store.close()


def _cmd_job_dep(args) -> int:
    """Manage dependencies between jobs."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            if getattr(args, "after", None):
                result = client._request("POST", f"/api/jobs/{args.job_id}/deps",
                                         {"depends_on_job_id": args.after})
            elif getattr(args, "rm", None):
                result = client._request("DELETE", f"/api/jobs/{args.job_id}/deps/{args.rm}")
            else:
                result = client._request("GET", f"/api/jobs/{args.job_id}/deps")
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                if getattr(args, "after", None):
                    io.log("success", f"Added dependency: {args.job_id} waits for {args.after}")
                elif getattr(args, "rm", None):
                    io.log("success", f"Removed dependency: {args.job_id} no longer waits for {args.rm}")
                else:
                    deps = result.get("depends_on", [])
                    if deps:
                        io.log("info", f"Dependencies for {args.job_id}: {', '.join(deps)}")
                    else:
                        io.log("info", f"No dependencies for {args.job_id}")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        if getattr(args, "after", None):
            # Add dependency
            try:
                job = store.load_job(args.job_id)
            except KeyError:
                io.log("error", f"Job not found: {args.job_id}")
                return EXIT_JOB_FAILED
            try:
                store.load_job(args.after)
            except KeyError:
                io.log("error", f"Dependency target not found: {args.after}")
                return EXIT_JOB_FAILED
            if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
                io.log("error", f"Can only add deps to STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
                return EXIT_USAGE_ERROR
            if store.would_create_cycle(args.job_id, args.after):
                io.log("error", "Cannot add dependency: would create a cycle")
                return EXIT_USAGE_ERROR
            store.add_job_dependency(args.job_id, args.after)
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "depends_on": args.after, "action": "added"}))
            else:
                io.log("success", f"Added dependency: {args.job_id} waits for {args.after}")

        elif getattr(args, "rm", None):
            # Remove dependency
            try:
                job = store.load_job(args.job_id)
            except KeyError:
                io.log("error", f"Job not found: {args.job_id}")
                return EXIT_JOB_FAILED
            if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
                io.log("error", f"Can only remove deps from STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
                return EXIT_USAGE_ERROR
            store.remove_job_dependency(args.job_id, args.rm)
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "depends_on": args.rm, "action": "removed"}))
            else:
                io.log("success", f"Removed dependency: {args.job_id} no longer waits for {args.rm}")

        else:
            # List dependencies
            try:
                job = store.load_job(args.job_id)
            except KeyError:
                io.log("error", f"Job not found: {args.job_id}")
                return EXIT_JOB_FAILED
            deps = job.depends_on
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "depends_on": deps}))
            else:
                if deps:
                    io.log("info", f"Dependencies for {args.job_id}: {', '.join(deps)}")
                else:
                    io.log("info", f"No dependencies for {args.job_id}")

        return EXIT_SUCCESS
    finally:
        store.close()


def _cmd_job_cancel(args) -> int:
    """Cancel a staged/pending/running job."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore
    from stepwise.events import JOB_CANCELLED

    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client.cancel(args.job_id)
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                io.log("success", f"Cancelled {args.job_id}")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            io.log("error", f"Job not found: {args.job_id}")
            return EXIT_JOB_FAILED

        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            io.log("error", f"Job already {job.status.value}")
            return EXIT_USAGE_ERROR

        if job.status in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
            # Direct cancel for staged jobs
            from stepwise.models import _now
            job.status = JobStatus.CANCELLED
            job.updated_at = _now()
            store.save_job(job)
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "status": "cancelled"}))
            else:
                io.log("success", f"Cancelled {args.job_id}")
        else:
            # Delegate to engine for pending/running jobs
            from stepwise.engine import Engine
            from stepwise.registry_factory import create_default_registry
            registry = create_default_registry()
            engine = Engine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)
            engine.cancel_job(args.job_id)
            if args.output == "json":
                print(json.dumps({"job_id": args.job_id, "status": "cancelled"}))
            else:
                io.log("success", f"Cancelled {args.job_id}")

        return EXIT_SUCCESS
    finally:
        store.close()


def _cmd_job_approve(args) -> int:
    """Approve a job awaiting approval."""
    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client.approve(args.job_id)
            if args.output == "json":
                print(json.dumps(result))
            else:
                io.log("success", f"Job {args.job_id} approved — now PENDING")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    from stepwise.store import SQLiteStore
    store = SQLiteStore(str(project.db_path))
    try:
        store.transition_job_to_approved(args.job_id)
        if args.output == "json":
            print(json.dumps({"status": "approved", "job_id": args.job_id}))
        else:
            io.log("success", f"Job {args.job_id} approved — now PENDING")
        return EXIT_SUCCESS
    except (KeyError, ValueError) as e:
        io.log("error", str(e))
        return EXIT_JOB_FAILED
    finally:
        store.close()


def _cmd_job_rm(args) -> int:
    """Remove a staged job."""
    from stepwise.models import JobStatus
    from stepwise.store import SQLiteStore

    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client._request("DELETE", f"/api/jobs/{args.job_id}")
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                io.log("success", f"Removed job {args.job_id}")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            io.log("error", f"Job not found: {args.job_id}")
            return EXIT_JOB_FAILED

        if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
            io.log("error", f"Can only remove STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
            return EXIT_USAGE_ERROR

        # Check for dependents
        dependents = store.get_job_dependents(args.job_id)
        if dependents:
            io.log("error",
                   f"Cannot remove: {len(dependents)} job(s) depend on this job: "
                   f"{', '.join(dependents)}. Remove deps first with 'job dep <id> --rm {args.job_id}'")
            return EXIT_USAGE_ERROR

        store.delete_job(args.job_id)
        if args.output == "json":
            print(json.dumps({"job_id": args.job_id, "action": "removed"}))
        else:
            io.log("success", f"Removed job {args.job_id}")
        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_job(args: argparse.Namespace) -> int:
    """Job staging and orchestration commands."""
    action = getattr(args, "job_command", None)
    handlers = {
        "create": _cmd_job_create,
        "show": _cmd_job_show,
        "run": _cmd_job_run,
        "approve": _cmd_job_approve,
        "dep": _cmd_job_dep,
        "cancel": _cmd_job_cancel,
        "rm": _cmd_job_rm,
    }
    handler = handlers.get(action)
    if handler:
        return handler(args)
    # No subcommand — print help
    print("Usage: stepwise job {create|show|run|approve|dep|cancel|rm} ...", file=sys.stderr)
    return EXIT_USAGE_ERROR


def cmd_cache(args: argparse.Namespace) -> int:
    """Manage step result cache."""
    from stepwise.cache import StepResultCache

    project = _find_project_or_exit(args)
    cache_path = str(project.dot_dir / "cache" / "results.db")
    action = getattr(args, "cache_action", None)

    if action == "clear":
        cache = StepResultCache(cache_path)
        try:
            count = cache.clear(
                flow_name=getattr(args, "flow", None),
                step_name=getattr(args, "step", None),
            )
            print(f"Cleared {count} cache entries.")
        finally:
            cache.close()
        return EXIT_SUCCESS

    elif action == "stats":
        import os
        if not os.path.exists(cache_path):
            print("No cache database found.")
            return EXIT_SUCCESS
        cache = StepResultCache(cache_path)
        try:
            s = cache.stats()
            print(f"Total entries: {s['total_entries']}")
            print(f"Total hits:    {s['total_hits']}")
            if s['size_bytes'] > 0:
                size_kb = s['size_bytes'] / 1024
                print(f"Size on disk:  {size_kb:.1f} KB")
            if s['by_flow']:
                print("\nBy flow:")
                for flow, info in s['by_flow'].items():
                    print(f"  {flow}: {info['entries']} entries, {info['hits']} hits")
            if s['by_step']:
                print("\nBy step:")
                for step, info in s['by_step'].items():
                    print(f"  {step}: {info['entries']} entries, {info['hits']} hits")
        finally:
            cache.close()
        return EXIT_SUCCESS

    elif action == "debug":
        from stepwise.cache import compute_cache_key
        from stepwise.runner import parse_inputs
        from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

        flow_path = Path(args.flow)
        if not flow_path.exists():
            print(f"Error: File not found: {flow_path}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        try:
            workflow = load_workflow_yaml(str(flow_path))
        except YAMLLoadError as e:
            print(f"Error: {'; '.join(e.errors)}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        step_name = args.step
        if step_name not in workflow.steps:
            print(f"Error: Step '{step_name}' not found in flow", file=sys.stderr)
            return EXIT_USAGE_ERROR

        step_def = workflow.steps[step_name]
        if step_def.cache is None:
            print(f"Step '{step_name}' has no cache config.", file=sys.stderr)
            return EXIT_USAGE_ERROR

        # Parse inputs
        try:
            inputs = parse_inputs(getattr(args, "inputs", None))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        # Resolve job inputs for the step
        resolved_inputs = {}
        for binding in step_def.inputs:
            if binding.source_step == "$job":
                resolved_inputs[binding.local_name] = inputs.get(binding.source_field, "")

        exec_ref = step_def.executor
        from stepwise.engine import _interpolate_config
        interpolated = _interpolate_config(exec_ref.config, resolved_inputs)
        if interpolated != exec_ref.config:
            from stepwise.models import ExecutorRef
            exec_ref = ExecutorRef(
                type=exec_ref.type, config=interpolated,
                decorators=exec_ref.decorators,
            )

        try:
            from importlib.metadata import version
            engine_version = version("stepwise-run")
        except Exception:
            engine_version = "0.0.0"

        key = compute_cache_key(
            resolved_inputs, exec_ref, engine_version,
            step_def.cache.key_extra,
        )

        print(f"Cache key: {key}")
        print(f"Step:      {step_name}")
        print(f"Executor:  {step_def.executor.type}")
        print(f"Version:   {engine_version}")
        if step_def.cache.key_extra:
            print(f"Key extra: {step_def.cache.key_extra}")
        if step_def.cache.ttl:
            print(f"TTL:       {step_def.cache.ttl}s")

        # Check if key exists in cache
        import os
        if os.path.exists(cache_path):
            cache = StepResultCache(cache_path)
            try:
                hit = cache.get(key)
                if hit:
                    print(f"Status:    HIT (cached result available)")
                else:
                    print(f"Status:    MISS")
            finally:
                cache.close()
        else:
            print(f"Status:    MISS (no cache database)")

        return EXIT_SUCCESS

    else:
        print("Usage: stepwise cache {clear|stats|debug}", file=sys.stderr)
        return EXIT_USAGE_ERROR


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


def _hoist_global_flags(argv: list[str]) -> list[str]:
    """Move global flags (--server URL, --standalone) from after the subcommand to before it.

    argparse only recognizes top-level flags before the subcommand.
    Users naturally write 'stepwise jobs --server URL', so we relocate
    these flags to the front so argparse can parse them.
    """
    # Flags that take a value argument
    VALUE_FLAGS = {"--server", "--project-dir"}
    # Flags that are boolean (no value)
    BOOL_FLAGS = {"--standalone"}

    hoisted: list[str] = []
    rest: list[str] = []
    i = 0
    found_subcommand = False
    while i < len(argv):
        arg = argv[i]
        if not found_subcommand and not arg.startswith("-"):
            found_subcommand = True
        if found_subcommand and arg in VALUE_FLAGS:
            if i + 1 < len(argv):
                hoisted.extend([arg, argv[i + 1]])
                i += 2
                continue
        elif found_subcommand and arg in BOOL_FLAGS:
            hoisted.append(arg)
            i += 1
            continue
        rest.append(arg)
        i += 1
    # Insert hoisted flags before the first positional (subcommand)
    if hoisted:
        # Find insertion point: before the first non-flag token in rest
        insert_at = 0
        for j, tok in enumerate(rest):
            if not tok.startswith("-"):
                insert_at = j
                break
            # skip value for flags that take one
            if tok in VALUE_FLAGS:
                insert_at = j + 2
        return rest[:insert_at] + hoisted + rest[insert_at:]
    return rest


def main(argv: list[str] | None = None) -> int:
    # Intercept "help" before argparse (conflicts with built-in -h/--help)
    raw = argv if argv is not None else sys.argv[1:]
    # Find the first positional arg (skip --flags)
    positionals = [a for a in raw if not a.startswith("-")]
    if positionals and positionals[0] == "help":
        # Build a minimal namespace with the fields cmd_help needs
        question_words = raw[raw.index("help") + 1:]
        args = argparse.Namespace(
            question=question_words,
            project_dir=None,
        )
        # Check for --project-dir before "help"
        for i, a in enumerate(raw):
            if a == "--project-dir" and i + 1 < len(raw):
                args.project_dir = raw[i + 1]
        return cmd_help(args)

    # Hoist global flags that may appear after the subcommand
    raw = _hoist_global_flags(raw)

    parser = build_parser()
    args = parser.parse_args(raw)

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
        "test-fixture": cmd_test_fixture,
        "preflight": cmd_preflight,
        "diagram": cmd_diagram,
        "templates": cmd_templates,
        "flows": cmd_flows,
        "config": cmd_config,
        "check": cmd_check,
        "login": cmd_login,
        "logout": cmd_logout,
        "share": cmd_share,
        "get": cmd_get,
        "search": cmd_search,
        "info": cmd_info,
        "jobs": cmd_jobs,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "archive": cmd_archive,
        "unarchive": cmd_unarchive,
        "rm": cmd_rm,
        "list": cmd_list,
        "wait": cmd_wait,
        "schema": cmd_schema,
        "tail": cmd_tail,
        "logs": cmd_logs,
        "output": cmd_output,
        "fulfill": cmd_fulfill,
        "agent-help": cmd_agent_help,
        "docs": cmd_docs,
        "cache": cmd_cache,
        "job": cmd_job,
        "update": cmd_self_update,
        "version": lambda args: (print(f"stepwise {_get_version()}"), EXIT_SUCCESS)[1],
        "welcome": cmd_welcome,
        "uninstall": cmd_uninstall,
        "extensions": cmd_extensions,
        "extension": cmd_extensions,
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
