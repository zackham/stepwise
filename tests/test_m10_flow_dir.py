"""Tests for M10: Script Path Resolution (Phase 3) and Prompt File References (Phase 4)."""

import json
import os
import pytest
from pathlib import Path

from stepwise.models import WorkflowDefinition
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
from stepwise.executors import ScriptExecutor, ExecutionContext
from stepwise.engine import Engine
from stepwise.store import SQLiteStore


# ── Phase 3: Script Path Resolution ──────────────────────────────────


class TestSourceDir:
    """WorkflowDefinition.source_dir is set from the YAML file location."""

    def test_source_dir_set_from_file(self, tmp_path):
        flow = tmp_path / "my-flow.yaml"
        flow.write_text("""\
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
steps:
  analyze:
    executor: llm
    prompt_file: prompts/system.md
    outputs: [result]
""")
        wf = load_workflow_yaml(str(marker))
        step = wf.steps["analyze"]
        assert step.executor.config["prompt"] == "Be concise and precise."

    def test_prompt_file_with_human_executor(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "review.md"
        prompt_file.write_text("Please review the output and approve.")

        flow = tmp_path / "test.yaml"
        flow.write_text("""\
steps:
  review:
    executor: human
    prompt_file: prompts/review.md
    outputs: [approved]
""")
        wf = load_workflow_yaml(str(flow))
        step = wf.steps["review"]
        assert step.executor.config["prompt"] == "Please review the output and approve."
