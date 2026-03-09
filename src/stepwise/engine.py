"""Tick-based engine: readiness, currentness, launching, exit resolution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from stepwise.events import Event, EventBus, EventType
from stepwise.executors import Executor, ExecutorRegistry, ExecutorResult
from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepStatus,
    TERMINAL_JOB_STATUSES,
    TERMINAL_STEP_STATUSES,
    WorkflowDefinition,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    """Tick-based workflow engine.

    Drives a Job through its lifecycle by repeatedly calling tick().
    Each tick evaluates step readiness, launches ready steps, collects
    results, and checks for job completion.
    """

    def __init__(
        self,
        job: Job,
        store: Any | None = None,  # StepwiseStore
        event_bus: EventBus | None = None,
    ) -> None:
        self.job = job
        self._store = store
        self._event_bus = event_bus or EventBus()
        self._registry = ExecutorRegistry()
        self._tasks: dict[str, asyncio.Task[ExecutorResult]] = {}

    def register_executor(self, name: str, executor: Executor) -> None:
        self._registry.register(name, executor)

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    # ── Public API ───────────────────────────────────────────────────────

    async def run(self) -> Job:
        """Run the job to completion."""
        # Allow resumption after mark_stale
        if self.job.status in TERMINAL_JOB_STATUSES:
            if any(
                sr.status in (StepStatus.PENDING, StepStatus.READY)
                for sr in self.job.step_runs.values()
            ):
                self.job.status = JobStatus.RUNNING
            else:
                return self.job

        if self.job.status == JobStatus.PENDING:
            self.job.status = JobStatus.RUNNING
            self.job.updated_at = _now()
            self._persist_job()
            await self._emit(EventType.JOB_STARTED)

        while True:
            active = await self.tick()
            if not active:
                break
            # Wait for at least one running task to finish
            if self._tasks:
                done, _ = await asyncio.wait(
                    list(self._tasks.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )

        return self.job

    async def tick(self) -> bool:
        """Perform one engine cycle. Returns True if the job is still active."""
        if self.job.status in TERMINAL_JOB_STATUSES:
            return False

        # 1. Collect completed tasks
        await self._collect_completed()

        # 2. Launch ready steps
        launched = await self._launch_ready_steps()

        # 3. Check if all steps are terminal
        if self._all_steps_terminal():
            await self._resolve_exit()
            return False

        # 4. Deadlock detection
        if not self._tasks and not launched:
            has_pending = any(
                sr.status in (StepStatus.PENDING, StepStatus.READY)
                for sr in self.job.step_runs.values()
            )
            if has_pending:
                self.job.status = JobStatus.FAILED
                self.job.outputs = {"error": "Deadlock: no steps can proceed"}
                self.job.updated_at = _now()
                self._persist_job()
                await self._emit(
                    EventType.JOB_FAILED, data={"error": "Deadlock detected"}
                )
            else:
                await self._resolve_exit()
            return False

        return True

    def mark_stale(self, step_name: str) -> None:
        """Mark a step and all its dependents as needing re-execution."""
        sr = self.job.step_runs.get(step_name)
        if not sr:
            return
        if sr.status in (StepStatus.PENDING, StepStatus.READY):
            return  # already pending

        sr.status = StepStatus.PENDING
        sr.outputs = None
        sr.error = None
        sr.input_hash = None
        sr.started_at = None
        sr.completed_at = None

        # Also remove loop iteration entries
        to_remove = [
            k for k in self.job.step_runs if k.startswith(f"{step_name}:")
        ]
        for k in to_remove:
            del self.job.step_runs[k]

        # Cascade to dependents
        for step_def in self.job.workflow.steps:
            if step_name in step_def.depends_on:
                self.mark_stale(step_def.name)
            for binding in step_def.inputs:
                if binding.source_step == step_name:
                    self.mark_stale(step_def.name)

    def get_current_steps(self) -> list[StepRun]:
        """Return the active frontier: READY and RUNNING steps."""
        return [
            sr
            for sr in self.job.step_runs.values()
            if sr.status in (StepStatus.READY, StepStatus.RUNNING)
        ]

    def is_current(self, step_name: str) -> bool:
        """Check if a step's outputs are still up-to-date with its inputs."""
        sr = self.job.step_runs.get(step_name)
        if not sr or sr.status != StepStatus.COMPLETED:
            return False
        current_inputs = self._resolve_inputs(step_name)
        current_hash = StepRun.create("", "").inputs  # throwaway
        # Compute hash for current inputs
        import hashlib
        import json

        h = hashlib.sha256(
            json.dumps(current_inputs, sort_keys=True, default=str).encode()
        ).hexdigest()
        return sr.input_hash == h

    # ── Internal: Task management ────────────────────────────────────────

    async def _collect_completed(self) -> None:
        """Check for completed async tasks and handle their results."""
        completed = [name for name, task in self._tasks.items() if task.done()]
        for step_name in completed:
            task = self._tasks.pop(step_name)
            try:
                result = task.result()
            except Exception as e:
                result = ExecutorResult(error=str(e))
            await self._handle_step_result(step_name, result)

    async def _launch_ready_steps(self) -> list[str]:
        """Find and launch all ready steps. Returns names of launched steps."""
        ready = self._find_ready_steps()
        for step_name in ready:
            await self._launch_step(step_name)
        return ready

    def _find_ready_steps(self) -> list[str]:
        """Find steps whose dependencies are all met."""
        ready: list[str] = []
        for step_def in self.job.workflow.steps:
            sr = self.job.step_runs.get(step_def.name)
            if not sr or sr.status != StepStatus.PENDING:
                continue
            if self._deps_satisfied(step_def):
                ready.append(step_def.name)
        return ready

    def _deps_satisfied(self, step_def: StepDefinition) -> bool:
        """Check if all dependencies of a step are completed."""
        for dep_name in step_def.depends_on:
            dep_sr = self.job.step_runs.get(dep_name)
            if not dep_sr or dep_sr.status != StepStatus.COMPLETED:
                return False

        for binding in step_def.inputs:
            src_sr = self.job.step_runs.get(binding.source_step)
            if not src_sr or src_sr.status != StepStatus.COMPLETED:
                return False

        # For loop_over, the source step must be completed
        if step_def.loop_over:
            src_name = step_def.loop_over.split(".")[0]
            src_sr = self.job.step_runs.get(src_name)
            if not src_sr or src_sr.status != StepStatus.COMPLETED:
                return False

        return True

    async def _launch_step(self, step_name: str) -> None:
        """Launch a single step."""
        step_def = self.job.workflow.get_step(step_name)
        if not step_def:
            return
        sr = self.job.step_runs[step_name]

        # Evaluate condition
        if step_def.condition is not None:
            if not self._evaluate_condition(step_def.condition):
                sr.status = StepStatus.SKIPPED
                sr.completed_at = _now()
                self._persist_step_run(sr)
                await self._emit(EventType.STEP_SKIPPED, step_name=step_name)
                return

        # Resolve inputs
        inputs = self._resolve_inputs(step_name)
        sr.inputs = inputs
        sr.input_hash = sr.compute_input_hash()
        sr.status = StepStatus.RUNNING
        sr.started_at = _now()
        self._persist_step_run(sr)
        await self._emit(EventType.STEP_STARTED, step_name=step_name)

        # Get executor
        try:
            executor = self._registry.get(step_def.executor)
        except KeyError as e:
            sr.status = StepStatus.FAILED
            sr.error = str(e)
            sr.completed_at = _now()
            self._persist_step_run(sr)
            await self._emit(
                EventType.STEP_FAILED,
                step_name=step_name,
                data={"error": sr.error},
            )
            return

        # Launch as async task
        if step_def.loop_over:
            task = asyncio.create_task(
                self._execute_loop(step_name, step_def, executor)
            )
        else:
            task = asyncio.create_task(executor.execute(sr, step_def.config))

        self._tasks[step_name] = task

    async def _execute_loop(
        self,
        step_name: str,
        step_def: StepDefinition,
        executor: Executor,
    ) -> ExecutorResult:
        """Execute all iterations of a loop step."""
        items = self._resolve_loop_items(step_def.loop_over)  # type: ignore[arg-type]
        base_inputs = self._resolve_inputs(step_name)

        results: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            iter_inputs = {**base_inputs, "item": item, "index": i}
            iter_run = StepRun.create(
                job_id=self.job.id,
                step_name=f"{step_name}:{i}",
                inputs=iter_inputs,
                iteration_index=i,
                iteration_value=item,
            )
            iter_run.status = StepStatus.RUNNING
            iter_run.started_at = _now()
            self.job.step_runs[f"{step_name}:{i}"] = iter_run

            result = await executor.execute(iter_run, step_def.config)

            if result.error:
                iter_run.status = StepStatus.FAILED
                iter_run.error = result.error
                iter_run.completed_at = _now()
                return ExecutorResult(
                    error=f"Loop iteration {i} failed: {result.error}"
                )

            iter_run.status = StepStatus.COMPLETED
            iter_run.outputs = result.outputs
            iter_run.completed_at = _now()
            results.append(result.outputs)

        return ExecutorResult(outputs={"results": results, "count": len(results)})

    async def _handle_step_result(
        self, step_name: str, result: ExecutorResult
    ) -> None:
        """Update step run state after executor completes."""
        sr = self.job.step_runs.get(step_name)
        if not sr:
            return

        if result.success:
            sr.status = StepStatus.COMPLETED
            sr.outputs = result.outputs
            sr.completed_at = _now()
            self._persist_step_run(sr)
            await self._emit(
                EventType.STEP_COMPLETED,
                step_name=step_name,
                data={"outputs": result.outputs},
            )
        else:
            sr.status = StepStatus.FAILED
            sr.error = result.error
            sr.outputs = result.outputs if result.outputs else None
            sr.completed_at = _now()
            self._persist_step_run(sr)
            await self._emit(
                EventType.STEP_FAILED,
                step_name=step_name,
                data={"error": result.error},
            )

    # ── Internal: Input resolution ───────────────────────────────────────

    def _resolve_inputs(self, step_name: str) -> dict[str, Any]:
        """Resolve all input bindings for a step."""
        step_def = self.job.workflow.get_step(step_name)
        if not step_def:
            return {}

        inputs: dict[str, Any] = {}

        # Start with job-level inputs
        inputs.update(self.job.inputs)

        # Apply input bindings (override job inputs)
        for binding in step_def.inputs:
            src_sr = self.job.step_runs.get(binding.source_step)
            if src_sr and src_sr.outputs and binding.source_key in src_sr.outputs:
                inputs[binding.target_key] = src_sr.outputs[binding.source_key]

        return inputs

    def _resolve_loop_items(self, loop_over: str) -> list[Any]:
        """Resolve a loop_over expression like 'step_name.key' to a list."""
        parts = loop_over.split(".", 1)
        step_name = parts[0]
        key = parts[1] if len(parts) > 1 else "result"

        sr = self.job.step_runs.get(step_name)
        if sr and sr.outputs:
            items = sr.outputs.get(key, [])
            if isinstance(items, list):
                return items
            return [items]
        return []

    def _evaluate_condition(self, condition: str) -> bool:
        """Evaluate a condition expression against available step outputs."""
        context: dict[str, Any] = {
            "inputs": self.job.inputs,
            "steps": {},
        }
        for name, sr in self.job.step_runs.items():
            if sr.outputs is not None:
                context["steps"][name] = sr.outputs

        try:
            return bool(eval(condition, {"__builtins__": {}}, context))  # noqa: S307
        except Exception:
            return False

    # ── Internal: Completion ─────────────────────────────────────────────

    def _all_steps_terminal(self) -> bool:
        """Check if all workflow steps (not loop iterations) are in terminal states."""
        for step_def in self.job.workflow.steps:
            sr = self.job.step_runs.get(step_def.name)
            if not sr or sr.status not in TERMINAL_STEP_STATUSES:
                return False
        return True

    async def _resolve_exit(self) -> None:
        """Determine final job status from step results."""
        failed = []
        outputs: dict[str, Any] = {}

        for step_def in self.job.workflow.steps:
            sr = self.job.step_runs.get(step_def.name)
            if not sr:
                continue
            if sr.status == StepStatus.FAILED:
                failed.append(step_def.name)
            elif sr.status == StepStatus.COMPLETED and sr.outputs:
                outputs[step_def.name] = sr.outputs

        if failed:
            self.job.status = JobStatus.FAILED
            self.job.outputs = {
                "error": f"Steps failed: {', '.join(failed)}",
                "step_outputs": outputs,
            }
            await self._emit(
                EventType.JOB_FAILED,
                data={"failed_steps": failed},
            )
        else:
            self.job.status = JobStatus.COMPLETED
            self.job.outputs = outputs
            await self._emit(EventType.JOB_COMPLETED, data={"outputs": outputs})

        self.job.updated_at = _now()
        self._persist_job()

    # ── Internal: Persistence ────────────────────────────────────────────

    def _persist_job(self) -> None:
        if self._store:
            self._store.save_job(self.job)

    def _persist_step_run(self, sr: StepRun) -> None:
        if self._store:
            self._store.save_step_run(sr)

    async def _emit(
        self,
        event_type: EventType,
        step_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = Event.create(
            self.job.id, event_type, step_name=step_name, data=data
        )
        await self._event_bus.emit(event)
        if self._store:
            self._store.save_event(event)
