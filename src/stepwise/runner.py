"""Headless flow execution with terminal output and human step stdin interaction.

Used by `stepwise run` (without --watch).
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import asyncio

from stepwise.config import StepwiseConfig, load_config
from stepwise.engine import AsyncEngine, Engine
from stepwise.models import (
    Job,
    JobStatus,
    StepRun,
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


@dataclass
class TerminalReporter:
    """Prints step status updates to terminal."""

    quiet: bool = False
    _out: TextIO = field(default_factory=lambda: sys.stderr)
    _started_steps: dict[str, float] = field(default_factory=dict)

    def on_flow_start(self, name: str) -> None:
        if not self.quiet:
            self._out.write(f"▸ entering flow...\n\n")
            self._out.flush()

    def on_step_started(self, step_name: str, executor_type: str) -> None:
        self._started_steps[step_name] = time.time()
        if not self.quiet:
            self._out.write(f"  ⠋ {step_name:<16} running...\n")
            self._out.flush()

    def on_step_completed(self, step_name: str, duration: float, cost: float | None) -> None:
        if not self.quiet:
            parts = [f"{duration:.1f}s"]
            if cost is not None:
                parts.append(f"${cost:.3f}")
            self._out.write(f"  ✓ {step_name:<16} completed  ({', '.join(parts)})\n")
            self._out.flush()

    def on_step_failed(self, step_name: str, error: str) -> None:
        if not self.quiet:
            self._out.write(f"  ✗ {step_name:<16} failed     {error}\n")
            self._out.flush()

    def on_step_suspended(self, step_name: str) -> None:
        if not self.quiet:
            self._out.write(f"  ◆ {step_name:<16} needs input\n")
            self._out.flush()

    def on_flow_completed(self, job: Job, total_steps: int, total_time: float) -> None:
        if not self.quiet:
            self._out.write(f"\n✓ Flow completed ({total_steps} steps, {total_time:.1f}s)\n")
            self._out.flush()

    def on_flow_failed(self, job: Job, error: str | None = None) -> None:
        if not self.quiet:
            msg = f"\n✗ Flow failed"
            if error:
                msg += f": {error}"
            self._out.write(msg + "\n")
            self._out.flush()


class StdinHumanHandler:
    """Handles human steps by prompting on stdin."""

    def __init__(self, input_stream: TextIO | None = None, output_stream: TextIO | None = None):
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stderr

    def handle_suspended_step(self, engine: Engine | AsyncEngine, run: StepRun) -> None:
        """Print prompt, collect input per field, call engine.fulfill_watch()."""
        if not run.watch:
            return

        prompt = (run.watch.config or {}).get("prompt", "")
        fields = run.watch.fulfillment_outputs

        if prompt:
            self._output.write(f"\n  {prompt}\n\n")
            self._output.flush()

        payload: dict[str, str] = {}
        if len(fields) == 1:
            # Single field — simpler prompt
            field_name = fields[0]
            self._output.write(f"  {field_name}: ")
            self._output.flush()
            value = self._input.readline().strip()
            payload[field_name] = value
        else:
            # Multi-field
            self._output.write("  Fields:\n")
            self._output.flush()
            for field_name in fields:
                self._output.write(f"    {field_name}: ")
                self._output.flush()
                value = self._input.readline().strip()
                payload[field_name] = value

        engine.fulfill_watch(run.id, payload)


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
    """
    # Configure logging to stderr so we can see engine/executor errors
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )

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

    # 2. Create engine with project paths + default registry
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    registry = create_default_registry(config)
    engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)

    # 3. Create and start job
    flow_name = objective or flow_path.stem
    job = engine.create_job(
        objective=flow_name,
        workflow=workflow,
        inputs=inputs or {},
        workspace_path=workspace,
    )

    reporter = TerminalReporter(quiet=quiet, _out=output_stream or sys.stderr)
    human_handler = StdinHumanHandler(
        input_stream=input_stream,
        output_stream=output_stream or sys.stderr,
    )

    reporter.on_flow_start(flow_name)

    # 4. Run async
    try:
        return asyncio.run(_async_run_flow(
            engine, job, store, reporter, human_handler,
            output_stream, output_json, report, report_output, flow_path,
        ))
    finally:
        store.close()


async def _async_run_flow(
    engine: AsyncEngine,
    job: Job,
    store: SQLiteStore,
    reporter: TerminalReporter,
    human_handler: StdinHumanHandler,
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
    seen_running: set[str] = set()
    seen_completed: set[str] = set()
    steps_completed = 0

    try:
        engine.start_job(job.id)

        while True:
            if shutdown_requested:
                engine.cancel_job(job.id)
                _err("Interrupted — cancelled active runs.", output_stream)
                return 130

            job = engine.get_job(job.id)

            # Report new step transitions
            all_runs = engine.get_runs(job.id)
            for run in all_runs:
                if run.id not in seen_running and run.status in (
                    StepRunStatus.RUNNING, StepRunStatus.SUSPENDED,
                    StepRunStatus.DELEGATED,
                ):
                    seen_running.add(run.id)
                    step_def = job.workflow.steps.get(run.step_name)
                    executor_type = step_def.executor.type if step_def else "unknown"
                    es = run.executor_state or {}
                    if es.get("for_each"):
                        count = es.get("item_count", 0)
                        reporter.on_step_started(
                            f"{run.step_name} ({count} items)", executor_type
                        )
                    else:
                        reporter.on_step_started(run.step_name, executor_type)

                if run.id not in seen_completed:
                    if run.status == StepRunStatus.COMPLETED:
                        seen_completed.add(run.id)
                        duration = 0.0
                        if run.started_at and run.completed_at:
                            duration = (run.completed_at - run.started_at).total_seconds()
                        cost = store.accumulated_cost(run.id) or None
                        reporter.on_step_completed(run.step_name, duration, cost)
                        steps_completed += 1
                    elif run.status == StepRunStatus.FAILED:
                        seen_completed.add(run.id)
                        reporter.on_step_failed(run.step_name, run.error or "unknown error")

                # Handle suspended (human) steps
                if run.status == StepRunStatus.SUSPENDED and run.watch:
                    if run.watch.mode == "human" and run.id not in seen_completed:
                        reporter.on_step_suspended(run.step_name)
                        await asyncio.to_thread(
                            human_handler.handle_suspended_step, engine, run
                        )
                        seen_completed.add(run.id)

            # Check job terminal state
            if job.status == JobStatus.COMPLETED:
                total_time = time.time() - start_time
                reporter.on_flow_completed(job, steps_completed, total_time)
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
                reporter.on_flow_failed(job)
                if output_json:
                    cost = engine.job_cost(job.id)
                    failed_step = None
                    error_msg = None
                    for run in engine.get_runs(job.id):
                        if run.status == StepRunStatus.FAILED:
                            failed_step = run.step_name
                            error_msg = run.error
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
        missing_list = ", ".join(sorted(missing))
        usage = " ".join(f'--var {f}="..."' for f in sorted(missing))
        _json_error(
            2,
            f"Missing required input(s): {missing_list}. "
            f"Usage: stepwise run {flow_path} --wait --output json {usage}",
        )
        return EXIT_USAGE_ERROR

    # Create engine
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    registry = create_default_registry(config)
    engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)

    # Create and start job
    flow_name = objective or flow_path.stem
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


def run_async(
    flow_path: Path,
    project: StepwiseProject,
    objective: str | None = None,
    inputs: dict | None = None,
    workspace: str | None = None,
    config: StepwiseConfig | None = None,
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

    # Create the job in the store so we have a job_id
    if config is None:
        config = load_config()

    from stepwise.registry_factory import create_default_registry

    store = SQLiteStore(str(project.db_path))
    try:
        registry = create_default_registry(config)
        engine = AsyncEngine(store, registry, jobs_dir=str(project.jobs_dir), project_dir=project.dot_dir)

        flow_name = objective or flow_path.stem
        job = engine.create_job(
            objective=flow_name,
            workflow=workflow,
            inputs=inputs or {},
            workspace_path=workspace,
        )
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
