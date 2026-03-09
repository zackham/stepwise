"""Executor interface, registry, and M1 implementations: ScriptExecutor, HumanExecutor, MockLLMExecutor."""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from stepwise.models import Job, StepRun, WorkflowDefinition


@dataclass
class ExecutorResult:
    """Result returned by an executor."""

    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


class Executor(ABC):
    """Base class for all executors."""

    @abstractmethod
    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        ...


class ExecutorRegistry:
    """Maps executor type names to Executor instances."""

    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, name: str, executor: Executor) -> None:
        self._executors[name] = executor

    def get(self, name: str) -> Executor:
        if name not in self._executors:
            raise KeyError(f"Unknown executor type: '{name}'")
        return self._executors[name]

    def __contains__(self, name: str) -> bool:
        return name in self._executors

    def names(self) -> list[str]:
        return list(self._executors.keys())


# ── M1 Implementations ──────────────────────────────────────────────────


class ScriptExecutor(Executor):
    """Runs a shell command or Python callable.

    Config keys:
      command:  str — shell command to execute
      callable: Callable — Python function(inputs) -> dict  (takes priority)
      shell:    bool — use shell mode for command (default True)
    """

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        fn = config.get("callable")
        if fn is not None:
            return await self._run_callable(fn, step_run)
        command = config.get("command")
        if command is not None:
            return await self._run_command(command, step_run, config)
        return ExecutorResult(error="ScriptExecutor: no 'command' or 'callable' in config")

    async def _run_callable(
        self, fn: Callable, step_run: StepRun
    ) -> ExecutorResult:
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(step_run.inputs)
            else:
                result = fn(step_run.inputs)
            if isinstance(result, dict):
                return ExecutorResult(outputs=result)
            return ExecutorResult(outputs={"result": result})
        except Exception as e:
            return ExecutorResult(error=str(e))

    async def _run_command(
        self, command: str, step_run: StepRun, config: dict[str, Any]
    ) -> ExecutorResult:
        # Template substitution in command
        for key, value in step_run.inputs.items():
            command = command.replace(f"{{{key}}}", str(value))

        env = {**os.environ, **{k: str(v) for k, v in step_run.inputs.items()}}

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            stdout = stdout_bytes.decode().strip() if stdout_bytes else ""
            stderr = stderr_bytes.decode().strip() if stderr_bytes else ""

            outputs = {
                "stdout": stdout,
                "stderr": stderr,
                "return_code": proc.returncode,
            }

            if proc.returncode != 0:
                return ExecutorResult(
                    outputs=outputs,
                    error=f"Command exited with code {proc.returncode}",
                )
            return ExecutorResult(outputs=outputs)
        except Exception as e:
            return ExecutorResult(error=f"ScriptExecutor error: {e}")


class HumanExecutor(Executor):
    """Waits for external human input via an asyncio.Future.

    Config keys:
      prompt: str — message shown to the human
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[ExecutorResult]] = {}

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ExecutorResult] = loop.create_future()
        self._pending[step_run.id] = future
        try:
            return await future
        finally:
            self._pending.pop(step_run.id, None)

    def complete(self, step_run_id: str, outputs: dict[str, Any]) -> None:
        """Externally complete a pending human step."""
        future = self._pending.get(step_run_id)
        if future and not future.done():
            future.set_result(ExecutorResult(outputs=outputs))

    def fail(self, step_run_id: str, error: str) -> None:
        """Externally fail a pending human step."""
        future = self._pending.get(step_run_id)
        if future and not future.done():
            future.set_result(ExecutorResult(error=error))

    @property
    def pending_ids(self) -> list[str]:
        return list(self._pending.keys())


class MockLLMExecutor(Executor):
    """Returns preconfigured responses for testing.

    Config keys:
      response:   Any — static response to return
      response_fn: Callable(inputs, config) -> Any — dynamic response generator
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses = responses or {}

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        # Check config-level response first
        fn = config.get("response_fn")
        if fn is not None:
            try:
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(step_run.inputs, config)
                else:
                    result = fn(step_run.inputs, config)
                if isinstance(result, dict):
                    return ExecutorResult(outputs=result)
                return ExecutorResult(outputs={"response": result})
            except Exception as e:
                return ExecutorResult(error=str(e))

        if "response" in config:
            resp = config["response"]
            if isinstance(resp, dict):
                return ExecutorResult(outputs=resp)
            return ExecutorResult(outputs={"response": resp})

        # Fall back to instance-level responses by step name
        resp = self._responses.get(
            step_run.step_name, self._responses.get("default", "Mock LLM response")
        )
        if callable(resp):
            resp = resp(step_run.inputs, config)
        if isinstance(resp, dict):
            return ExecutorResult(outputs=resp)
        return ExecutorResult(outputs={"response": resp})


class SubJobExecutor(Executor):
    """Executes a nested workflow as a sub-job.

    Config keys:
      workflow: dict — WorkflowDefinition as a dict
    """

    def __init__(
        self,
        executor_registry: ExecutorRegistry | None = None,
        store: Any | None = None,
    ) -> None:
        self._registry = executor_registry
        self._store = store

    async def execute(self, step_run: StepRun, config: dict[str, Any]) -> ExecutorResult:
        from stepwise.engine import Engine
        from stepwise.events import EventBus
        from stepwise.models import JobStatus

        workflow_dict = config.get("workflow")
        if not workflow_dict:
            return ExecutorResult(error="SubJobExecutor: no 'workflow' in config")

        workflow = WorkflowDefinition.from_dict(workflow_dict)
        sub_job = Job.create(
            workflow, inputs=step_run.inputs, parent_job_id=step_run.job_id
        )

        engine = Engine(sub_job, store=self._store, event_bus=EventBus())
        if self._registry:
            for name in self._registry.names():
                engine.register_executor(name, self._registry.get(name))

        result_job = await engine.run()

        if result_job.status == JobStatus.COMPLETED:
            return ExecutorResult(outputs=result_job.outputs or {})
        error_msg = f"Sub-job failed with status {result_job.status.value}"
        if result_job.outputs and "error" in result_job.outputs:
            error_msg += f": {result_job.outputs['error']}"
        return ExecutorResult(error=error_msg)
