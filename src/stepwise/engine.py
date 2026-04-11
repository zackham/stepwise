# test job staging
"""Tick-based workflow engine: readiness, currentness, launching, exit resolution."""

from __future__ import annotations

import copy
import json
import logging
import os
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from stepwise.events import (
    CONTEXT_INJECTED,
    EXIT_RESOLVED,
    FOR_EACH_COMPLETED,
    FOR_EACH_STARTED,
    EXTERNAL_RERUN,
    JOB_APPROVED,
    JOB_AWAITING_APPROVAL,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_QUEUED,
    JOB_RESUMED,
    JOB_STARTED,
    LOOP_ITERATION,
    LOOP_MAX_REACHED,
    STEP_COMPLETED,
    STEP_DELEGATED,
    STEP_FAILED,
    STEP_LIMIT_EXCEEDED,
    STEP_SKIPPED,
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
from string import Template

from stepwise.models import (
    Event,
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    Job,
    JobConfig,
    JobStatus,
    OutputFieldSpec,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    SubJobDefinition,
    WatchSpec,
    WorkflowDefinition,
    _gen_id,
    _now,
    validate_job_metadata,
)
from stepwise.hooks import build_event_envelope, fire_hook_for_event, fire_notify_webhook
from stepwise.store import SQLiteStore

_engine_logger = logging.getLogger("stepwise.engine")

# Default max artifact size (5 MB). Prevents runaway outputs from bloating the DB.
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


@dataclass
class SessionState:
    """Per-job tracking of named session lifecycle."""
    name: str
    session_id: str | None = None
    is_forked: bool = False
    agent: str = "claude"
    created: bool = False


# Config keys whose values are passed to subprocess.run(shell=True).
# Values interpolated into these keys must be shell-quoted.
_SHELL_COMMAND_KEYS = frozenset({"command", "check_command"})


def _interpolate_config(config: dict, inputs: dict) -> dict:
    """Substitute $variable references in executor config string values.

    Supports dotted access for dict inputs: if inputs has reviewer={model: "x"},
    then $reviewer.model resolves to "x" in config values.

    Values substituted into shell-executed fields (command, check_command) are
    automatically shell-quoted via shlex.quote() to prevent injection.
    """
    str_inputs = {}
    for k, v in inputs.items():
        if isinstance(v, str):
            str_inputs[k] = v
        elif isinstance(v, (dict, list)):
            str_inputs[k] = json.dumps(v, indent=2)
            # Flatten dict fields for dotted access: $var.field
            if isinstance(v, dict):
                for fk, fv in v.items():
                    flat_key = f"{k}.{fk}"
                    if isinstance(fv, str):
                        str_inputs[flat_key] = fv
                    elif fv is None:
                        str_inputs[flat_key] = ""
                    elif isinstance(fv, (dict, list)):
                        str_inputs[flat_key] = json.dumps(fv, indent=2)
                    else:
                        str_inputs[flat_key] = str(fv)
        else:
            str_inputs[k] = str(v)
    if not str_inputs:
        return config
    # Build a shell-safe copy for command fields
    quoted_inputs = {k: shlex.quote(v) for k, v in str_inputs.items()}
    result = {}
    changed = False
    for k, v in config.items():
        if isinstance(v, str) and "$" in v:
            # Use quoted values for shell-executed fields, raw for others
            effective = quoted_inputs if k in _SHELL_COMMAND_KEYS else str_inputs
            # First pass: replace dotted vars ($var.field) before Template,
            # since Template only handles simple $var names.
            new_v = v
            for sk in sorted(effective, key=len, reverse=True):
                if "." in sk and ("$" + sk) in new_v:
                    new_v = new_v.replace("$" + sk, effective[sk])
            new_v = Template(new_v).safe_substitute(effective)
            if new_v != v:
                changed = True
            result[k] = new_v
        else:
            result[k] = v
    return result if changed else config


class Engine:
    """Workflow engine base class. Business logic for readiness, exit rules, input resolution, and currentness. AsyncEngine subclass adds event-driven async execution."""

    def __init__(
        self,
        store: SQLiteStore,
        registry: ExecutorRegistry | None = None,
        jobs_dir: str | None = None,
        project_dir: Path | None = None,
        billing_mode: str = "subscription",
        config: object | None = None,
        cache: "StepResultCache | None" = None,
    ) -> None:
        self.store = store
        self.registry = registry or ExecutorRegistry()
        self.jobs_dir = jobs_dir or "jobs"
        self.project_dir = project_dir  # .stepwise/ dir for hooks
        self.billing_mode = billing_mode  # "subscription" | "api_key"
        self.config = config  # StepwiseConfig — used for emit_flow instructions
        self.cache = cache  # step result cache (optional)
        self.on_event: Callable[[dict], None] | None = None
        self._injected_contexts: dict[str, list[str]] = {}  # job_id -> contexts
        self._rerun_steps: dict[str, set[str]] = {}  # job_id -> step names to bypass cache
        self._session_registries: dict[str, dict[str, SessionState]] = {}

    # ── Named Session Registry ──────────────────────────────────────────

    def _build_session_registry(self, job: Job) -> dict[str, SessionState]:
        """Build named session registry from workflow step definitions.

        Under step-name fork_from semantics, the session-level state only
        tracks whether *any* writer on the session declares fork_from
        (the per-step "fork from which step" is read from
        ``step_def.fork_from`` directly at the launch site).
        """
        registry: dict[str, SessionState] = {}
        for step_name, step_def in job.workflow.steps.items():
            if not step_def.session:
                continue
            session_name = step_def.session
            if session_name not in registry:
                agent = "claude"
                if step_def.executor and step_def.executor.config:
                    agent = step_def.executor.config.get("agent", "claude")
                registry[session_name] = SessionState(name=session_name, agent=agent)
            if step_def.fork_from:
                registry[session_name].is_forked = True
        return registry

    def _ensure_session_registry(self, job: Job) -> None:
        if job.id not in self._session_registries:
            self._session_registries[job.id] = self._build_session_registry(job)

    # ── LoopFrame stack helpers (§11.5 / step 7 decision 6) ─────────────

    def _loop_owner_targets(self, job: Job) -> list[tuple[str, str]]:
        """Return all (owner_step, target_step) loop pairs in the flow."""
        out: list[tuple[str, str]] = []
        for owner_name, owner_def in job.workflow.steps.items():
            for rule in owner_def.exit_rules:
                if rule.config.get("action") == "loop":
                    target = rule.config.get("target")
                    if target and target in job.workflow.steps:
                        out.append((owner_name, target))
        return out

    def _parent_frame_for_target(
        self, job: Job, target: str,
    ) -> str | None:
        """Find the parent loop frame's frame_id for a new loop on `target`.

        Walks the LoopFrame stack: the parent is the loop frame whose
        body contains `target` (i.e., target is a step that lives inside
        an outer loop's iteration window). Determined by searching the
        loop_owner_targets list — if there's another loop pair (outer,
        outer_target) such that target is forward-reachable from
        outer_target in the static workflow graph, that outer loop is a
        candidate. Returns the innermost candidate or None.
        """
        from stepwise.models import collect_loop_back_edges  # lazy

        loop_pairs = self._loop_owner_targets(job)
        if not loop_pairs:
            return None

        # Build forward adjacency over the workflow steps (excluding back-edges).
        # If target is forward-reachable from some other loop's target via
        # non-back-edge edges, that loop is an enclosing parent.
        steps = job.workflow.steps
        back_edges = collect_loop_back_edges(steps)
        fwd: dict[str, set[str]] = {n: set() for n in steps}
        for cname, cdef in steps.items():
            for b in cdef.inputs:
                if b.is_back_edge:
                    continue
                if b.any_of_sources:
                    for src, _f in b.any_of_sources:
                        if src in steps and src != cname and (cname, src) not in back_edges:
                            fwd[src].add(cname)
                elif b.source_step and b.source_step != "$job" and b.source_step in steps:
                    if b.source_step != cname and (cname, b.source_step) not in back_edges:
                        fwd[b.source_step].add(cname)
            for s in cdef.after:
                if s in steps and s != cname:
                    fwd[s].add(cname)

        def reachable(start: str, end: str) -> bool:
            if start == end:
                return True
            seen = {start}
            stack = [start]
            while stack:
                cur = stack.pop()
                for nxt in fwd.get(cur, ()):
                    if nxt == end:
                        return True
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return False

        # Candidates: loop targets (other than `target`) such that `target`
        # is forward-reachable from them.
        candidates: list[str] = []
        for (_owner, t) in loop_pairs:
            if t == target:
                continue
            if reachable(t, target):
                candidates.append(t)
        if not candidates:
            return None
        # Innermost: the candidate that has the FEWEST other candidates as
        # ancestors (i.e., it's the deepest in the nesting). For two-level
        # nesting this is unambiguous.
        if len(candidates) == 1:
            return candidates[0]
        # Pick the candidate that is reachable from every other candidate
        # (the "deepest" one). Sort: an innermost candidate is forward-
        # reachable from every other candidate.
        best = candidates[0]
        for c in candidates[1:]:
            # If `c` is reachable from `best`, then c is deeper.
            if reachable(best, c):
                best = c
        return best

    def _get_or_create_loop_frame(
        self, job: Job, frame_id: str, parent_frame_id: str | None = None,
    ) -> "LoopFrame":
        """Lazily allocate or look up the LoopFrame for `frame_id`.

        Idempotent: repeated calls with the same frame_id return the same
        frame. The parent_frame_id is set only on creation; calling with a
        mismatched parent_frame_id later emits a warning but does not
        mutate the existing frame.
        """
        from stepwise.models import LoopFrame
        existing = job.loop_frames.get(frame_id)
        if existing is not None:
            if (
                parent_frame_id is not None
                and existing.parent_frame_id is not None
                and existing.parent_frame_id != parent_frame_id
            ):
                _engine_logger.warning(
                    "loop frame %r already exists with parent %r; "
                    "ignoring requested parent %r",
                    frame_id, existing.parent_frame_id, parent_frame_id,
                )
            return existing
        frame = LoopFrame(
            frame_id=frame_id,
            iteration_index=0,
            parent_frame_id=parent_frame_id,
            presence={},
        )
        job.loop_frames[frame_id] = frame
        return frame

    def _invalidate_child_frames(self, job: Job, parent_frame_id: str) -> None:
        """Remove (or reset) all loop frames whose parent is `parent_frame_id`.

        Per §11.5: when an outer frame's iteration_index increments, every
        child frame is reset because the inner loop scope is logically
        re-entered for each outer iteration.
        """
        # Remove direct children. Recursive: any grandchild whose own
        # parent is being removed should also go.
        to_remove: set[str] = set()
        # BFS over the parent → children relation
        queue: list[str] = [parent_frame_id]
        while queue:
            cur = queue.pop(0)
            for fid, f in list(job.loop_frames.items()):
                if f.parent_frame_id == cur and fid not in to_remove:
                    to_remove.add(fid)
                    queue.append(fid)
        for fid in to_remove:
            job.loop_frames.pop(fid, None)

    def _rebuild_loop_frames(self, job: Job) -> None:
        """Crash recovery: rebuild Job.loop_frames from step_runs.

        Walks the job's step_runs and reconstructs LoopFrame state for
        each loop initiator step (a step that is targeted by some other
        step's `loop` exit rule). The frame's iteration_index is set to
        the count of completed runs of that step. Parent_frame_id is
        derived from the static workflow nesting.
        """
        from stepwise.models import LoopFrame
        loop_pairs = self._loop_owner_targets(job)
        if not loop_pairs:
            return
        # Loop initiators (= the targets of the loop pairs)
        initiators: set[str] = {target for _owner, target in loop_pairs}
        rebuilt: dict[str, LoopFrame] = {}
        for init in initiators:
            count = self.store.completed_run_count(job.id, init)
            if count <= 1:
                # Either never run, or exactly one run = iteration 1 (no
                # relaunches yet). Frame iteration_index is the count.
                iteration = max(count, 0)
            else:
                iteration = count
            parent = self._parent_frame_for_target(job, init)
            rebuilt[init] = LoopFrame(
                frame_id=init,
                iteration_index=iteration,
                parent_frame_id=parent,
                presence={},  # presence is recomputed at next _resolve_inputs call
            )
        # Replace the in-memory frames with the rebuilt set; persist.
        job.loop_frames = rebuilt
        try:
            self.store.save_job(job)
        except Exception:
            # Best-effort; recovery should not fail loudly here.
            _engine_logger.warning(
                "failed to persist rebuilt loop_frames for job %s",
                job.id, exc_info=True,
            )

    # ── Fork source / snapshot UUID lookup (§9 / step 6) ────────────────

    def _fork_source_step_names(self, job: Job) -> set[str]:
        """Return the set of step names that are fork sources.

        Under step-name fork_from semantics (design doc §8.2), a step is a
        fork source iff some other step in the flow declares
        ``fork_from: <this step name>``. These steps get the §9.3
        post-exit synchronous lifecycle: subprocess exit → lock acquire →
        snapshot → metadata commit → lock release.

        Per §9.7.3, steps whose ``_session`` virtual output is consumed
        by any input binding are also fork sources (the ``_session``
        reference implies the consumer will fork from the snapshot).
        """
        sources: set[str] = set()
        for s in job.workflow.steps.values():
            if s.fork_from and not s.fork_from.startswith("$job."):
                sources.add(s.fork_from)
        # §9.7.3: steps whose _session output is consumed by input bindings
        for s in job.workflow.steps.values():
            for b in s.inputs:
                if b.any_of_sources:
                    for src_step, src_field in b.any_of_sources:
                        if src_field == "_session" and src_step in job.workflow.steps:
                            sources.add(src_step)
                elif b.source_field == "_session" and b.source_step and b.source_step != "$job":
                    if b.source_step in job.workflow.steps:
                        sources.add(b.source_step)
        return sources

    def _lookup_snapshot_uuid(self, job: Job, parent_step_name: str) -> str | None:
        """Look up the snapshot UUID persisted on a parent step's run.

        Under step-name fork_from semantics (§8.2), the parameter is the
        STEP name named in some downstream step's ``fork_from``. The
        snapshot UUID lives on the parent step's latest completed run's
        ``executor_state["snapshot_uuid"]``. Downstream forks use this
        UUID via ``claude --resume <snap_uuid> --fork-session`` to avoid
        the §9.1 live-tail race.

        Returns None if no completed run exists or no snapshot_uuid was
        persisted on it.
        """
        latest = self.store.latest_completed_run(job.id, parent_step_name)
        if not latest or not latest.executor_state:
            return None
        return latest.executor_state.get("snapshot_uuid")

    def _maybe_snapshot_for_fork_source(
        self, job: Job, run: StepRun, step_name: str,
    ) -> None:
        """If `step_name` is a fork source, snapshot its session inside the lock.

        Per §9.3 of the coordination doc. Acquires an exclusive lock on the
        live session UUID, copies the session JSON to a fresh snapshot
        filename, persists the snapshot UUID on the run's executor_state,
        and re-saves the run inside the lock so observers see the
        snapshot_uuid landed atomically with the COMPLETED status.

        On snapshot failure, logs and proceeds without raising — the lack
        of a snapshot_uuid on a fork-source RUNNING run is what triggers
        re-execution at recovery time. Acceptable v1.0 limitation per §9.3
        (duplicate-turn risk on retry after crash).
        """
        fork_sources = self._fork_source_step_names(job)
        if step_name not in fork_sources:
            return

        state = self._session_registries.get(job.id, {}).get(
            run.step_name and job.workflow.steps[step_name].session or ""
        )
        # Robust fallback: re-fetch via step_def
        if state is None:
            step_def = job.workflow.steps.get(step_name)
            if step_def and step_def.session and job.id in self._session_registries:
                state = self._session_registries[job.id].get(step_def.session)

        if state is None or not state.session_id:
            _engine_logger.warning(
                "fork-source step %r completed without a captured session_id; "
                "snapshot skipped (fork would fall back to live session_id)",
                step_name,
            )
            return

        # Resolve the working_dir for this step. Claude stores sessions per
        # project under ~/.claude/projects/<slug>/, so the snapshot path
        # depends on which project this step ran against.
        step_def = job.workflow.steps.get(step_name)
        working_dir = None
        if step_def and step_def.executor and step_def.executor.config:
            working_dir = step_def.executor.config.get("working_dir")
        if not working_dir:
            working_dir = (run.executor_state or {}).get("working_dir")
        if not working_dir:
            _engine_logger.warning(
                "fork-source step %r has no resolvable working_dir; "
                "snapshot skipped (fork would fall back to live UUID)",
                step_name,
            )
            return

        try:
            from stepwise.snapshot import snapshot_session
            from stepwise.session_lock import SessionLock
            with SessionLock(state.session_id, working_dir, "exclusive"):
                snap_uuid = snapshot_session(state.session_id, working_dir)
                run.executor_state = {
                    **(run.executor_state or {}),
                    "snapshot_uuid": snap_uuid,
                    "snapshot_working_dir": str(working_dir),
                }
                # Re-save inside the lock so observers see the snapshot
                # uuid landed at the same instant the lock is released.
                self.store.save_run(run)
            _engine_logger.info(
                "snapshotted session %s -> %s for fork-source step %r",
                state.session_id, snap_uuid, step_name,
            )
        except Exception as exc:
            # Acceptable v1.0 wart per §9.3: if the snapshot fails, leave
            # the step in a recoverable state. Re-execution on restart
            # will retry the snapshot. The lack of a snapshot_uuid is what
            # triggers re-exec in _recover_fork_source_steps_without_snapshot.
            _engine_logger.error(
                "snapshot failed for fork-source step %r: %s",
                step_name, exc, exc_info=True,
            )

    # ── Cross-Job Data Wiring ────────────────────────────────────────────

    def _process_job_ref_inputs(self, job: Job) -> list[str]:
        """Validate $job_ref inputs and return list of referenced job IDs.

        Raises ValueError if a referenced job doesn't exist.
        """
        ref_job_ids = []
        for key, value in job.inputs.items():
            if isinstance(value, dict) and "$job_ref" in value:
                ref_id = value["$job_ref"]
                try:
                    self.store.load_job(ref_id)
                except KeyError:
                    raise ValueError(f"Referenced job not found: {ref_id} (from input '{key}')")
                ref_job_ids.append(ref_id)
        return ref_job_ids

    def _resolve_job_ref_inputs(self, job: Job) -> None:
        """Resolve all $job_ref inputs to actual values. Mutates job.inputs in place.

        Raises ValueError if a referenced job is not COMPLETED.
        """
        for key, value in list(job.inputs.items()):
            if isinstance(value, dict) and "$job_ref" in value:
                ref_id = value["$job_ref"]
                field_path = value["field"]
                ref_job = self.store.load_job(ref_id)
                if ref_job.status != JobStatus.COMPLETED:
                    raise ValueError(
                        f"Referenced job {ref_id} is {ref_job.status.value}, expected COMPLETED"
                    )
                resolved, found = self.store.get_job_output_field(ref_id, field_path)
                if not found:
                    _engine_logger.warning(
                        "Job %s input '%s': field '%s' not found in job %s outputs, resolving to None",
                        job.id, key, field_path, ref_id,
                    )
                job.inputs[key] = resolved

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
        name: str | None = None,
        metadata: dict | None = None,
    ) -> Job:
        errors = workflow.validate()
        if errors:
            raise ValueError(f"Invalid workflow: {'; '.join(errors)}")

        job_id = _gen_id("job")
        ws = workspace_path or os.path.join(self.jobs_dir, job_id, "workspace")

        # Metadata: validate, then auto-populate sys fields
        metadata = copy.deepcopy(metadata) if metadata else {"sys": {}, "app": {}}
        metadata.setdefault("sys", {})
        metadata.setdefault("app", {})
        validate_job_metadata(metadata)

        # Auto-populate sys.depth and sys.root_job_id
        parent_meta_job_id = metadata["sys"].get("parent_job_id") or parent_job_id
        if parent_meta_job_id:
            try:
                parent_job = self.store.load_job(parent_meta_job_id)
                parent_depth = parent_job.metadata["sys"].get("depth", 0)
                metadata["sys"]["depth"] = parent_depth + 1
                metadata["sys"]["root_job_id"] = parent_job.metadata["sys"].get(
                    "root_job_id", parent_job.id
                )
            except KeyError:
                metadata["sys"]["depth"] = 0
                metadata["sys"]["root_job_id"] = job_id
        else:
            metadata["sys"]["depth"] = 0
            metadata["sys"]["root_job_id"] = job_id

        if metadata["sys"]["depth"] > 10:
            raise ValueError(
                f"Job depth {metadata['sys']['depth']} exceeds maximum of 10"
            )

        job = Job(
            id=job_id,
            objective=objective,
            name=name,
            workflow=workflow,
            status=JobStatus.PENDING,
            inputs=inputs or {},
            parent_job_id=parent_job_id,
            parent_step_run_id=parent_step_run_id,
            workspace_path=ws,
            config=config or JobConfig(),
            metadata=metadata,
        )

        # Validate $job_ref inputs before saving
        ref_job_ids = self._process_job_ref_inputs(job)

        self.store.save_job(job)

        # Auto-add dependency edges for referenced jobs
        for ref_id in ref_job_ids:
            if self.store.would_create_cycle(job.id, ref_id):
                # Roll back: delete the job we just saved
                self.store.delete_job(job.id)
                raise ValueError(
                    f"Adding dependency on {ref_id} would create a cycle"
                )
            self.store.add_job_dependency(job.id, ref_id)

        return job

    def start_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        if job.status != JobStatus.PENDING:
            raise ValueError(f"Cannot start job in status {job.status.value}")
        # Resolve cross-job data references before running
        self._resolve_job_ref_inputs(job)
        # Build named session registry
        self._ensure_session_registry(job)
        # Extract rerun_steps from job metadata
        rerun = job.config.metadata.get("rerun_steps", [])
        if rerun:
            self._rerun_steps[job_id] = set(rerun)
        # Atomic status transition: only set RUNNING if still PENDING
        updated = self.store.atomic_status_transition(
            job_id, from_status=JobStatus.PENDING, to_status=JobStatus.RUNNING
        )
        if not updated:
            return  # Job was cancelled/modified between load and start
        self._emit(job_id, JOB_STARTED)
        # Run initial tick
        self.tick()

    def pause_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        if job.status != JobStatus.RUNNING:
            raise ValueError(f"Cannot pause job in status {job.status.value}")

        # Cancel running subprocesses and suspend their runs
        for run in self.store.running_runs(job_id):
            step_def = job.workflow.steps.get(run.step_name)
            if step_def:
                try:
                    executor = self.registry.create(step_def.executor)
                    executor.cancel(run.executor_state or {})
                except Exception:
                    _engine_logger.warning(
                        "Failed to cancel executor for step %s (run %s)",
                        run.step_name, run.id, exc_info=True,
                    )
            run.status = StepRunStatus.SUSPENDED
            run.completed_at = _now()
            self.store.save_run(run)

        job.status = JobStatus.PAUSED
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job_id, JOB_PAUSED)

    def approve_job(self, job_id: str) -> None:
        """Approve job: AWAITING_APPROVAL → PENDING."""
        self.store.transition_job_to_approved(job_id)
        self._emit(job_id, JOB_APPROVED)

    def resume_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        resumable = {JobStatus.PAUSED, JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.FAILED}
        if job.status not in resumable:
            raise ValueError(f"Cannot resume job in status {job.status.value}")
        job.status = JobStatus.RUNNING
        job.updated_at = _now()
        self.store.save_job(job)
        self._prepare_suspended_runs_for_resume(job_id)
        self._emit(job_id, JOB_RESUMED)
        self.tick()

    def cancel_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)

        # Cancel all active runs
        for run in self.store.running_runs(job_id):
            step_def = job.workflow.steps.get(run.step_name)
            if step_def:
                state = run.executor_state or {}
                if not state.get("pid") and not state.get("pgid"):
                    _engine_logger.warning(
                        "No pid/pgid in executor_state for step %s (run %s) "
                        "— subprocess may not be killed",
                        run.step_name, run.id,
                    )
                try:
                    executor = self.registry.create(step_def.executor)
                    executor.cancel(state)
                except Exception:
                    _engine_logger.warning(
                        "Failed to cancel executor for step %s (run %s)",
                        run.step_name, run.id, exc_info=True,
                    )
            run.status = StepRunStatus.CANCELLED
            run.error = "Job cancelled"
            run.pid = None
            run.completed_at = _now()
            self.store.save_run(run)

        for run in self.store.suspended_runs(job_id):
            run.status = StepRunStatus.CANCELLED
            run.error = "Job cancelled"
            run.pid = None
            run.completed_at = _now()
            self.store.save_run(run)

        for run in self.store.delegated_runs(job_id):
            run.status = StepRunStatus.CANCELLED
            run.error = "Job cancelled"
            run.pid = None
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
        self._cleanup_job_sessions(job.id, job)

    def _prepare_suspended_runs_for_resume(self, job_id: str) -> None:
        """Clear pause-only suspended runs so resume can relaunch them."""
        for run in self.store.suspended_runs(job_id):
            if run.watch:
                continue
            run.status = StepRunStatus.CANCELLED
            run.error = "Paused and restarted on resume"
            run.pid = None
            if not run.completed_at:
                run.completed_at = _now()
            self.store.save_run(run)

    def _cleanup_run_executor(self, job: Job, run: StepRun) -> None:
        """Best-effort cleanup for executor-owned resources from a run."""
        state = run.executor_state or {}
        if not any(state.get(key) for key in ("pid", "pgid", "session_id", "session_name")):
            return

        step_def = job.workflow.steps.get(run.step_name)
        if step_def is None:
            return

        try:
            executor = self.registry.create(step_def.executor)
            executor.cancel(state)
        except Exception:
            _engine_logger.warning(
                "Failed to clean up executor resources for step %s (run %s)",
                run.step_name, run.id, exc_info=True,
            )

    def _collect_descendant_job_ids(self, job_id: str) -> list[str]:
        """Collect transitive descendant job IDs created under a job."""
        descendants: list[str] = []
        seen: set[str] = set()
        queue = [job_id]

        while queue:
            current_id = queue.pop(0)
            child_ids: set[str] = set()

            for child in self.store.child_jobs(current_id):
                child_ids.add(child.id)

            for run in self.store.runs_for_job(current_id):
                if run.sub_job_id:
                    child_ids.add(run.sub_job_id)
                child_ids.update((run.executor_state or {}).get("sub_job_ids", []))

            for child_id in child_ids:
                if child_id == job_id or child_id in seen:
                    continue
                seen.add(child_id)
                descendants.append(child_id)
                queue.append(child_id)

        return descendants

    def reset_job(self, job_id: str) -> None:
        """Clear a job's runtime history and return it to PENDING."""
        job = self.store.load_job(job_id)

        for descendant_id in reversed(self._collect_descendant_job_ids(job_id)):
            try:
                descendant = self.store.load_job(descendant_id)
            except KeyError:
                continue
            for run in self.store.runs_for_job(descendant_id):
                self._cleanup_run_executor(descendant, run)
            self.store.delete_job(descendant_id)
            self._injected_contexts.pop(descendant_id, None)
            self._rerun_steps.pop(descendant_id, None)

        for run in self.store.runs_for_job(job_id):
            self._cleanup_run_executor(job, run)

        self.store.reset_job(job_id)
        self._injected_contexts.pop(job_id, None)
        self._rerun_steps.pop(job_id, None)

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

        if latest:
            self._cleanup_run_executor(job, latest)

        self._emit(job_id, EXTERNAL_RERUN, {"step": step_name})

        # Make sure job is running
        if job.status in (
            JobStatus.PAUSED,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ):
            job.status = JobStatus.RUNNING
            job.updated_at = _now()
            self.store.save_job(job)

        # Launch directly — this is the external API, synchronous launch.
        # The new run supersedes any existing completed run.
        run = self._launch(job, step_name)
        return run

    @staticmethod
    def _validate_fulfill_payload(
        payload: dict, schema: dict[str, dict],
    ) -> tuple[dict, list[str]]:
        """Validate and coerce a fulfillment payload against an output schema.

        Returns (coerced_payload, errors).
        """
        coerced = dict(payload)
        errors: list[str] = []

        for field_name, spec_dict in schema.items():
            spec = OutputFieldSpec.from_dict(spec_dict)
            value = coerced.get(field_name)

            # Handle missing/None
            if value is None or (isinstance(value, str) and not value.strip()):
                if not spec.required:
                    if spec.default is not None:
                        coerced[field_name] = spec.default
                    elif field_name in coerced:
                        del coerced[field_name]
                    continue
                # required fields checked by the key-presence loop below
                continue

            # Type coercion/validation
            if spec.type == "number":
                try:
                    num = float(value)
                    if spec.min is not None and num < spec.min:
                        errors.append(f"Field '{field_name}': value {num} below minimum {spec.min}")
                    if spec.max is not None and num > spec.max:
                        errors.append(f"Field '{field_name}': value {num} above maximum {spec.max}")
                    coerced[field_name] = num
                except (ValueError, TypeError):
                    errors.append(f"Field '{field_name}': expected a number, got {value!r}")

            elif spec.type == "bool":
                if isinstance(value, bool):
                    coerced[field_name] = value
                elif isinstance(value, str):
                    lower = value.strip().lower()
                    if lower in ("true", "yes", "1"):
                        coerced[field_name] = True
                    elif lower in ("false", "no", "0"):
                        coerced[field_name] = False
                    else:
                        errors.append(f"Field '{field_name}': expected bool, got {value!r}")
                else:
                    errors.append(f"Field '{field_name}': expected bool, got {value!r}")

            elif spec.type == "choice":
                if spec.multiple:
                    if not isinstance(value, list):
                        errors.append(f"Field '{field_name}': expected a list for multi-select choice")
                    elif spec.options:
                        invalid = [v for v in value if v not in spec.options]
                        if invalid:
                            errors.append(
                                f"Field '{field_name}': invalid choice(s) {invalid}. "
                                f"Valid: {spec.options}"
                            )
                else:
                    if spec.options and value not in spec.options:
                        errors.append(
                            f"Field '{field_name}': invalid choice {value!r}. "
                            f"Valid: {spec.options}"
                        )

            # str and text: accept anything stringable
            elif spec.type in ("str", "text"):
                if not isinstance(value, str):
                    coerced[field_name] = str(value)

        return coerced, errors

    def fulfill_watch(self, run_id: str, payload: dict) -> dict | None:
        """Complete a suspended step's watch with the provided payload.

        Returns None on success. Returns error dict if already fulfilled
        (idempotent — does not corrupt state on double-fulfill).
        """
        run = self.store.load_run(run_id)
        if run.status != StepRunStatus.SUSPENDED:
            # Idempotent: if already completed, return structured error instead of raising
            if run.status == StepRunStatus.COMPLETED:
                job = self.store.load_job(run.job_id)
                return {
                    "error": "already_fulfilled",
                    "run_id": run_id,
                    "fulfilled_at": run.completed_at.isoformat() if run.completed_at else None,
                    "job_id": run.job_id,
                    "job_status": job.status.value,
                }
            raise ValueError(f"Run {run_id} is not suspended (status: {run.status.value})")
        if not run.watch:
            raise ValueError(f"Run {run_id} has no watch spec")

        schema = run.watch.output_schema or {}

        # Validate payload has required fulfillment_outputs
        for field in run.watch.fulfillment_outputs:
            # Skip required-check for optional fields
            field_spec = schema.get(field, {})
            if not field_spec.get("required", True):
                continue
            if field not in payload or (isinstance(payload.get(field), str) and not payload[field].strip()):
                raise ValueError(
                    f"Payload missing required field '{field}' "
                    f"(expected: {run.watch.fulfillment_outputs})"
                )

        # Validate typed fields if schema exists
        if schema:
            payload, validation_errors = self._validate_fulfill_payload(payload, schema)
            if validation_errors:
                raise ValueError(
                    "Payload validation failed: " + "; ".join(validation_errors)
                )

        job = self.store.load_job(run.job_id)
        step_def = job.workflow.steps.get(run.step_name)

        # Create result from payload
        run.result = HandoffEnvelope(
            artifact=payload,
            sidecar=Sidecar(),
            workspace=job.workspace_path,
            timestamp=_now(),
        )

        # Apply derived outputs before completing
        if step_def:
            derived_error = self._apply_derived_outputs(step_def, run.result)
            if derived_error:
                raise ValueError(derived_error)

        run.status = StepRunStatus.COMPLETED
        run.completed_at = _now()
        run.watch = None
        self.store.save_run(run)

        self._emit(run.job_id, WATCH_FULFILLED, {
            "run_id": run_id,
            "mode": "external",
            "payload": payload,
        })

        self._process_completion(job, run)
        return None

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

    def terminal_outputs(self, job_id: str) -> list[dict]:
        """Collect output artifacts from all terminal steps.

        Returns a list of dicts (one per terminal step that completed).
        Used by --output json, the output command, and --wait.
        """
        job = self.store.load_job(job_id)
        terminal_names = job.workflow.terminal_steps()
        outputs: list[dict] = []
        for name in terminal_names:
            run = self.store.latest_completed_run(job_id, name)
            if run and run.result:
                outputs.append(run.result.artifact)
        return outputs

    def completed_outputs(self, job_id: str) -> list[dict]:
        """Collect output artifacts from all completed steps (for partial output on failure)."""
        runs = self.store.runs_for_job(job_id)
        outputs: list[dict] = []
        seen: set[str] = set()
        for run in runs:
            if run.status == StepRunStatus.COMPLETED and run.result and run.step_name not in seen:
                seen.add(run.step_name)
                outputs.append(run.result.artifact)
        return outputs

    def suspended_step_details(self, job_id: str) -> list[dict]:
        """Get details of suspended steps for agent fulfillment."""
        runs = self.store.runs_for_job(job_id)
        suspended: list[dict] = []
        for run in runs:
            if run.status == StepRunStatus.SUSPENDED and run.watch:
                entry: dict = {
                    "run_id": run.id,
                    "step": run.step_name,
                    "prompt": (run.watch.config or {}).get("prompt", ""),
                    "fields": run.watch.fulfillment_outputs,
                }
                if run.watch.output_schema:
                    entry["output_schema"] = run.watch.output_schema
                suspended.append(entry)
        return suspended

    def _run_cost(self, run: StepRun) -> float:
        """Get cost for a single run, checking step_events first, then executor_meta."""
        cost = self.store.accumulated_cost(run.id)
        if cost:
            return cost
        # Fallback: LLMExecutor and AgentExecutor store cost in executor_meta
        if run.result and run.result.executor_meta:
            meta_cost = run.result.executor_meta.get("cost_usd")
            if meta_cost:
                return float(meta_cost)
        return 0.0

    def job_cost(self, job_id: str) -> float:
        """Total accumulated cost across all runs for a job, including sub-jobs."""
        runs = self.store.runs_for_job(job_id)
        total = 0.0
        for run in runs:
            total += self._run_cost(run)
            # Include sub-job costs (for-each, sub-flow delegation)
            es = run.executor_state or {}
            if es.get("for_each"):
                for sub_id in es.get("sub_job_ids", []):
                    try:
                        total += self.job_cost(sub_id)
                    except KeyError:
                        pass
            elif run.sub_job_id:
                try:
                    total += self.job_cost(run.sub_job_id)
                except KeyError:
                    pass
        return total

    def resolved_flow_status(self, job_id: str) -> dict:
        """Full resolved execution DAG with per-step statuses, costs, and metadata.

        Used by `status --output json` to give agents a complete picture of a job.
        """
        job = self.store.load_job(job_id)
        all_runs = self.store.runs_for_job(job_id)

        # Index runs by step name (latest run per step)
        latest_runs: dict[str, StepRun] = {}
        for run in all_runs:
            existing = latest_runs.get(run.step_name)
            if existing is None or run.attempt > existing.attempt:
                latest_runs[run.step_name] = run

        steps: list[dict] = []
        sub_jobs: list[dict] = []

        for step_name, step_def in job.workflow.steps.items():
            run = latest_runs.get(step_name)

            step_info: dict = {
                "name": step_name,
                "type": step_def.executor.type,
            }

            if run:
                step_info["status"] = run.status.value
                step_info["attempt"] = run.attempt
                step_info["cost_usd"] = round(self._run_cost(run), 4)

                if run.status == StepRunStatus.COMPLETED and run.result:
                    step_info["outputs"] = list(run.result.artifact.keys())

                if run.status == StepRunStatus.SUSPENDED and run.watch:
                    step_info["suspended_at"] = run.started_at.isoformat() if run.started_at else None
                    step_info["prompt"] = (run.watch.config or {}).get("prompt", "")
                    step_info["expected_outputs"] = run.watch.fulfillment_outputs
                    step_info["run_id"] = run.id

                if run.status == StepRunStatus.FAILED:
                    step_info["error"] = run.error

                # Track sub-jobs
                if run.sub_job_id:
                    try:
                        sub_job = self.store.load_job(run.sub_job_id)
                        sub_jobs.append({
                            "parent_step": step_name,
                            "job_id": run.sub_job_id,
                            "status": sub_job.status.value,
                        })
                    except KeyError:
                        pass

                # For-each sub-jobs
                es = run.executor_state or {}
                if es.get("for_each"):
                    for i, sid in enumerate(es.get("sub_job_ids", [])):
                        try:
                            sub_job = self.store.load_job(sid)
                            sub_jobs.append({
                                "parent_step": step_name,
                                "index": i,
                                "job_id": sid,
                                "status": sub_job.status.value,
                            })
                        except KeyError:
                            pass

            else:
                step_info["status"] = "pending"
                # Check if throttled: step would be ready but executor is at capacity
                if (hasattr(self, '_executor_limits') and self._executor_limits
                        and self._is_step_ready(job, step_name, step_def)):
                    exec_type = step_def.executor.type
                    limit = self._executor_limits.get(exec_type, 0)
                    if limit > 0:
                        running = self._running_count_for_type(exec_type)
                        if running >= limit:
                            step_info["status"] = "throttled"
                            step_info["throttle_info"] = {
                                "executor_type": exec_type,
                                "running": running,
                                "limit": limit,
                            }
                # Show dependencies for pending/throttled steps
                deps = []
                for binding in step_def.inputs:
                    if binding.source_step and binding.source_step != "$job":
                        if binding.source_step not in deps:
                            deps.append(binding.source_step)
                if deps:
                    step_info["depends_on"] = deps

            steps.append(step_info)

        result = {
            "job_id": job.id,
            "status": job.status.value,
            "flow": job.objective,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "cost_usd": round(self.job_cost(job_id), 4),
            "steps": steps,
            "sub_jobs": sub_jobs,
        }
        if job.metadata != {"sys": {}, "app": {}}:
            result["metadata"] = job.metadata
        return result

    # ── Job dependency readiness ─────────────────────────────────────────

    def _check_dependent_jobs(self, job_id: str) -> None:
        """After a job completes, start any PENDING dependents whose deps are all met."""
        for dep_job in self.store.job_dependents(job_id):
            if dep_job.status != JobStatus.PENDING:
                continue
            # Check if ALL jobs in depends_on are COMPLETED
            all_met = True
            for req_id in dep_job.depends_on:
                try:
                    req_job = self.store.load_job(req_id)
                    if req_job.status != JobStatus.COMPLETED:
                        all_met = False
                        break
                except KeyError:
                    all_met = False
                    break
            if all_met:
                try:
                    self.start_job(dep_job.id)
                except ValueError:
                    pass  # status changed between check and start

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

                            # Apply derived outputs then validate artifact
                            derived_error = self._apply_derived_outputs(step_def, result_envelope)
                            validation_error = derived_error or self._validate_artifact(step_def, result_envelope)
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
                        _engine_logger.warning("Tick loop error", exc_info=True)

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

            # 5. Check job completion / settlement
            job = self.store.load_job(job.id)
            if job.status != JobStatus.RUNNING:
                return
            if self._job_complete(job):
                self._settle_unstarted_steps(job)
                job.status = JobStatus.COMPLETED
                job.updated_at = _now()
                self.store.save_job(job)
                self._emit(job.id, JOB_COMPLETED)
                self._cleanup_job_sessions(job.id, job)
                self._check_dependent_jobs(job.id)
                return

            if not made_progress:
                # Check for settled-but-failed: nothing active, nothing ready
                if (not self.store.running_runs(job.id) and
                        not self.store.suspended_runs(job.id) and
                        not self.store.delegated_runs(job.id)):
                    self._settle_unstarted_steps(job)
                    # Re-check: settlement may have skipped when-blocked steps,
                    # enabling job completion
                    if self._job_complete(job):
                        job.status = JobStatus.COMPLETED
                        job.updated_at = _now()
                        self.store.save_job(job)
                        self._emit(job.id, JOB_COMPLETED)
                        self._cleanup_job_sessions(job.id, job)
                        self._check_dependent_jobs(job.id)
                    else:
                        job.status = JobStatus.FAILED
                        job.updated_at = _now()
                        self.store.save_job(job)
                        self._emit(job.id, JOB_FAILED, {"reason": "no_terminal_reached"})
                        self._cleanup_job_sessions(job.id, job)
                return  # No progress possible, wait for next tick

    # ── Readiness ─────────────────────────────────────────────────────────

    def _find_ready(self, job: Job) -> list[str]:
        """Find steps that are ready to launch."""
        ready = []
        for step_name, step_def in job.workflow.steps.items():
            if self._is_step_ready(job, step_name, step_def):
                ready.append(step_name)
        return ready

    # Central readiness gate — all step launches flow through here
    def _is_step_ready(self, job: Job, step_name: str, step_def: StepDefinition) -> bool:
        """A step is ready when:
        1. No active run exists (running, suspended, delegated)
        2. No current completed run exists (or loop guard prevents re-trigger)
        3. All dep steps have current completed run (any_of: at least one)
        4. No in-flight loop will supersede a dep (loop-aware)
        5. `when` condition (if set) evaluates to True against resolved inputs
        """
        # Check no active run
        latest = self.store.latest_run(job.id, step_name)
        if latest and latest.status in (
            StepRunStatus.RUNNING,
            StepRunStatus.SUSPENDED,
            StepRunStatus.DELEGATED,
        ):
            return False

        # on_error: continue — if this step already failed, it is settled and should not re-run.
        if latest and latest.status == StepRunStatus.FAILED and step_def.on_error == "continue":
            return False

        # If this step FAILED and its exit rules launched a loop target,
        # the failure is "handled" — don't re-launch via _dispatch_ready.
        # Otherwise both the loop target AND a retry of this step run concurrently.
        if latest and latest.status == StepRunStatus.FAILED and step_def.exit_rules:
            for rule in step_def.exit_rules:
                if rule.config.get("action") == "loop":
                    target = rule.config.get("target", step_name)
                    target_latest = self.store.latest_run(job.id, target)
                    if target_latest and target_latest.status in (
                        StepRunStatus.RUNNING, StepRunStatus.SUSPENDED, StepRunStatus.DELEGATED,
                    ):
                        return False  # loop target is in-flight — don't re-launch this step

        # Check no current completed run
        if latest and latest.status == StepRunStatus.COMPLETED:
            if self._is_current(job, latest):
                return False

            # Settled terminal guard: steps without loop exit rules don't
            # relaunch UNLESS a dep has a genuinely newer completed run.
            # Prevents infinite relaunch from cycle invalidation while
            # allowing relaunch when deps produce new output (rerun_step).
            has_loop_exit = any(
                r.config.get("action") == "loop" for r in step_def.exit_rules
            )
            if not has_loop_exit and latest.dep_run_ids:
                has_newer_dep = False
                for dep_step, used_run_id in latest.dep_run_ids.items():
                    dep_latest = self.store.latest_completed_run(job.id, dep_step)
                    if dep_latest and dep_latest.id != used_run_id:
                        has_newer_dep = True
                        break
                if not has_newer_dep:
                    return False

            # Loop guard 1: unconditional loop rules targeting deps
            dep_step_names = set(self._dep_steps(step_def))
            for rule in step_def.exit_rules:
                if rule.config.get("action") == "loop" and rule.type == "always":
                    target = rule.config.get("target", step_name)
                    if target in dep_step_names:
                        return False

            # Loop guard 2: circular dep chains (score→refine→score via any_of).
            # The currentness check walks the cycle and always finds a
            # superseded run, causing infinite relaunch. Block if in cycle.
            # Exception 1: external steps provide genuinely new data (human input)
            # and should always be allowed to relaunch in cycles.
            # Exception 2: if the cycle contains an external step, it acts as
            # a natural gate (suspends for fulfillment), preventing infinite
            # relaunch. Non-external steps in such cycles should re-run.
            if (step_def.executor.type != "external"
                    and self._step_in_dep_cycle(job, step_name)
                    and not self._cycle_has_external_gate(job, step_name)):
                return False

        # Check regular deps (non-any_of, non-$job, non-optional): ALL must have current completed runs
        # (or have failed with on_error: continue, which also unblocks downstream).
        # Step 7 (§11.7): bindings marked is_back_edge=True are treated as
        # permanently settled — they never block readiness, even on iter-1
        # of their closing loop.
        regular_deps: list[str] = [
            b.source_step for b in step_def.inputs
            if not b.any_of_sources and b.source_step != "$job"
            and not b.optional and not b.is_back_edge
        ]
        regular_deps.extend(step_def.after)
        if step_def.for_each:
            regular_deps.append(step_def.for_each.source_step)

        for dep_step in regular_deps:
            if not self._is_dep_settled(job, dep_step):
                return False

        # Check after_any_of groups: at least ONE member per group must be settled
        # (per §10.2 first-success-wins eligibility, no cancellation).
        for group in step_def.after_any_of:
            has_settled = False
            for member in group:
                if self._is_dep_settled(job, member):
                    has_settled = True
                    break
                if self._is_dep_settled_on_error_continue(job, member):
                    has_settled = True
                    break
            if not has_settled:
                return False

        # Check any_of groups: at least ONE source per group must have current completed run
        # (or failed with on_error: continue — unless the binding is optional, in which case missing is OK)
        # Step 7 (§11.4): if the entire any_of binding is marked is_back_edge,
        # skip the readiness check (on iter-1 the loop body hasn't produced
        # anything yet; the resolver will set presence=False).
        for binding in step_def.inputs:
            if binding.any_of_sources:
                if binding.is_back_edge:
                    continue
                has_available = False
                for src_step, _ in binding.any_of_sources:
                    dep_latest = self.store.latest_completed_run(job.id, src_step)
                    if dep_latest and self._is_current(job, dep_latest):
                        if not self._dep_will_be_superseded(job, src_step):
                            has_available = True
                            break
                    # Also consider on_error: continue failed deps as available
                    if not has_available and self._is_dep_settled_on_error_continue(job, src_step):
                        has_available = True
                        break
                if not has_available and not binding.optional:
                    return False

        # Evaluate step-level `when` condition against resolved inputs
        if step_def.when is not None:
            try:
                from stepwise.models import WhenPredicate
                inputs, _, presence = self._resolve_inputs(job, step_def)
                if isinstance(step_def.when, WhenPredicate):
                    from stepwise.validator.mutex import evaluate_when_predicate
                    # Step 7 (§11.3): pass presence map for is_present:/is_null:
                    if not evaluate_when_predicate(step_def.when, inputs, presence):
                        return False
                else:
                    from stepwise.yaml_loader import evaluate_when_condition
                    if not evaluate_when_condition(step_def.when, inputs):
                        return False
            except Exception:
                import logging
                logging.getLogger("stepwise.engine").warning(
                    "when evaluation failed for step %s", step_name, exc_info=True
                )
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

    def _is_dep_settled_on_error_continue(self, job: Job, dep_step_name: str) -> bool:
        """Check if a dep step has failed with on_error: continue (treated as settled).

        Returns True if the dep's latest run is FAILED and its step definition
        has on_error: continue. This allows downstream steps to proceed with
        a null/error marker for that dep's outputs.
        """
        dep_def = job.workflow.steps.get(dep_step_name)
        if not dep_def or dep_def.on_error != "continue":
            return False
        latest = self.store.latest_run(job.id, dep_step_name)
        return latest is not None and latest.status == StepRunStatus.FAILED

    def _is_dep_settled(self, job: Job, dep_step_name: str) -> bool:
        """Check if a dep step is settled: either has a current completed run
        or has failed with on_error: continue.

        Used by _is_step_ready to determine if a regular dep unblocks downstream.
        """
        # Standard: current completed run
        dep_latest = self.store.latest_completed_run(job.id, dep_step_name)
        if dep_latest and self._is_current(job, dep_latest):
            if not self._dep_will_be_superseded(job, dep_step_name):
                return True
        # on_error: continue failed dep also counts as settled
        if self._is_dep_settled_on_error_continue(job, dep_step_name):
            return True
        return False

    # ── Currentness ───────────────────────────────────────────────────────

    # Currentness with cycle detection
    def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
        """A run is current if:
        1. It is the latest run (any status) for its step
        2. It has COMPLETED status
        3. Every dependency run it used is itself current

        _checking_steps tracks which step names are being checked up the call
        stack. If we encounter a step already being checked, we've hit a
        circular dep chain (score→refine→score). Treat it as current to
        break the cycle — the runs in the cycle are self-consistent.
        """
        if _checking_steps is None:
            _checking_steps = set()
        if run.step_name in _checking_steps:
            return True  # Break cycle — steps in the loop are self-consistent
        _checking_steps = _checking_steps | {run.step_name}  # copy to avoid mutation across branches

        # Supersession: ANY newer run invalidates this one
        latest = self.store.latest_run(job.id, run.step_name)
        if not latest or latest.id != run.id:
            return False

        if run.status != StepRunStatus.COMPLETED:
            return False

        step_def = job.workflow.steps.get(run.step_name)
        if not step_def:
            return False

        # Check dependency provenance
        # For regular deps (non-optional, non-back-edge): check each dep step.
        # Step 7 (§11.7): bindings marked is_back_edge=True are skipped here
        # — same as optional bindings — so the existing currentness chain
        # doesn't incorrectly invalidate consumer runs across loop iterations.
        regular_dep_steps: list[str] = [
            b.source_step for b in step_def.inputs
            if not b.any_of_sources and b.source_step != "$job"
            and not b.optional and not b.is_back_edge
        ]
        regular_dep_steps.extend(step_def.after)
        if step_def.for_each:
            regular_dep_steps.append(step_def.for_each.source_step)

        for dep_step in regular_dep_steps:
            if not run.dep_run_ids:
                return False
            source_run_id = run.dep_run_ids.get(dep_step)
            if not source_run_id:
                return False
            try:
                source_run = self.store.load_run(source_run_id)
            except KeyError:
                return False
            # on_error: continue failed deps are current if they are the latest run
            dep_def = job.workflow.steps.get(dep_step)
            if (dep_def and dep_def.on_error == "continue"
                    and source_run.status == StepRunStatus.FAILED):
                latest_dep = self.store.latest_run(job.id, dep_step)
                if latest_dep and latest_dep.id == source_run.id:
                    continue  # settled — treat as current
                return False
            if not self._is_current(job, source_run, _checking_steps):
                return False

        # For any_of deps: only check the source that was actually used (in dep_run_ids).
        # Step 7 (§11.4): if the any_of binding is itself a back-edge,
        # skip the currentness check entirely (same rationale as regular
        # back-edges above).
        for binding in step_def.inputs:
            if binding.any_of_sources:
                if binding.is_back_edge:
                    continue
                # Find which source was used
                found_current = False
                any_recorded = False
                for src_step, _ in binding.any_of_sources:
                    if run.dep_run_ids and src_step in run.dep_run_ids:
                        any_recorded = True
                        source_run_id = run.dep_run_ids[src_step]
                        try:
                            source_run = self.store.load_run(source_run_id)
                            if self._is_current(job, source_run, _checking_steps):
                                found_current = True
                                break
                        except KeyError:
                            pass
                # Optional any_of with no source used → still current
                if not found_current and not (binding.optional and not any_recorded):
                    return False

        return True

    def _dep_steps(self, step_def: StepDefinition) -> list[str]:
        """All dependency steps: input binding sources + after + for_each source.

        Step 7 (§11.7): bindings marked is_back_edge=True are EXCLUDED from
        the dependency walk used by cycle detection and currentness checks.
        Back-edges are by construction part of a loop closed by an exit
        rule, so they don't represent forward dependencies.
        """
        deps: list[str] = []
        for b in step_def.inputs:
            if b.is_back_edge:
                continue
            if b.any_of_sources:
                for src_step, _ in b.any_of_sources:
                    deps.append(src_step)
            else:
                deps.append(b.source_step)
        deps.extend(step_def.after)
        if step_def.for_each:
            deps.append(step_def.for_each.source_step)
        return deps

    def _step_in_dep_cycle(self, job: Job, step_name: str) -> bool:
        """Check if a step participates in a circular dependency chain.

        Returns True if step_name can reach itself by following input/after
        edges. Detects score→refine→score patterns where _dispatch_ready
        would infinitely relaunch due to currentness invalidation.
        """
        step_def = job.workflow.steps.get(step_name)
        if not step_def:
            return False
        visited: set[str] = set()
        queue = list(set(self._dep_steps(step_def)))
        while queue:
            dep = queue.pop(0)
            if dep == step_name:
                return True
            if dep in visited or dep == "$job":
                continue
            visited.add(dep)
            dep_def = job.workflow.steps.get(dep)
            if dep_def:
                queue.extend(self._dep_steps(dep_def))
        return False

    def _cycle_has_external_gate(self, job: Job, step_name: str) -> bool:
        """Check if any cycle containing step_name passes through an external step.

        External steps suspend and require explicit fulfillment, so they
        naturally prevent infinite relaunch in dependency cycles. When a
        cycle contains an external step, non-external steps in that cycle
        should be allowed to re-run after the external step is fulfilled.
        """
        step_def = job.workflow.steps.get(step_name)
        if not step_def:
            return False
        # BFS from step_name's deps — find paths back to step_name
        # and check if any path passes through an external step.
        visited: set[str] = set()
        # Each queue entry: (current_dep, has_external_on_path)
        queue: list[tuple[str, bool]] = [
            (dep, False) for dep in set(self._dep_steps(step_def))
        ]
        while queue:
            dep, has_ext = queue.pop(0)
            if dep == "$job":
                continue
            dep_def = job.workflow.steps.get(dep)
            if not dep_def:
                continue
            is_ext = dep_def.executor.type == "external"
            path_has_ext = has_ext or is_ext
            if dep == step_name:
                if path_has_ext:
                    return True
                continue  # cycle found but no external gate on this path
            if dep in visited:
                continue
            visited.add(dep)
            for next_dep in self._dep_steps(dep_def):
                queue.append((next_dep, path_has_ext))
        return False

    # ── Job Completion ────────────────────────────────────────────────────

    def _job_complete(self, job: Job) -> bool:
        """Job is complete when nothing is in motion, nothing is ready,
        and at least one terminal has a current completed run — or all
        steps are resolved (completed or skipped)."""
        # Anything in motion → not done
        if (self.store.running_runs(job.id) or
                self.store.suspended_runs(job.id) or
                self.store.delegated_runs(job.id)):
            return False
        # Anything ready to launch → not done
        if self._find_ready(job):
            return False
        # Nothing in motion, nothing ready → settled
        # Complete if at least one terminal has a current completed run
        for t in job.workflow.terminal_steps():
            latest = self.store.latest_completed_run(job.id, t)
            if latest and self._is_current(job, latest):
                return True
        # Also complete if every step has been resolved (completed or skipped),
        # or failed with on_error: continue (treated as settled for completion purposes).
        # This handles cases where terminal steps were skipped due to
        # conditional branching (when conditions) after a loop resolved.
        all_resolved = True
        for step_name in job.workflow.steps:
            latest = self.store.latest_run(job.id, step_name)
            step_def = job.workflow.steps.get(step_name)
            if latest and latest.status == StepRunStatus.FAILED:
                if step_def and step_def.on_error == "continue":
                    continue  # on_error: continue — treated as settled
                all_resolved = False
                break
            if not latest or latest.status not in (StepRunStatus.COMPLETED, StepRunStatus.SKIPPED):
                all_resolved = False
                break
        return all_resolved

    def _settle_unstarted_steps(self, job: Job) -> None:
        """Mark never-run steps as SKIPPED for bookkeeping. Called at job settlement."""
        for step_name in job.workflow.steps:
            latest = self.store.latest_run(job.id, step_name)
            if latest is None:
                run = StepRun(
                    id=_gen_id("run"), job_id=job.id, step_name=step_name,
                    attempt=1, status=StepRunStatus.SKIPPED,
                    error="Not reached",
                    started_at=_now(), completed_at=_now(),
                )
                self.store.save_run(run)
                self._emit(job.id, STEP_SKIPPED, {"step": step_name, "reason": "settlement"})

    def _cleanup_job_sessions(self, job_id: str, job: Job | None = None) -> None:
        """Clean up in-memory session registry for a completed/failed job.

        Process cleanup is handled by ACPBackend's lifecycle manager.
        """
        self._session_registries.pop(job_id, None)

    # ── Launching ─────────────────────────────────────────────────────────

    def _launch(self, job: Job, step_name: str) -> StepRun:
        step_def = job.workflow.steps[step_name]

        # For-each steps get special handling
        if step_def.for_each and step_def.sub_flow:
            try:
                return self._launch_for_each(job, step_def)
            except (ValueError, KeyError) as e:
                import logging
                import traceback as tb_mod
                logging.getLogger("stepwise.engine").error(
                    f"For-each step '{step_name}' failed to launch: {e}", exc_info=True
                )
                tb_str = "".join(tb_mod.format_exception(type(e), e, e.__traceback__))
                run = StepRun(
                    id=_gen_id("run"),
                    job_id=job.id,
                    step_name=step_name,
                    attempt=self.store.next_attempt(job.id, step_name),
                    status=StepRunStatus.FAILED,
                    error=str(e),
                    traceback=tb_str,
                    started_at=_now(),
                    completed_at=_now(),
                )
                self.store.save_run(run)
                self._emit(job.id, STEP_FAILED, {"step": step_name, "error": str(e)})
                self._halt_job(job, run)
                return run

        # Direct sub-flow steps
        if step_def.executor.type == "sub_flow" and step_def.sub_flow:
            return self._launch_sub_flow(job, step_def)

        # Normal step: prepare, execute synchronously, process result
        run, exec_ref, inputs, ctx = self._prepare_step_run(job, step_name)

        # Cache hit — _prepare_step_run already completed the run
        if exec_ref is None:
            return run

        try:
            executor = self.registry.create(exec_ref)
            result = executor.start(inputs, ctx)
        except Exception as e:
            self._handle_executor_crash(job, run, step_name, e)
            return run

        self._process_launch_result(job, run, result)
        return run

    def _get_engine_version(self) -> str:
        """Get engine version for cache key computation."""
        try:
            from importlib.metadata import version
            return version("stepwise-run")
        except Exception:
            return "0.0.0"

    def _check_step_cache(
        self,
        job: Job,
        step_def: StepDefinition,
        exec_ref: ExecutorRef,
        inputs: dict,
        run: StepRun,
    ) -> HandoffEnvelope | None:
        """Check cache for a step result. Returns envelope on hit, None on miss."""
        from stepwise.cache import UNCACHEABLE_TYPES, compute_cache_key

        if self.cache is None or step_def.cache is None:
            return None
        if not step_def.cache.enabled:
            return None
        if exec_ref.type in UNCACHEABLE_TYPES:
            return None
        # Agent steps with emit_flow are uncacheable
        if exec_ref.type == "agent" and exec_ref.config.get("emit_flow"):
            return None
        # --rerun bypass
        if run.step_name in self._rerun_steps.get(job.id, set()):
            _engine_logger.info(
                "Cache bypassed for step '%s' (--rerun)", run.step_name
            )
            return None

        key = compute_cache_key(
            inputs, exec_ref, self._get_engine_version(),
            step_def.cache.key_extra,
        )

        envelope = self.cache.get(key)
        if envelope is not None:
            _engine_logger.info(
                "Cache hit for step '%s' (key=%s…)", run.step_name, key[:12]
            )
            return envelope

        _engine_logger.debug(
            "Cache miss for step '%s' (key=%s…)", run.step_name, key[:12]
        )
        return None

    def _write_step_cache(
        self,
        job: Job,
        step_def: StepDefinition,
        run: StepRun,
        envelope: HandoffEnvelope,
    ) -> None:
        """Write a successful step result to cache."""
        from stepwise.cache import DEFAULT_TTL, UNCACHEABLE_TYPES, compute_cache_key

        if self.cache is None or step_def.cache is None:
            return
        if not step_def.cache.enabled:
            return
        if step_def.executor.type in UNCACHEABLE_TYPES:
            return
        if step_def.executor.type == "agent" and step_def.executor.config.get("emit_flow"):
            return

        # Recompute cache key from the run's resolved inputs and the step's executor config
        exec_ref = step_def.executor
        interpolated = _interpolate_config(exec_ref.config, run.inputs or {})
        if interpolated != exec_ref.config:
            exec_ref = ExecutorRef(
                type=exec_ref.type, config=interpolated,
                decorators=exec_ref.decorators,
            )

        cache_key = compute_cache_key(
            run.inputs or {}, exec_ref, self._get_engine_version(),
            step_def.cache.key_extra,
        )

        # Determine TTL
        ttl = step_def.cache.ttl
        if ttl is None:
            ttl = DEFAULT_TTL.get(step_def.executor.type, 3600)

        flow_name = job.workflow.metadata.name if job.workflow.metadata else ""
        self.cache.put(cache_key, run.step_name, flow_name, envelope, ttl)
        _engine_logger.debug(
            "Cached result for step '%s' (key=%s…, ttl=%ds)",
            run.step_name, cache_key[:12], ttl,
        )

    def _prepare_step_run(
        self, job: Job, step_name: str,
    ) -> tuple[StepRun, "ExecutorRef", dict, ExecutionContext]:
        """Create StepRun, resolve inputs, build ExecutionContext.

        Returns (run, exec_ref, inputs, ctx). Run is saved to store in RUNNING status.
        """
        step_def = job.workflow.steps[step_name]
        attempt = self.store.next_attempt(job.id, step_name)
        inputs, dep_run_ids, _presence = self._resolve_inputs(job, step_def)

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
            flow_source_dir=job.workflow.source_dir,
        )

        exec_ref = step_def.executor

        # Interpolate $variable references in executor config from resolved inputs
        interpolated = _interpolate_config(exec_ref.config, inputs)
        if interpolated != exec_ref.config:
            # Persist interpolated config for frontend inspection
            state = run.executor_state or {}
            state["_interpolated_config"] = interpolated
            run.executor_state = state
            self.store.save_run(run)
            exec_ref = ExecutorRef(
                type=exec_ref.type, config=interpolated,
                decorators=exec_ref.decorators,
            )

        # Cache check: after interpolation, before executor dispatch
        cached_envelope = self._check_step_cache(job, step_def, exec_ref, inputs, run)
        if cached_envelope is not None:
            run.result = cached_envelope
            run.status = StepRunStatus.COMPLETED
            run.completed_at = _now()
            run.executor_state = {**(run.executor_state or {}), "from_cache": True}
            self.store.save_run(run)
            self._emit(job.id, STEP_COMPLETED, {
                "step": step_name,
                "attempt": run.attempt,
                "from_cache": True,
            })
            self._process_completion(job, run)
            return run, None, None, None  # sentinel: cache hit

        if step_def.outputs and "output_fields" not in exec_ref.config:
            exec_ref = exec_ref.with_config({"output_fields": step_def.outputs})
        if job.workflow.source_dir and exec_ref.type == "script":
            exec_ref = exec_ref.with_config({"flow_dir": job.workflow.source_dir})

        # Pass session fields to agent executor
        if exec_ref.type == "agent":
            session_ctx: dict = {"_billing_mode": self.billing_mode}

            # Named sessions (new mechanism)
            if step_def.session and job.id in self._session_registries:
                session_state = self._session_registries[job.id].get(step_def.session)
                if session_state:
                    session_ctx["_session_name"] = session_state.name
                    session_ctx["_agent"] = session_state.agent

                    if session_state.session_id and not step_def.fork_from:
                        # Continue existing session
                        session_ctx["_session_uuid"] = session_state.session_id
                    elif step_def.fork_from and not session_state.created:
                        # First (chain-root) step on a forked session.
                        # Try snapshot UUID first, fall back to live session_id.
                        snap_uuid = self._lookup_snapshot_uuid(
                            job, step_def.fork_from
                        )
                        if snap_uuid:
                            session_ctx["_fork_from_session_id"] = snap_uuid
                        else:
                            # Fallback to live session_id if no snapshot exists.
                            live_uuid: str | None = None
                            parent_run = self.store.latest_completed_run(
                                job.id, step_def.fork_from
                            )
                            if parent_run and parent_run.executor_state:
                                live_uuid = parent_run.executor_state.get(
                                    "session_id"
                                )
                            if not live_uuid:
                                parent_step_def = job.workflow.steps.get(
                                    step_def.fork_from
                                )
                                parent_session_name = (
                                    parent_step_def.session
                                    if parent_step_def else None
                                )
                                if parent_session_name:
                                    parent_state = (
                                        self._session_registries[job.id]
                                        .get(parent_session_name)
                                    )
                                    if parent_state and parent_state.session_id:
                                        live_uuid = parent_state.session_id
                            if live_uuid:
                                _engine_logger.warning(
                                    "fork from step %r missing snapshot_uuid; "
                                    "falling back to live parent session_id (race risk)",
                                    step_def.fork_from,
                                )
                                session_ctx["_fork_from_session_id"] = live_uuid
                    elif session_state.created:
                        # Subsequent step on forked session — continue
                        session_ctx["_session_uuid"] = session_state.session_id

            # §9.7.1: Ephemeral fork — fork_from set but NO session declared.
            # Look up the fork source's snapshot UUID and pass it directly
            # without touching the session registry. The forked session is
            # transient — no downstream step can continue it.
            elif step_def.fork_from and not step_def.session:
                if step_def.fork_from.startswith("$job."):
                    # §9.7.3: fork_from: $job.<input> — read UUID from job inputs
                    input_name = step_def.fork_from[len("$job."):]
                    uuid_from_input = job.inputs.get(input_name)
                    if uuid_from_input:
                        session_ctx["_fork_from_session_id"] = uuid_from_input
                else:
                    # Ephemeral fork from a same-scope step name
                    snap_uuid = self._lookup_snapshot_uuid(
                        job, step_def.fork_from
                    )
                    if snap_uuid:
                        session_ctx["_fork_from_session_id"] = snap_uuid
                    else:
                        # Fallback: try live session UUID from executor_state
                        parent_run = self.store.latest_completed_run(
                            job.id, step_def.fork_from
                        )
                        if parent_run and parent_run.executor_state:
                            live_uuid = parent_run.executor_state.get("session_id")
                            if live_uuid:
                                session_ctx["_fork_from_session_id"] = live_uuid

            # Legacy continue_session support
            elif step_def.continue_session:
                session_ctx["continue_session"] = True
                # Pass previous session name from last completed run
                prev_run = self.store.latest_completed_run(job.id, step_name)
                if prev_run and prev_run.executor_state and prev_run.executor_state.get("session_name"):
                    session_ctx["_prev_session_name"] = prev_run.executor_state["session_name"]

            # §9.7.5 Inference 2: working_dir inherited from fork source.
            # The session snapshot lives at ~/.claude/projects/<slug>/ where
            # slug is derived from working_dir — the fork must run in the
            # same project context as its source.
            if step_def.fork_from and not exec_ref.config.get("working_dir"):
                if not step_def.fork_from.startswith("$job."):
                    fork_source_def = job.workflow.steps.get(step_def.fork_from)
                    if fork_source_def and fork_source_def.executor and fork_source_def.executor.config:
                        source_wd = fork_source_def.executor.config.get("working_dir")
                        if source_wd:
                            session_ctx["working_dir"] = source_wd

            # loop_prompt and circuit breaker (independent of session mechanism)
            if step_def.loop_prompt is not None:
                session_ctx["loop_prompt"] = step_def.loop_prompt
            if step_def.max_continuous_attempts is not None:
                session_ctx["max_continuous_attempts"] = step_def.max_continuous_attempts
            if session_ctx:
                exec_ref = exec_ref.with_config(session_ctx)

        # Inject runtime context for agent executors with emit_flow enabled
        if exec_ref.type == "agent" and exec_ref.config.get("emit_flow"):
            emit_ctx: dict = {
                "_registry": self.registry,
                "_config": self.config,
            }
            depth = self._get_job_depth(job)
            max_depth = job.config.max_sub_job_depth
            emit_ctx["_depth_remaining"] = max(0, max_depth - depth - 1)
            if self.project_dir:
                emit_ctx["_project_dir"] = self.project_dir.parent
            exec_ref = exec_ref.with_config(emit_ctx)

        return run, exec_ref, inputs, ctx

    def _handle_executor_crash(
        self, job: Job, run: StepRun, step_name: str, error: Exception,
    ) -> None:
        """Handle exception from executor creation or start()."""
        import logging
        import traceback as tb_mod
        logging.getLogger("stepwise.engine").error(
            f"Step '{step_name}' executor crashed: {type(error).__name__}: {error}",
            exc_info=True,
        )
        step_def = job.workflow.steps.get(step_name)
        error_msg = f"Executor crash: {type(error).__name__}: {error}"
        tb_str = "".join(tb_mod.format_exception(type(error), error, error.__traceback__))
        if step_def:
            self._fail_run(job, run, step_def,
                           error=error_msg, error_category="executor_crash",
                           traceback_str=tb_str)
        else:
            run.status = StepRunStatus.FAILED
            run.error = error_msg
            run.pid = None
            run.completed_at = _now()
            self.store.save_run(run)
            self._emit(job.id, STEP_FAILED, {
                "step": step_name,
                "attempt": run.attempt,
                "error": str(error),
            })
            self._halt_job(job, run)

    def _process_launch_result(
        self, job: Job, run: StepRun, result: ExecutorResult,
    ) -> None:
        """Process ExecutorResult after executor.start() returns."""
        step_name = run.step_name
        attempt = run.attempt
        step_def = job.workflow.steps[step_name]

        match result.type:
            case "data":
                is_failure = False
                error_msg = None
                if result.executor_state and result.executor_state.get("failed"):
                    is_failure = True
                    error_msg = result.executor_state.get("error", "Executor failed")
                elif result.envelope and result.envelope.executor_meta.get("failed"):
                    is_failure = True
                    error_msg = result.envelope.executor_meta.get("reason", "Executor failed")

                if is_failure:
                    error_cat = None
                    if result.executor_state:
                        error_cat = result.executor_state.get("error_category")
                    run.result = result.envelope
                    run.executor_state = result.executor_state
                    self._fail_run(job, run, step_def,
                                   error=error_msg or "Executor failed",
                                   error_category=error_cat)
                else:
                    derived_error = self._apply_derived_outputs(step_def, result.envelope)
                    validation_error = derived_error or self._validate_artifact(step_def, result.envelope)
                    if not validation_error:
                        validation_error = self._check_artifact_size(step_def, result.envelope)
                    if validation_error:
                        run.status = StepRunStatus.FAILED
                        run.error = validation_error
                        run.result = result.envelope
                        run.pid = None
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
                        run.executor_state = result.executor_state
                        run.status = StepRunStatus.COMPLETED
                        run.pid = None
                        run.completed_at = _now()
                        self.store.save_run(run)
                        self._emit(job.id, STEP_COMPLETED, {
                            "step": step_name,
                            "attempt": attempt,
                        })
                        self._emit_effector_events(job.id, result.envelope)
                        # Write to cache if enabled
                        self._write_step_cache(job, step_def, run, result.envelope)
                        # Capture session ID for named sessions
                        if step_def.session and job.id in self._session_registries:
                            state = self._session_registries[job.id].get(step_def.session)
                            if state and not state.created:
                                output_path = (run.executor_state or {}).get("output_path")
                                if output_path:
                                    try:
                                        from stepwise.acp_ndjson import extract_session_id
                                        state.session_id = extract_session_id(output_path, result_only=True)
                                    except Exception:
                                        pass
                                # Fallback: the backend captures the session ID
                                # directly and stashes it on executor_state["session_id"].
                                if not state.session_id:
                                    state.session_id = (run.executor_state or {}).get("session_id")
                                state.created = True
                            # §9.3 critical section: if this step is a
                            # fork source, snapshot the session JSON inside
                            # an exclusive lock and persist the snapshot
                            # UUID atomically with completion. Otherwise
                            # downstream forks would resume from the live
                            # (potentially-mutating) tail and capture the
                            # wrong fork point (§9.1).
                            self._maybe_snapshot_for_fork_source(job, run, step_name)
                        self._process_completion(job, run)

            case "watch":
                run.status = StepRunStatus.SUSPENDED
                run.watch = result.watch
                run.executor_state = result.executor_state
                if run.watch and not run.watch.fulfillment_outputs:
                    run.watch.fulfillment_outputs = list(step_def.outputs)
                if run.watch and not run.watch.output_schema and step_def.output_schema:
                    run.watch.output_schema = {k: v.to_dict() for k, v in step_def.output_schema.items()}
                self.store.save_run(run)
                self._emit(job.id, STEP_SUSPENDED, {
                    "step": step_name,
                    "run_id": run.id,
                    "watch_mode": result.watch.mode if result.watch else None,
                    "prompt": result.watch.config.get("prompt") if result.watch else None,
                })

            case "async":
                run.executor_state = result.executor_state
                self.store.save_run(run)
                self._emit(job.id, STEP_STARTED_ASYNC, {
                    "step": step_name,
                    "attempt": attempt,
                    "executor_type": step_def.executor.type,
                })

            case "delegate":
                sub_def = result.sub_job_def
                if not sub_def:
                    self._fail_run(job, run, step_def,
                                   error="Delegate result missing sub_job_def")
                    return

                run.status = StepRunStatus.DELEGATED
                run.executor_state = {
                    **(result.executor_state or {}),
                    "emitted_flow": True,
                }
                self.store.save_run(run)

                try:
                    sub = self._create_sub_job(job, run, sub_def)
                except Exception as e:
                    import traceback as tb_mod
                    run.status = StepRunStatus.FAILED
                    run.error = f"Failed to create sub-job for emitted flow: {e}"
                    run.traceback = "".join(tb_mod.format_exception(type(e), e, e.__traceback__))
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._halt_job(job, run)
                    return

                run.sub_job_id = sub.id
                self.store.save_run(run)
                self._emit(job.id, STEP_DELEGATED, {
                    "step": step_name,
                    "attempt": attempt,
                    "sub_job_id": sub.id,
                    "emitted_flow": True,
                })

    # ── For-Each Launching ────────────────────────────────────────────────

    def _for_each_batch_cache_check(
        self,
        step_def: StepDefinition,
        parent_inputs: dict,
        source_list: list,
        item_var: str,
        job_inputs: dict | None = None,
    ) -> dict[int, dict]:
        """Batch check cache for for-each items. Returns {index: artifact_dict} for hits."""
        from stepwise.cache import UNCACHEABLE_TYPES, compute_cache_key

        if self.cache is None:
            return {}

        # Find cacheable terminal steps in the sub-flow
        sub_flow = step_def.sub_flow
        if sub_flow is None:
            return {}

        # Look for terminal steps with cache enabled
        cacheable_terminals: list[StepDefinition] = []
        terminal_names = self._find_terminal_steps(sub_flow)
        for tname in terminal_names:
            tstep = sub_flow.steps[tname]
            if tstep.cache is not None and tstep.cache.enabled:
                if tstep.executor.type not in UNCACHEABLE_TYPES:
                    cacheable_terminals.append(tstep)

        if not cacheable_terminals:
            return {}

        # For simplicity, cache check on the first cacheable terminal step
        target_step = cacheable_terminals[0]
        engine_version = self._get_engine_version()

        # Compute cache keys for each item
        keys_by_index: dict[str, int] = {}  # cache_key → item_index
        for i, item in enumerate(source_list):
            sub_inputs = {**(job_inputs or {}), **parent_inputs, item_var: item}
            exec_ref = target_step.executor
            interpolated = _interpolate_config(exec_ref.config, sub_inputs)
            if interpolated != exec_ref.config:
                exec_ref = ExecutorRef(
                    type=exec_ref.type, config=interpolated,
                    decorators=exec_ref.decorators,
                )
            key = compute_cache_key(
                sub_inputs, exec_ref, engine_version,
                target_step.cache.key_extra if target_step.cache else None,
            )
            keys_by_index[key] = i

        # Batch query
        hits = self.cache.batch_get(list(keys_by_index.keys()))

        results: dict[int, dict] = {}
        for key, envelope in hits.items():
            idx = keys_by_index[key]
            results[idx] = envelope.artifact
            _engine_logger.info(
                "For-each cache hit for item %d (key=%s…)", idx, key[:12]
            )

        return results

    def _find_terminal_steps(self, workflow: WorkflowDefinition) -> list[str]:
        """Find terminal steps (no other step depends on them)."""
        all_steps = set(workflow.steps.keys())
        has_dependents: set[str] = set()
        for step in workflow.steps.values():
            for binding in step.inputs:
                if binding.source_step != "$job" and binding.source_step in all_steps:
                    has_dependents.add(binding.source_step)
            for seq in step.after:
                if seq in all_steps:
                    has_dependents.add(seq)
        return [s for s in all_steps if s not in has_dependents]

    def _launch_for_each(self, job: Job, step_def: StepDefinition) -> StepRun:
        """Launch a for_each step: resolve source list, create N sub-jobs."""
        fe = step_def.for_each
        assert fe is not None
        assert step_def.sub_flow is not None

        step_name = step_def.name
        attempt = self.store.next_attempt(job.id, step_name)
        inputs, dep_run_ids, _presence = self._resolve_inputs(job, step_def)

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

        # LLM executors often return complex values as JSON strings — auto-parse.
        if isinstance(source_list, str):
            try:
                import json
                source_list = json.loads(source_list)
            except (json.JSONDecodeError, TypeError):
                pass

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

        # Batch cache check for for-each items
        cached_results: dict[int, dict] = {}  # index → artifact dict
        if self.cache is not None:
            cached_results = self._for_each_batch_cache_check(
                step_def, inputs, source_list, fe.item_var,
                job_inputs=job.inputs,
            )

        # Create sub-jobs for uncached items only
        sub_job_ids: list[str | None] = [None] * len(source_list)
        for i, item in enumerate(source_list):
            if i in cached_results:
                continue  # cache hit — no sub-job needed
            sub_inputs = {
                **job.inputs,  # propagate parent job inputs so inner $job.x refs resolve
                **inputs,  # explicit for_each step inputs override parent values
                fe.item_var: item,  # the iteration variable
            }
            sub_workspace = os.path.join(
                job.workspace_path, "for_each", step_name, str(i),
            )
            fe_meta = copy.deepcopy(job.metadata)
            fe_meta["sys"]["parent_job_id"] = job.id
            sub_job = self.create_job(
                objective=f"{job.objective} > {step_name}[{i}]",
                workflow=step_def.sub_flow,
                inputs=sub_inputs,
                config=job.config,
                parent_job_id=job.id,
                parent_step_run_id=run.id,
                workspace_path=sub_workspace,
                metadata=fe_meta,
            )
            sub_job_ids[i] = sub_job.id

        # Store sub-job tracking info in executor_state
        actual_sub_job_ids = [sid for sid in sub_job_ids if sid is not None]
        run.executor_state = {
            "for_each": True,
            "sub_job_ids": actual_sub_job_ids,
            "sub_job_index_map": {sid: i for i, sid in enumerate(sub_job_ids) if sid is not None},
            "cached_results": cached_results,
            "item_count": len(source_list),
            "on_error": fe.on_error,
        }

        # If all items were cached, complete immediately
        if not actual_sub_job_ids:
            results = [cached_results.get(i, {}) for i in range(len(source_list))]
            run.status = StepRunStatus.COMPLETED
            run.completed_at = _now()
            run.result = HandoffEnvelope(
                artifact={"results": results},
                sidecar=Sidecar(),
                workspace=job.workspace_path,
                timestamp=_now(),
            )
            self.store.save_run(run)
            self._emit(job.id, STEP_COMPLETED, {
                "step": step_name,
                "attempt": attempt,
                "for_each": True,
                "item_count": len(source_list),
                "cached_count": len(cached_results),
            })
            self._process_completion(job, run)
            return run

        self.store.save_run(run)

        self._emit(job.id, FOR_EACH_STARTED, {
            "step": step_name,
            "attempt": attempt,
            "item_count": len(source_list),
            "sub_job_ids": actual_sub_job_ids,
            "cached_count": len(cached_results),
        })

        # Start all sub-jobs
        for sub_job_id in actual_sub_job_ids:
            self.start_job(sub_job_id)

        return run

    def _check_for_each_completion(self, job: Job, run: StepRun) -> bool:
        """Check if all sub-jobs for a for_each step are complete.
        Returns True if progress was made.
        """
        if not run.executor_state or not run.executor_state.get("for_each"):
            return False

        sub_job_ids = run.executor_state.get("sub_job_ids", [])
        sub_job_index_map = run.executor_state.get("sub_job_index_map", {})
        cached_results = run.executor_state.get("cached_results", {})
        on_error = run.executor_state.get("on_error", "fail_fast")
        item_count = run.executor_state.get("item_count", len(sub_job_ids))

        # Build full results array: start with cached, fill in sub-job results
        # Convert cached_results keys from str (JSON serialization) to int
        all_results: dict[int, dict | None] = {}
        for idx_str, artifact in cached_results.items():
            all_results[int(idx_str)] = artifact

        all_done = True
        any_failed = False
        failed_indices: list[int] = []

        for sub_job_id in sub_job_ids:
            # Map sub_job_id back to original item index
            original_idx = sub_job_index_map.get(sub_job_id)
            if original_idx is None:
                # Fallback for older runs without index map
                original_idx = sub_job_ids.index(sub_job_id)

            try:
                sub_job = self.store.load_job(sub_job_id)
            except KeyError:
                all_done = False
                continue

            if sub_job.status == JobStatus.COMPLETED:
                terminal_output = self._terminal_output(sub_job)
                all_results[original_idx] = terminal_output.artifact
            elif sub_job.status == JobStatus.FAILED:
                any_failed = True
                failed_indices.append(original_idx)
                if on_error == "fail_fast":
                    # Cancel remaining sub-jobs
                    for other_id in sub_job_ids:
                        if other_id != sub_job_id:
                            try:
                                other = self.store.load_job(other_id)
                                if other.status == JobStatus.RUNNING:
                                    self.cancel_job(other_id)
                            except (KeyError, ValueError):
                                pass
                    # Fail the for_each run
                    run.status = StepRunStatus.FAILED
                    run.error = f"For-each item {original_idx} failed"
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._halt_job(job, run)
                    return True
                else:
                    # continue mode: record failure, keep going
                    all_results[original_idx] = {"_error": f"Sub-job {sub_job_id} failed"}
            elif sub_job.status in (JobStatus.CANCELLED, JobStatus.PAUSED):
                any_failed = True
                failed_indices.append(original_idx)
                all_results[original_idx] = {"_error": f"Sub-job {sub_job.status.value}"}
            else:
                all_done = False

        if not all_done:
            return False

        # All sub-jobs are done — collect results in original order
        results = []
        for i in range(item_count):
            results.append(all_results.get(i, {}))

        # If ALL items failed, fail the for-each step regardless of on_error setting.
        # on_error: continue means "tolerate partial failures", not "accept 100% failure".
        if any_failed and len(failed_indices) == len(sub_job_ids):
            run.result = HandoffEnvelope(
                artifact={"results": results},
                sidecar=Sidecar(),
                workspace=job.workspace_path,
                timestamp=_now(),
            )
            run.status = StepRunStatus.FAILED
            run.error = f"All {len(sub_job_ids)} sub-jobs failed"
            run.completed_at = _now()
            self.store.save_run(run)
            self._emit(job.id, FOR_EACH_COMPLETED, {
                "step": run.step_name,
                "item_count": item_count,
                "failed_count": len(failed_indices),
            })
            self._halt_job(job, run)
            return True

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
            "item_count": item_count,
            "failed_count": len(failed_indices),
        })
        self._emit(job.id, STEP_COMPLETED, {
            "step": run.step_name,
            "attempt": run.attempt,
            "for_each": True,
        })

        self._process_completion(job, run)
        return True

    # ── Direct Sub-Flow Steps ─────────────────────────────────────────────

    def _launch_sub_flow(self, job: Job, step_def: StepDefinition) -> StepRun:
        """Launch a direct sub-flow step: delegate to embedded workflow."""
        assert step_def.sub_flow is not None

        attempt = self.store.next_attempt(job.id, step_def.name)
        inputs, dep_run_ids, _presence = self._resolve_inputs(job, step_def)

        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name=step_def.name,
            attempt=attempt, status=StepRunStatus.DELEGATED,
            inputs=inputs, dep_run_ids=dep_run_ids,
            started_at=_now(),
        )
        self.store.save_run(run)

        flow_ref = step_def.executor.config.get("flow_ref", step_def.name)
        sub_def = SubJobDefinition(
            objective=f"Sub-flow '{flow_ref}' for step '{step_def.name}'",
            workflow=step_def.sub_flow,
        )
        try:
            sub = self._create_sub_job(job, run, sub_def)
        except Exception as e:
            run.status = StepRunStatus.FAILED
            run.error = f"Failed to create sub-job for flow step '{step_def.name}': {e}"
            run.completed_at = _now()
            self.store.save_run(run)
            self._halt_job(job, run)
            return run

        run.sub_job_id = sub.id
        run.executor_state = {"sub_flow": True, "flow_ref": flow_ref}
        self.store.save_run(run)
        self._emit(job.id, STEP_DELEGATED, {
            "step": step_def.name, "sub_job_id": sub.id, "flow_ref": flow_ref,
        })
        return run

    def _resolve_flow_ref(self, ref: str, job: Job) -> WorkflowDefinition:
        """Resolve a flow reference to a WorkflowDefinition.

        File paths are baked at parse time, so only @author:name refs reach here.
        """
        if ref.startswith("@"):
            raise ValueError(
                f"Registry references must be resolved at parse time: {ref}. "
                f"Use load_workflow_yaml() to resolve @author:name refs before execution."
            )
        # File paths should never reach here — they're baked at parse time
        raise ValueError(
            f"Unexpected file ref at runtime: {ref}. "
            f"File refs should be resolved at parse time."
        )

    # ── Input Resolution ──────────────────────────────────────────────────

    def _resolve_inputs(
        self, job: Job, step_def: StepDefinition,
    ) -> tuple[dict, dict, dict[str, bool]]:
        """Returns (inputs_dict, dep_run_ids_dict, presence).

        Step 7 (§11.7): the third element is a per-binding presence map
        keyed by ``binding.local_name``. ``presence[name]`` is True iff
        the binding has a producer-side run that resolved to a concrete
        value (or the value was explicitly set to None via on_error:
        continue or any_of-all-failed). False on iter-1 of a loop-back
        binding (key absent from inputs dict in that case). The presence
        map is consulted by ``evaluate_when_predicate`` for ``is_present:``
        / ``is_null:`` semantics.

        Loop-back bindings additionally consult the ``Job.loop_frames``
        stack: if a frame exists for the binding's ``closing_loop_id``,
        the resolver writes ``frame.presence[binding.local_name]`` so
        nested-loop semantics work end-to-end (§11.5).
        """
        inputs: dict = {}
        dep_run_ids: dict[str, str] = {}
        presence: dict[str, bool] = {}

        def _record_presence(b, value: bool) -> None:
            presence[b.local_name] = value
            if b.is_back_edge and b.closing_loop_id and b.closing_loop_id in job.loop_frames:
                job.loop_frames[b.closing_loop_id].presence[b.local_name] = value

        def _back_edge_frame_active(b) -> bool:
            """Per §11.5: a back-edge resolves to a value only if its
            closing-loop frame exists AND has fired at least once (i.e.,
            iteration_index > 0). On iter-1 of a new (or freshly-reset)
            loop scope, the frame doesn't exist yet — the resolver must
            treat the binding as absent.
            """
            if not b.is_back_edge or not b.closing_loop_id:
                return True  # not a loop-back binding — fall through to normal resolution
            frame = job.loop_frames.get(b.closing_loop_id)
            return frame is not None and frame.iteration_index > 0

        for binding in step_def.inputs:
            if binding.any_of_sources:
                # Loop-back any_of: if the closing loop frame is not active,
                # short-circuit to absent (iter-1 / fresh-iteration semantics).
                if binding.is_back_edge and not _back_edge_frame_active(binding):
                    _record_presence(binding, False)
                    continue
                # Resolve from first available completed source.
                # Skip sources that failed with on_error: continue — prefer successful ones.
                resolved = False
                for src_step, src_field in binding.any_of_sources:
                    latest = self.store.latest_completed_run(job.id, src_step)
                    if latest and latest.result:
                        # §9.7.3: _session virtual output resolves from executor_state
                        if src_field == "_session":
                            es = latest.executor_state or {}
                            value = es.get("snapshot_uuid") or es.get("session_id")
                        else:
                            value = latest.result.artifact.get(src_field)
                            if value is None and "." in src_field:
                                parts = src_field.split(".")
                                value = latest.result.artifact
                                for part in parts:
                                    if isinstance(value, dict):
                                        value = value.get(part)
                                    else:
                                        value = None
                                        break
                        inputs[binding.local_name] = value
                        dep_run_ids[src_step] = latest.id
                        _record_presence(binding, True)
                        resolved = True
                        break  # first available wins
                if not resolved:
                    # Check if any source failed with on_error: continue
                    for src_step, src_field in binding.any_of_sources:
                        if self._is_dep_settled_on_error_continue(job, src_step):
                            failed_run = self.store.latest_run(job.id, src_step)
                            inputs[binding.local_name] = None
                            if failed_run:
                                dep_run_ids[src_step] = failed_run.id
                            _record_presence(binding, True)  # opted into None
                            resolved = True
                            break
                if not resolved and binding.is_back_edge:
                    # Loop-back any_of on iter-1: absent (key not added to inputs)
                    _record_presence(binding, False)
                elif not resolved and binding.optional:
                    inputs[binding.local_name] = None
                    _record_presence(binding, False)
            elif binding.source_step == "$job":
                inputs[binding.local_name] = job.inputs.get(binding.source_field)
                dep_run_ids["$job"] = "$job"
                _record_presence(binding, True)
            else:
                # Loop-back regular binding: §11.5 frame check first.
                if binding.is_back_edge and not _back_edge_frame_active(binding):
                    _record_presence(binding, False)
                    continue
                latest = self.store.latest_completed_run(job.id, binding.source_step)
                if latest and latest.result:
                    # §9.7.3: _session virtual output resolves from executor_state
                    if binding.source_field == "_session":
                        es = latest.executor_state or {}
                        value = es.get("snapshot_uuid") or es.get("session_id")
                    else:
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
                    _record_presence(binding, True)
                elif binding.is_back_edge:
                    # Loop-back regular binding on iter-1: absent.
                    # Do NOT set inputs[binding.local_name] — key absent
                    # matches §11.7 semantics (executor sees missing key).
                    _record_presence(binding, False)
                elif binding.optional:
                    # Optional non-loop-back dep not available — set to None
                    inputs[binding.local_name] = None
                    _record_presence(binding, False)
                elif self._is_dep_settled_on_error_continue(job, binding.source_step):
                    # Dep failed with on_error: continue — resolve input as None
                    failed_run = self.store.latest_run(job.id, binding.source_step)
                    inputs[binding.local_name] = None
                    if failed_run:
                        dep_run_ids[binding.source_step] = failed_run.id
                    _record_presence(binding, True)  # opted into None

        # Record after deps
        for seq_step in step_def.after:
            latest = self.store.latest_completed_run(job.id, seq_step)
            if latest:
                dep_run_ids[seq_step] = latest.id

        return inputs, dep_run_ids, presence

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
                        # Step 7 (§11.5): increment the loop frame for this
                        # target. The frame's iteration_index bumps and its
                        # presence map clears (each iteration starts fresh).
                        # Child frames whose parent_frame_id == target are
                        # invalidated (nested loops re-enter from scratch).
                        parent_fid = self._parent_frame_for_target(job, target)
                        frame = self._get_or_create_loop_frame(
                            job, frame_id=target, parent_frame_id=parent_fid,
                        )
                        frame.iteration_index += 1
                        frame.presence.clear()
                        self._invalidate_child_frames(job, parent_frame_id=target)
                        try:
                            self.store.save_job(job)
                        except Exception:
                            _engine_logger.warning(
                                "failed to persist loop_frames after loop fire",
                                exc_info=True,
                            )
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

        # No rule matched — behavior depends on whether advance rules exist
        has_advance_rule = any(
            rule.config.get("action", "advance") == "advance"
            for rule in sorted_rules
        )
        if has_advance_rule:
            # Author explicitly defined when to advance — unmatched = failure
            self._fail_run(job, run, step_def,
                           error=f"No exit rule matched for step '{run.step_name}' "
                                 f"(artifact: {list(artifact.keys())})")
        else:
            # No advance rule — implicit advance (loop/escalate only paths)
            self._emit(job.id, EXIT_RESOLVED, {
                "step": run.step_name,
                "rule": "implicit_advance",
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
                except ValueError as e:
                    import logging
                    logging.getLogger("stepwise.engine").warning(
                        "Exit rule '%s' eval failed: %s (artifact keys: %s)",
                        rule.name, e, list(artifact.keys()),
                    )
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
        step_io_dir = Path(workspace) / ".stepwise" / "step-io"
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

                # Apply derived outputs
                step_def = job.workflow.steps.get(run.step_name)
                if step_def:
                    derived_error = self._apply_derived_outputs(step_def, run.result)
                    if derived_error:
                        _engine_logger.warning("Derived output error in poll watch: %s", derived_error)
                        self._update_watch_state(run, error=derived_error)
                        return False

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

    # ── Validation ────────────────────────────────────────────────────────

    def _check_artifact_size(self, step_def: StepDefinition, envelope: HandoffEnvelope | None) -> str | None:
        """Reject artifacts over MAX_ARTIFACT_BYTES. Returns error string or None."""
        if not envelope or not envelope.artifact:
            return None
        try:
            size = len(json.dumps(envelope.artifact, default=str))
        except (TypeError, ValueError):
            return None  # can't measure — let it through
        if size > MAX_ARTIFACT_BYTES:
            mb = size / (1024 * 1024)
            limit_mb = MAX_ARTIFACT_BYTES / (1024 * 1024)
            return (
                f"Step '{step_def.name}' artifact too large: {mb:.1f}MB "
                f"(limit: {limit_mb:.0f}MB). Reduce output size or split into smaller steps."
            )
        return None

    def _apply_derived_outputs(self, step_def: StepDefinition, envelope: HandoffEnvelope | None) -> str | None:
        """Evaluate derived_outputs expressions and merge results into the artifact.

        Returns an error string on failure, None on success.
        """
        if not step_def.derived_outputs:
            return None
        if not envelope or not envelope.artifact:
            return None
        try:
            from stepwise.yaml_loader import evaluate_derived_outputs
            computed = evaluate_derived_outputs(step_def.derived_outputs, envelope.artifact)
            envelope.artifact.update(computed)
        except ValueError as e:
            return f"Step '{step_def.name}': {e}"
        return None

    def _validate_artifact(self, step_def: StepDefinition, envelope: HandoffEnvelope | None) -> str | None:
        """M1: hard validation — artifact must contain all declared output fields.
        Returns error string if validation fails, None if valid.
        Fields prefixed with _ are exempt (auto-injected metadata like _session_id).
        """
        if not step_def.outputs:
            return None  # No declared outputs to validate
        if not envelope or not envelope.artifact:
            # Check if all outputs are optional
            if step_def.output_schema:
                all_optional = all(
                    not step_def.output_schema[f].required
                    for f in step_def.outputs
                    if f in step_def.output_schema
                )
                if all_optional:
                    return None
            return (
                f"Step '{step_def.name}' declares outputs {step_def.outputs} "
                f"but artifact is empty"
            )
        # Filter out _-prefixed keys from artifact for validation purposes
        declared_keys = {k for k in envelope.artifact if not k.startswith("_")}
        missing = []
        for f in step_def.outputs:
            if f in envelope.artifact:
                continue
            # Skip optional fields
            if f in step_def.output_schema and not step_def.output_schema[f].required:
                continue
            missing.append(f)
        if missing:
            return (
                f"Step '{step_def.name}' artifact missing declared outputs: {missing} "
                f"(got: {list(declared_keys)})"
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
        sub_meta = copy.deepcopy(parent_job.metadata)
        sub_meta["sys"]["parent_job_id"] = parent_job.id
        sub_job = self.create_job(
            objective=sub_def.objective,
            workflow=sub_def.workflow,
            inputs=parent_run.inputs or {},
            config=sub_def.config or parent_job.config,
            parent_job_id=parent_job.id,
            parent_step_run_id=parent_run.id,
            workspace_path=sub_workspace,
            metadata=sub_meta,
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
        """Merge outputs from all completed terminal steps.

        Terminal steps may be mutually exclusive (conditional branches) or
        parallel. Merging all completed terminals produces the union of
        whichever branches actually ran.
        """
        terminal = job.workflow.terminal_steps()
        if not terminal:
            return HandoffEnvelope(artifact={}, sidecar=Sidecar(), workspace=job.workspace_path, timestamp=_now())
        merged: dict = {}
        last_result: HandoffEnvelope | None = None
        for name in terminal:
            run = self.store.latest_completed_run(job.id, name)
            if run and run.result and run.result.artifact:
                merged.update(run.result.artifact)
                last_result = run.result
        if last_result:
            return HandoffEnvelope(
                artifact=merged,
                sidecar=last_result.sidecar,
                workspace=last_result.workspace,
                timestamp=last_result.timestamp,
            )
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

        # Cost limit (only enforced for api_key billing)
        if limits.max_cost_usd and self.billing_mode == "api_key":
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
                  error: str, error_category: str | None = None,
                  traceback_str: str | None = None) -> None:
        """Fail a step run and evaluate exit rules for error routing.
        If no exit rule handles the failure, halt the job (unless on_error: continue).
        """
        run.status = StepRunStatus.FAILED
        run.error = error
        run.error_category = error_category
        run.traceback = traceback_str
        run.pid = None
        if run.result is None:
            run.result = HandoffEnvelope(
                artifact={"error_category": error_category, "_error": error} if error_category else {"_error": error},
                sidecar=Sidecar(),
                workspace=job.workspace_path,
                timestamp=_now(),
            )
        else:
            # Inject error info into existing artifact for exit rule evaluation
            if error_category:
                run.result.artifact["error_category"] = error_category
            run.result.artifact["_error"] = error
        run.completed_at = _now()
        self.store.save_run(run)

        self._emit(job.id, STEP_FAILED, {
            "step": run.step_name,
            "attempt": run.attempt,
            "error": error,
            "error_category": error_category,
            "on_error": step_def.on_error,
        })

        # on_error: continue — record failure but do not halt the job.
        # Downstream steps will receive a null/error marker for this step's outputs.
        if step_def.on_error == "continue":
            _engine_logger.info(
                "Step '%s' failed with on_error=continue — job continues",
                run.step_name,
            )
            return

        # ── Circuit breaker: permanent errors halt immediately ──
        _PERMANENT_ERROR_CATEGORIES = {"auth_error", "quota_error", "context_length"}
        if error_category in _PERMANENT_ERROR_CATEGORIES:
            _engine_logger.warning(
                "Permanent error for step '%s' (category=%s) — halting job immediately",
                run.step_name, error_category,
            )
            self._halt_job(job, run)
            return

        # ── Circuit breaker: consecutive infrastructure failures ──
        max_infra_retries = step_def.limits.max_infra_retries if step_def.limits else 3
        if max_infra_retries > 0:
            recent_runs = self.store.runs_for_step(job.id, run.step_name)
            consecutive_failures = 0
            for r in reversed(recent_runs):
                if r.status == StepRunStatus.FAILED:
                    consecutive_failures += 1
                else:
                    break
            if consecutive_failures >= max_infra_retries:
                _engine_logger.error(
                    "Step '%s' hit circuit breaker: %d consecutive failures "
                    "(max_infra_retries=%d, last error: %s)",
                    run.step_name, consecutive_failures, max_infra_retries, error,
                )
                run.error = (
                    f"Step '{run.step_name}' failed after {consecutive_failures} "
                    f"consecutive failures (last: {error})"
                )
                self.store.save_run(run)
                self._halt_job(job, run)
                return

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
                                total_count = self.store.run_count(job.id, target)
                                if total_count >= max_iterations:
                                    self._halt_job(job, run)
                                    return
                            self._emit(job.id, LOOP_ITERATION, {
                                "step": run.step_name, "target": target,
                                "count": self.store.run_count(job.id, target),
                            })
                            # Step 7 (§11.5): bump loop frame iteration_index
                            # for failure-routing loop fires too.
                            parent_fid = self._parent_frame_for_target(job, target)
                            frame = self._get_or_create_loop_frame(
                                job, frame_id=target, parent_frame_id=parent_fid,
                            )
                            frame.iteration_index += 1
                            frame.presence.clear()
                            self._invalidate_child_frames(job, parent_frame_id=target)
                            try:
                                self.store.save_job(job)
                            except Exception:
                                _engine_logger.warning(
                                    "failed to persist loop_frames after fail-loop",
                                    exc_info=True,
                                )
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
                            self._cleanup_job_sessions(job.id, job)
                            return
                        case "advance":
                            return  # Move past the failure

        # No exit rule handled the failure — halt the job
        self._halt_job(job, run)

    # ── Halt ──────────────────────────────────────────────────────────────

    def _halt_job(self, job: Job, run: StepRun) -> None:
        """Halt job on step failure."""
        self._settle_unstarted_steps(job)
        job.status = JobStatus.FAILED
        job.updated_at = _now()
        self.store.save_job(job)
        self._emit(job.id, JOB_FAILED, {
            "reason": "step_failed",
            "step": run.step_name,
            "error": run.error,
        })
        self._cleanup_job_sessions(job.id, job)

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
        rowid = self.store.save_event(event)

        # Load job once for metadata and notify_url
        try:
            job = self.store.load_job(job_id)
            job_metadata = job.metadata
        except KeyError:
            job_metadata = {"sys": {}, "app": {}}
            job = None

        envelope = build_event_envelope(
            event_type, event.data, job_id, rowid,
            job_metadata, event.timestamp.isoformat(),
        )

        # Dispatch to event stream subscribers
        if self.on_event is not None:
            self.on_event(envelope)

        # Fire project hooks for relevant events
        fire_hook_for_event(event_type, event.data, job_id, self.project_dir, envelope=envelope)
        # Fire webhook notification if configured on the job
        if job and job.notify_url:
            fire_notify_webhook(
                event_type, event.data, job_id, job.notify_url,
                job.notify_context, envelope=envelope,
            )

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


# ── Async Engine ─────────────────────────────────────────────────────────

import asyncio
import logging as _logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_async_logger = _logging.getLogger("stepwise.async_engine")


class _SessionLockManager:
    """Serialize concurrent access to agent sessions.

    Used by AsyncEngine to prevent multiple steps from accessing
    the same agent session simultaneously.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def is_locked(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()


def _unwrap_executor(executor: "Executor") -> "Executor":
    """Unwrap decorator chain to reach the inner executor.

    Decorators (TimeoutDecorator, RetryDecorator, FallbackDecorator) store the
    inner executor as self._executor but do NOT implement __getattr__. Calling
    finalize_surviving() on a decorated executor raises AttributeError. Walk
    until we reach a non-decorator (no _executor attr).
    """
    while hasattr(executor, "_executor"):
        executor = executor._executor
    return executor


class AsyncEngine(Engine):
    """Event-driven async workflow engine.

    Runs executors in a thread pool via asyncio.to_thread(). Steps complete by
    pushing events onto an asyncio.Queue; the main loop reacts immediately
    instead of polling. All business logic (readiness, exit rules, input
    resolution, currentness) is inherited from Engine.
    """

    def __init__(
        self,
        store: SQLiteStore,
        registry: ExecutorRegistry | None = None,
        jobs_dir: str | None = None,
        project_dir: Path | None = None,
        billing_mode: str = "subscription",
        config: object | None = None,
        cache: "StepResultCache | None" = None,
        max_concurrent_jobs: int = 0,  # 0 = unlimited
    ) -> None:
        super().__init__(store, registry, jobs_dir, project_dir, billing_mode=billing_mode, config=config, cache=cache)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: dict = {}  # run_id → Task or Future (for cancellation)
        self._poll_tasks: dict = {}  # run_id → asyncio.Task (poll watch timers)
        self._job_done: dict[str, asyncio.Event] = {}  # job_id → done signal
        self._loop: asyncio.AbstractEventLoop | None = None  # set by run()
        self.on_broadcast: Callable[[dict], None] | None = None
        self._session_locks = _SessionLockManager()
        self.max_concurrent_jobs = max_concurrent_jobs
        pool_size = int(os.environ.get("STEPWISE_EXECUTOR_THREADS", "32"))
        self._executor_pool = ThreadPoolExecutor(
            max_workers=pool_size, thread_name_prefix="stepwise-exec"
        )
        # Per-executor-type dispatch gating
        self._executor_limits: dict[str, int] = {}
        self._task_exec_types: dict[str, str] = {}  # run_id → executor type name
        self._throttled_jobs: set[str] = set()  # job IDs with steps waiting for executor capacity
        if config and hasattr(config, "resolved_executor_limits"):
            self._executor_limits = config.resolved_executor_limits()
        self._agent_last_launch: float = 0.0  # monotonic timestamp
        self._agent_stagger_lock = asyncio.Lock()
        self._agent_stagger_seconds = 2.0

    async def shutdown(self) -> None:
        """Clean up thread pool and cancel pending tasks."""
        self._executor_pool.shutdown(wait=False)
        for task in self._tasks.values():
            task.cancel()
        for task in self._poll_tasks.values():
            task.cancel()
        self._task_exec_types.clear()
        self._throttled_jobs.clear()

    # ── Main loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop — blocks on queue, processes events.

        Uses a 5-second timeout on queue.get() so that external state changes
        (e.g. `stepwise fulfill` from another process) are picked up even when
        no internal events are queued.
        """
        self._loop = asyncio.get_running_loop()
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # Poll for external changes — check if any suspended runs
                # were fulfilled by another process (e.g. CLI fulfill)
                self._poll_external_changes()
                continue
            try:
                self._handle_queue_event(event)
            except Exception:
                _async_logger.error("Error handling queue event", exc_info=True)

    def _poll_external_changes(self) -> None:
        """Check for runs that were fulfilled externally (by another engine instance).

        Also detects stuck RUNNING steps whose executor task has vanished from
        the task registry (e.g. thread pool crash without pushing to queue).

        Belt-and-suspenders: also checks runs that ARE in _tasks but whose
        subprocess PID is dead (e.g. process crashed but thread is stuck on I/O).

        _dispatch_ready is idempotent — it only launches steps whose deps are
        met and that don't already have a run. Safe to call unconditionally.
        """
        from stepwise.process_lifecycle import _is_pid_alive

        for job in self.store.active_jobs():
            # Detect stuck running steps: run is RUNNING but no task in registry.
            # Only flag if started >60s ago (avoids race with task creation).
            for run in self.store.running_runs(job.id):
                if run.id not in self._tasks and run.started_at:
                    age = (_now() - run.started_at).total_seconds()
                    if age > 60:
                        _async_logger.warning(
                            "Stuck running step detected: %s/%s (run %s, age %.0fs) — "
                            "no executor task found, routing through _fail_run",
                            job.id, run.step_name, run.id, age,
                        )
                        self._task_exec_types.pop(run.id, None)
                        step_def = job.workflow.steps.get(run.step_name)
                        if step_def is None:
                            run.status = StepRunStatus.FAILED
                            run.error = "Executor task lost (orphan step — no definition found)"
                            run.completed_at = _now()
                            self.store.save_run(run)
                            self._emit(job.id, STEP_FAILED, {
                                "step": run.step_name,
                                "error": run.error,
                            })
                        else:
                            self._fail_run(
                                job, run, step_def,
                                error="Executor task lost (possible thread pool crash)",
                                error_category="infra_failure",
                            )
                # PID liveness check: task exists but subprocess is dead
                # (e.g. process crashed but executor thread is stuck on I/O)
                elif run.id in self._tasks and run.pid and run.started_at:
                    if not _is_pid_alive(run.pid):
                        age = (_now() - run.started_at).total_seconds()
                        if age > 30:  # 30s grace for process teardown
                            _async_logger.warning(
                                "Dead PID detected: %s/%s (run %s, PID %d, age %.0fs)",
                                job.id, run.step_name, run.id, run.pid, age,
                            )
                            task = self._tasks.pop(run.id, None)
                            if task:
                                task.cancel()
                            self._task_exec_types.pop(run.id, None)
                            step_def = job.workflow.steps.get(run.step_name)
                            if step_def:
                                self._fail_run(
                                    job, run, step_def,
                                    error=f"Script process died (PID {run.pid} no longer alive)",
                                    error_category="infra_failure",
                                )
            self._dispatch_ready(job.id)
            self._check_job_terminal(job.id)

    # ── Job lifecycle overrides ──────────────────────────────────────────

    def start_job(self, job_id: str) -> None:
        """Start a job and dispatch all initially-ready steps.

        If max_concurrent_jobs is reached, the job stays PENDING and will be
        started when a slot opens (see _start_queued_jobs).
        """
        job = self.store.load_job(job_id)
        if job.status != JobStatus.PENDING:
            raise ValueError(f"Cannot start job in status {job.status.value}")
        if self.max_concurrent_jobs > 0 and len(self.store.active_jobs()) >= self.max_concurrent_jobs:
            self._emit(job_id, JOB_QUEUED)
            _async_logger.info(
                "Job %s queued: %d concurrent jobs at limit",
                job_id, self.max_concurrent_jobs,
            )
            return  # stays PENDING, started later by _start_queued_jobs
        # Group concurrency limit
        if job.job_group:
            group_limit = self.store.get_group_max_concurrent(job.job_group)
            if group_limit > 0 and len(self.store.active_jobs_in_group(job.job_group)) >= group_limit:
                self._emit(job_id, JOB_QUEUED)
                _async_logger.info(
                    "Job %s queued: group '%s' at capacity (%d/%d)",
                    job_id, job.job_group, group_limit, group_limit,
                )
                return
        # Resolve cross-job data references before running
        self._resolve_job_ref_inputs(job)
        # Build named session registry
        self._ensure_session_registry(job)
        # Atomic status transition: only set RUNNING if still PENDING
        # (prevents race with concurrent cancel_job)
        updated = self.store.atomic_status_transition(
            job_id, from_status=JobStatus.PENDING, to_status=JobStatus.RUNNING
        )
        if not updated:
            _async_logger.info(
                "Job %s was cancelled before it could start (race condition avoided)",
                job_id,
            )
            return
        self._emit(job_id, JOB_STARTED)
        self._dispatch_ready(job_id)

    def recover_jobs(self) -> None:
        """Re-evaluate all RUNNING server-owned jobs after startup.

        Phases:
        0. Clean up orphaned snapshot .tmp files from interrupted snapshots
           (per §9.3).
        1. Recover dead script runs (stdout file recovery for crashed subprocesses).
        2. Re-execute fork-source steps in indeterminate state (RUNNING but
           no snapshot_uuid in executor_state — per §9.3).
        3. Settle terminal jobs (complete or fail jobs whose steps are all done).
        4. Dispatch ready steps (launch steps whose deps completed while server was down).

        Also reconciles PENDING jobs whose queue deps may have completed.

        Safe to call multiple times — all operations are idempotent.
        """
        # Phase 0: orphan tmp cleanup before any per-job recovery so the
        # recovery code never trips over stale snapshot temp files.
        try:
            from stepwise.snapshot import cleanup_orphaned_tmps
            removed = cleanup_orphaned_tmps()
            if removed:
                _async_logger.info(
                    "recover_jobs: cleaned up %d orphaned snapshot tmp file(s)",
                    removed,
                )
        except Exception:
            _async_logger.warning(
                "recover_jobs: orphan tmp cleanup failed", exc_info=True,
            )

        for job in self.store.active_jobs():
            if job.created_by != "server":
                continue
            self._recover_dead_script_runs(job)
            self._recover_fork_source_steps_without_snapshot(job)
            # Step 7 (§11.5): rebuild LoopFrame stack from step_runs in case
            # the engine crashed mid-loop and the in-memory frames were lost.
            try:
                self._rebuild_loop_frames(job)
            except Exception:
                _async_logger.warning(
                    "recover_jobs: failed to rebuild loop frames for job %s",
                    job.id, exc_info=True,
                )
            self._check_job_terminal(job.id)
        # Dispatch ready steps for RUNNING jobs — catches steps that became ready
        # while the server was down (e.g. upstream completed, downstream never launched).
        # _dispatch_ready is idempotent: skips jobs that aren't RUNNING and steps
        # that already have active runs, so this is safe after reattach.
        for job in self.store.active_jobs():
            if job.created_by != "server":
                continue
            self._dispatch_ready(job.id)
        # Reconcile pending jobs with deps — a dep may have completed while server was down
        self._start_queued_jobs()

    def _recover_dead_script_runs(self, job: Job) -> None:
        """Recover RUNNING script step runs whose process died but left output files.

        On server restart, script steps that were mid-flight have lost their
        stdout pipe. If the subprocess wrote its output to the step-io file
        before dying, we can recover the result. If not, the run is failed.

        Only handles script-type executors. Agent reattach is handled separately
        by reattach_surviving_runs().
        """
        from pathlib import Path
        from stepwise.process_lifecycle import _is_pid_alive

        for run in self.store.running_runs(job.id):
            step_def = job.workflow.steps.get(run.step_name)
            if not step_def or step_def.executor.type != "script":
                continue

            if not run.pid:
                # No PID stored — can't determine process state, fail the run
                _async_logger.warning(
                    "Script run %s (step %s) has no PID — failing as unrecoverable",
                    run.id, run.step_name,
                )
                self._fail_run(
                    job, run, step_def,
                    error="Script process lost on restart (no PID stored, no stdout file)",
                )
                continue

            if _is_pid_alive(run.pid):
                continue  # Still running — will be handled by normal engine flow

            # PID is dead — attempt file-based recovery
            workspace = job.workspace_path or "."
            step_io_dir = Path(workspace) / ".stepwise" / "step-io"
            stdout_path = step_io_dir / f"{run.step_name}-{run.attempt}.stdout"
            exitcode_path = step_io_dir / f"{run.step_name}-{run.attempt}.exitcode"
            stderr_path = step_io_dir / f"{run.step_name}-{run.attempt}.stderr"

            if not stdout_path.exists():
                _async_logger.warning(
                    "Script run %s (step %s, PID %d) died with no stdout file — failing",
                    run.id, run.step_name, run.pid,
                )
                self._fail_run(
                    job, run, step_def,
                    error="Script process died on restart (PID gone, no stdout file for recovery)",
                )
                continue

            # Read recovered output
            stdout = stdout_path.read_text().strip()
            stderr = stderr_path.read_text().strip() if stderr_path.exists() else ""
            exitcode = 0
            if exitcode_path.exists():
                try:
                    exitcode = int(exitcode_path.read_text().strip())
                except ValueError:
                    pass

            _async_logger.info(
                "Recovering script run %s (step %s, PID %d) from stdout file (exitcode=%d)",
                run.id, run.step_name, run.pid, exitcode,
            )

            # Build ExecutorResult as ScriptExecutor would, then process it
            base_meta: dict = {
                "shell_mode": "recovered",
                "return_code": exitcode,
                "recovered": True,
            }
            if stdout:
                base_meta["stdout"] = stdout
            if stderr:
                base_meta["stderr"] = stderr

            if exitcode != 0:
                result = ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"stdout": stdout} if stdout else {"stdout": ""},
                        sidecar=Sidecar(),
                        workspace=workspace,
                        timestamp=_now(),
                        executor_meta={**base_meta, "failed": True},
                    ),
                    executor_state={"failed": True, "error": stderr or f"Exit code {exitcode}"},
                )
            else:
                # Parse stdout as JSON (same logic as ScriptExecutor)
                artifact: dict
                try:
                    parsed = json.loads(stdout) if stdout else {}
                    artifact = parsed if isinstance(parsed, dict) else {"stdout": stdout}
                except (json.JSONDecodeError, ValueError):
                    artifact = {"stdout": stdout} if stdout else {"stdout": ""}

                result = ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact=artifact,
                        sidecar=Sidecar(),
                        workspace=workspace,
                        timestamp=_now(),
                        executor_meta=base_meta,
                    ),
                )

            self._process_launch_result(job, run, result)

    def _recover_fork_source_steps_without_snapshot(self, job: Job) -> None:
        """Re-execute RUNNING fork-source steps that crashed before snapshot persistence.

        Per §9.3 of the coordination doc: if the runner crashed between
        subprocess exit and snapshot persistence, the step run is in an
        indeterminate state. The recovery action is to re-execute the
        step, which carries a documented v1.0 acceptable wart: claude
        CLI's --resume mutates the session by appending to it, so
        re-running the subprocess may append the same turn a second
        time. Acceptable for v1.0; flagged as a known limitation.

        Idempotent: a fork-source step whose snapshot WAS persisted has
        snapshot_uuid in executor_state and is left alone.
        """
        fork_sources = self._fork_source_step_names(job)
        if not fork_sources:
            return
        for run in self.store.running_runs(job.id):
            if run.step_name not in fork_sources:
                continue
            es = run.executor_state or {}
            if es.get("snapshot_uuid"):
                continue  # snapshot was persisted; recovery is a no-op
            _async_logger.warning(
                "fork-source step %r (run %s) is in RUNNING state without "
                "snapshot_uuid — re-executing per §9.3 (duplicate-turn risk)",
                run.step_name, run.id,
            )
            step_def = job.workflow.steps.get(run.step_name)
            if step_def is None:
                continue
            self._fail_run(
                job, run, step_def,
                error="Crash during fork-source snapshot critical section",
                error_category="fork_source_crash_recovery",
            )

    # ── Restart resilience: reattach surviving runs ─────────────────────

    def _get_exec_ref_for_run(self, job: Job, run: StepRun) -> ExecutorRef:
        """Reconstruct ExecutorRef for a surviving run, replaying config enrichment.

        Mirrors the config injection from _prepare_step_run() so that
        registry.create(exec_ref) produces an executor with the same
        config as the original launch.
        """
        step_def = job.workflow.steps[run.step_name]
        exec_ref = step_def.executor
        if step_def.outputs and "output_fields" not in exec_ref.config:
            exec_ref = exec_ref.with_config({"output_fields": step_def.outputs})
        if exec_ref.type == "agent":
            session_ctx: dict = {"_billing_mode": self.billing_mode}

            # Named sessions
            if step_def.session:
                self._ensure_session_registry(job)
                if job.id in self._session_registries:
                    session_state = self._session_registries[job.id].get(step_def.session)
                    if session_state:
                        session_ctx["_session_name"] = session_state.name
                        session_ctx["_agent"] = session_state.agent
                        if session_state.session_id:
                            session_ctx["_session_uuid"] = session_state.session_id

            # Legacy continue_session
            elif step_def.continue_session:
                session_ctx["continue_session"] = True

            if step_def.loop_prompt is not None:
                session_ctx["loop_prompt"] = step_def.loop_prompt
            if step_def.max_continuous_attempts is not None:
                session_ctx["max_continuous_attempts"] = step_def.max_continuous_attempts
            if session_ctx:
                exec_ref = exec_ref.with_config(session_ctx)
            if exec_ref.config.get("emit_flow"):
                emit_ctx: dict = {"_registry": self.registry, "_config": self.config}
                depth = self._get_job_depth(job)
                max_depth = job.config.max_sub_job_depth
                emit_ctx["_depth_remaining"] = max(0, max_depth - depth - 1)
                if self.project_dir:
                    emit_ctx["_project_dir"] = self.project_dir.parent
                exec_ref = exec_ref.with_config(emit_ctx)
        return exec_ref

    async def _monitor_surviving_run(
        self,
        job_id: str,
        step_name: str,
        run_id: str,
        executor_state: dict,
        exec_ref: ExecutorRef,
    ) -> None:
        """Monitor a surviving agent process from a previous server instance.

        Creates the executor via registry.create (preserving custom registries),
        unwraps the decorator chain, calls finalize_surviving() in the thread
        pool, then pushes the result to the engine queue for normal processing.

        Does NOT acquire the agent semaphore (process already running).
        Does NOT acquire session lock (no prompt being sent).
        """
        try:
            executor = self.registry.create(exec_ref)
            inner = _unwrap_executor(executor)
            if not hasattr(inner, "finalize_surviving"):
                raise TypeError(
                    f"Executor type {exec_ref.type} does not support finalize_surviving"
                )
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor_pool,
                inner.finalize_surviving,
                executor_state,
            )
            await self._queue.put(("step_result", job_id, step_name, run_id, result))
        except asyncio.CancelledError:
            return
        except Exception as e:
            _async_logger.error(
                "Reattach failed for step %s (job %s, run %s): %s",
                step_name, job_id, run_id, e, exc_info=True,
            )
            await self._queue.put(("step_error", job_id, step_name, run_id, e))

    async def reattach_surviving_runs(self) -> int:
        """Reattach monitoring tasks for agent steps that survived server restart.

        MUST be called after _cleanup_zombie_jobs() and recover_jobs(), but
        before run(). Runs inside the lifespan async context.

        For RUNNING step runs with live PIDs:
        - Creates _monitor_surviving_run() coroutine
        - Registers in self._tasks to prevent _poll_external_changes kill

        For SUSPENDED step runs with poll watches:
        - Re-schedules poll watch timers (skips if already exists)

        Returns the number of runs reattached.
        """
        reattached = 0
        for job in self.store.active_jobs():
            if job.created_by != "server":
                continue

            for run in self.store.running_runs(job.id):
                if not run.executor_state or not run.pid:
                    await self._queue.put(("step_error", job.id, run.step_name, run.id,
                        RuntimeError("No executor_state for reattach")))
                    continue
                exec_ref = self._get_exec_ref_for_run(job, run)
                task = asyncio.create_task(
                    self._monitor_surviving_run(
                        job.id, run.step_name, run.id, run.executor_state, exec_ref)
                )
                self._tasks[run.id] = task
                self._task_exec_types[run.id] = exec_ref.type
                reattached += 1
                _async_logger.info(
                    "Reattaching surviving run %s (job %s step %s, PID %d)",
                    run.id, job.id, run.step_name, run.pid,
                )

            for run in self.store.suspended_runs(job.id):
                if (run.watch and run.watch.mode == "poll"
                        and run.id not in self._poll_tasks):
                    self._schedule_poll_watch(job.id, run.id, run.watch)
                    reattached += 1
                    _async_logger.info(
                        "Rescheduled poll watch for run %s (job %s step %s)",
                        run.id, job.id, run.step_name,
                    )

        return reattached

    def resume_job(self, job_id: str) -> None:
        job = self.store.load_job(job_id)
        resumable = {JobStatus.PAUSED, JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.FAILED}
        if job.status not in resumable:
            raise ValueError(f"Cannot resume job in status {job.status.value}")
        job.status = JobStatus.RUNNING
        job.updated_at = _now()
        self.store.save_job(job)
        self._prepare_suspended_runs_for_resume(job_id)
        for run in self.store.suspended_runs(job_id):
            if run.watch and run.watch.mode == "poll" and run.id not in self._poll_tasks:
                self._schedule_poll_watch(job_id, run.id, run.watch)
        self._emit(job_id, JOB_RESUMED)
        self._dispatch_ready(job_id)

    def cancel_job(self, job_id: str) -> None:
        from stepwise.process_lifecycle import kill_job_processes

        # Snapshot running runs before cancel mutates their status
        running_runs = list(self.store.running_runs(job_id))

        # Cancel running async tasks before cancelling runs
        for run in running_runs:
            task = self._tasks.pop(run.id, None)
            self._task_exec_types.pop(run.id, None)
            if task:
                task.cancel()
        # Cancel poll watch timers for suspended runs
        for run in self.store.suspended_runs(job_id):
            self._cancel_poll_task(run.id)

        # Kill runner processes directly (SIGTERM → 5s grace → SIGKILL).
        # This runs before super().cancel_job() to ensure processes are killed
        # even if executor.cancel() fails due to missing/incomplete state.
        killed = kill_job_processes(running_runs, grace_seconds=5)
        if killed:
            _async_logger.info(
                "Killed %d runner process(es) for cancelled job %s: %s",
                len(killed), job_id, killed,
            )

        super().cancel_job(job_id)
        self._signal_job_done(job_id)
        # Recursive cascade: cancel all STAGED/PENDING transitive dependents
        visited: set[str] = set()
        queue = list(self.store.get_job_dependents(job_id))
        while queue:
            dep_job_id = queue.pop(0)
            if dep_job_id in visited:
                continue
            visited.add(dep_job_id)
            try:
                dep_job = self.store.load_job(dep_job_id)
                if dep_job.status in (JobStatus.PENDING, JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
                    dep_job.status = JobStatus.CANCELLED
                    dep_job.updated_at = _now()
                    self.store.save_job(dep_job)
                    self._emit(dep_job_id, JOB_CANCELLED, {
                        "reason": "dependency_cancelled",
                        "cancelled_dep": job_id,
                    })
                    self._signal_job_done(dep_job_id)
                    # Recurse into this job's dependents
                    queue.extend(self.store.get_job_dependents(dep_job_id))
            except KeyError:
                pass
        # A slot opened — start queued jobs
        self._start_queued_jobs()

    def pause_job(self, job_id: str) -> None:
        from stepwise.process_lifecycle import kill_job_processes

        # Snapshot running runs before pause mutates their status
        running_runs = list(self.store.running_runs(job_id))

        # Cancel running async tasks before pausing runs
        for run in running_runs:
            task = self._tasks.pop(run.id, None)
            self._task_exec_types.pop(run.id, None)
            if task:
                task.cancel()
        # Cancel poll watch timers for suspended runs
        for run in self.store.suspended_runs(job_id):
            self._cancel_poll_task(run.id)

        # Kill runner processes directly (SIGTERM only — job may be resumed).
        killed = kill_job_processes(running_runs, grace_seconds=0)
        if killed:
            _async_logger.info(
                "Killed %d runner process(es) for paused job %s: %s",
                len(killed), job_id, killed,
            )

        super().pause_job(job_id)
        # A slot opened — start queued jobs
        self._start_queued_jobs()

    def fulfill_watch(self, run_id: str, payload: dict) -> dict | None:
        self._cancel_poll_task(run_id)
        result = super().fulfill_watch(run_id, payload)
        if result is None:  # success
            run = self.store.load_run(run_id)
            self._dispatch_ready(run.job_id)
            self._check_job_terminal(run.job_id)
        return result

    def rerun_step(self, job_id: str, step_name: str) -> StepRun:
        run = super().rerun_step(job_id, step_name)
        self._dispatch_ready(job_id)
        self._check_job_terminal(job_id)
        return run

    def reset_job(self, job_id: str) -> None:
        # Cancel running async tasks before clearing runs
        for run in self.store.runs_for_job(job_id):
            task = self._tasks.pop(run.id, None)
            self._task_exec_types.pop(run.id, None)
            if task:
                task.cancel()
            self._cancel_poll_task(run.id)

        for descendant_id in self._collect_descendant_job_ids(job_id):
            for run in self.store.runs_for_job(descendant_id):
                task = self._tasks.pop(run.id, None)
                self._task_exec_types.pop(run.id, None)
                if task:
                    task.cancel()
                self._cancel_poll_task(run.id)

        super().reset_job(job_id)
        self._job_done[job_id] = asyncio.Event()

    async def wait_for_job(self, job_id: str, timeout: float | None = None) -> Job:
        """Wait for a job to reach a terminal state."""
        if job_id not in self._job_done:
            self._job_done[job_id] = asyncio.Event()

        # Already terminal?
        job = self.store.load_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job

        if timeout:
            await asyncio.wait_for(self._job_done[job_id].wait(), timeout)
        else:
            await self._job_done[job_id].wait()
        return self.store.load_job(job_id)

    # ── Step dispatch ────────────────────────────────────────────────────

    def _dispatch_ready(self, job_id: str) -> None:
        """Find and launch all ready steps for a job (respecting executor limits)."""
        job = self.store.load_job(job_id)
        if job.status != JobStatus.RUNNING:
            return
        ready = self._find_ready(job)
        if ready:
            _async_logger.info(f"Dispatching {len(ready)} ready step(s) for job {job_id}: {ready}")
        throttled = False
        for step_name in ready:
            step_def = job.workflow.steps[step_name]
            exec_type = step_def.executor.type
            if self._executor_at_capacity(exec_type):
                _async_logger.debug(
                    "Step %s throttled: %s at capacity (%d/%d)",
                    step_name, exec_type,
                    self._running_count_for_type(exec_type),
                    self._executor_limits.get(exec_type, 0),
                )
                throttled = True
                continue
            self._launch(job, step_name)
            # Reload — _launch may change job status (for_each, route, sub_flow)
            job = self.store.load_job(job_id)
            if job.status != JobStatus.RUNNING:
                self._throttled_jobs.discard(job_id)
                return
        # Track whether this job has steps waiting for executor capacity
        if throttled:
            self._throttled_jobs.add(job_id)
        else:
            self._throttled_jobs.discard(job_id)

    def _launch(self, job: Job, step_name: str) -> StepRun:
        """Override: dispatch normal-step executors to thread pool."""
        # Guard against concurrent launches of the same step (race between
        # HTTP fulfill thread and event loop poll thread).
        existing = self.store.latest_run(job.id, step_name)
        if existing and existing.status == StepRunStatus.RUNNING:
            _async_logger.debug(
                "Skipping duplicate launch of %s/%s — already running (run %s)",
                job.id, step_name, existing.id,
            )
            return existing

        step_def = job.workflow.steps[step_name]

        # Special step types: synchronous (they create sub-jobs)
        if step_def.for_each and step_def.sub_flow:
            try:
                return self._launch_for_each(job, step_def)
            except (ValueError, KeyError) as e:
                import traceback as tb_mod
                _async_logger.error(
                    f"For-each step '{step_name}' failed to launch: {e}", exc_info=True
                )
                tb_str = "".join(tb_mod.format_exception(type(e), e, e.__traceback__))
                run = StepRun(
                    id=_gen_id("run"),
                    job_id=job.id,
                    step_name=step_name,
                    attempt=self.store.next_attempt(job.id, step_name),
                    status=StepRunStatus.FAILED,
                    error=str(e),
                    traceback=tb_str,
                    started_at=_now(),
                    completed_at=_now(),
                )
                self.store.save_run(run)
                self._emit(job.id, STEP_FAILED, {"step": step_name, "error": str(e)})
                self._halt_job(job, run)
                return run

        if step_def.executor.type == "sub_flow" and step_def.sub_flow:
            return self._launch_sub_flow(job, step_def)

        # Normal step: prepare run, dispatch executor to thread pool
        run, exec_ref, inputs, ctx = self._prepare_step_run(job, step_name)

        # Cache hit — _prepare_step_run already completed the run
        if exec_ref is None:
            self._after_step_change(job.id)
            return run

        coro = self._run_executor(job.id, step_name, run.id, exec_ref, inputs, ctx)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
        except RuntimeError:
            # Called from a non-async thread (e.g. FastAPI threadpool)
            if self._loop is None:
                raise RuntimeError("AsyncEngine.run() must be started before dispatching steps")
            task = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self._tasks[run.id] = task
        self._task_exec_types[run.id] = step_def.executor.type
        return run

    async def _apply_agent_stagger(self) -> None:
        """Enforce a minimum delay between consecutive agent launches."""
        async with self._agent_stagger_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._agent_last_launch
            if elapsed < self._agent_stagger_seconds:
                await asyncio.sleep(self._agent_stagger_seconds - elapsed)
            self._agent_last_launch = asyncio.get_event_loop().time()

    def _running_count_for_type(self, exec_type: str) -> int:
        """Count in-flight executor tasks of a given type (across all jobs)."""
        return sum(1 for t in self._task_exec_types.values() if t == exec_type)

    def _executor_at_capacity(self, exec_type: str) -> bool:
        """Check if an executor type has hit its concurrency limit."""
        limit = self._executor_limits.get(exec_type, 0)
        return limit > 0 and self._running_count_for_type(exec_type) >= limit

    async def _run_executor(
        self,
        job_id: str,
        step_name: str,
        run_id: str,
        exec_ref: "ExecutorRef",
        inputs: dict,
        ctx: ExecutionContext,
    ) -> None:
        """Run executor.start() in thread pool, push result to queue."""
        _async_logger.info(
            f"Executor coroutine started for {step_name} (job {job_id}, type={exec_ref.type})"
        )
        try:
            if exec_ref.type == "agent":
                await self._apply_agent_stagger()

            executor = self.registry.create(exec_ref)

            # Capture event loop ref — update_state runs in thread pool
            # and must schedule store access back on the event loop to avoid
            # concurrent sqlite3 access with the engine's main thread.
            try:
                _loop = asyncio.get_running_loop()
            except RuntimeError:
                _loop = None

            def update_state(state: dict) -> None:
                def _do_update():
                    run = self.store.load_run(run_id)
                    run.executor_state = state
                    if "pid" in state:
                        run.pid = state["pid"]
                    self.store.save_run(run)
                    # Broadcast tick when usage limit state changes so UI refreshes
                    if "usage_limit_waiting" in state and self.on_broadcast:
                        self.on_broadcast({"job_id": job_id})
                if _loop and _loop.is_running():
                    _loop.call_soon_threadsafe(_do_update)
                else:
                    _do_update()
            ctx.state_update_fn = update_state

            # Session locking: serialize access by named session or legacy _session_id
            loop = asyncio.get_running_loop()
            active = len([t for t in self._executor_pool._threads if t.is_alive()])
            _async_logger.info(
                f"Submitting {step_name} to thread pool (active threads: {active}/{self._executor_pool._max_workers})"
            )
            # Determine session name for locking: prefer named session from config,
            # fall back to legacy _session_id from inputs
            session_name = exec_ref.config.get("_session_name") or inputs.get("_session_id")
            if session_name:
                lock = self._session_locks.get_lock(session_name)
                async with lock:
                    result = await loop.run_in_executor(
                        self._executor_pool, executor.start, inputs, ctx
                    )
            else:
                result = await loop.run_in_executor(
                    self._executor_pool, executor.start, inputs, ctx
                )

            await self._queue.put(("step_result", job_id, step_name, run_id, result))
        except asyncio.CancelledError:
            # Task was cancelled (job cancellation) — don't push event
            return
        except Exception as e:
            _async_logger.error(
                "Executor thread failed for step %s (job %s, run %s): %s",
                step_name, job_id, run_id, e, exc_info=True,
            )
            await self._queue.put(("step_error", job_id, step_name, run_id, e))

    # ── Poll watch scheduling ────────────────────────────────────────────

    def _schedule_poll_watch(self, job_id: str, run_id: str, watch: WatchSpec) -> None:
        """Start an asyncio task that periodically pushes poll_check events."""
        interval = watch.config.get("interval_seconds", 60)

        async def _poll_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    await self._queue.put(("poll_check", job_id, run_id))
            except asyncio.CancelledError:
                return

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_poll_loop())
        except RuntimeError:
            if self._loop is None:
                return
            task = asyncio.run_coroutine_threadsafe(_poll_loop(), self._loop)
        self._poll_tasks[run_id] = task

    def _cancel_poll_task(self, run_id: str) -> None:
        """Cancel a poll watch timer task."""
        task = self._poll_tasks.pop(run_id, None)
        if task:
            task.cancel()

    # ── Event handling ───────────────────────────────────────────────────

    def _handle_queue_event(self, event: tuple) -> None:
        """Process an event from the queue."""
        event_type = event[0]

        if event_type == "step_result":
            _, job_id, step_name, run_id, result = event
            self._tasks.pop(run_id, None)
            self._task_exec_types.pop(run_id, None)

            try:
                job = self.store.load_job(job_id)
            except KeyError:
                return  # job was removed or never persisted
            if job.status != JobStatus.RUNNING:
                return

            try:
                run = self.store.load_run(run_id)
            except KeyError:
                return
            if run.status != StepRunStatus.RUNNING:
                return  # already handled (e.g. cancelled)

            self._process_launch_result(job, run, result)
            # If the step suspended with a poll watch, schedule periodic checking
            run = self.store.load_run(run_id)
            if run.status == StepRunStatus.SUSPENDED and run.watch and run.watch.mode == "poll":
                self._schedule_poll_watch(job_id, run_id, run.watch)
            self._after_step_change(job_id)

        elif event_type == "step_error":
            _, job_id, step_name, run_id, error = event
            self._tasks.pop(run_id, None)
            self._task_exec_types.pop(run_id, None)

            try:
                job = self.store.load_job(job_id)
            except KeyError:
                return
            if job.status != JobStatus.RUNNING:
                return

            try:
                run = self.store.load_run(run_id)
            except KeyError:
                return
            if run.status != StepRunStatus.RUNNING:
                return

            self._handle_executor_crash(job, run, step_name, error)
            self._after_step_change(job_id)

        elif event_type == "poll_check":
            _, job_id, run_id = event
            try:
                job = self.store.load_job(job_id)
                run = self.store.load_run(run_id)
            except KeyError:
                self._cancel_poll_task(run_id)
                return
            if job.status != JobStatus.RUNNING or run.status != StepRunStatus.SUSPENDED:
                self._cancel_poll_task(run_id)
                return
            if self._check_poll_watch(job, run):
                self._cancel_poll_task(run_id)
                self._after_step_change(job_id)

    def _after_step_change(self, job_id: str) -> None:
        """After a step result is processed, broadcast, dispatch ready steps, check terminal."""
        self._broadcast({"type": "job_changed", "job_id": job_id})
        # Also broadcast ancestor jobs so the UI refreshes parent tree views
        try:
            job = self.store.load_job(job_id)
            parent_id = job.parent_job_id
            while parent_id:
                self._broadcast({"type": "job_changed", "job_id": parent_id})
                parent_job = self.store.load_job(parent_id)
                parent_id = parent_job.parent_job_id
        except KeyError:
            pass
        self._dispatch_ready(job_id)
        # When executor limits are active, a slot opening in this job may
        # unblock throttled steps in other jobs. Re-evaluate all running jobs.
        if self._executor_limits:
            for other_job in self.store.active_jobs():
                if other_job.id != job_id:
                    self._dispatch_ready(other_job.id)
        self._check_job_terminal(job_id)

    def _check_job_terminal(self, job_id: str) -> None:
        """Check if job reached terminal state; settle and complete/fail."""
        job = self.store.load_job(job_id)

        if job.status == JobStatus.RUNNING and self._job_complete(job):
            self._settle_unstarted_steps(job)
            job.status = JobStatus.COMPLETED
            job.updated_at = _now()
            self.store.save_job(job)
            self._emit(job.id, JOB_COMPLETED)
            self._cleanup_job_sessions(job.id, job)
            self._check_dependent_jobs(job.id)
        elif job.status == JobStatus.RUNNING:
            # Don't kill jobs that have steps waiting for executor capacity
            if job_id in self._throttled_jobs:
                return
            # Check for settled-but-failed: nothing active, nothing ready, no terminal completed
            if (not self.store.running_runs(job.id) and
                    not self.store.suspended_runs(job.id) and
                    not self.store.delegated_runs(job.id) and
                    not self._find_ready(job)):
                self._settle_unstarted_steps(job)
                # Re-check: settlement may have skipped when-blocked steps,
                # enabling job completion
                if self._job_complete(job):
                    job.status = JobStatus.COMPLETED
                    job.updated_at = _now()
                    self.store.save_job(job)
                    self._emit(job.id, JOB_COMPLETED)
                    self._cleanup_job_sessions(job.id, job)
                    self._check_dependent_jobs(job.id)
                else:
                    job.status = JobStatus.FAILED
                    job.updated_at = _now()
                    self.store.save_job(job)
                    self._emit(job.id, JOB_FAILED, {"reason": "no_terminal_reached"})
                    self._cleanup_job_sessions(job.id, job)

        # Signal done if terminal
        job = self.store.load_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            self._broadcast({"type": "job_changed", "job_id": job_id, "status": job.status.value})
            self._signal_job_done(job_id)
            # Cascade to parent
            if job.parent_job_id:
                self._handle_sub_job_done(job)
            # A slot opened — start queued jobs
            self._start_queued_jobs()

    def _start_queued_jobs(self) -> None:
        """Start PENDING jobs if slots are available and deps are met (FIFO order)."""
        active_count = len(self.store.active_jobs())
        if self.max_concurrent_jobs > 0 and active_count >= self.max_concurrent_jobs:
            return
        # Pre-load group limits and active counts for efficient checking
        group_limits = self.store.list_group_settings()
        group_active: dict[str, int] = {}
        for pending_job in self.store.pending_jobs_with_deps_met():
            if self.max_concurrent_jobs > 0 and active_count >= self.max_concurrent_jobs:
                break
            # Only auto-start top-level pending jobs (sub-jobs are managed by parent)
            if pending_job.parent_job_id:
                continue
            # Check group concurrency limit
            grp = pending_job.job_group
            if grp and grp in group_limits and group_limits[grp] > 0:
                if grp not in group_active:
                    group_active[grp] = len(self.store.active_jobs_in_group(grp))
                if group_active[grp] >= group_limits[grp]:
                    continue  # skip — group at capacity
            try:
                self.start_job(pending_job.id)
                active_count += 1
                if grp:
                    group_active[grp] = group_active.get(grp, 0) + 1
            except ValueError:
                pass  # job status changed between query and start

    def _broadcast(self, event: dict) -> None:
        """Fire the on_broadcast callback if set."""
        if self.on_broadcast:
            self.on_broadcast(event)

    def _signal_job_done(self, job_id: str) -> None:
        """Signal the asyncio.Event for wait_for_job()."""
        done = self._job_done.get(job_id)
        if done:
            done.set()

    def _handle_sub_job_done(self, sub_job: Job) -> None:
        """When a sub-job finishes, check parent's delegated runs."""
        if not sub_job.parent_job_id:
            return

        try:
            parent_job = self.store.load_job(sub_job.parent_job_id)
        except KeyError:
            return
        if parent_job.status != JobStatus.RUNNING:
            return

        for run in self.store.delegated_runs(parent_job.id):
            # For-each sub-jobs
            if run.executor_state and run.executor_state.get("for_each"):
                if self._check_for_each_completion(parent_job, run):
                    self._after_step_change(parent_job.id)
            # Single sub-job
            elif run.sub_job_id == sub_job.id:
                if sub_job.status == JobStatus.COMPLETED:
                    run.result = self._terminal_output(sub_job)
                    # Inject delegation marker for exit rule evaluation
                    if run.executor_state and run.executor_state.get("emitted_flow"):
                        if run.result and run.result.artifact is not None:
                            run.result.artifact["_delegated"] = True
                    run.status = StepRunStatus.COMPLETED
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._emit(parent_job.id, STEP_COMPLETED, {
                        "step": run.step_name,
                        "attempt": run.attempt,
                    })
                    self._process_completion(parent_job, run)
                    self._after_step_change(parent_job.id)
                elif sub_job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    run.status = StepRunStatus.FAILED
                    run.error = f"Sub-job {sub_job.status.value}"
                    run.completed_at = _now()
                    self.store.save_run(run)
                    self._halt_job(parent_job, run)
                    self._check_job_terminal(parent_job.id)


def _adopt_stale_cli_job(engine: AsyncEngine, job: Job) -> None:
    """Adopt a single stale CLI-owned job, transferring ownership to the server.

    Fails all RUNNING steps (their runner process is dead), transfers ownership,
    and triggers engine re-evaluation so exit rules can recover.
    """
    from stepwise.process_lifecycle import _is_pid_alive

    _engine_logger.info(
        "Auto-adopting stale CLI job %s (%s) — owner %s, last heartbeat %s",
        job.id, job.objective, job.created_by,
        job.heartbeat_at.isoformat() if job.heartbeat_at else "never",
    )

    # Fail all orphaned RUNNING steps whose process is dead
    for run in engine.store.running_runs(job.id):
        if run.pid and _is_pid_alive(run.pid):
            continue  # process still alive — leave it
        run.status = StepRunStatus.FAILED
        run.error = "Runner died: CLI process lost, job adopted by server"
        run.completed_at = _now()
        engine.store.save_run(run)
        _engine_logger.info(
            "Failed orphaned step run %s (step %s) in adopted job %s",
            run.id, run.step_name, job.id,
        )

    # Transfer ownership
    job.created_by = "server"
    job.runner_pid = None
    job.updated_at = _now()
    engine.store.save_job(job)


def _auto_adopt_stale_cli_jobs(engine: AsyncEngine, max_age_seconds: int = 120) -> list[str]:
    """Find and adopt CLI-owned jobs with stale heartbeats.

    Returns list of adopted job IDs. After calling this, run recover_jobs()
    to re-evaluate the newly server-owned jobs.
    """
    stale = engine.store.stale_jobs(max_age_seconds=max_age_seconds)
    adopted = []
    for job in stale:
        _adopt_stale_cli_job(engine, job)
        adopted.append(job.id)
    if adopted:
        _engine_logger.info("Auto-adopted %d stale CLI job(s): %s", len(adopted), adopted)
    return adopted
