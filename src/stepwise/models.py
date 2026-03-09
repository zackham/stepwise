"""Core data structures: Job, StepDefinition, StepRun, InputBinding, etc."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StepStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_STEP_STATUSES = frozenset(
    {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


@dataclass
class InputBinding:
    """Maps an output key from a source step to an input key on the target step."""

    source_step: str
    source_key: str
    target_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_step": self.source_step,
            "source_key": self.source_key,
            "target_key": self.target_key,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputBinding:
        return cls(
            source_step=d["source_step"],
            source_key=d["source_key"],
            target_key=d["target_key"],
        )


@dataclass
class StepDefinition:
    """Blueprint for a step in a workflow."""

    name: str
    executor: str
    config: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    inputs: list[InputBinding] = field(default_factory=list)
    max_retries: int = 0
    timeout_seconds: float | None = None
    condition: str | None = None
    loop_over: str | None = None  # "step_name.output_key" -> iterate over that list
    is_sub_job: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executor": self.executor,
            "config": self.config,
            "depends_on": self.depends_on,
            "inputs": [b.to_dict() for b in self.inputs],
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "condition": self.condition,
            "loop_over": self.loop_over,
            "is_sub_job": self.is_sub_job,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepDefinition:
        return cls(
            name=d["name"],
            executor=d["executor"],
            config=d.get("config", {}),
            depends_on=d.get("depends_on", []),
            inputs=[InputBinding.from_dict(b) for b in d.get("inputs", [])],
            max_retries=d.get("max_retries", 0),
            timeout_seconds=d.get("timeout_seconds"),
            condition=d.get("condition"),
            loop_over=d.get("loop_over"),
            is_sub_job=d.get("is_sub_job", False),
        )


@dataclass
class StepRun:
    """Runtime state of a single step execution."""

    id: str
    job_id: str
    step_name: str
    status: StepStatus = StepStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] | None = None
    error: str | None = None
    attempt: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    iteration_index: int | None = None
    iteration_value: Any = None
    input_hash: str | None = None

    @classmethod
    def create(cls, job_id: str, step_name: str, **kwargs: Any) -> StepRun:
        return cls(id=str(uuid.uuid4()), job_id=job_id, step_name=step_name, **kwargs)

    def compute_input_hash(self) -> str:
        raw = json.dumps(self.inputs, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "step_name": self.step_name,
            "status": self.status.value,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "attempt": self.attempt,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "iteration_index": self.iteration_index,
            "iteration_value": self.iteration_value,
            "input_hash": self.input_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepRun:
        return cls(
            id=d["id"],
            job_id=d["job_id"],
            step_name=d["step_name"],
            status=StepStatus(d["status"]),
            inputs=d.get("inputs", {}),
            outputs=d.get("outputs"),
            error=d.get("error"),
            attempt=d.get("attempt", 1),
            started_at=(
                datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(d["completed_at"])
                if d.get("completed_at")
                else None
            ),
            iteration_index=d.get("iteration_index"),
            iteration_value=d.get("iteration_value"),
            input_hash=d.get("input_hash"),
        )


@dataclass
class WorkflowDefinition:
    """Blueprint for a workflow — a DAG of steps."""

    name: str
    steps: list[StepDefinition] = field(default_factory=list)
    description: str = ""

    def get_step(self, name: str) -> StepDefinition | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty if valid)."""
        errors: list[str] = []
        step_names = [s.name for s in self.steps]
        name_set = set(step_names)

        if len(name_set) != len(step_names):
            seen: set[str] = set()
            for n in step_names:
                if n in seen:
                    errors.append(f"Duplicate step name: '{n}'")
                seen.add(n)

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in name_set:
                    errors.append(
                        f"Step '{step.name}' depends on unknown step '{dep}'"
                    )
            for binding in step.inputs:
                if binding.source_step not in name_set:
                    errors.append(
                        f"Step '{step.name}' has input from unknown step '{binding.source_step}'"
                    )
            if step.loop_over:
                src = step.loop_over.split(".")[0]
                if src not in name_set:
                    errors.append(
                        f"Step '{step.name}' loops over unknown step '{src}'"
                    )

        if not errors:
            cycle_errors = self._detect_cycles()
            errors.extend(cycle_errors)

        return errors

    def _detect_cycles(self) -> list[str]:
        """Detect cycles via topological sort (Kahn's algorithm)."""
        adj: dict[str, list[str]] = {s.name: [] for s in self.steps}
        in_degree: dict[str, int] = {s.name: 0 for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                if dep in adj:
                    adj[dep].append(step.name)
                    in_degree[step.name] += 1
            for binding in step.inputs:
                if binding.source_step in adj and binding.source_step not in step.depends_on:
                    adj[binding.source_step].append(step.name)
                    in_degree[step.name] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(self.steps):
            remaining = [n for n, d in in_degree.items() if d > 0]
            return [f"Cycle detected involving steps: {', '.join(sorted(remaining))}"]
        return []

    def topological_order(self) -> list[str]:
        """Return step names in topological order."""
        adj: dict[str, list[str]] = {s.name: [] for s in self.steps}
        in_degree: dict[str, int] = {s.name: 0 for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                if dep in adj:
                    adj[dep].append(step.name)
                    in_degree[step.name] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowDefinition:
        return cls(
            name=d["name"],
            steps=[StepDefinition.from_dict(s) for s in d.get("steps", [])],
            description=d.get("description", ""),
        )


@dataclass
class Job:
    """A running instance of a workflow."""

    id: str
    workflow: WorkflowDefinition
    status: JobStatus = JobStatus.PENDING
    step_runs: dict[str, StepRun] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_job_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
    ) -> Job:
        job = cls(
            id=str(uuid.uuid4()),
            workflow=workflow,
            inputs=inputs or {},
            parent_job_id=parent_job_id,
        )
        for step_def in workflow.steps:
            sr = StepRun.create(job_id=job.id, step_name=step_def.name)
            job.step_runs[step_def.name] = sr
        return job

    def get_step_run(self, step_name: str) -> StepRun | None:
        return self.step_runs.get(step_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow": self.workflow.to_dict(),
            "status": self.status.value,
            "step_runs": {k: v.to_dict() for k, v in self.step_runs.items()},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "parent_job_id": self.parent_job_id,
            "inputs": self.inputs,
            "outputs": self.outputs,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        job = cls(
            id=d["id"],
            workflow=WorkflowDefinition.from_dict(d["workflow"]),
            status=JobStatus(d["status"]),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            parent_job_id=d.get("parent_job_id"),
            inputs=d.get("inputs", {}),
            outputs=d.get("outputs"),
        )
        job.step_runs = {
            k: StepRun.from_dict(v) for k, v in d.get("step_runs", {}).items()
        }
        return job
