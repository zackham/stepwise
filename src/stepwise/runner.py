"""Headless flow execution with terminal output and human step stdin interaction.

Used by `stepwise run` (without --watch).
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import asyncio

import httpx

from stepwise.config import StepwiseConfig, load_config
from stepwise.flow_resolution import flow_display_name
from stepwise.engine import AsyncEngine, Engine
from stepwise.io import IOAdapter, LiveFlowHandle, StepNode, HumanInputAborted, create_adapter
from stepwise.models import (
    Job,
    JobStatus,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.project import StepwiseProject
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError


# Exit codes (must match cli.py)
EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_SUSPENDED = 5



def parse_vars(var_list: list[str] | None) -> dict[str, str]:
    """Parse --var KEY=VALUE flags. Splits on first = only."""
    result: dict[str, str] = {}
    if not var_list:
        return result
    for item in var_list:
        if "=" not in item:
            raise ValueError(f"Invalid --var format: '{item}' (expected KEY=VALUE)")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def load_vars_file(path: str) -> dict:
    """Load variables from a YAML or JSON file."""
    import json
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Vars file not found: {path}")

    content = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(content) or {}
    elif p.suffix == ".json":
        return json.loads(content)
    else:
        # Try JSON first, then YAML
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import yaml
            return yaml.safe_load(content) or {}


def load_flow_config(flow_path: Path, workflow) -> dict:
    """Load config values from defaults, env vars, and co-located config.local.yaml.

    Resolution order (lowest to highest priority):
      1. Config var defaults from YAML
      2. STEPWISE_VAR_{NAME} environment variables (for sensitive vars)
      3. config.local.yaml values

    Returns a dict suitable as the lowest-priority layer in the inputs merge chain.
    """
    import os
    import yaml

    # Extract defaults from config_vars
    defaults = {v.name: v.default for v in workflow.config_vars if v.default is not None}

    # Resolve env vars for sensitive config vars (STEPWISE_VAR_{NAME})
    env_values: dict = {}
    for v in workflow.config_vars:
        env_key = f"STEPWISE_VAR_{v.name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            env_values[v.name] = env_val

    # Determine config file path
    if flow_path.name == "FLOW.yaml":
        config_file = flow_path.parent / "config.local.yaml"
    else:
        # Single-file flow: my-flow.flow.yaml → my-flow.config.local.yaml
        stem = flow_path.stem
        if stem.endswith(".flow"):
            stem = stem[:-5]
        config_file = flow_path.parent / f"{stem}.config.local.yaml"

    # Load local config values
    local_values: dict = {}
    if config_file.is_file():
        import logging
        logging.getLogger("stepwise").info(
            "Loaded config from %s (%d bytes)", config_file, config_file.stat().st_size
        )
        content = config_file.read_text()
        loaded = yaml.safe_load(content)
        if isinstance(loaded, dict):
            local_values = loaded

    return {**defaults, **env_values, **local_values}


def _ws_url_from_server(server_url: str) -> str:
    """Convert an HTTP server URL to a WebSocket URL."""
    url = server_url.rstrip("/")
    if url.startswith("https://"):
        return url.replace("https://", "wss://", 1) + "/ws"
    return url.replace("http://", "ws://", 1) + "/ws"


async def _fetch_job_state(
    client, job_id: str,
) -> tuple[dict, list[dict]]:
    """Fetch job and runs from the server. Returns (job_dict, runs_list)."""
    job_resp = await client.get(f"/api/jobs/{job_id}")
    job_resp.raise_for_status()
    runs_resp = await client.get(f"/api/jobs/{job_id}/runs")
    runs_resp.raise_for_status()
    return job_resp.json(), runs_resp.json()


def _build_tree_from_dicts(runs: list[dict]) -> list[StepNode]:
    """Build step tree from REST API run dicts (server delegation path)."""
    nodes = []
    for run in runs:
        status_map = {"running": "running", "completed": "completed",
                      "failed": "failed", "suspended": "suspended",
                      "delegated": "running"}
        status = status_map.get(run["status"], "pending")
        duration = None
        if run.get("started_at") and run.get("completed_at"):
            s = datetime.fromisoformat(run["started_at"]).timestamp()
            e = datetime.fromisoformat(run["completed_at"]).timestamp()
            duration = e - s
        nodes.append(StepNode(
            name=run["step_name"],
            status=status,
            duration=duration,
            error=run.get("error"),
        ))
    return nodes


def _delegated_create_and_start(
    server_url: str,
    workflow: WorkflowDefinition,
    objective: str,
    inputs: dict | None,
    workspace: str | None,
    notify_url: str | None = None,
    notify_context: dict | None = None,
) -> tuple[str | None, str | None]:
    """Create and start a job on the server. Returns (job_id, error_message)."""
    base = server_url.rstrip("/")
    try:
        payload = {
            "objective": objective,
            "workflow": workflow.to_dict(),
            "inputs": inputs,
            "workspace_path": workspace,
        }
        if notify_url:
            payload["notify_url"] = notify_url
            payload["notify_context"] = notify_context or {}
        resp = httpx.post(f"{base}/api/jobs", json=payload, timeout=10)
        resp.raise_for_status()
        job_id = resp.json()["id"]
    except Exception as e:
        return None, f"Failed to create job on server: {e}"

    try:
        resp = httpx.post(f"{base}/api/jobs/{job_id}/start", timeout=10)
        resp.raise_for_status()
    except Exception as e:
        return None, f"Failed to start job on server: {e}"

    return job_id, None


def _delegated_run_flow(
    server_url: str,
    workflow: WorkflowDefinition,
    objective: str,
    inputs: dict | None,
    workspace: str | None,
    adapter: IOAdapter,
    output_stream: TextIO | None,
    output_json: bool,
    report: bool,
    report_output: str | None,
    flow_path: Path,
) -> int:
    """Delegate flow execution to a running server. Returns exit code."""
    job_id, err = _delegated_create_and_start(server_url, workflow, objective, inputs, workspace)
    if err:
        _err(err, output_stream)
        return EXIT_JOB_FAILED

    return asyncio.run(_delegated_ws_loop(
        server_url, job_id, adapter,
        output_stream, output_json, report, report_output, flow_path,
        step_names=list(workflow.steps.keys()),
    ))


async def _delegated_ws_loop(
    server_url: str,
    job_id: str,
    adapter: IOAdapter,
    output_stream: TextIO | None,
    output_json: bool,
    report: bool,
    report_output: str | None,
    flow_path: Path,
    step_names: list[str] | None = None,
) -> int:
    """Watch server via WebSocket for job updates, fall back to REST polling."""
    import json as json_mod

    shutdown_requested = False
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: _set_flag())
    loop.add_signal_handler(signal.SIGTERM, lambda: _set_flag())

    def _set_flag():
        nonlocal shutdown_requested
        shutdown_requested = True

    start_time = time.time()
    seen_completed: set[str] = set()  # human steps already prompted
    base_url = server_url.rstrip("/")

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        # Try WebSocket first
        ws_url = _ws_url_from_server(server_url)
        use_ws = True
        try:
            import websockets
            ws_conn = await websockets.connect(ws_url)
        except Exception:
            logging.getLogger("stepwise.runner").warning(
                "WebSocket connection failed, falling back to REST polling"
            )
            use_ws = False
            ws_conn = None

        try:
            with adapter.live_flow("flow", step_names or []) as handle:
                while True:
                    if shutdown_requested:
                        try:
                            await client.post(f"/api/jobs/{job_id}/cancel")
                        except Exception:
                            pass
                        _err("Interrupted — cancelled active runs.", output_stream)
                        return 130

                    # Wait for notification or poll
                    if use_ws and ws_conn:
                        try:
                            msg = await asyncio.wait_for(ws_conn.recv(), timeout=2.0)
                            data = json_mod.loads(msg)
                            # Only fetch if this tick is for our job
                            changed = data.get("changed_jobs", [])
                            if data.get("type") != "tick" or (changed and job_id not in changed):
                                continue
                        except asyncio.TimeoutError:
                            # No message in 2s, do a fetch anyway to stay current
                            pass
                        except Exception:
                            # WS died — degrade to polling
                            logging.getLogger("stepwise.runner").warning(
                                "WebSocket disconnected, falling back to REST polling"
                            )
                            use_ws = False
                            ws_conn = None
                    else:
                        await asyncio.sleep(2.0)

                    # Fetch state and report
                    try:
                        job_data, runs = await _fetch_job_state(client, job_id)
                    except Exception as e:
                        _err(f"Lost connection to server: {e}", output_stream)
                        return EXIT_JOB_FAILED

                    tree = _build_tree_from_dicts(runs)
                    handle.render_tree(tree)

                    # Handle suspended human steps
                    for run in runs:
                        run_id = run["id"]
                        status = run["status"]
                        if status == "suspended" and run_id not in seen_completed:
                            watch = run.get("watch")
                            if watch and watch.get("mode") == "human":
                                handle.pause_for_input()
                                fields = watch.get("fulfillment_outputs", [])
                                prompt = (watch.get("config") or {}).get("prompt", "")
                                schema = watch.get("output_schema")
                                payload = await asyncio.to_thread(
                                    adapter.collect_human_input, prompt, fields, schema,
                                )
                                handle.resume_after_input()
                                try:
                                    await client.post(
                                        f"/api/runs/{run_id}/fulfill",
                                        json={"payload": payload},
                                    )
                                except Exception as e:
                                    _err(f"Failed to fulfill step: {e}", output_stream)
                                seen_completed.add(run_id)

                    # Check terminal state
                    job_status = job_data["status"]
                    if job_status == "completed":
                        handle.flush_all()
                        total_time = time.time() - start_time
                        completed = sum(1 for n in tree if n.status == "completed")
                        adapter.flow_complete(completed, total_time)
                        if output_json:
                            _json_stdout({
                                "status": "completed",
                                "job_id": job_id,
                                "duration_seconds": round(total_time, 1),
                            })
                        return EXIT_SUCCESS
                    elif job_status in ("failed", "cancelled"):
                        error_msg = None
                        failed_step = None
                        for run in runs:
                            if run["status"] == "failed":
                                failed_step = run["step_name"]
                                error_msg = run.get("error")
                                break
                        adapter.flow_failed(error_msg)
                        if output_json:
                            _json_stdout({
                                "status": "failed",
                                "job_id": job_id,
                                "error": error_msg or "Unknown error",
                                "failed_step": failed_step,
                                "duration_seconds": round(time.time() - start_time, 1),
                            })
                        return EXIT_JOB_FAILED
        finally:
            if ws_conn:
                await ws_conn.close()


def run_flow(
    flow_path: Path,
    project: StepwiseProject,
    objective: str | None = None,
    inputs: dict | None = None,
    workspace: str | None = None,
    quiet: bool = False,
    config: StepwiseConfig | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    report: bool = False,
    report_output: str | None = None,
    output_json: bool = False,
    force_local: bool = False,
    adapter: IOAdapter | None = None,
) -> int:
    """Run a flow headlessly. Returns exit code.

    Args:
        flow_path: Path to .flow.yaml file.
        project: Resolved project.
        objective: Job objective (defaults to flow name).
        inputs: Input variables for the flow.
        workspace: Override workspace directory.
        quiet: Suppress output.
        config: Optional config (loads from disk if None).
        input_stream: Override stdin for human steps (testing).
        output_stream: Override output stream (testing).
        force_local: Skip server delegation, always run locally.
    """
    # Configure logging to stderr so we can see engine/executor errors
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    # Suppress noisy HTTP client logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # 1. Load and validate flow
    if not flow_path.exists():
        _err(f"File not found: {flow_path}", output_stream)
        return EXIT_USAGE_ERROR

    try:
        workflow = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        _err(f"Invalid flow: {'; '.join(e.errors)}", output_stream)
        return EXIT_USAGE_ERROR
    except Exception as e:
        _err(f"Error loading flow: {e}", output_stream)
        return EXIT_USAGE_ERROR

    errors = workflow.validate()
    if errors:
        _err(f"Invalid flow: {'; '.join(errors)}", output_stream)
        return EXIT_USAGE_ERROR

    # 1b. Create adapter if not provided
    if adapter is None:
        adapter = create_adapter(
            quiet=quiet,
            force_plain=bool(output_stream),
            output=output_stream,
            input_stream=input_stream,
        )

    # 1c. Check for running server — delegate if available
    flow_name = objective or flow_display_name(flow_path)
    if not force_local:
        from stepwise.server_detect import detect_server
        server_url = detect_server(project.dot_dir)
        if server_url:
            return _delegated_run_flow(
                server_url=server_url,
                workflow=workflow,
                objective=flow_name,
                inputs=inputs,
                workspace=workspace,
                adapter=adapter,
                output_stream=output_stream,
                output_json=output_json,
                report=report,
                report_output=report_output,
                flow_path=flow_path,
            )

    # 2. Create engine with project paths + default registry
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    registry = create_default_registry(config)
    engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir, billing_mode=config.billing, config=config)

    # 3. Create and start job
    import os
    flow_name = objective or flow_display_name(flow_path)
    job = engine.create_job(
        objective=flow_name,
        workflow=workflow,
        inputs=inputs or {},
        workspace_path=workspace,
    )
    job.created_by = f"cli:{os.getpid()}"
    job.runner_pid = os.getpid()
    store.save_job(job)

    # 4. Run async (live_flow display handles the banner)
    try:
        return asyncio.run(_async_run_flow(
            engine, job, store, adapter,
            output_stream, output_json, report, report_output, flow_path,
        ))
    finally:
        store.close()


def _build_step_tree(
    engine: AsyncEngine,
    store: SQLiteStore,
    job_id: str,
) -> list[StepNode]:
    """Build a tree of StepNodes from current job state, recursively walking sub-jobs."""
    nodes: list[StepNode] = []
    # Group runs by step_name, keep latest per step (highest attempt)
    all_runs = engine.get_runs(job_id)
    # Process all runs — multiple attempts of same step become separate nodes
    for run in all_runs:
        status_map = {
            StepRunStatus.RUNNING: "running",
            StepRunStatus.COMPLETED: "completed",
            StepRunStatus.FAILED: "failed",
            StepRunStatus.SUSPENDED: "suspended",
            StepRunStatus.DELEGATED: "running",
        }
        status = status_map.get(run.status, "pending")
        duration = None
        if run.started_at and run.completed_at:
            duration = (run.completed_at - run.started_at).total_seconds()

        es = run.executor_state or {}
        name = run.step_name
        if es.get("for_each"):
            count = es.get("item_count", 0)
            name = f"{run.step_name} ({count} items)"

        node = StepNode(
            name=name,
            status=status,
            duration=duration,
            cost=store.accumulated_cost(run.id) or None,
            outputs=run.result.artifact if run.result else None,
            is_retry=run.attempt > 1,
            error=run.error,
        )

        # Recurse into sub-jobs (sub-flows, for-each)
        if run.sub_job_id:
            node.children = _build_step_tree(engine, store, run.sub_job_id)

        # Recurse into for_each sub-jobs with item labels
        if es.get("for_each"):
            parent_job = engine.get_job(job_id)
            step_def = parent_job.workflow.steps.get(run.step_name)
            item_var = "item"
            if step_def and step_def.for_each:
                item_var = step_def.for_each.item_var

            for sub_id in es.get("sub_job_ids", []):
                try:
                    sub_job = engine.get_job(sub_id)
                    item_val = str(sub_job.inputs.get(item_var, ""))
                except KeyError:
                    item_val = ""
                # Create a wrapper node for the item group
                item_children = _build_step_tree(engine, store, sub_id)
                if item_children:
                    # Set label on first child to act as item header
                    item_children[0].label = item_val or None
                    node.children.extend(item_children)

        nodes.append(node)
    return nodes


async def _handle_human_input(
    engine: AsyncEngine,
    adapter: IOAdapter,
    handle: LiveFlowHandle,
    job_id: str,
    seen_prompted: set[str],
) -> None:
    """Check for and handle suspended human steps, recursively through sub-jobs.

    Raises HumanInputAborted (action="suspend" or "cancel") if the user
    chooses to leave the step suspended or cancel the job via Ctrl+C menu.
    """
    all_runs = engine.get_runs(job_id)
    for run in all_runs:
        if run.status == StepRunStatus.SUSPENDED and run.watch:
            if run.watch.mode == "human" and run.id not in seen_prompted:
                seen_prompted.add(run.id)
                handle.pause_for_input()

                prompt = (run.watch.config or {}).get("prompt", "")
                fields = run.watch.fulfillment_outputs
                schema = run.watch.output_schema

                while True:
                    try:
                        payload = await asyncio.to_thread(
                            adapter.collect_human_input,
                            prompt, fields, schema,
                        )
                    except HumanInputAborted as e:
                        if e.action == "retry":
                            continue  # re-prompt
                        handle.resume_after_input()
                        raise  # suspend or cancel — let caller handle

                    try:
                        engine.fulfill_watch(run.id, payload)
                    except ValueError as e:
                        # Missing required fields — show error and re-prompt
                        await asyncio.to_thread(
                            adapter.note, str(e), "Missing fields",
                        )
                        continue

                    break  # fulfilled successfully

                handle.resume_after_input()
                return  # handle one at a time

        # Check sub-jobs
        if run.sub_job_id:
            await _handle_human_input(
                engine, adapter, handle, run.sub_job_id, seen_prompted,
            )
        es = run.executor_state or {}
        if es.get("for_each"):
            for sub_id in es.get("sub_job_ids", []):
                await _handle_human_input(
                    engine, adapter, handle, sub_id, seen_prompted,
                )


async def _async_run_flow(
    engine: AsyncEngine,
    job: Job,
    store: SQLiteStore,
    adapter: IOAdapter,
    output_stream: TextIO | None,
    output_json: bool,
    report: bool,
    report_output: str | None,
    flow_path: Path,
) -> int:
    """Async inner loop: engine runs autonomously, we poll for reporting."""
    engine_task = asyncio.create_task(engine.run())
    shutdown_requested = False

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: _set_flag())
    loop.add_signal_handler(signal.SIGTERM, lambda: _set_flag())

    def _set_flag():
        nonlocal shutdown_requested
        shutdown_requested = True

    start_time = time.time()
    last_heartbeat = 0.0
    seen_prompted: set[str] = set()  # human steps we've already prompted for
    step_names = list(job.workflow.steps.keys())

    try:
        engine.start_job(job.id)

        with adapter.live_flow(job.objective or "flow", step_names) as handle:
            while True:
                # Heartbeat every 10s so server can detect stale CLI jobs
                now = time.time()
                if now - last_heartbeat > 10:
                    store.heartbeat(job.id)
                    last_heartbeat = now

                if shutdown_requested:
                    engine.cancel_job(job.id)
                    _err("Interrupted — cancelled active runs.", output_stream)
                    return 130

                job = engine.get_job(job.id)

                # Build step tree and render
                tree = _build_step_tree(engine, store, job.id)
                handle.render_tree(tree)

                # Handle human input (separate from rendering)
                try:
                    await _handle_human_input(
                        engine, adapter, handle, job.id, seen_prompted,
                    )
                except HumanInputAborted as e:
                    if e.action == "cancel":
                        engine.cancel_job(job.id)
                        _err("Cancelled.", output_stream)
                        return 130
                    else:  # suspend
                        _err("Left suspended — resume via web UI or re-run.", output_stream)
                        return EXIT_SUSPENDED

                # Check job terminal state
                if job.status == JobStatus.COMPLETED:
                    tree = _build_step_tree(engine, store, job.id)
                    handle.render_tree(tree)
                    handle.flush_all()
                    total_time = time.time() - start_time
                    completed = sum(1 for n in tree if n.status == "completed")
                    adapter.flow_complete(completed, total_time)
                    if output_json:
                        cost = engine.job_cost(job.id)
                        _json_stdout({
                            "status": "completed",
                            "job_id": job.id,
                            "outputs": engine.terminal_outputs(job.id),
                            "cost_usd": round(cost, 4) if cost else 0,
                            "duration_seconds": round(total_time, 1),
                        })
                    if report:
                        _generate_report(job, store, flow_path, report_output, output_stream)
                    return EXIT_SUCCESS
                elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    error_msg = None
                    for run in engine.get_runs(job.id):
                        if run.status == StepRunStatus.FAILED:
                            error_msg = run.error
                            break
                    adapter.flow_failed(error_msg)
                    if output_json:
                        cost = engine.job_cost(job.id)
                        failed_step = None
                        for run in engine.get_runs(job.id):
                            if run.status == StepRunStatus.FAILED:
                                failed_step = run.step_name
                                break
                        _json_stdout({
                            "status": "failed",
                            "job_id": job.id,
                            "error": error_msg or "Unknown error",
                            "failed_step": failed_step,
                            "completed_outputs": engine.completed_outputs(job.id),
                            "cost_usd": round(cost, 4) if cost else 0,
                            "duration_seconds": round(time.time() - start_time, 1),
                        })
                    if report:
                        _generate_report(job, store, flow_path, report_output, output_stream)
                    return EXIT_JOB_FAILED

                await asyncio.sleep(0.1)

    finally:
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass


def _generate_report(
    job: Job, store: SQLiteStore, flow_path: Path,
    report_output: str | None, output_stream: TextIO | None,
) -> None:
    """Generate HTML report after flow completion."""
    from stepwise.report import generate_report, save_report, default_report_path

    try:
        html = generate_report(job, store, flow_path)
        if report_output:
            out_path = Path(report_output)
        else:
            out_path = default_report_path(flow_path)
        save_report(html, out_path)
        out = output_stream or sys.stderr
        out.write(f"\n📄 Report: {out_path}\n")
        out.flush()
    except Exception as e:
        out = output_stream or sys.stderr
        out.write(f"\nWarning: Failed to generate report: {e}\n")
        out.flush()


def run_wait(
    flow_path: Path,
    project: StepwiseProject,
    objective: str | None = None,
    inputs: dict | None = None,
    workspace: str | None = None,
    timeout: int | None = None,
    config: StepwiseConfig | None = None,
    force_local: bool = False,
) -> int:
    """Run a flow in blocking mode with JSON output on stdout.

    All logging goes to stderr. Stdout contains ONLY the JSON payload.
    Returns exit codes: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled, 5=suspended.
    """
    import json as json_mod
    import logging

    logging.basicConfig(
        level=logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )

    # Load and validate flow
    if not flow_path.exists():
        _json_error(2, f"File not found: {flow_path}. Check the path and try again.")
        return EXIT_USAGE_ERROR

    try:
        workflow = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        _json_error(2, f"Invalid flow YAML: {'; '.join(e.errors)}")
        return EXIT_USAGE_ERROR
    except Exception as e:
        _json_error(2, f"Error loading flow: {e}")
        return EXIT_USAGE_ERROR

    errors = workflow.validate()
    if errors:
        _json_error(2, f"Invalid flow: {'; '.join(errors)}")
        return EXIT_USAGE_ERROR

    # Validate required inputs
    required_inputs = set()
    for step in workflow.steps.values():
        for binding in step.inputs:
            if binding.source_step == "$job":
                required_inputs.add(binding.source_field)

    provided = set((inputs or {}).keys())
    missing = required_inputs - provided
    if missing:
        # Enhance with config var descriptions when available
        config_map = {v.name: v for v in workflow.config_vars}
        missing_parts = []
        usage_parts = []
        for m in sorted(missing):
            cv = config_map.get(m)
            if cv and cv.description:
                missing_parts.append(f"{m} ({cv.description})")
            else:
                missing_parts.append(m)
            if cv and cv.sensitive:
                usage_parts.append(f"STEPWISE_VAR_{m.upper()}=...")
            else:
                usage_parts.append(f'--var {m}="..."')
        missing_list = ", ".join(missing_parts)
        usage = " ".join(usage_parts)
        _json_error(
            2,
            f"Missing required input(s): {missing_list}. "
            f"Usage: stepwise run {flow_path} --wait --output json {usage}",
        )
        return EXIT_USAGE_ERROR

    # Check for running server — delegate if available
    if not force_local:
        from stepwise.server_detect import detect_server
        server_url = detect_server(project.dot_dir)
        if server_url:
            return _delegated_run_wait(
                server_url=server_url,
                workflow=workflow,
                objective=objective or flow_display_name(flow_path),
                inputs=inputs,
                workspace=workspace,
                timeout=timeout,
            )

    # Create engine
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    registry = create_default_registry(config)
    engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir, billing_mode=config.billing, config=config)

    # Create and start job
    flow_name = objective or flow_display_name(flow_path)
    job = engine.create_job(
        objective=flow_name,
        workflow=workflow,
        inputs=inputs or {},
        workspace_path=workspace,
    )

    try:
        return asyncio.run(_async_wait_for_job(engine, store, job.id, timeout=timeout))
    finally:
        store.close()


def _delegated_run_wait(
    server_url: str,
    workflow: WorkflowDefinition,
    objective: str,
    inputs: dict | None,
    workspace: str | None,
    timeout: int | None,
) -> int:
    """Delegate --wait mode to a running server. Returns exit code."""
    job_id, err = _delegated_create_and_start(server_url, workflow, objective, inputs, workspace)
    if err:
        _json_stdout({"status": "error", "exit_code": EXIT_JOB_FAILED, "error": err})
        return EXIT_JOB_FAILED

    return asyncio.run(_delegated_wait_ws_loop(server_url, job_id, timeout))


async def _delegated_wait_ws_loop(
    server_url: str,
    job_id: str,
    timeout: int | None,
) -> int:
    """WebSocket-driven wait loop for delegated --wait mode."""
    import json as json_mod

    shutdown_requested = False
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: _set_flag())
    loop.add_signal_handler(signal.SIGTERM, lambda: _set_flag())

    def _set_flag():
        nonlocal shutdown_requested
        shutdown_requested = True

    start_time = time.time()
    base_url = server_url.rstrip("/")

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        # Try WebSocket
        ws_url = _ws_url_from_server(server_url)
        use_ws = True
        try:
            import websockets
            ws_conn = await websockets.connect(ws_url)
        except Exception:
            logging.getLogger("stepwise.runner").warning(
                "WebSocket connection failed, falling back to REST polling"
            )
            use_ws = False
            ws_conn = None

        try:
            while True:
                if shutdown_requested:
                    try:
                        await client.post(f"/api/jobs/{job_id}/cancel")
                    except Exception:
                        pass
                    _json_stdout({
                        "status": "cancelled",
                        "job_id": job_id,
                        "duration_seconds": round(time.time() - start_time, 1),
                    })
                    return 4  # EXIT_CANCELLED

                # Check timeout
                if timeout and (time.time() - start_time) > timeout:
                    _json_stdout({
                        "status": "timeout",
                        "job_id": job_id,
                        "timeout_seconds": timeout,
                        "duration_seconds": round(time.time() - start_time, 1),
                    })
                    return 3  # EXIT_TIMEOUT

                # Wait for WS notification or poll
                if use_ws and ws_conn:
                    try:
                        msg = await asyncio.wait_for(ws_conn.recv(), timeout=2.0)
                        data = json_mod.loads(msg)
                        changed = data.get("changed_jobs", [])
                        if data.get("type") != "tick" or (changed and job_id not in changed):
                            continue
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        use_ws = False
                        ws_conn = None
                else:
                    await asyncio.sleep(2.0)

                # Fetch state
                try:
                    job_data, runs = await _fetch_job_state(client, job_id)
                except Exception as e:
                    _json_stdout({
                        "status": "error",
                        "exit_code": EXIT_JOB_FAILED,
                        "error": f"Lost connection to server: {e}",
                    })
                    return EXIT_JOB_FAILED

                job_status = job_data["status"]
                duration = round(time.time() - start_time, 1)

                if job_status == "completed":
                    # Fetch outputs and cost
                    try:
                        out_resp = await client.get(f"/api/jobs/{job_id}/output")
                        outputs = out_resp.json()
                    except Exception:
                        outputs = {}
                    try:
                        cost_resp = await client.get(f"/api/jobs/{job_id}/cost")
                        cost_usd = cost_resp.json().get("cost_usd", 0)
                    except Exception:
                        cost_usd = 0

                    _json_stdout({
                        "status": "completed",
                        "job_id": job_id,
                        "outputs": outputs,
                        "cost_usd": cost_usd,
                        "duration_seconds": duration,
                    })
                    return EXIT_SUCCESS

                elif job_status in ("failed", "cancelled"):
                    failed_step = None
                    error_msg = None
                    for run in runs:
                        if run["status"] == "failed":
                            failed_step = run["step_name"]
                            error_msg = run.get("error")
                            break
                    try:
                        cost_resp = await client.get(f"/api/jobs/{job_id}/cost")
                        cost_usd = cost_resp.json().get("cost_usd", 0)
                    except Exception:
                        cost_usd = 0

                    _json_stdout({
                        "status": "failed",
                        "job_id": job_id,
                        "error": error_msg or "Unknown error",
                        "failed_step": failed_step,
                        "cost_usd": cost_usd,
                        "duration_seconds": duration,
                    })
                    return EXIT_JOB_FAILED

                # Check for suspension
                elif _is_blocked_by_suspension_from_runs(runs):
                    try:
                        sus_resp = await client.get(f"/api/jobs/{job_id}/suspended")
                        suspended_details = sus_resp.json().get("suspended_steps", [])
                    except Exception:
                        suspended_details = []
                    try:
                        cost_resp = await client.get(f"/api/jobs/{job_id}/cost")
                        cost_usd = cost_resp.json().get("cost_usd", 0)
                    except Exception:
                        cost_usd = 0

                    completed_steps = [
                        r["step_name"] for r in runs if r["status"] == "completed"
                    ]
                    _json_stdout({
                        "status": "suspended",
                        "job_id": job_id,
                        "suspended_steps": suspended_details,
                        "completed_steps": completed_steps,
                        "cost_usd": cost_usd,
                        "duration_seconds": duration,
                    })
                    return EXIT_SUSPENDED
        finally:
            if ws_conn:
                await ws_conn.close()


async def _async_wait_for_job(
    engine: AsyncEngine,
    store: SQLiteStore,
    job_id: str,
    timeout: int | None = None,
) -> int:
    """Async inner loop for wait_for_job: engine runs autonomously."""
    engine_task = asyncio.create_task(engine.run())
    shutdown_requested = False

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: _set_flag())
    loop.add_signal_handler(signal.SIGTERM, lambda: _set_flag())

    def _set_flag():
        nonlocal shutdown_requested
        shutdown_requested = True

    engine.start_job(job_id)
    start_time = time.time()

    try:
        while True:
            if shutdown_requested:
                engine.cancel_job(job_id)
                result = {
                    "status": "cancelled",
                    "job_id": job_id,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "duration_seconds": round(time.time() - start_time, 1),
                }
                _json_stdout(result)
                return 4  # EXIT_CANCELLED

            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                job = engine.get_job(job_id)
                suspended = engine.suspended_step_details(job_id)
                result = {
                    "status": "timeout",
                    "job_id": job_id,
                    "timeout_seconds": timeout,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "duration_seconds": round(time.time() - start_time, 1),
                }
                if suspended:
                    result["suspended_at_step"] = suspended[0]["step"]
                    result["resume_hint"] = (
                        f"Job is still running. Resume with: "
                        f"stepwise fulfill {suspended[0]['run_id']} '{{...}}'"
                    )
                _json_stdout(result)
                return 3  # EXIT_TIMEOUT

            job = engine.get_job(job_id)

            # Check terminal states
            if job.status == JobStatus.COMPLETED:
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                result = {
                    "status": "completed",
                    "job_id": job_id,
                    "outputs": engine.terminal_outputs(job_id),
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_SUCCESS

            elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                failed_step = None
                error_msg = None
                for run in engine.get_runs(job_id):
                    if run.status == StepRunStatus.FAILED:
                        failed_step = run.step_name
                        error_msg = run.error
                        break

                result = {
                    "status": "failed",
                    "job_id": job_id,
                    "error": error_msg or "Unknown error",
                    "failed_step": failed_step,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_JOB_FAILED

            # Check for suspension: all progress blocked by suspended steps
            if _is_blocked_by_suspension(engine, job_id):
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                suspended_details = engine.suspended_step_details(job_id)
                for detail in suspended_details:
                    run = store.load_run(detail["run_id"])
                    detail["inputs"] = run.inputs or {}
                    detail["suspended_at"] = run.started_at.isoformat() if run.started_at else None

                completed_steps = [
                    r.step_name for r in engine.get_runs(job_id)
                    if r.status == StepRunStatus.COMPLETED
                ]
                result = {
                    "status": "suspended",
                    "job_id": job_id,
                    "suspended_steps": suspended_details,
                    "completed_steps": completed_steps,
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_SUSPENDED

            await asyncio.sleep(0.1)

    finally:
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass


def wait_for_job(
    engine: Engine,
    store: SQLiteStore,
    job_id: str,
    timeout: int | None = None,
) -> int:
    """Block until a job reaches terminal state or suspension (legacy sync API).

    Returns exit code: 0=completed, 1=failed, 3=timeout, 4=cancelled, 5=suspended.
    Outputs JSON to stdout.
    """
    # Signal handling
    shutdown_requested = False
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _shutdown_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    start_time = time.time()

    try:
        while True:
            if shutdown_requested:
                engine.cancel_job(job_id)
                result = {
                    "status": "cancelled",
                    "job_id": job_id,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "duration_seconds": round(time.time() - start_time, 1),
                }
                _json_stdout(result)
                return 4  # EXIT_CANCELLED

            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                job = engine.get_job(job_id)
                suspended = engine.suspended_step_details(job_id)
                result = {
                    "status": "timeout",
                    "job_id": job_id,
                    "timeout_seconds": timeout,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "duration_seconds": round(time.time() - start_time, 1),
                }
                if suspended:
                    result["suspended_at_step"] = suspended[0]["step"]
                    result["resume_hint"] = (
                        f"Job is still running. Resume with: "
                        f"stepwise fulfill {suspended[0]['run_id']} '{{...}}'"
                    )
                _json_stdout(result)
                return 3  # EXIT_TIMEOUT

            job = engine.get_job(job_id)

            # Check terminal states
            if job.status == JobStatus.COMPLETED:
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                result = {
                    "status": "completed",
                    "job_id": job_id,
                    "outputs": engine.terminal_outputs(job_id),
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_SUCCESS

            elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                failed_step = None
                error_msg = None
                for run in engine.get_runs(job_id):
                    if run.status == StepRunStatus.FAILED:
                        failed_step = run.step_name
                        error_msg = run.error
                        break

                result = {
                    "status": "failed",
                    "job_id": job_id,
                    "error": error_msg or "Unknown error",
                    "failed_step": failed_step,
                    "completed_outputs": engine.completed_outputs(job_id),
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_JOB_FAILED

            # Check for suspension: all progress blocked by suspended steps
            if _is_blocked_by_suspension(engine, job_id):
                duration = round(time.time() - start_time, 1)
                cost = engine.job_cost(job_id)
                suspended_details = engine.suspended_step_details(job_id)
                for detail in suspended_details:
                    run = store.load_run(detail["run_id"])
                    detail["inputs"] = run.inputs or {}
                    detail["suspended_at"] = run.started_at.isoformat() if run.started_at else None

                completed_steps = [
                    r.step_name for r in engine.get_runs(job_id)
                    if r.status == StepRunStatus.COMPLETED
                ]
                result = {
                    "status": "suspended",
                    "job_id": job_id,
                    "suspended_steps": suspended_details,
                    "completed_steps": completed_steps,
                    "cost_usd": round(cost, 4) if cost else 0,
                    "duration_seconds": duration,
                }
                _json_stdout(result)
                return EXIT_SUSPENDED

            # Tick
            time.sleep(0.1)
            try:
                engine.tick()
            except Exception as e:
                result = {
                    "status": "failed",
                    "job_id": job_id,
                    "error": f"Engine error: {type(e).__name__}: {e}",
                    "completed_outputs": engine.completed_outputs(job_id),
                    "duration_seconds": round(time.time() - start_time, 1),
                }
                _json_stdout(result)
                return EXIT_JOB_FAILED

    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)


def _is_blocked_by_suspension(engine: Engine | AsyncEngine, job_id: str) -> bool:
    """Check if all forward progress is blocked by suspended steps.

    Returns True when there are suspended runs AND no running/delegated runs.
    """
    runs = engine.get_runs(job_id)
    has_suspended = False
    has_active = False
    for run in runs:
        if run.status == StepRunStatus.SUSPENDED:
            has_suspended = True
        elif run.status in (StepRunStatus.RUNNING, StepRunStatus.DELEGATED):
            has_active = True
    return has_suspended and not has_active


def _is_blocked_by_suspension_from_runs(runs: list[dict]) -> bool:
    """Check suspension from REST API run dicts (same logic as _is_blocked_by_suspension)."""
    has_suspended = False
    has_active = False
    for run in runs:
        status = run["status"]
        if status == "suspended":
            has_suspended = True
        elif status in ("running", "delegated"):
            has_active = True
    return has_suspended and not has_active


def run_async(
    flow_path: Path,
    project: StepwiseProject,
    objective: str | None = None,
    inputs: dict | None = None,
    workspace: str | None = None,
    config: StepwiseConfig | None = None,
    force_local: bool = False,
    notify_url: str | None = None,
    notify_context: dict | None = None,
) -> int:
    """Fire-and-forget flow execution. Spawns a detached background process.

    Prints {"job_id": "...", "status": "running"} to stdout, exits immediately.
    """
    import json as json_mod
    import subprocess as sp

    # Validate flow first (fail fast on bad input)
    if not flow_path.exists():
        _json_error(2, f"File not found: {flow_path}. Check the path and try again.")
        return EXIT_USAGE_ERROR

    try:
        workflow = load_workflow_yaml(str(flow_path))
    except YAMLLoadError as e:
        _json_error(2, f"Invalid flow YAML: {'; '.join(e.errors)}")
        return EXIT_USAGE_ERROR

    errors = workflow.validate()
    if errors:
        _json_error(2, f"Invalid flow: {'; '.join(errors)}")
        return EXIT_USAGE_ERROR

    # Check for running server — delegate if available
    if not force_local:
        from stepwise.server_detect import detect_server
        server_url = detect_server(project.dot_dir)
        if server_url:
            return _delegated_run_async(server_url, workflow, objective or flow_display_name(flow_path), inputs, workspace, notify_url, notify_context)

    # Create the job in the store so we have a job_id
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    try:
        registry = create_default_registry(config)
        engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir, billing_mode=config.billing, config=config)

        flow_name = objective or flow_display_name(flow_path)
        job = engine.create_job(
            objective=flow_name,
            workflow=workflow,
            inputs=inputs or {},
            workspace_path=workspace,
        )
        if notify_url:
            job.notify_url = notify_url
            job.notify_context = notify_context or {}
            store.save_job(job)
        job_id = job.id
    finally:
        store.close()

    # Spawn detached background process
    cmd = [
        sys.executable, "-m", "stepwise.runner_bg",
        "--db", str(project.db_path),
        "--jobs-dir", str(project.jobs_dir),
        "--job-id", job_id,
        "--project-dir", str(project.dot_dir),
    ]

    # Detach: new session, no stdin/stdout/stderr inheritance
    sp.Popen(
        cmd,
        stdin=sp.DEVNULL,
        stdout=sp.DEVNULL,
        stderr=sp.DEVNULL,
        start_new_session=True,
    )

    _json_stdout({"job_id": job_id, "status": "running"})
    return EXIT_SUCCESS


def _delegated_run_async(
    server_url: str,
    workflow: WorkflowDefinition,
    objective: str,
    inputs: dict | None,
    workspace: str | None,
    notify_url: str | None = None,
    notify_context: dict | None = None,
) -> int:
    """Delegate --async mode to a running server. No polling, exits immediately."""
    job_id, err = _delegated_create_and_start(server_url, workflow, objective, inputs, workspace, notify_url, notify_context)
    if err:
        _json_stdout({"status": "error", "exit_code": EXIT_JOB_FAILED, "error": err})
        return EXIT_JOB_FAILED

    _json_stdout({"job_id": job_id, "status": "running"})
    return EXIT_SUCCESS


def _json_stdout(data: dict) -> None:
    """Print JSON to stdout (machine-readable output)."""
    import json as json_mod
    sys.stdout.write(json_mod.dumps(data, default=str) + "\n")
    sys.stdout.flush()


def _json_error(exit_code: int, message: str) -> None:
    """Print structured error JSON to stdout."""
    import json as json_mod
    sys.stdout.write(json_mod.dumps({
        "status": "error",
        "exit_code": exit_code,
        "error": message,
    }) + "\n")
    sys.stdout.flush()


def _err(msg: str, stream: TextIO | None = None) -> None:
    out = stream or sys.stderr
    out.write(f"Error: {msg}\n")
    out.flush()
