"""Headless flow execution with terminal output and human step stdin interaction.

Used by `stepwise run` (without --watch).
"""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from stepwise.config import StepwiseConfig, load_config
from stepwise.engine import Engine
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

    def handle_suspended_step(self, engine: Engine, run: StepRun) -> None:
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
    engine = Engine(store, registry, jobs_dir=str(project.jobs_dir))

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

    # 4. Signal handling
    shutdown_requested = False
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _shutdown_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    start_time = time.time()
    seen_running: set[str] = set()  # run IDs we've already reported as started
    seen_completed: set[str] = set()  # run IDs we've already reported as completed/failed
    steps_completed = 0

    try:
        engine.start_job(job.id)

        # 5. Tick loop
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
                ):
                    seen_running.add(run.id)
                    step_def = job.workflow.steps.get(run.step_name)
                    executor_type = step_def.executor.type if step_def else "unknown"
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
                        human_handler.handle_suspended_step(engine, run)
                        seen_completed.add(run.id)  # mark so we don't re-prompt

            # Check job terminal state
            if job.status == JobStatus.COMPLETED:
                total_time = time.time() - start_time
                reporter.on_flow_completed(job, steps_completed, total_time)
                return EXIT_SUCCESS
            elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                reporter.on_flow_failed(job)
                return EXIT_JOB_FAILED

            # Tick again
            time.sleep(0.1)
            engine.tick()

    finally:
        # Restore signal handlers
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)
        store.close()


def _err(msg: str, stream: TextIO | None = None) -> None:
    out = stream or sys.stderr
    out.write(f"Error: {msg}\n")
    out.flush()
