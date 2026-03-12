"""Stepwise CLI entry point.

Usage:
    stepwise init                          Create .stepwise/ in cwd
    stepwise run <file> [flags]            Run a flow
    stepwise serve [flags]                 Persistent server
    stepwise flow get|share|search         Flow sharing
    stepwise jobs [flags]                  List jobs
    stepwise status <job-id>               Show job detail
    stepwise cancel <job-id>               Cancel running job
    stepwise validate <file>               Validate flow syntax
    stepwise templates                     List templates
    stepwise config get|set [key] [value]  Manage configuration
    stepwise schema <file>                 Generate JSON tool contract
    stepwise output <job-id> [--scope]     Retrieve job outputs
    stepwise fulfill <run-id> '<json>'     Satisfy a suspended human step (or --stdin)
    stepwise agent-help [--update <file>]  Generate agent instructions
    stepwise self-update                   Upgrade to the latest version
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
        return "0.1.0"


def _find_project_or_exit(args: argparse.Namespace) -> StepwiseProject:
    """Find project, respecting --project-dir flag."""
    start = Path(args.project_dir) if args.project_dir else None
    try:
        return find_project(start)
    except ProjectNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_PROJECT_ERROR)


# ── Command handlers ─────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.project_dir) if args.project_dir else None
    try:
        project = init_project(target, force=args.force)
        print(f"Initialized Stepwise project in {project.dot_dir}")
        print(f"  Run 'stepwise run <flow.yaml>' to execute a flow.")
        return EXIT_SUCCESS
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR


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

    print(f"Stepwise server running at http://{host}:{port}")
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
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    flow_path = Path(args.file)
    if not flow_path.exists():
        print(f"Error: File not found: {flow_path}", file=sys.stderr)
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
    from stepwise.runner import run_flow, parse_vars, load_vars_file

    project = _find_project_or_exit(args)
    flow_path = Path(args.file)

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


def cmd_flow(args: argparse.Namespace) -> int:
    action = args.flow_action

    if action == "get":
        url = args.target
        if not url:
            print("Error: flow get requires a URL or name", file=sys.stderr)
            return EXIT_USAGE_ERROR

        # URL download (starts with http)
        if url.startswith("http://") or url.startswith("https://"):
            return _flow_get_url(url)

        # Name-based lookup (stub)
        print(f"Flow registry coming soon. For now, use a direct URL:")
        print(f"  stepwise flow get https://example.com/{url}.flow.yaml")
        return EXIT_SUCCESS

    elif action == "share":
        flow_file = args.file
        if flow_file:
            flow_path = Path(flow_file)
            if not flow_path.exists():
                print(f"Error: File not found: {flow_path}", file=sys.stderr)
                return EXIT_USAGE_ERROR
            print(f"Validating {flow_path}...")
            print(f"Flow sharing coming soon.")
        else:
            print("Flow sharing coming soon.")
        return EXIT_SUCCESS

    elif action == "search":
        query = " ".join(args.query) if args.query else ""
        if query:
            print(f"Searching for '{query}'...")
        print("Flow registry coming soon.")
        return EXIT_SUCCESS

    else:
        print("Error: flow requires 'get', 'share', or 'search' action", file=sys.stderr)
        return EXIT_USAGE_ERROR


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
    from stepwise.schema import generate_schema
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    flow_path = Path(args.file)
    if not flow_path.exists():
        print(f"Error: File not found: {flow_path}", file=sys.stderr)
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

    content = generate_agent_help(project_dir, flows_dir=flows_dir)

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

    upgrade_cmds = {
        "uv": ["uv", "tool", "upgrade", "stepwise-run"],
        "pipx": ["pipx", "upgrade", "stepwise-run"],
        "pip": [sys.executable, "-m", "pip", "install", "--upgrade", "stepwise-run"],
    }

    cmd = upgrade_cmds[method]
    print(f"Upgrading via {method}...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Upgrade failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
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

    if new_version == old_version:
        print(f"Already up to date ({old_version}).")
    else:
        print(f"Updated: {old_version} → {new_version}")

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

    # serve
    p_serve = sub.add_parser("serve", help="Start persistent server with web UI")
    p_serve.add_argument("--port", type=int, help="Port to listen on (default: 8340)")
    p_serve.add_argument("--host", help="Bind address (default: 127.0.0.1)")
    p_serve.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    # run
    p_run = sub.add_parser("run", help="Run a flow")
    p_run.add_argument("file", help="Path to .flow.yaml file")
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
    p_validate.add_argument("file", help="Path to flow file")

    # templates
    sub.add_parser("templates", help="List available templates")

    # config
    p_config = sub.add_parser("config", help="Manage configuration")
    p_config.add_argument("config_action", choices=["get", "set"], help="Action")
    p_config.add_argument("key", nargs="?", help="Config key")
    p_config.add_argument("value", nargs="?", help="Config value (for set)")
    p_config.add_argument("--stdin", action="store_true", help="Read value from stdin")
    p_config.add_argument("--unmask", action="store_true", help="Show full values")

    # flow
    p_flow = sub.add_parser("flow", help="Flow sharing commands")
    flow_sub = p_flow.add_subparsers(dest="flow_action")

    p_flow_get = flow_sub.add_parser("get", help="Download a flow")
    p_flow_get.add_argument("target", help="URL or flow name")

    p_flow_share = flow_sub.add_parser("share", help="Publish a flow")
    p_flow_share.add_argument("file", nargs="?", help="Flow file to share")

    p_flow_search = flow_sub.add_parser("search", help="Search flows")
    p_flow_search.add_argument("query", nargs="*", help="Search query")

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
    p_schema.add_argument("file", help="Path to .flow.yaml file")

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
                              help="Update a file in-place between markers")
    p_agent_help.add_argument("--flows-dir", metavar="DIR",
                              help="Override flow discovery directory")

    # self-update
    sub.add_parser("self-update", help="Upgrade stepwise to the latest version")

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
        "validate": cmd_validate,
        "templates": cmd_templates,
        "config": cmd_config,
        "flow": cmd_flow,
        "jobs": cmd_jobs,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "schema": cmd_schema,
        "output": cmd_output,
        "fulfill": cmd_fulfill,
        "agent-help": cmd_agent_help,
        "self-update": cmd_self_update,
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
