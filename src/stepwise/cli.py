"""Stepwise CLI entry point.

Usage:
    stepwise init                          Create .stepwise/ in cwd
    stepwise run <flow> [flags]            Run a flow
    stepwise new <name>                    Create a new flow
    stepwise serve [flags]                 Persistent server
    stepwise share <flow> [--author]       Publish a flow to the registry
    stepwise get <target>                  Download a flow (URL, @author:name, or slug)
    stepwise search [query] [--tag]        Search the flow registry
    stepwise info <name>                   Show flow details
    stepwise jobs [flags]                  List jobs
    stepwise status <job-id>               Show job detail
    stepwise cancel <job-id>               Cancel running job
    stepwise validate <flow>               Validate flow syntax
    stepwise templates                     List templates
    stepwise config get|set [key] [value]  Manage configuration
    stepwise schema <flow>                 Generate JSON tool contract
    stepwise output <job-id> [--scope]     Retrieve job outputs
    stepwise fulfill <run-id> '<json>'     Satisfy a suspended human step (or --stdin)
    stepwise agent-help [--update <file>]  Generate agent instructions
    stepwise update                   Upgrade to the latest version
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def _find_project_or_exit(args: argparse.Namespace) -> StepwiseProject:
    """Find project, respecting --project-dir flag."""
    start = Path(args.project_dir) if args.project_dir else None
    try:
        return find_project(start)
    except ProjectNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_PROJECT_ERROR)


def _project_dir(args: argparse.Namespace) -> Path | None:
    """Return explicit --project-dir or None (let resolve_flow use cwd)."""
    return Path(args.project_dir) if getattr(args, "project_dir", None) else None


# ── Command handlers ─────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.project_dir) if args.project_dir else None
    root = (target or Path.cwd()).resolve()

    try:
        project = init_project(target, force=args.force)
        print(f"Initialized Stepwise project in {project.dot_dir}")
        print(f"  Run 'stepwise run <flow.yaml>' to execute a flow.")
    except FileExistsError:
        if args.no_skill:
            print(f"Project already initialized in {root / DOT_DIR_NAME}. "
                  f"Use --force to reinitialize.", file=sys.stderr)
            return EXIT_USAGE_ERROR
        # .stepwise/ exists but we can still handle skill installation
        print(f"Project already initialized in {root / DOT_DIR_NAME}.")

    # Agent skill installation
    if args.no_skill:
        return EXIT_SUCCESS

    _handle_skill_install(root, args.skill)
    return EXIT_SUCCESS


def _handle_skill_install(root: Path, skill_target: str | None) -> None:
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
        print(f"  Installed agent skill in {installed}")
        return

    # Report symlink detection
    for group in detection.symlinked_groups:
        names = " and ".join(loc.framework_dir for loc in group)
        print(f"  Note: {names} are symlinked (same directory)")

    # Check if already installed and current
    if detection.any_installed and detection.all_current:
        installed_in = [loc for loc in detection.locations if loc.has_skill]
        dirs = ", ".join(loc.framework_dir for loc in installed_in)
        print(f"  Agent skill already up to date in {dirs}")
        return

    # Check if installed but outdated
    outdated = [loc for loc in detection.locations if loc.has_skill and not loc.skill_current]
    if outdated:
        for loc in outdated:
            print(f"  Agent skill in {loc.framework_dir} is outdated, updating...")
            install_agent_skill(loc.path)
            print(f"  Updated {loc.framework_dir}/skills/stepwise/")
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
        _prompt_create_framework_dir(root)
        return

    if len(candidates) == 1:
        framework_dir, candidate = candidates[0]
        answer = _prompt(
            f"  Install agent skill in {framework_dir}/skills/stepwise/? [Y/n] "
        )
        if answer.lower() not in ("n", "no"):
            installed = install_agent_skill(candidate)
            print(f"  Installed agent skill in {installed}")
        return

    # Multiple candidates
    print("  Agent skill can be installed in:")
    for i, (framework_dir, _) in enumerate(candidates, 1):
        print(f"    [{i}] {framework_dir}/skills/stepwise/")
    print(f"    [a] All of the above")
    print(f"    [s] Skip")

    answer = _prompt("  Install to: ").strip().lower()
    if answer == "s":
        return
    if answer == "a":
        for framework_dir, candidate in candidates:
            installed = install_agent_skill(candidate)
            print(f"  Installed agent skill in {installed}")
        return
    try:
        idx = int(answer) - 1
        if 0 <= idx < len(candidates):
            framework_dir, candidate = candidates[idx]
            installed = install_agent_skill(candidate)
            print(f"  Installed agent skill in {installed}")
        else:
            print("  Skipped agent skill installation.")
    except ValueError:
        print("  Skipped agent skill installation.")


def _prompt_create_framework_dir(root: Path) -> None:
    """No agent framework dirs exist. Ask user what to create."""
    print("  No agent framework directory found (.claude/ or .agents/).")
    print("  Install agent skill for:")
    print("    [1] Claude Code  (.claude/skills/stepwise/)")
    print("    [2] Agents       (.agents/skills/stepwise/)")
    print("    [3] Both")
    print("    [s] Skip")

    answer = _prompt("  Choice: ").strip().lower()
    if answer == "s":
        return

    from stepwise.project import install_agent_skill

    targets = []
    if answer in ("1", "3"):
        targets.append(root / ".claude")
    if answer in ("2", "3"):
        targets.append(root / ".agents")

    for target in targets:
        installed = install_agent_skill(target)
        print(f"  Installed agent skill in {installed}")


def _prompt(message: str) -> str:
    """Read user input, or return empty string if not interactive."""
    try:
        return input(message)
    except (EOFError, KeyboardInterrupt):
        return ""


def cmd_serve(args: argparse.Namespace) -> int:
    project = _find_project_or_exit(args)

    import os
    import uvicorn

    # Set env vars so server.py picks them up in lifespan
    os.environ["STEPWISE_DB"] = str(project.db_path)
    os.environ["STEPWISE_TEMPLATES"] = str(project.templates_dir)
    os.environ["STEPWISE_JOBS_DIR"] = str(project.jobs_dir)

    host = args.host or "127.0.0.1"
    port = args.port or 8340

    if not args.port and not _port_available(host, port):
        port = _find_free_port()
        print(f"Port 8340 in use, using {port}", file=sys.stderr)

    print(f"Stepwise v{_get_version()} — http://{host}:{port}")

    # Non-blocking upgrade check (fail silently)
    try:
        upgrade_msg = _check_for_upgrade()
        if upgrade_msg:
            print(f"  ↑ {upgrade_msg}")
    except Exception:
        pass

    if not args.no_open:
        _open_browser(f"http://{host}:{port}")

    uvicorn.run(
        "stepwise.server:app",
        host=host,
        port=port,
        log_level="warning",
    )
    return EXIT_SUCCESS


def cmd_validate(args: argparse.Namespace) -> int:
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        wf = load_workflow_yaml(str(flow_path))
        errors = wf.validate()
        if errors:
            print(f"✗ {flow_path}:")
            for err in errors:
                print(f"  - {err}")
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
        print(f"✓ {flow_path} ({', '.join(parts)})")
        return EXIT_SUCCESS
    except YAMLLoadError as e:
        print(f"✗ {flow_path}:")
        for err in e.errors:
            print(f"  - {err}")
        return EXIT_JOB_FAILED
    except Exception as e:
        print(f"✗ {flow_path}: {e}", file=sys.stderr)
        return EXIT_JOB_FAILED


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new flow directory with a minimal template."""
    from stepwise.flow_resolution import FLOW_DIR_MARKER, FLOW_NAME_PATTERN

    name = args.name
    if not FLOW_NAME_PATTERN.match(name):
        print(
            f"Error: Invalid flow name: '{name}'. "
            f"Flow names must match [a-zA-Z0-9_-]+",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()
    flows_dir = project_dir / "flows"
    flow_dir = flows_dir / name

    if flow_dir.exists():
        print(f"Error: Directory already exists: {flow_dir}", file=sys.stderr)
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

    print(f"Created flows/{name}/{FLOW_DIR_MARKER}")
    print(f"  Edit: {flow_dir / FLOW_DIR_MARKER}")
    print(f"  Run:  stepwise run {name}")
    return EXIT_SUCCESS


def cmd_templates(args: argparse.Namespace) -> int:
    # Bundled templates
    bundled_dir = get_bundled_templates_dir()
    bundled = []
    if bundled_dir.exists():
        for f in sorted(bundled_dir.iterdir()):
            if f.suffix in (".yaml", ".yml", ".json"):
                bundled.append(f.stem)

    print("BUILT-IN:")
    if bundled:
        for name in bundled:
            print(f"  {name}")
    else:
        print("  (none)")

    # Project templates
    try:
        project = find_project(Path(args.project_dir) if args.project_dir else None)
        user_templates = []
        if project.templates_dir.exists():
            for f in sorted(project.templates_dir.iterdir()):
                if f.suffix in (".yaml", ".yml", ".json"):
                    user_templates.append(f.stem)

        print("\nPROJECT:")
        if user_templates:
            for name in user_templates:
                print(f"  {name}")
        else:
            print("  (none — save templates via the web UI)")
    except ProjectNotFoundError:
        print("\nPROJECT:")
        print("  (no project — run 'stepwise init' first)")

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
        elif args.key == "default_model":
            config.default_model = value
        else:
            print(f"Error: Unknown config key '{args.key}'", file=sys.stderr)
            return EXIT_USAGE_ERROR

        save_config(config)
        print(f"✓ Set {args.key}")
        return EXIT_SUCCESS

    elif action == "get":
        if not args.key:
            print("Error: config get requires a key", file=sys.stderr)
            return EXIT_USAGE_ERROR

        config = load_config()

        if args.key == "openrouter_api_key":
            val = config.openrouter_api_key or ""
        elif args.key == "default_model":
            val = config.default_model or ""
        else:
            print(f"Error: Unknown config key '{args.key}'", file=sys.stderr)
            return EXIT_USAGE_ERROR

        if val and not args.unmask and args.key in ("openrouter_api_key",):
            # Mask all but last 3 chars
            masked = "*" * max(0, len(val) - 3) + val[-3:]
            print(masked)
        else:
            print(val)
        return EXIT_SUCCESS

    else:
        print("Error: config requires 'get' or 'set' action", file=sys.stderr)
        return EXIT_USAGE_ERROR


def cmd_run(args: argparse.Namespace) -> int:
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.runner import run_flow, parse_vars, load_vars_file

    project = _find_project_or_exit(args)

    # Resolve flow name/path early
    try:
        flow_path = resolve_flow(args.flow, _project_dir(args))
    except FlowResolutionError as e:
        if getattr(args, "wait", False) or getattr(args, "async_mode", False):
            from stepwise.runner import _json_error
            _json_error(2, str(e))
            return EXIT_USAGE_ERROR
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # Parse input variables (shared across all modes)
    inputs: dict = {}
    try:
        inputs.update(parse_vars(args.vars))
    except ValueError as e:
        if getattr(args, "wait", False) or getattr(args, "async_mode", False):
            from stepwise.runner import _json_error
            _json_error(2, str(e))
            return EXIT_USAGE_ERROR
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

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
        return run_async(
            flow_path=flow_path,
            project=project,
            objective=args.objective,
            inputs=inputs if inputs else None,
            workspace=args.workspace,
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
        )

    # Everything below uses stderr for errors
    if not flow_path.exists():
        print(f"Error: File not found: {flow_path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    if args.watch:
        return _run_watch(args, project, flow_path, inputs)

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
    )


def _run_watch(
    args: argparse.Namespace,
    project,
    flow_path: Path,
    inputs: dict,
) -> int:
    """--watch mode: ephemeral server with pre-loaded job."""
    import os
    import socket
    import uvicorn
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

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

    # Pick port
    if args.port:
        port = args.port
    else:
        port = _find_free_port()

    host = "127.0.0.1"

    # Set env vars for server lifespan
    os.environ["STEPWISE_DB"] = str(project.db_path)
    os.environ["STEPWISE_TEMPLATES"] = str(project.templates_dir)
    os.environ["STEPWISE_JOBS_DIR"] = str(project.jobs_dir)

    # Stash flow data in env for the lifespan to pick up
    import json
    os.environ["STEPWISE_WATCH_WORKFLOW"] = json.dumps(workflow.to_dict())
    os.environ["STEPWISE_WATCH_OBJECTIVE"] = args.objective or flow_path.stem
    if inputs:
        os.environ["STEPWISE_WATCH_INPUTS"] = json.dumps(inputs)

    print(f"▸ entering flow...")
    print(f"  http://{host}:{port}")
    print()
    print(f"  Press Ctrl+C to stop.")

    if not args.no_open:
        _open_browser(f"http://{host}:{port}")

    uvicorn.run(
        "stepwise.server:app",
        host=host,
        port=port,
        log_level="warning",
    )
    return EXIT_SUCCESS


def _port_available(host: str, port: int) -> bool:
    """Check if a port is available to bind."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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

        if not args.all:
            jobs = jobs[-args.limit:]

        if args.output == "json":
            import json
            print(json.dumps([_job_summary(j) for j in jobs], indent=2, default=str))
            return EXIT_SUCCESS

        # Table format
        if not jobs:
            print("No jobs found.")
            return EXIT_SUCCESS

        print(f"{'ID':<16} {'STATUS':<12} {'OBJECTIVE':<24} {'STEPS':<8} {'CREATED'}")
        for j in jobs:
            runs = store.runs_for_job(j.id)
            completed = sum(1 for r in runs if r.status.value == "completed")
            total = len(j.workflow.steps)
            obj = (j.objective or "")[:23]
            created = _relative_time(j.created_at) if j.created_at else ""
            print(f"{j.id:<16} {j.status.value:<12} {obj:<24} {completed}/{total:<5} {created}")

        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_status(args: argparse.Namespace) -> int:
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
            import json
            data = _job_summary(job)
            data["runs"] = [r.to_dict() for r in runs]
            print(json.dumps(data, indent=2, default=str))
            return EXIT_SUCCESS

        # Table format
        print(f"Job: {job.id}")
        print(f"Status: {job.status.value}")
        print(f"Objective: {job.objective}")
        if job.created_at:
            print(f"Created: {_relative_time(job.created_at)}")
        print()
        print("Steps:")

        # Group runs by step, show latest
        step_runs: dict[str, list] = {}
        for r in runs:
            step_runs.setdefault(r.step_name, []).append(r)

        status_icons = {
            "completed": "✓",
            "failed": "✗",
            "running": "⠋",
            "suspended": "◆",
            "delegated": "↗",
        }

        for step_name in job.workflow.steps:
            step_def = job.workflow.steps[step_name]
            executor_type = step_def.executor.type
            step_r = step_runs.get(step_name, [])
            if step_r:
                latest = step_r[-1]
                icon = status_icons.get(latest.status.value, "○")
                status_str = latest.status.value
                extra = f"  {executor_type}"
                if latest.started_at and latest.completed_at:
                    dur = (latest.completed_at - latest.started_at).total_seconds()
                    cost = store.accumulated_cost(latest.id)
                    extra += f"   ({dur:.1f}s"
                    if cost:
                        extra += f", ${cost:.3f}"
                    extra += ")"
                elif latest.status.value == "suspended":
                    extra += "   waiting for input..."
                print(f"  {icon} {step_name:<16} {status_str:<12}{extra}")
            else:
                print(f"  ○ {step_name:<16} pending")

        return EXIT_SUCCESS
    finally:
        store.close()


def cmd_cancel(args: argparse.Namespace) -> int:
    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.store import SQLiteStore
    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            job = store.load_job(args.job_id)
        except KeyError:
            print(f"Error: Job not found: {args.job_id}", file=sys.stderr)
            return EXIT_JOB_FAILED

        from stepwise.models import JobStatus
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            print(f"Error: Job already {job.status.value}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        registry = create_default_registry()
        engine = Engine(store, registry, jobs_dir=str(project.jobs_dir))
        engine.cancel_job(args.job_id)
        print(f"✓ Cancelled {args.job_id}")
        return EXIT_SUCCESS
    finally:
        store.close()


def _job_summary(job) -> dict:
    """Create a JSON-serializable job summary."""
    return {
        "id": job.id,
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


def cmd_get(args: argparse.Namespace) -> int:
    """Download a flow by URL or registry name."""
    from stepwise.bundle import unpack_bundle
    from stepwise.registry_client import fetch_flow, get_registry_url, RegistryError

    target = args.target
    if not target:
        print("Error: flow get requires a URL or name", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # URL download
    if target.startswith("http://") or target.startswith("https://"):
        return _flow_get_url(target)

    # Registry name lookup — parse @author:name format
    if target.startswith("@"):
        ref_body = target.lstrip("@")
        if ":" in ref_body:
            _author, slug = ref_body.split(":", 1)
        else:
            slug = ref_body
    else:
        slug = target
    force = getattr(args, "force", False)
    try:
        data = fetch_flow(slug)
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    bundle_files = data.get("files")
    steps = data.get("steps", "?")
    author = data.get("author", "unknown")
    downloads = data.get("downloads", 0)

    # Determine install location
    flows_dir = Path("flows")
    target_dir = flows_dir / slug

    if target_dir.exists() and not force:
        print(f"Error: {target_dir} already exists (use --force to overwrite)", file=sys.stderr)
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
    print(f"✓ Downloaded {target_dir}/{file_msg} ({steps} steps, by {author}, {downloads:,} downloads)")
    print(f"  Run: stepwise run {flow_path}")
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
        wf.validate()
    except (YAMLLoadError, ValueError) as e:
        print(f"Error: Validation failed: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    yaml_content = flow_path.read_text()
    author = getattr(args, "author", None)
    do_update = getattr(args, "update", False)

    print(f"Validating {flow_path}... ✓ ({len(wf.steps)} steps)")

    # Collect bundle files if this is a directory flow
    bundle_files: dict[str, str] | None = None
    is_dir_flow = flow_path.name == "FLOW.yaml"
    if is_dir_flow:
        try:
            bundle_files = collect_bundle(flow_path.parent)
        except BundleError as e:
            print(f"Error: {e}", file=sys.stderr)
            return EXIT_USAGE_ERROR

        if bundle_files:
            print(f"Bundling {len(bundle_files)} co-located file(s):")
            for rel_path in sorted(bundle_files):
                size = len(bundle_files[rel_path].encode("utf-8"))
                print(f"  {rel_path} ({size:,} bytes)")
            answer = _prompt("Proceed? [Y/n] ")
            if answer.strip().lower() in ("n", "no"):
                print("Cancelled.")
                return EXIT_SUCCESS

    try:
        if do_update:
            import yaml as yaml_lib
            data = yaml_lib.safe_load(yaml_content)
            name = data.get("name", flow_path.stem.replace(".flow", ""))
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
            result = update_flow(slug, yaml_content)
            print(f"✓ Updated: {result.get('url', slug)}")
        else:
            result = publish_flow(yaml_content, author=author, files=bundle_files)
            slug = result.get("slug", "")
            file_msg = f" + {len(bundle_files)} file(s)" if bundle_files else ""
            print(f"Publishing as \"{result.get('name', '')}\" by {result.get('author', 'anonymous')}...")
            print(f"✓ Published: {result.get('url', '')}{file_msg}")
            print(f"  Get: stepwise get {slug}")
            if result.get("update_token"):
                print(f"  Token saved to ~/.config/stepwise/tokens.json")
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
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
    if not flows:
        print("No flows found.")
        return EXIT_SUCCESS

    if output == "json":
        print(json.dumps(result, indent=2))
        return EXIT_SUCCESS

    # Table output
    print(f"{'NAME':<25} {'AUTHOR':<12} {'STEPS':>5}  {'DOWNLOADS':>9}  TAGS")
    for f in flows:
        tags = ", ".join(f.get("tags", []))
        print(f"{f['slug']:<25} {f.get('author', '?'):<12} {f.get('steps', '?'):>5}  {f.get('downloads', 0):>9,}  {tags}")

    total = result.get("total", len(flows))
    if total > len(flows):
        print(f"\nShowing {len(flows)} of {total} flows")
    return EXIT_SUCCESS


def cmd_info(args: argparse.Namespace) -> int:
    """Show details about a published flow."""
    from stepwise.registry_client import fetch_flow, RegistryError

    slug = args.name
    if not slug:
        print("Error: flow info requires a flow name", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        data = fetch_flow(slug)
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    print(f"Name:        {data.get('name', '?')}")
    print(f"Author:      {data.get('author', '?')}")
    print(f"Version:     {data.get('version', '?')}")
    print(f"Description: {data.get('description', '')}")
    tags = ", ".join(data.get("tags", []))
    print(f"Tags:        {tags or '(none)'}")
    print(f"Downloads:   {data.get('downloads', 0):,}")
    print(f"Published:   {data.get('created_at', '?')}")
    print(f"URL:         {data.get('url', '?')}")

    # Show steps summary
    executors = data.get("executor_types", [])
    steps = data.get("steps", 0)
    loops = data.get("loops", 0)
    print(f"\nSteps:       {steps}")
    if loops:
        print(f"Loops:       {loops}")
    if executors:
        print(f"Executors:   {', '.join(executors)}")

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
    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.models import JobStatus
    from stepwise.registry_factory import create_default_registry
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir))
            job = engine.get_job(args.job_id)
        except KeyError:
            print(json.dumps({"status": "error", "error": f"Job not found: {args.job_id}"}))
            return EXIT_JOB_FAILED

        result: dict = {"status": job.status.value}

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
    project = _find_project_or_exit(args)

    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir))

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
            engine.fulfill_watch(args.run_id, payload)
        except (ValueError, KeyError) as e:
            print(json.dumps({"status": "error", "error": str(e)}))
            return EXIT_USAGE_ERROR

        print(json.dumps({"status": "fulfilled", "run_id": args.run_id}))
        return EXIT_SUCCESS
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

    cmd = upgrade_cmds[method]
    print(f"Upgrading via {method}...")

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

    if new_version == old_version:
        print(f"Already up to date (v{old_version}).")
        return EXIT_SUCCESS

    print(f"\nUpdated: v{old_version} → v{new_version}\n")

    # Show what changed
    changelog = _fetch_changelog_sections(old_version, new_version)
    if changelog:
        print("What's new:")
        print("─" * 60)
        print(changelog)
        print("─" * 60)
    elif old_version != new_version:
        print("Run `stepwise changelog` or see CHANGELOG.md for details.")

    return EXIT_SUCCESS


def _open_browser(url: str) -> None:
    """Open URL in default browser (best-effort, non-blocking)."""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


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

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Create .stepwise/ in current directory")
    p_init.add_argument("--force", action="store_true", help="Reinitialize existing project")
    p_init.add_argument("--no-skill", action="store_true",
                        help="Skip agent skill installation")
    p_init.add_argument("--skill", metavar="DIR",
                        help="Install agent skill to specific directory (e.g., .claude or .agents)")

    # serve
    p_serve = sub.add_parser("serve", help="Start persistent server with web UI")
    p_serve.add_argument("--port", type=int, help="Port to listen on (default: 8340)")
    p_serve.add_argument("--host", help="Bind address (default: 127.0.0.1)")
    p_serve.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

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
    p_run.add_argument("--workspace", help="Override workspace directory")
    p_run.add_argument("--report", action="store_true", help="Generate HTML report after completion")
    p_run.add_argument("--report-output", help="Report output path (default: <flow>-report.html)")
    p_run.add_argument("--no-open", action="store_true", help="Don't auto-open browser (for --watch)")

    # validate
    p_validate = sub.add_parser("validate", help="Validate a flow file")
    p_validate.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # templates
    sub.add_parser("templates", help="List available templates")

    # config
    p_config = sub.add_parser("config", help="Manage configuration")
    p_config.add_argument("config_action", choices=["get", "set"], help="Action")
    p_config.add_argument("key", nargs="?", help="Config key")
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

    # status
    p_status = sub.add_parser("status", help="Show job detail")
    p_status.add_argument("job_id", help="Job ID")
    p_status.add_argument("--output", choices=["table", "json"], default="table")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a running job")
    p_cancel.add_argument("job_id", help="Job ID")

    # schema
    p_schema = sub.add_parser("schema", help="Generate JSON tool contract from a flow file")
    p_schema.add_argument("flow", help="Flow name or path to .flow.yaml file")

    # output
    p_output = sub.add_parser("output", help="Retrieve job outputs")
    p_output.add_argument("job_id", help="Job ID")
    p_output.add_argument("--scope", choices=["default", "full"], default="default",
                          help="Output scope (default: terminal outputs only)")

    # fulfill
    p_fulfill = sub.add_parser("fulfill", help="Satisfy a suspended human step")
    p_fulfill.add_argument("run_id", help="Run ID of the suspended step")
    p_fulfill.add_argument("payload", nargs="?", default=None,
                           help="JSON payload with field values (use --stdin or '-' to read from stdin)")
    p_fulfill.add_argument("--stdin", action="store_true",
                           help="Read JSON payload from stdin")

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"stepwise {_get_version()}")
        return EXIT_SUCCESS

    if not args.command:
        parser.print_help()
        return EXIT_USAGE_ERROR

    handlers = {
        "init": cmd_init,
        "serve": cmd_serve,
        "run": cmd_run,
        "new": cmd_new,
        "validate": cmd_validate,
        "templates": cmd_templates,
        "config": cmd_config,
        "share": cmd_share,
        "get": cmd_get,
        "search": cmd_search,
        "info": cmd_info,
        "jobs": cmd_jobs,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "schema": cmd_schema,
        "output": cmd_output,
        "fulfill": cmd_fulfill,
        "agent-help": cmd_agent_help,
        "update": cmd_self_update,
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
