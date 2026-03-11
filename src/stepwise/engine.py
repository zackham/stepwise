"""Tick-based workflow engine: readiness, currentness, launching, exit resolution."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from stepwise.events import (
    CONTEXT_INJECTED,
    EXIT_RESOLVED,
    FOR_EACH_COMPLETED,
    FOR_EACH_ITEM_COMPLETED,
    FOR_EACH_STARTED,
    HUMAN_RERUN,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_RESUMED,
    JOB_STARTED,
    LOOP_ITERATION,
    LOOP_MAX_REACHED,
    STEP_CANCELLED,
    STEP_COMPLETED,
    STEP_DELEGATED,
    STEP_FAILED,
    STEP_LIMIT_EXCEEDED,
    STEP_STARTED,
    STEP_STARTED_ASYNC,
    STEP_SUSPENDED,
    WATCH_FULFILLED,
)
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
)
from stepwise.models import (
    Event,
    ExitRule,
    HandoffEnvelope,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    SubJobDefinition,
    WatchSpec,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore


class Engine:
    """Tick-based workflow engine."""

    def __init__(
        self,
        store: SQLiteStore,
        registry: ExecutorRegistry | None = None,
        jobs_dir: str | None = None,
    ) -> None:
        self.store = store
        self.registry = registry or ExecutorRegistry()
        self.jobs_dir = jobs_dir or "jobs"
        self._injected_contexts: dict[str, list[str]] = {}  # job_id -> contexts

    # ── Job Lifecycle ─────────────────────────────────────────────────────

    def create_job(
        self,
        objective: str,
        workflow: WorkflowDefinition,
        inputs: dict | None = None,
        config: JobConfig | None = None,
        parent_job_id: str | None = None,
        parent_step_run_id: str | None = None,
        workspace_path: str | None = None,
    ) -> Job:
        errors = workflow.validate()
        if errors:
            raise ValueError(f"Invalid workflow: {'; '.join(errors)}")

        job_id = _gen_id("job")
        ws = workspace_path or os.path.join(self.jobs_dir, job_id, "workspace")

        job = Job(
            id=job_id,
            objective=objective,
            workflow=workflow,
            status=JobStatus.PENDING,
            inputs=inputs or {},
            parent_job_id=parent_job_id,
            parent_step_run_id=parent_step_run_id,
            workspace_path=ws,
            config=config or JobConfig(),
        )
        self.store.save_job(job)
        return job

    def start_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        if job.status != JobStatus.PENDING:
            raise ValueError(f"Cannot start job in status {job.status.value}")
        job.status = JobStatus.RUNNING
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job_id, JOB_STARTED)
        # Run initial tick
        self.tick()

    def pause_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        if job.status != JobStatus.RUNNING:
            raise ValueError(f"Cannot pause job in status {job.status.value}")
        job.status = JobStatus.PAUSED
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job_id, JOB_PAUSED)

    def resume_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        if job.status != JobStatus.PAUSED:
            raise ValueError(f"Cannot resume job in status {job.status.value}")
        job.status = JobStatus.RUNNING
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job_id, JOB_RESUMED)
        self.tick()

    def cancel_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)

        # Cancel all active runs
        for run in self.store.running_runs(job_id):
            step_def = job.workflow.steps.get(run.step_name)
            if step_def:
                try:
                    executor = self.registry.create(step_def.executor)
                    executor.cancel(run.executor_state or {})
                except Exception:
                    pass
            run.status = StepRunStatus.FAILED
            run.error = "Job cancelled"
            run.completed_at = _now()
            self.store.save_run(run)

        for run in self.store.suspended_runs(job_id):
            run.status = StepRunStatus.FAILED
            run.error = "Job cancelled"
            run.completed_at = _now()
            self.store.save_run(run)

        for run in self.store.delegated_runs(job_id):
            run.status = StepRunStatus.FAILED
            run.error = "Job cancelled"
            run.completed_at = _now()
            self.store.save_run(run)
            # Cancel sub-job(s)
            if run.sub_job_id:
                try:
                    self.cancel_job(run.sub_job_id)
                except Exception:
                    pass
            # Cancel for_each sub-jobs
            es = run.executor_state or {}
            if es.get("for_each"):
                for sid in es.get("sub_job_ids", []):
                    try:
                        self.cancel_job(sid)
                    except Exception:
                        pass

        job.status = JobStatus.CANCELLED
        job.updated_at = _now()
        self.store.save_job(job)

    # ── Step Control ──────────────────────────────────────────────────────

    def rerun_step(self, job_id: str, step_name: str) -> StepRun:
        """Create a new StepRun for a step. Rejects if latest run is not terminal."""
        job = self.store.load_job(job_id)
        if step_name not in job.workflow.steps:
            raise ValueError(f"Unknown step: {step_name}")

        latest = self.store.latest_run(job_id, step_name)
        if latest and latest.status in (
            StepRunStatus.RUNNING,
            StepRunStatus.SUSPENDED,
            StepRunStatus.DELEGATED,
        ):
            raise ValueError(
                f"Cannot rerun step '{step_name}': latest run is {latest.status.value}. "
                f"Cancel the active run first."
            )

        self._emit(job_id, HUMAN_RERUN, {"step": step_name})

        # Make sure job is running
        if job.status in (JobStatus.PAUSED, JobStatus.COMPLETED, JobStatus.FAILED):
            job.status = JobStatus.RUNNING
            job.updated_at = _now()
            self.store.save_job(job)

        # Launch directly — this is the human API, synchronous launch.
        # The new run supersedes any existing completed run.
        run = self._launch(job, step_name)
        return run

    def fulfill_watch(self, run_id: str, payload: dict) -> None:
        """Complete a suspended step's watch with the provided payload."""
        run = self.store.load_run(run_id)
        if run.status != StepRunStatus.SUSPENDED:
            raise ValueError(f"Run {run_id} is not suspended (status: {run.status.value})")
        if not run.watch:
            raise ValueError(f"Run {run_id} has no watch spec")

        # Validate payload has fulfillment_outputs
        for field in run.watch.fulfillment_outputs:
            if field not in payload:
                raise ValueError(
                    f"Payload missing required field '{field}' "
                    f"(expected: {run.watch.fulfillment_outputs})"
                )

        job = self.store.load_job(run.job_id)

        # Create result from payload
        run.result = HandoffEnvelope(
            artifact=payload,
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        )
        run.status = StepRunStatus.COMPLETED
        run.completed_at = _now()
        run.watch = None
        self.store.save_run(run)

        self._emit(run.job_id, WATCH_FULFILLED, {
            "run_id": run_id,
            "mode": "human",
            "payload": payload,
        })

        self._process_completion(job, run)

    def inject_context(self, job_id: str, context: str) -> None:
        """Append context to job's event log for future step executions."""
        if job_id not in self._injected_contexts:
            self._injected_contexts[job_id] = []
        self._injected_contexts[job_id].append(context)
        self._emit(job_id, CONTEXT_INJECTED, {"context": context})

    # ── Observation ───────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Job:
        return self.store.load_job(job_id)

    def get_runs(self, job_id: str, step_name: str | None = None) -> list[StepRun]:
        if step_name:
            return self.store.runs_for_step(job_id, step_name)
        return self.store.runs_for_job(job_id)

    def get_events(self, job_id: str, since: datetime | None = None) -> list[Event]:
        return self.store.load_events(job_id, since)

    def get_job_tree(self, job_id: str) -> dict:
        """Get a job and all its sub-jobs recursively."""
        job = self.store.load_job(job_id)
        runs = self.store.runs_for_job(job_id)
        sub_jobs = []
        for run in runs:
            if run.sub_job_id:
                sub_jobs.append(self.get_job_tree(run.sub_job_id))
            # for_each sub-jobs
            es = run.executor_state or {}
            if es.get("for_each"):
                for sid in es.get("sub_job_ids", []):
                    try:
                        sub_jobs.append(self.get_job_tree(sid))
                    except KeyError:
                        pass
        return {
            "job": job,
            "runs": runs,
            "sub_jobs": sub_jobs,
        }

    # ── Tick Loop ─────────────────────────────────────────────────────────

    def tick(self) -> None:
        """Process all active jobs."""
        for job in self.store.active_jobs():
            self._tick_job(job)

    def _tick_job(self, job: Job) -> None:
        if job.status != JobStatus.RUNNING:
            return

        # M1: synchronous execution. Loop until no more progress.
        max_iterations = 100  # safety bound
        for _ in range(max_iterations):
            job = self.store.load_job(job.id)
            if job.status != JobStatus.RUNNING:
                return

            made_progress = False

            # 1. Check running step runs (async executors polled here)
            for run in self.store.running_runs(job.id):
                step_def = job.workflow.steps.get(run.step_name)
                if step_def:
                    try:
                        # Check limits before polling executor
                        limit_result = self._check_limits(run, step_def)
                        if limit_result:
                            # Limits exceeded — cancel and handle failure
                            try:
                                executor = self.registry.create(step_def.executor)
                                executor.cancel(run.executor_state or {})
                            except Exception:
                                pass
                            self._fail_run(
                                job, run, step_def,
                                error=limit_result["message"],
                                error_category=limit_result["category"],
                            )
                            made_progress = True
                            job = self.store.load_job(job.id)
                            if job.status != JobStatus.RUNNING:
                                return
                            continue

                        executor = self.registry.create(step_def.executor)
                        status = executor.check_status(run.executor_state or {})

                        if status.state == "completed":
                            # Async executor completed — extract result
                            if status.result and status.result.envelope:
                                result_envelope = status.result.envelope
                            else:
                                result_envelope = HandoffEnvelope(
                                    artifact={}, sidecar=Sidecar(),
                                    workspace=job.workspace_path, timestamp=_now(),
                                )

                            # Validate artifact
                            validation_error = self._validate_artifact(step_def, result_envelope)
                            if validation_error:
                                self._fail_run(
                                    job, run, step_def,
                                    error=validation_error,
                                    error_category="output_invalid",
                                )
                            else:
                                run.result = result_envelope
                                run.status = StepRunStatus.COMPLETED
                                run.completed_at = _now()
                                self.store.save_run(run)
                                self._emit(job.id, STEP_COMPLETED, {
                                    "step": run.step_name,
                                    "attempt": run.attempt,
                                })
                                self._emit_effector_events(job.id, result_envelope)
                                self._process_completion(job, run)
                            made_progress = True
                            job = self.store.load_job(job.id)
                            if job.status != JobStatus.RUNNING:
                                return
                        elif status.state == "failed":
                            error_cat = status.error_category or "agent_failure"
                            self._fail_run(
                                job, run, step_def,
                                error=status.message or "Executor failed",
                                error_category=error_cat,
                            )
                            made_progress = True
                            job = self.store.load_job(job.id)
                            if job.status != JobStatus.RUNNING:
                                return
                    except Exception:
                        pass

            # 2. Check delegated runs
            for run in self.store.delegated_runs(job.id):
                # For-each delegated runs (multiple sub-jobs)
                if run.executor_state and run.executor_state.get("for_each"):
                    if self._check_for_each_completion(job, run):
                        made_progress = True
                        job = self.store.load_job(job.id)
                        if job.status != JobStatus.RUNNING:
                            return
                elif run.sub_job_id:
                    try:
                        sub_job = self.store.load_job(run.sub_job_id)
                        if sub_job.status == JobStatus.COMPLETED:
                            run.result = self._terminal_output(sub_job)
                            run.status = StepRunStatus.COMPLETED
                            run.completed_at = _now()
                            self.store.save_run(run)
                            self._process_completion(job, run)
                            made_progress = True
                        elif sub_job.status == JobStatus.FAILED:
                            run.status = StepRunStatus.FAILED
                            run.error = "Sub-job failed"
                            run.completed_at = _now()
                            self.store.save_run(run)
                            self._halt_job(job, run)
                            return
                    except KeyError:
                        pass

            # 3. Check suspended runs (poll watches)
            for run in self.store.suspended_runs(job.id):
                if run.watch and run.watch.mode == "poll":
                    if self._check_poll_watch(job, run):
                        made_progress = True

            # 4. Launch ready steps
            job = self.store.load_job(job.id)
            if job.status != JobStatus.RUNNING:
                return
            ready = self._find_ready(job)
            for step_name in ready:
                self._launch(job, step_name)
                made_progress = True
                # Re-load job after each launch since state changes
                job = self.store.load_job(job.id)
                if job.status != JobStatus.RUNNING:
                    return

            # 5. Check job completion
            job = self.store.load_job(job.id)
            if job.status != JobStatus.RUNNING:
                return
            if self._job_complete(job):
                job.status = JobStatus.COMPLETED
                job.updated_at = _now()
                self.store.save_job(job)
                self._emit(job.id, JOB_COMPLETED)
                return

            if not made_progress:
                return  # No progress possible, wait for next tick

    # ── Readiness ─────────────────────────────────────────────────────────

    def _find_ready(self, job: Job) -> list[str]:
        """Find steps that are ready to launch."""
        ready = []
        for step_name, step_def in job.workflow.steps.items():
            if self._is_step_ready(job, step_name, step_def):
                ready.append(step_name)
        return ready

    def _is_step_ready(self, job: Job, step_name: str, step_def: StepDefinition) -> bool:
        """A step is ready when:
        1. All dep steps have current completed run
        2. No active run exists (running, suspended, delegated)
        3. No current completed run exists
        4. No in-flight loop will supersede a dep (loop-aware)
        5. Re-triggering won't create an infinite loop
        """
        # Check no active run
        latest = self.store.latest_run(job.id, step_name)
        if latest and latest.status in (
            StepRunStatus.RUNNING,
            StepRunStatus.SUSPENDED,
            StepRunStatus.DELEGATED,
        ):
            return False

        # Check no current completed run
        if latest and latest.status == StepRunStatus.COMPLETED:
            if self._is_current(job, latest):
                return False

            # Loop guard: if this step has a non-current completed run AND
            # it has an unconditional loop exit rule targeting one of its own
            # deps, re-triggering would create an infinite loop. Skip it.
            # Conditional loops (field_match etc.) are fine — they can advance.
            dep_step_names = set(self._dep_steps(step_def))
            for rule in step_def.exit_rules:
                if rule.config.get("action") == "loop" and rule.type == "always":
                    target = rule.config.get("target", step_name)
                    if target in dep_step_names:
                        return False

        # Check all deps have current completed runs
        dep_steps = self._dep_steps(step_def)
        for dep_step in dep_steps:
            if dep_step == "$job":
                continue
            dep_latest = self.store.latest_completed_run(job.id, dep_step)
            if not dep_latest:
                return False
            if not self._is_current(job, dep_latest):
                return False
            # Loop guard: don't launch if an in-flight loop will supersede this dep
            if self._dep_will_be_superseded(job, dep_step):
                return False

        return True

    def _dep_will_be_superseded(self, job: Job, dep_step_name: str) -> bool:
        """Check if a dep step will be superseded by an in-flight loop.

        Returns True if any running step has a loop exit rule targeting
        dep_step_name, meaning dep's current completed run will be replaced.
        """
        for run in self.store.running_runs(job.id):
            run_step_def = job.workflow.steps.get(run.step_name)
            if not run_step_def:
                continue
            for rule in run_step_def.exit_rules:
                if rule.config.get("action") == "loop":
                    target = rule.config.get("target", run.step_name)
                    if target == dep_step_name:
                        return True
        return False

    # ── Currentness ───────────────────────────────────────────────────────

    def _is_current(self, job: Job, run: StepRun) -> bool:
        """A run is current if:
        1. It is the latest run (any status) for its step
        2. It has COMPLETED status
        3. Every dependency run it used is itself current
        """
        # Supersession: ANY newer run invalidates this one
        latest = self.store.latest_run(job.id, run.step_name)
        if not latest or latest.id != run.id:
            return False

        if run.status != StepRunStatus.COMPLETED:
            return False

        step_def = job.workflow.steps.get(run.step_name)
        if not step_def:
            return False

        # Check all dependency provenance
        dep_steps = self._dep_steps(step_def)
        for dep_step in dep_steps:
            if dep_step == "$job":
                continue
            if not run.dep_run_ids:
                return False
            source_run_id = run.dep_run_ids.get(dep_step)
            if not source_run_id:
                return False
            try:
                source_run = self.store.load_run(source_run_id)
            except KeyError:
                return False
            if not self._is_current(job, source_run):
                return False

        return True

    def _dep_steps(self, step_def: StepDefinition) -> list[str]:
        """All dependency steps: input binding sources + sequencing + for_each source."""
        deps = [b.source_step for b in step_def.inputs]
        deps.extend(step_def.sequencing)
        if step_def.for_each:
            deps.append(step_def.for_each.source_step)
        return deps

    # ── Job Completion ────────────────────────────────────────────────────

    def _job_complete(self, job: Job) -> bool:
        """A job is complete when all terminal steps have current completed runs."""
        terminal = job.workflow.terminal_steps()
        for step_name in terminal:
            latest = self.store.latest_completed_run(job.id, step_name)
            if not latest or not self._is_current(job, latest):
                return False
        return True

    # ── Launching ─────────────────────────────────────────────────────────

    def _launch(self, job: Job, step_name: str) -> StepRun:
        step_def = job.workflow.steps[step_name]

        # For-each steps get special handling
        if step_def.for_each and step_def.sub_flow:
            try:
                return self._launch_for_each(job, step_def)
            except (ValueError, KeyError) as e:
                import logging
                logging.getLogger("stepwise.engine").error(
                    f"For-each step '{step_name}' failed to launch: {e}", exc_info=True
                )
                run = StepRun(
                    id=_gen_id("run"),
                    job_id=job.id,
                    step_name=step_name,
                    attempt=self.store.next_attempt(job.id, step_name),
                    status=StepRunStatus.FAILED,
                    error=str(e),
                    started_at=_now(),
                    completed_at=_now(),
                )
                self.store.save_run(run)
                self._emit(job.id, STEP_FAILED, {"step": step_name, "error": str(e)})
                self._halt_job(job, run)
                return run

        attempt = self.store.next_attempt(job.id, step_name)
        inputs, dep_run_ids = self._resolve_inputs(job, step_def)

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name=step_name,
            attempt=attempt,
            status=StepRunStatus.RUNNING,
            inputs=inputs,
            dep_run_ids=dep_run_ids,
            started_at=_now(),
        )
        self.store.save_run(run)
        self._emit(job.id, STEP_STARTED, {
            "step": step_name,
            "attempt": attempt,
        })

        ctx = ExecutionContext(
            job_id=job.id,
            step_name=step_name,
            attempt=attempt,
            workspace_path=job.workspace_path,
            idempotency=step_def.idempotency,
            objective=job.objective,
            timeout_minutes=job.config.timeout_minutes,
            injected_context=self._injected_contexts.get(job.id),
        )

        # Inject step output fields into executor config for LLM structured output
        exec_ref = step_def.executor
        if step_def.outputs and "output_fields" not in exec_ref.config:
            exec_ref = exec_ref.with_config({"output_fields": step_def.outputs})

        try:
            executor = self.registry.create(exec_ref)
            result = executor.start(inputs, ctx)
        except Exception as e:
            import logging
            logging.getLogger("stepwise.engine").error(
                f"Step '{step_name}' executor crashed: {type(e).__name__}: {e}", exc_info=True
            )
            run.status = StepRunStatus.FAILED
            run.error = f"Executor crash: {type(e).__name__}: {e}"
            run.completed_at = _now()
            self.store.save_run(run)
            self._emit(job.id, STEP_FAILED, {
                "step": step_name,
                "attempt": attempt,
                "error": str(e),
            })
            self._halt_job(job, run)
            return run

        match result.type:
            case "data":
                # Check for failure
                is_failure = False
                error_msg = None
                if result.executor_state and result.executor_state.get("failed"):
                    is_failure = True
                    error_msg = result.executor_state.get("error", "Executor failed")
                elif result.envelope and result.envelope.executor_meta.get("failed"):
                    is_failure = True
                    error_msg = result.envelope.executor_meta.get("reason", "Executor failed")

                if is_failure:
                    run.status = StepRunStatus.FAILED
                    run.error = error_msg
                    run.result = result.envelope
                    run.executor_state = result.executor_state
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._emit(job.id, STEP_FAILED, {
                        "step": step_name,
                        "attempt": attempt,
                        "error": error_msg,
                    })
                    self._halt_job(job, run)
                else:
                    # M1: hard validation — artifact must contain declared outputs
                    validation_error = self._validate_artifact(step_def, result.envelope)
                    if validation_error:
                        run.status = StepRunStatus.FAILED
                        run.error = validation_error
                        run.result = result.envelope
                        run.completed_at = _now()
                        self.store.save_run(run)
                        self._emit(job.id, STEP_FAILED, {
                            "step": step_name,
                            "attempt": attempt,
                            "error": validation_error,
                        })
                        self._halt_job(job, run)
                    else:
                        run.result = result.envelope
                        run.status = StepRunStatus.COMPLETED
                        run.completed_at = _now()
                        self.store.save_run(run)
                        self._emit(job.id, STEP_COMPLETED, {
                            "step": step_name,
                            "attempt": attempt,
                        })
                        # Emit effector events from executor_meta
                        self._emit_effector_events(job.id, result.envelope)
                        self._process_completion(job, run)

            case "sub_job":
                self._validate_sub_job(step_def, result.sub_job_def)
                sub = self._create_sub_job(job, run, result.sub_job_def)
                run.status = StepRunStatus.DELEGATED
                run.sub_job_id = sub.id
                self.store.save_run(run)
                self._emit(job.id, STEP_DELEGATED, {
                    "step": step_name,
                    "sub_job_id": sub.id,
                })

            case "watch":
                run.status = StepRunStatus.SUSPENDED
                run.watch = result.watch
                run.executor_state = result.executor_state
                # Set fulfillment_outputs from step definition if watch doesn't have them
                if run.watch and not run.watch.fulfillment_outputs:
                    run.watch.fulfillment_outputs = list(step_def.outputs)
                self.store.save_run(run)
                self._emit(job.id, STEP_SUSPENDED, {
                    "step": step_name,
                    "watch_mode": result.watch.mode if result.watch else None,
                })

            case "async":
                # M4: Long-running executor — stays RUNNING, polled in tick loop
                run.executor_state = result.executor_state
                self.store.save_run(run)
                self._emit(job.id, STEP_STARTED_ASYNC, {
                    "step": step_name,
                    "attempt": attempt,
                    "executor_type": step_def.executor.type,
                })

        return run

    # ── For-Each Launching ────────────────────────────────────────────────

    def _launch_for_each(self, job: Job, step_def: StepDefinition) -> StepRun:
        """Launch a for_each step: resolve source list, create N sub-jobs."""
        fe = step_def.for_each
        assert fe is not None
        assert step_def.sub_flow is not None

        step_name = step_def.name
        attempt = self.store.next_attempt(job.id, step_name)
        inputs, dep_run_ids = self._resolve_inputs(job, step_def)

        # Resolve the source list from the for_each source step
        source_run = self.store.latest_completed_run(job.id, fe.source_step)
        if not source_run or not source_run.result:
            raise ValueError(
                f"For-each step '{step_name}': source step '{fe.source_step}' "
                f"has no completed run"
            )

        # Navigate to the source field (supports nested: "design.sections")
        source_list = source_run.result.artifact
        for part in fe.source_field.split("."):
            if isinstance(source_list, dict):
                source_list = source_list.get(part)
            else:
                source_list = None
                break

        if not isinstance(source_list, list):
            raise ValueError(
                f"For-each step '{step_name}': '{fe.source_step}.{fe.source_field}' "
                f"is not a list (got {type(source_list).__name__})"
            )

        # Create the run
        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name=step_name,
            attempt=attempt,
            status=StepRunStatus.DELEGATED,
            inputs=inputs,
            dep_run_ids={**dep_run_ids, fe.source_step: source_run.id},
            started_at=_now(),
        )

        # Handle empty list: complete immediately with empty results
        if len(source_list) == 0:
            run.status = StepRunStatus.COMPLETED
            run.completed_at = _now()
            run.result = HandoffEnvelope(
                artifact={"results": []},
                sidecar=Sidecar(),
                workspace=job.workspace_path,
                timestamp=_now(),
            )
            self.store.save_run(run)
            self._emit(job.id, STEP_COMPLETED, {
                "step": step_name,
                "attempt": attempt,
                "for_each": True,
                "item_count": 0,
            })
            self._process_completion(job, run)
            return run

        # Create sub-jobs for each item
        sub_job_ids: list[str] = []
        for i, item in enumerate(source_list):
            sub_inputs = {
                **inputs,  # parent inputs passed through
                fe.item_var: item,  # the iteration variable
            }
            sub_workspace = os.path.join(
                job.workspace_path, "for_each", step_name, str(i),
            )
            sub_job = self.create_job(
                objective=f"{job.objective} > {step_name}[{i}]",
                workflow=step_def.sub_flow,
                inputs=sub_inputs,
                config=job.config,
                parent_job_id=job.id,
                parent_step_run_id=run.id,
                workspace_path=sub_workspace,
            )
            sub_job_ids.append(sub_job.id)

        # Store sub-job tracking info in executor_state
        run.executor_state = {
            "for_each": True,
            "sub_job_ids": sub_job_ids,
            "item_count": len(source_list),
            "on_error": fe.on_error,
        }
        self.store.save_run(run)

        self._emit(job.id, FOR_EACH_STARTED, {
            "step": step_name,
            "attempt": attempt,
            "item_count": len(source_list),
            "sub_job_ids": sub_job_ids,
        })

        # Start all sub-jobs
        for sub_job_id in sub_job_ids:
            self.start_job(sub_job_id)

        return run

    def _check_for_each_completion(self, job: Job, run: StepRun) -> bool:
        """Check if all sub-jobs for a for_each step are complete.
        Returns True if progress was made.
        """
        if not run.executor_state or not run.executor_state.get("for_each"):
            return False

        sub_job_ids = run.executor_state.get("sub_job_ids", [])
        on_error = run.executor_state.get("on_error", "fail_fast")

        completed_results: list[dict | None] = [None] * len(sub_job_ids)
        all_done = True
        any_failed = False
        failed_indices: list[int] = []

        for i, sub_job_id in enumerate(sub_job_ids):
            try:
                sub_job = self.store.load_job(sub_job_id)
            except KeyError:
                all_done = False
                continue

            if sub_job.status == JobStatus.COMPLETED:
                terminal_output = self._terminal_output(sub_job)
                completed_results[i] = terminal_output.artifact
            elif sub_job.status == JobStatus.FAILED:
                any_failed = True
                failed_indices.append(i)
                if on_error == "fail_fast":
                    # Cancel remaining sub-jobs
                    for j, other_id in enumerate(sub_job_ids):
                        if j != i:
                            try:
                                other = self.store.load_job(other_id)
                                if other.status == JobStatus.RUNNING:
                                    self.cancel_job(other_id)
                            except (KeyError, ValueError):
                                pass
                    # Fail the for_each run
                    run.status = StepRunStatus.FAILED
                    run.error = f"For-each item {i} failed"
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._halt_job(job, run)
                    return True
                else:
                    # continue mode: record failure, keep going
                    completed_results[i] = {"_error": f"Sub-job {sub_job_id} failed"}
            elif sub_job.status in (JobStatus.CANCELLED, JobStatus.PAUSED):
                any_failed = True
                failed_indices.append(i)
                completed_results[i] = {"_error": f"Sub-job {sub_job.status.value}"}
            else:
                all_done = False

        if not all_done:
            return False

        # All sub-jobs are done — collect results in order
        results = []
        for r in completed_results:
            results.append(r if r is not None else {})

        run.result = HandoffEnvelope(
            artifact={"results": results},
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        )
        run.status = StepRunStatus.COMPLETED
        run.completed_at = _now()
        self.store.save_run(run)

        self._emit(job.id, FOR_EACH_COMPLETED, {
            "step": run.step_name,
            "item_count": len(sub_job_ids),
            "failed_count": len(failed_indices),
        })
        self._emit(job.id, STEP_COMPLETED, {
            "step": run.step_name,
            "attempt": run.attempt,
            "for_each": True,
        })

        self._process_completion(job, run)
        return True

    # ── Input Resolution ──────────────────────────────────────────────────

    def _resolve_inputs(self, job: Job, step_def: StepDefinition) -> tuple[dict, dict]:
        """Returns (inputs_dict, dep_run_ids_dict)."""
        inputs: dict = {}
        dep_run_ids: dict[str, str] = {}

        for binding in step_def.inputs:
            if binding.source_step == "$job":
                inputs[binding.local_name] = job.inputs.get(binding.source_field)
                dep_run_ids["$job"] = "$job"
            else:
                latest = self.store.latest_completed_run(job.id, binding.source_step)
                if latest and latest.result:
                    value = latest.result.artifact.get(binding.source_field)
                    # Support nested field access: "hero.headline" → artifact["hero"]["headline"]
                    if value is None and "." in binding.source_field:
                        parts = binding.source_field.split(".")
                        value = latest.result.artifact
                        for part in parts:
                            if isinstance(value, dict):
                                value = value.get(part)
                            else:
                                value = None
                                break
                    inputs[binding.local_name] = value
                    dep_run_ids[binding.source_step] = latest.id

        # Record sequencing deps
        for seq_step in step_def.sequencing:
            latest = self.store.latest_completed_run(job.id, seq_step)
            if latest:
                dep_run_ids[seq_step] = latest.id

        return inputs, dep_run_ids

    # ── Exit Resolution ───────────────────────────────────────────────────

    def _process_completion(self, job: Job, run: StepRun) -> None:
        """Process exit rules after step completion."""
        step_def = job.workflow.steps.get(run.step_name)
        if not step_def:
            return

        # No exit rules or no artifact → advance
        if not step_def.exit_rules:
            self._emit(job.id, EXIT_RESOLVED, {
                "step": run.step_name,
                "rule": "implicit_advance",
                "action": "advance",
            })
            return

        # Evaluate rules in priority order (highest first)
        sorted_rules = sorted(step_def.exit_rules, key=lambda r: r.priority, reverse=True)
        artifact = run.result.artifact if run.result else {}

        for rule in sorted_rules:
            if self._evaluate_rule(rule, artifact, attempt=run.attempt):
                action = rule.config.get("action", "advance")
                self._emit(job.id, EXIT_RESOLVED, {
                    "step": run.step_name,
                    "rule": rule.name,
                    "action": action,
                })

                match action:
                    case "advance":
                        return  # Normal progression
                    case "loop":
                        target = rule.config.get("target", run.step_name)
                        max_iterations = rule.config.get("max_iterations")

                        # Count completed runs of target step
                        if max_iterations is not None:
                            completed_count = self.store.completed_run_count(
                                job.id, target
                            )
                            if completed_count >= max_iterations:
                                self._emit(job.id, LOOP_MAX_REACHED, {
                                    "step": run.step_name,
                                    "target": target,
                                    "completed_count": completed_count,
                                    "max_iterations": max_iterations,
                                })
                                # Escalate
                                job.status = JobStatus.PAUSED
                                job.updated_at = _now()
                                self.store.save_job(job)
                                self._emit(job.id, JOB_PAUSED, {
                                    "reason": "max_iterations_reached",
                                    "step": run.step_name,
                                    "target": target,
                                })
                                return

                        self._emit(job.id, LOOP_ITERATION, {
                            "step": run.step_name,
                            "target": target,
                            "count": self.store.completed_run_count(job.id, target),
                        })
                        # Explicitly launch the target step. Creating a new run
                        # supersedes the previous completed run, which makes
                        # downstream steps non-current (they'll re-execute).
                        job = self.store.load_job(job.id)
                        if job.status == JobStatus.RUNNING:
                            self._launch(job, target)
                        return

                    case "escalate":
                        job.status = JobStatus.PAUSED
                        job.updated_at = _now()
                        self.store.save_job(job)
                        self._emit(job.id, JOB_PAUSED, {
                            "reason": "escalated",
                            "step": run.step_name,
                            "rule": rule.name,
                        })
                        return

                    case "abandon":
                        job.status = JobStatus.FAILED
                        job.updated_at = _now()
                        self.store.save_job(job)
                        self._emit(job.id, JOB_FAILED, {
                            "reason": "abandoned",
                            "step": run.step_name,
                            "rule": rule.name,
                        })
                        return

        # No rule matched → advance
        self._emit(job.id, EXIT_RESOLVED, {
            "step": run.step_name,
            "rule": "no_match_advance",
            "action": "advance",
        })

    def _evaluate_rule(self, rule: ExitRule, artifact: dict,
                       attempt: int = 1) -> bool:
        """Evaluate a single exit rule against an artifact."""
        match rule.type:
            case "field_match":
                field = rule.config.get("field")
                value = rule.config.get("value")
                if field and field in artifact:
                    return artifact[field] == value
                return False
            case "expression":
                from stepwise.yaml_loader import evaluate_exit_condition
                condition = rule.config.get("condition", "False")
                try:
                    return evaluate_exit_condition(condition, artifact, attempt)
                except ValueError:
                    return False
            case "always":
                return True
            case _:
                return False

    # ── Watch Checking ────────────────────────────────────────────────────

    def _check_poll_watch(self, job: Job, run: StepRun) -> bool:
        """Check a poll watch. Returns True if fulfilled."""
        if not run.watch or run.watch.mode != "poll":
            return False

        config = run.watch.config
        check_command = config.get("check_command")
        interval_seconds = config.get("interval_seconds", 60)

        if not check_command:
            return False

        # Check timing
        watch_state = (run.executor_state or {}).get("_watch", {})
        last_checked = watch_state.get("last_checked_at")
        if last_checked:
            last_dt = datetime.fromisoformat(last_checked)
            elapsed = (_now() - last_dt).total_seconds()
            if elapsed < interval_seconds:
                return False

        # Write step input file for the check command
        workspace = job.workspace_path or "."
        step_io_dir = Path(workspace) / ".step-io"
        step_io_dir.mkdir(parents=True, exist_ok=True)
        input_file = step_io_dir / f"{run.step_name}-{run.attempt}.input.json"
        if run.inputs:
            input_file.write_text(json.dumps(run.inputs, default=str))

        env = {**os.environ, "JOB_ENGINE_INPUTS": str(input_file)}

        try:
            result = subprocess.run(
                check_command,
                shell=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=workspace,
            )
        except Exception as e:
            # Check error — log and retry next interval
            self._update_watch_state(run, error=str(e))
            return False

        if result.returncode != 0:
            # Non-zero = check error, retry next interval
            self._update_watch_state(run, error=result.stderr.strip())
            return False

        stdout = result.stdout.strip()
        if not stdout:
            # Empty = not ready
            self._update_watch_state(run)
            return False

        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict):
                # Fulfilled!
                run.result = HandoffEnvelope(
                    artifact=payload,
                    sidecar=Sidecar(),
                    workspace=job.workspace_path,
                    timestamp=_now(),
                )
                run.status = StepRunStatus.COMPLETED
                run.completed_at = _now()
                run.watch = None
                self.store.save_run(run)

                self._emit(job.id, WATCH_FULFILLED, {
                    "run_id": run.id,
                    "mode": "poll",
                    "payload": payload,
                })
                self._process_completion(job, run)
                return True
        except (json.JSONDecodeError, ValueError):
            # Non-JSON stdout = not ready
            self._update_watch_state(run)
            return False

        self._update_watch_state(run)
        return False

    def _update_watch_state(self, run: StepRun, error: str | None = None) -> None:
        """Update poll watch timing state."""
        if run.executor_state is None:
            run.executor_state = {}
        watch_state = run.executor_state.get("_watch", {})
        watch_state["last_checked_at"] = _now().isoformat()
        check_count = watch_state.get("check_count", 0) + 1
        watch_state["check_count"] = check_count
        watch_state["last_error"] = error
        # Calculate next check
        interval = (run.watch.config.get("interval_seconds", 60) if run.watch else 60)
        next_check = _now()  # simplified
        watch_state["next_check_at"] = next_check.isoformat()
        run.executor_state["_watch"] = watch_state
        self.store.save_run(run)

    # ── Sub-Job ───────────────────────────────────────────────────────────

    def _validate_sub_job(self, parent_step: StepDefinition, sub_def: SubJobDefinition) -> None:
        """Validate sub-job definition."""
        errors = sub_def.workflow.validate()
        if errors:
            raise ValueError(f"Invalid sub-job workflow: {'; '.join(errors)}")

        terminal = sub_def.workflow.terminal_steps()
        if len(terminal) != 1:
            raise ValueError(
                f"Sub-job must have exactly one terminal step, got {len(terminal)}: "
                f"{', '.join(terminal)}"
            )

        # Check terminal step outputs match parent step outputs
        terminal_step = sub_def.workflow.steps[terminal[0]]
        for out in parent_step.outputs:
            if out not in terminal_step.outputs:
                raise ValueError(
                    f"Sub-job terminal step '{terminal[0]}' missing output '{out}' "
                    f"required by parent step '{parent_step.name}'"
                )

    def _validate_artifact(self, step_def: StepDefinition, envelope: HandoffEnvelope | None) -> str | None:
        """M1: hard validation — artifact must contain all declared output fields.
        Returns error string if validation fails, None if valid.
        """
        if not step_def.outputs:
            return None  # No declared outputs to validate
        if not envelope or not envelope.artifact:
            return (
                f"Step '{step_def.name}' declares outputs {step_def.outputs} "
                f"but artifact is empty"
            )
        missing = [f for f in step_def.outputs if f not in envelope.artifact]
        if missing:
            return (
                f"Step '{step_def.name}' artifact missing declared outputs: {missing} "
                f"(got: {list(envelope.artifact.keys())})"
            )
        return None

    def _create_sub_job(self, parent_job: Job, parent_run: StepRun, sub_def: SubJobDefinition) -> Job:
        """Create a sub-job and start it."""
        # Check depth
        depth = self._get_job_depth(parent_job)
        max_depth = parent_job.config.max_sub_job_depth
        if depth >= max_depth:
            raise ValueError(
                f"Sub-job depth {depth + 1} exceeds max {max_depth}"
            )

        sub_workspace = os.path.join(parent_job.workspace_path, "jobs", _gen_id("job"))
        sub_job = self.create_job(
            objective=sub_def.objective,
            workflow=sub_def.workflow,
            inputs=parent_run.inputs or {},
            config=sub_def.config or parent_job.config,
            parent_job_id=parent_job.id,
            parent_step_run_id=parent_run.id,
            workspace_path=sub_workspace,
        )
        self.start_job(sub_job.id)
        return self.store.load_job(sub_job.id)

    def _get_job_depth(self, job: Job) -> int:
        depth = 0
        current = job
        while current.parent_job_id:
            depth += 1
            try:
                current = self.store.load_job(current.parent_job_id)
            except KeyError:
                break
        return depth

    def _terminal_output(self, job: Job) -> HandoffEnvelope:
        """Get the output of a job's terminal step."""
        terminal = job.workflow.terminal_steps()
        if not terminal:
            return HandoffEnvelope(artifact={}, sidecar=Sidecar(), workspace=job.workspace_path, timestamp=_now())
        terminal_run = self.store.latest_completed_run(job.id, terminal[0])
        if terminal_run and terminal_run.result:
            return terminal_run.result
        return HandoffEnvelope(artifact={}, sidecar=Sidecar(), workspace=job.workspace_path, timestamp=_now())

    # ── Limits ─────────────────────────────────────────────────────────────

    def _check_limits(self, run: StepRun, step_def: StepDefinition) -> dict | None:
        """Check if a running step has exceeded its limits.
        Returns {"message": str, "category": str} if exceeded, else None.
        """
        limits = step_def.limits
        if not limits:
            return None

        # Duration limit
        if limits.max_duration_minutes and run.started_at:
            elapsed = (_now() - run.started_at).total_seconds()
            if elapsed > limits.max_duration_minutes * 60:
                self._emit(run.job_id, STEP_LIMIT_EXCEEDED, {
                    "step": run.step_name,
                    "limit_type": "duration",
                    "limit_value": limits.max_duration_minutes,
                    "actual_value": elapsed / 60,
                })
                return {
                    "message": f"Duration limit exceeded: {elapsed / 60:.1f}m > {limits.max_duration_minutes}m",
                    "category": "timeout",
                }

        # Cost limit
        if limits.max_cost_usd:
            cost = self.store.accumulated_cost(run.id)
            if cost > limits.max_cost_usd:
                self._emit(run.job_id, STEP_LIMIT_EXCEEDED, {
                    "step": run.step_name,
                    "limit_type": "cost",
                    "limit_value": limits.max_cost_usd,
                    "actual_value": cost,
                })
                return {
                    "message": f"Cost limit exceeded: ${cost:.4f} > ${limits.max_cost_usd}",
                    "category": "cost_limit",
                }

        return None

    # ── Failure Handling ──────────────────────────────────────────────────

    def _fail_run(self, job: Job, run: StepRun, step_def: StepDefinition,
                  error: str, error_category: str | None = None) -> None:
        """Fail a step run and evaluate exit rules for error routing.
        If no exit rule handles the failure, halt the job.
        """
        run.status = StepRunStatus.FAILED
        run.error = error
        run.error_category = error_category
        run.result = HandoffEnvelope(
            artifact={"error_category": error_category} if error_category else {},
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        )
        run.completed_at = _now()
        self.store.save_run(run)

        self._emit(job.id, STEP_FAILED, {
            "step": run.step_name,
            "attempt": run.attempt,
            "error": error,
            "error_category": error_category,
        })

        # Try exit rules for failure routing (M4: exit rules can handle errors)
        if step_def.exit_rules:
            artifact = run.result.artifact if run.result else {}
            sorted_rules = sorted(step_def.exit_rules, key=lambda r: r.priority, reverse=True)
            for rule in sorted_rules:
                if self._evaluate_rule(rule, artifact, attempt=run.attempt):
                    action = rule.config.get("action", "advance")
                    self._emit(job.id, EXIT_RESOLVED, {
                        "step": run.step_name,
                        "rule": rule.name,
                        "action": action,
                        "on_failure": True,
                    })
                    match action:
                        case "loop":
                            target = rule.config.get("target", run.step_name)
                            max_iterations = rule.config.get("max_iterations")
                            if max_iterations is not None:
                                completed_count = self.store.completed_run_count(job.id, target)
                                if completed_count >= max_iterations:
                                    self._halt_job(job, run)
                                    return
                            self._emit(job.id, LOOP_ITERATION, {
                                "step": run.step_name, "target": target,
                                "count": self.store.completed_run_count(job.id, target),
                            })
                            job = self.store.load_job(job.id)
                            if job.status == JobStatus.RUNNING:
                                self._launch(job, target)
                            return
                        case "escalate":
                            job.status = JobStatus.PAUSED
                            job.updated_at = _now()
                            self.store.save_job(job)
                            self._emit(job.id, JOB_PAUSED, {
                                "reason": "escalated",
                                "step": run.step_name,
                                "rule": rule.name,
                            })
                            return
                        case "abandon":
                            job.status = JobStatus.FAILED
                            job.updated_at = _now()
                            self.store.save_job(job)
                            self._emit(job.id, JOB_FAILED, {
                                "reason": "abandoned",
                                "step": run.step_name,
                                "rule": rule.name,
                            })
                            return
                        case "advance":
                            return  # Move past the failure

        # No exit rule handled the failure — halt the job
        self._halt_job(job, run)

    # ── Halt ──────────────────────────────────────────────────────────────

    def _halt_job(self, job: Job, run: StepRun) -> None:
        """Halt job on step failure."""
        job.status = JobStatus.FAILED
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job.id, JOB_FAILED, {
            "reason": "step_failed",
            "step": run.step_name,
            "error": run.error,
        })

    # ── Events ────────────────────────────────────────────────────────────

    def _emit(self, job_id: str, event_type: str, data: dict | None = None, is_effector: bool = False) -> None:
        event = Event(
            id=_gen_id("evt"),
            job_id=job_id,
            timestamp=_now(),
            type=event_type,
            data=data or {},
            is_effector=is_effector,
        )
        self.store.save_event(event)

    def _emit_effector_events(self, job_id: str, envelope: HandoffEnvelope | None) -> None:
        """Check executor_meta for effector_events and emit them."""
        if not envelope or not envelope.executor_meta:
            return
        effector_events = envelope.executor_meta.get("effector_events")
        if not effector_events:
            return
        for evt_data in effector_events:
            self._emit(
                job_id,
                evt_data.get("type", "effector.action"),
                evt_data.get("data", {}),
                is_effector=True,
            )
