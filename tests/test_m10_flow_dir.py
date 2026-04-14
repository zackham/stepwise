"""Tests for M10: Script Path Resolution (Phase 3) and Prompt File References (Phase 4)."""

import json
import os
import pytest
from pathlib import Path

from stepwise.models import JobStatus, WorkflowDefinition
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
from stepwise.executors import (
    ExecutorRegistry,
    ExternalExecutor,
    MockLLMExecutor,
    ScriptExecutor,
    ExecutionContext,
)
from stepwise.engine import Engine
from stepwise.store import SQLiteStore


# ── Phase 3: Script Path Resolution ──────────────────────────────────


class TestSourceDir:
    """WorkflowDefinition.source_dir is set from the YAML file location."""

    def test_source_dir_set_from_file(self, tmp_path):
        flow = tmp_path / "my-flow.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  hello:
    run: 'echo "hi"'
    outputs: [msg]
""")
        wf = load_workflow_yaml(str(flow))
        assert wf.source_dir == str(tmp_path.resolve())

    def test_source_dir_none_for_string(self):
        yaml_str = """\
steps:
  hello:
    run: 'echo "hi"'
    outputs: [msg]
"""
        wf = load_workflow_yaml(yaml_str)
        assert wf.source_dir is None

    def test_source_dir_serializes(self, tmp_path):
        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  hello:
    run: 'echo "hi"'
    outputs: [msg]
""")
        wf = load_workflow_yaml(str(flow))
        d = wf.to_dict()
        assert d["source_dir"] == str(tmp_path.resolve())

        wf2 = WorkflowDefinition.from_dict(d)
        assert wf2.source_dir == str(tmp_path.resolve())

    def test_source_dir_omitted_when_none(self):
        wf = WorkflowDefinition()
        d = wf.to_dict()
        assert "source_dir" not in d

    def test_directory_flow_source_dir(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        marker = flow_dir / "FLOW.yaml"
        marker.write_text("""\
name: test
author: test
steps:
  hello:
    run: 'echo "hi"'
    outputs: [msg]
""")
        wf = load_workflow_yaml(str(marker))
        assert wf.source_dir == str(flow_dir.resolve())


class TestScriptPathResolution:
    """ScriptExecutor resolves script paths relative to flow_dir."""

    def test_resolve_relative_script(self, tmp_path):
        # Create a script in the flow directory
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "hello.py"
        script.write_text('import json; print(json.dumps({"msg": "hello"}))')

        executor = ScriptExecutor(
            command="python3 scripts/hello.py",
            flow_dir=str(tmp_path),
        )
        resolved = executor._resolve_command("python3 scripts/hello.py")
        assert str(scripts_dir / "hello.py") in resolved
        assert resolved.startswith("python3 ")

    def test_resolve_bare_script(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("echo hi")

        executor = ScriptExecutor(
            command="run.sh",
            flow_dir=str(tmp_path),
        )
        resolved = executor._resolve_command("run.sh")
        assert str(tmp_path / "run.sh") in resolved

    def test_no_resolution_without_flow_dir(self):
        executor = ScriptExecutor(command="scripts/hello.py")
        resolved = executor._resolve_command("scripts/hello.py")
        assert resolved == "scripts/hello.py"

    def test_no_resolution_for_system_command(self, tmp_path):
        executor = ScriptExecutor(
            command="echo hello",
            flow_dir=str(tmp_path),
        )
        resolved = executor._resolve_command("echo hello")
        assert resolved == "echo hello"

    def test_no_resolution_when_file_missing(self, tmp_path):
        executor = ScriptExecutor(
            command="python3 missing.py",
            flow_dir=str(tmp_path),
        )
        resolved = executor._resolve_command("python3 missing.py")
        assert resolved == "python3 missing.py"

    def test_flow_dir_env_var_set(self, tmp_path):
        """STEPWISE_FLOW_DIR is set in the subprocess environment."""
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        script = flow_dir / "check_env.sh"
        script.write_text('echo "{\\"flow_dir\\": \\"$STEPWISE_FLOW_DIR\\"}"')
        script.chmod(0o755)

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        executor = ScriptExecutor(
            command="bash check_env.sh",
            flow_dir=str(flow_dir),
        )
        ctx = ExecutionContext(
            job_id="test-job",
            step_name="test-step",
            attempt=1,
            workspace_path=str(workspace),
            idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        artifact = result.envelope.artifact
        assert artifact.get("flow_dir") == str(flow_dir)

    def test_cwd_is_workspace_not_flow_dir(self, tmp_path):
        """Script cwd remains the workspace, not the flow directory."""
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        script = flow_dir / "check_cwd.sh"
        script.write_text('echo "{\\"cwd\\": \\"$(pwd)\\"}"')
        script.chmod(0o755)

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        executor = ScriptExecutor(
            command="bash check_cwd.sh",
            flow_dir=str(flow_dir),
        )
        ctx = ExecutionContext(
            job_id="test-job",
            step_name="test-step",
            attempt=1,
            workspace_path=str(workspace),
            idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        artifact = result.envelope.artifact
        assert artifact.get("cwd") == str(workspace)

    def test_single_file_flow_no_flow_dir(self):
        """Single-file flows (YAML string) don't set flow_dir."""
        executor = ScriptExecutor(command="echo hello")
        assert executor.flow_dir is None

    def test_resolve_script_with_args(self, tmp_path):
        """Script path resolution preserves arguments after the script."""
        script = tmp_path / "process.py"
        script.write_text("pass")

        executor = ScriptExecutor(
            command="python3 process.py --verbose --count 5",
            flow_dir=str(tmp_path),
        )
        resolved = executor._resolve_command("python3 process.py --verbose --count 5")
        assert str(tmp_path / "process.py") in resolved
        assert "--verbose --count 5" in resolved


class TestScriptPathResolutionIntegration:
    """Integration test: directory flow with script resolution through engine."""

    def test_directory_flow_resolves_script(self, tmp_path):
        """A directory flow with run: scripts/hello.py resolves correctly."""
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        scripts_dir = flow_dir / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "hello.py"
        script.write_text('import json; print(json.dumps({"msg": "hello from script"}))')

        marker = flow_dir / "FLOW.yaml"
        marker.write_text("""\
name: test
author: test
steps:
  greet:
    run: scripts/hello.py
    outputs: [msg]
""")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        wf = load_workflow_yaml(str(marker))
        assert wf.source_dir == str(flow_dir.resolve())

        store = SQLiteStore(":memory:")
        from stepwise.executors import ExecutorRegistry
        registry = ExecutorRegistry()
        registry.register("script", lambda cfg: ScriptExecutor(
            command=cfg.get("command", "echo '{}'"),
            working_dir=cfg.get("working_dir"),
            flow_dir=cfg.get("flow_dir"),
        ))

        engine = Engine(store=store, registry=registry)
        job = engine.create_job("test", wf, workspace_path=str(workspace))
        engine.start_job(job.id)

        runs = store.runs_for_job(job.id)
        assert len(runs) == 1
        run = runs[0]
        assert run.result is not None
        assert run.result.artifact.get("msg") == "hello from script"

        store.close()


# ── Phase 4: Prompt File References ──────────────────────────────────


class TestPromptFileResolution:
    """prompt_file: loads file content into prompt at parse time."""

    def test_prompt_file_loads_content(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "system.md"
        prompt_file.write_text("You are a helpful assistant. Analyze $topic.")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  analyze:
    executor: llm
    prompt_file: prompts/system.md
    outputs: [result]
""")
        wf = load_workflow_yaml(str(flow))
        step = wf.steps["analyze"]
        assert step.executor.config["prompt"] == "You are a helpful assistant. Analyze $topic."

    def test_prompt_file_missing_raises_error(self, tmp_path):
        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  analyze:
    executor: llm
    prompt_file: missing/prompt.md
    outputs: [result]
""")
        with pytest.raises(YAMLLoadError, match="prompt file not found"):
            load_workflow_yaml(str(flow))

    def test_prompt_and_prompt_file_conflict(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("content")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  analyze:
    executor: llm
    prompt: "inline prompt"
    prompt_file: prompts/system.md
    outputs: [result]
""")
        with pytest.raises(YAMLLoadError, match="cannot specify both.*prompt.*prompt_file"):
            load_workflow_yaml(str(flow))

    def test_prompt_file_template_substitution(self, tmp_path):
        """Template variables ($variable) work in content loaded from prompt_file."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "analyze.md"
        prompt_file.write_text("Analyze the following topic: $topic\n\nContext: $context")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  analyze:
    executor: llm
    prompt_file: prompts/analyze.md
    outputs: [result]
""")
        wf = load_workflow_yaml(str(flow))
        step = wf.steps["analyze"]
        # Verify the raw template is preserved (substitution happens at execution time)
        assert "$topic" in step.executor.config["prompt"]
        assert "$context" in step.executor.config["prompt"]

    def test_prompt_file_with_agent_executor(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "agent.md"
        prompt_file.write_text("Do the thing with $input")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  work:
    executor: agent
    prompt_file: prompts/agent.md
    outputs: [result]
""")
        wf = load_workflow_yaml(str(flow))
        step = wf.steps["work"]
        assert step.executor.config["prompt"] == "Do the thing with $input"

    def test_prompt_file_without_base_dir(self):
        """prompt_file in a YAML string resolves relative to cwd; missing file raises error."""
        yaml_str = """\
steps:
  analyze:
    executor: llm
    prompt_file: prompts/nonexistent-system.md
    outputs: [result]
"""
        with pytest.raises(YAMLLoadError, match="prompt file not found"):
            load_workflow_yaml(yaml_str)

    def test_prompt_file_without_base_dir_via_string_loader(self):
        """prompt_file via load_workflow_string (no base_dir at all) raises clear error."""
        from stepwise.yaml_loader import load_workflow_string
        yaml_str = """\
steps:
  analyze:
    executor: llm
    prompt_file: prompts/system.md
    outputs: [result]
"""
        with pytest.raises(YAMLLoadError, match="cannot be resolved without a base directory"):
            load_workflow_string(yaml_str)

    def test_prompt_file_in_directory_flow(self, tmp_path):
        """prompt_file works in a directory flow (FLOW.yaml)."""
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        prompts_dir = flow_dir / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "system.md"
        prompt_file.write_text("Be concise and precise.")

        marker = flow_dir / "FLOW.yaml"
        marker.write_text("""\
name: test
author: test
steps:
  analyze:
    executor: llm
    prompt_file: prompts/system.md
    outputs: [result]
""")
        wf = load_workflow_yaml(str(marker))
        step = wf.steps["analyze"]
        assert step.executor.config["prompt"] == "Be concise and precise."

    def test_prompt_file_with_external_executor(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "review.md"
        prompt_file.write_text("Please review the output and approve.")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
name: test
author: test
steps:
  review:
    executor: external
    prompt_file: prompts/review.md
    outputs: [approved]
""")
        wf = load_workflow_yaml(str(flow))
        step = wf.steps["review"]
        assert step.executor.config["prompt"] == "Please review the output and approve."


# ── Sub-flow STEPWISE_FLOW_DIR resolution ─────────────────────────────


def _make_flow_dir_engine() -> tuple[SQLiteStore, Engine]:
    """Engine wired with a script registry that honors flow_dir."""
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("script", lambda cfg: ScriptExecutor(
        command=cfg.get("command", "echo '{}'"),
        working_dir=cfg.get("working_dir"),
        flow_dir=cfg.get("flow_dir"),
    ))
    reg.register("external", lambda cfg: ExternalExecutor(prompt=cfg.get("prompt", "")))
    reg.register("mock_llm", lambda cfg: MockLLMExecutor())
    reg.register("for_each", lambda cfg: ScriptExecutor(command="echo '{}'"))
    return store, Engine(store=store, registry=reg)


def _tick_until_done(engine: Engine, job_id: str, max_ticks: int = 50):
    for _ in range(max_ticks):
        job = engine.get_job(job_id)
        if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
            return job
        engine.tick()
    return engine.get_job(job_id)


class TestSubFlowFlowDir:
    """STEPWISE_FLOW_DIR resolves to the directory of the flow file that
    DEFINES the executing step, not the root job's flow.
    """

    def test_inline_for_each_subflow_uses_parent_flow_dir(self, tmp_path):
        """Inline `flow:` block under for_each inherits the enclosing flow's dir.

        Reproduces the bug: a `run: scripts/lookup.py` step inside an inline
        for_each sub-flow should resolve `scripts/lookup.py` relative to the
        PARENT flow's directory (which is where the script actually lives).
        Before the fix, this failed with `No such file or directory`.
        """
        flow_dir = tmp_path / "parent-flow"
        flow_dir.mkdir()
        scripts_dir = flow_dir / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "lookup.py"
        script.write_text(
            'import json, os\n'
            'item = os.environ.get("STEPWISE_INPUT_item", "")\n'
            'flow_dir = os.environ.get("STEPWISE_FLOW_DIR", "")\n'
            'print(json.dumps({"looked_up": item.upper(), "flow_dir": flow_dir}))\n'
        )

        marker = flow_dir / "FLOW.yaml"
        marker.write_text("""\
name: parent-flow
author: test
steps:
  produce:
    run: 'echo ''{"items": ["a", "b", "c"]}'''
    outputs: [items]

  fan-out:
    for_each: produce.items
    as: item
    flow:
      steps:
        lookup:
          run: scripts/lookup.py
          inputs:
            item: $job.item
          outputs: [looked_up, flow_dir]
    outputs: [results]
""")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        wf = load_workflow_yaml(str(marker))
        sub_flow = wf.steps["fan-out"].sub_flow
        assert sub_flow is not None
        # The crux of the fix: inline sub-flow inherits parent flow's source_dir.
        assert sub_flow.source_dir == str(flow_dir.resolve())

        store, engine = _make_flow_dir_engine()
        job = engine.create_job("test", wf, workspace_path=str(workspace))
        engine.start_job(job.id)
        job = _tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED, (
            f"job ended in {job.status}; runs="
            f"{[(r.step_name, r.status, r.error) for r in store.runs_for_job(job.id)]}"
        )

        fe_runs = engine.get_runs(job.id, "fan-out")
        assert len(fe_runs) == 1
        results = fe_runs[0].result.artifact["results"]
        assert len(results) == 3
        assert {r["looked_up"] for r in results} == {"A", "B", "C"}
        for r in results:
            assert r["flow_dir"] == str(flow_dir.resolve())

        store.close()

    def test_composed_subflow_uses_its_own_flow_dir(self, tmp_path):
        """A composed (file-loaded) sub-flow uses its OWN directory, not the
        caller's. Ensures `flow: name`/`flow: ./other.yaml` references stay
        self-contained when scripts/prompts live alongside the included flow.
        """
        # Parent flow lives in one directory.
        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        parent_marker = parent_dir / "FLOW.yaml"

        # Composed sub-flow lives in a sibling directory with its own scripts.
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_scripts = child_dir / "scripts"
        child_scripts.mkdir()
        (child_scripts / "report.py").write_text(
            'import json, os\n'
            'flow_dir = os.environ.get("STEPWISE_FLOW_DIR", "")\n'
            'print(json.dumps({"value": "ok", "flow_dir": flow_dir}))\n'
        )
        child_marker = child_dir / "FLOW.yaml"
        child_marker.write_text("""\
name: child-flow
author: test
steps:
  report:
    run: scripts/report.py
    outputs: [value, flow_dir]
""")

        parent_marker.write_text(f"""\
name: parent-flow
author: test
steps:
  call:
    flow: {child_marker}
    outputs: [value, flow_dir]
""")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        wf = load_workflow_yaml(str(parent_marker))
        # Composed sub-flow keeps its OWN source_dir.
        sub = wf.steps["call"].sub_flow
        assert sub is not None
        assert sub.source_dir == str(child_dir.resolve())

        store, engine = _make_flow_dir_engine()
        job = engine.create_job("test", wf, workspace_path=str(workspace))
        engine.start_job(job.id)
        job = _tick_until_done(engine, job.id)

        assert job.status == JobStatus.COMPLETED, (
            f"job ended in {job.status}; runs="
            f"{[(r.step_name, r.status, r.error) for r in store.runs_for_job(job.id)]}"
        )

        sub_jobs = store.child_jobs(job.id)
        assert sub_jobs, "expected a sub-job to be created for composed flow"
        sub_runs = store.runs_for_job(sub_jobs[0].id)
        report_runs = [r for r in sub_runs if r.step_name == "report"]
        assert report_runs
        artifact = report_runs[0].result.artifact
        assert artifact["value"] == "ok"
        assert artifact["flow_dir"] == str(child_dir.resolve())

        store.close()
