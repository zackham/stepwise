"""Tests for stepwise chain — ephemeral flow composition."""

import textwrap
from pathlib import Path

import pytest
import yaml

from stepwise.chain import compile_chain, _determine_result_binding
from stepwise.models import ConfigVar


# ── Helpers ──────────────────────────────────────────────────────────


def write_flow(tmp_dir: Path, name: str, content: str) -> Path:
    """Write a .flow.yaml to a temp directory, return its path."""
    path = tmp_dir / f"{name}.flow.yaml"
    path.write_text(textwrap.dedent(content))
    return path


def simple_echo_flow(name: str, outputs: list[str], config: dict | None = None) -> str:
    """Build a minimal flow YAML with a script step that echoes JSON."""
    import json
    artifact = {o: o for o in outputs}
    json_str = json.dumps(artifact)
    step: dict = {
        "run": f"echo '{json_str}'",
        "outputs": outputs,
    }
    if config:
        step["inputs"] = {k: f"$job.{k}" for k in config}

    flow: dict = {"name": name, "steps": {"step1": step}}
    if config:
        flow["config"] = {
            k: (v if isinstance(v, dict) else {"description": v})
            for k, v in config.items()
        }

    return yaml.dump(flow, default_flow_style=False, sort_keys=False)


# ── Unit: _determine_result_binding ──────────────────────────────────


class TestDetermineResultBinding:
    def test_spec_priority(self):
        """Flow with 'spec' config var gets result wired to 'spec'."""
        cvs = [ConfigVar(name="spec"), ConfigVar(name="other")]
        assert _determine_result_binding(cvs) == "spec"

    def test_topic_priority(self):
        """Flow with 'topic' (but no 'spec') gets result wired to 'topic'."""
        cvs = [ConfigVar(name="topic"), ConfigVar(name="other")]
        assert _determine_result_binding(cvs) == "topic"

    def test_prompt_priority(self):
        cvs = [ConfigVar(name="prompt")]
        assert _determine_result_binding(cvs) == "prompt"

    def test_question_priority(self):
        cvs = [ConfigVar(name="question")]
        assert _determine_result_binding(cvs) == "question"

    def test_spec_beats_topic(self):
        """When both spec and topic exist, spec wins."""
        cvs = [ConfigVar(name="topic"), ConfigVar(name="spec")]
        assert _determine_result_binding(cvs) == "spec"

    def test_first_required_config_var(self):
        """Fallback to first required config var not in priority list."""
        cvs = [ConfigVar(name="custom_input", required=True)]
        assert _determine_result_binding(cvs) == "custom_input"

    def test_first_optional_config_var(self):
        """If no required vars, use first config var."""
        cvs = [ConfigVar(name="optional_thing", required=False)]
        assert _determine_result_binding(cvs) == "optional_thing"

    def test_no_config_vars(self):
        """No config vars → 'result'."""
        assert _determine_result_binding([]) == "result"


# ── Unit: compile_chain ──────────────────────────────────────────────


class TestCompileChain:
    def test_two_flow_chain_with_spec_matching(self, tmp_path):
        """Stage-1 result wires to stage-2's 'spec' config var."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"],
                                             config={"spec": "The spec"}))

        result = compile_chain([flow_a, flow_b], [])
        parsed = yaml.safe_load(result)

        assert parsed["name"] == "chain-flow-a-flow-b"
        assert "stage-1" in parsed["steps"]
        assert "stage-2" in parsed["steps"]

        s2_inputs = parsed["steps"]["stage-2"]["inputs"]
        assert s2_inputs["spec"] == "stage-1.result"

    def test_three_flow_chain(self, tmp_path):
        """Three flows chain correctly: stage-2 from stage-1, stage-3 from stage-2."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))
        flow_c = write_flow(tmp_path, "flow-c",
                            simple_echo_flow("flow-c", ["result"],
                                             config={"topic": "The topic"}))

        result = compile_chain([flow_a, flow_b, flow_c], [])
        parsed = yaml.safe_load(result)

        assert len(parsed["steps"]) == 3
        # stage-2 wires from stage-1
        assert parsed["steps"]["stage-2"]["inputs"]["result"] == "stage-1.result"
        # stage-3 wires from stage-2
        assert parsed["steps"]["stage-3"]["inputs"]["topic"] == "stage-2.result"

    def test_no_config_vars_uses_result(self, tmp_path):
        """Flow with no config vars gets result wired as 'result'."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        result = compile_chain([flow_a, flow_b], [])
        parsed = yaml.safe_load(result)

        s2_inputs = parsed["steps"]["stage-2"]["inputs"]
        assert s2_inputs["result"] == "stage-1.result"

    def test_var_passthrough_matching(self, tmp_path):
        """--var values matching config vars are wired as $job.* passthrough."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"],
                                             config={"project": "Project name"}))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"],
                                             config={"spec": "The spec",
                                                     "project": "Project name"}))

        result = compile_chain([flow_a, flow_b], ["project"])
        parsed = yaml.safe_load(result)

        # Both stages should get project passthrough
        assert parsed["steps"]["stage-1"]["inputs"]["project"] == "$job.project"
        assert parsed["steps"]["stage-2"]["inputs"]["project"] == "$job.project"
        # Stage-2 also gets result wired to spec
        assert parsed["steps"]["stage-2"]["inputs"]["spec"] == "stage-1.result"

    def test_output_discovery(self, tmp_path):
        """Terminal step outputs are declared on the stage step."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result", "summary"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        result = compile_chain([flow_a, flow_b], [])
        parsed = yaml.safe_load(result)

        assert parsed["steps"]["stage-1"]["outputs"] == ["result", "summary"]
        assert parsed["steps"]["stage-2"]["outputs"] == ["result"]

    def test_name_truncation(self, tmp_path):
        """Chain name is truncated to 80 chars."""
        flows = []
        for i in range(10):
            name = f"very-long-flow-name-{i}"
            flows.append(write_flow(tmp_path, name,
                                    simple_echo_flow(name, ["result"])))

        result = compile_chain(flows, [])
        parsed = yaml.safe_load(result)
        assert len(parsed["name"]) <= 80

    def test_fewer_than_two_flows_raises(self, tmp_path):
        """Chain requires at least 2 flows."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        with pytest.raises(ValueError, match="at least 2"):
            compile_chain([flow_a], [])

    def test_flow_with_no_terminal_outputs_raises(self, tmp_path):
        """Flow whose terminal step has no declared outputs raises ValueError."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b", """\
            name: flow-b
            steps:
              step1:
                run: echo ok
        """)

        with pytest.raises(ValueError, match="no declared outputs"):
            compile_chain([flow_a, flow_b], [])

    def test_description_contains_arrow(self, tmp_path):
        """Description uses → separator between flow names."""
        flow_a = write_flow(tmp_path, "alpha",
                            simple_echo_flow("alpha", ["result"]))
        flow_b = write_flow(tmp_path, "beta",
                            simple_echo_flow("beta", ["result"]))

        result = compile_chain([flow_a, flow_b], [])
        parsed = yaml.safe_load(result)
        assert "alpha → beta" in parsed["description"]

    def test_flow_path_is_absolute(self, tmp_path):
        """Generated flow: references use absolute paths."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        result = compile_chain([flow_a, flow_b], [])
        parsed = yaml.safe_load(result)

        for step in parsed["steps"].values():
            assert Path(step["flow"]).is_absolute()

    def test_var_passthrough_no_config_vars(self, tmp_path):
        """When flow has no config vars, all --var values pass through."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        result = compile_chain([flow_a, flow_b], ["project", "extra"])
        parsed = yaml.safe_load(result)

        # Both stages (no config vars) should get all vars passed through
        s1_inputs = parsed["steps"]["stage-1"].get("inputs", {})
        assert s1_inputs.get("project") == "$job.project"
        assert s1_inputs.get("extra") == "$job.extra"

    def test_non_matching_vars_not_passed(self, tmp_path):
        """Vars that don't match a flow's config vars are not passed to that stage."""
        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"],
                                             config={"project": "Project name"}))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"],
                                             config={"spec": "The spec"}))

        result = compile_chain([flow_a, flow_b], ["project", "unrelated"])
        parsed = yaml.safe_load(result)

        # stage-1 has project config var → gets project
        s1_inputs = parsed["steps"]["stage-1"]["inputs"]
        assert s1_inputs.get("project") == "$job.project"
        assert "unrelated" not in s1_inputs

        # stage-2 has spec config var → gets result as spec, no project
        s2_inputs = parsed["steps"]["stage-2"]["inputs"]
        assert s2_inputs["spec"] == "stage-1.result"
        assert "project" not in s2_inputs
        assert "unrelated" not in s2_inputs


# ── Integration: compile + load ──────────────────────────────────────


class TestChainIntegration:
    def test_chain_yaml_loads_as_sub_flow_steps(self, tmp_path):
        """Compiled chain YAML loads through yaml_loader with sub_flow executor type."""
        from stepwise.yaml_loader import load_workflow_yaml

        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        chain_yaml = compile_chain([flow_a, flow_b], [])

        # Write to temp file so yaml_loader can resolve flow: refs
        chain_path = tmp_path / "chain.flow.yaml"
        chain_path.write_text(chain_yaml)
        wf = load_workflow_yaml(str(chain_path))

        assert "stage-1" in wf.steps
        assert "stage-2" in wf.steps
        assert wf.steps["stage-1"].executor.type == "sub_flow"
        assert wf.steps["stage-2"].executor.type == "sub_flow"

    def test_no_files_persisted_in_flows_dir(self, tmp_path):
        """After compile_chain, no files are created in flows/ directory."""
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()

        flow_a = write_flow(tmp_path, "flow-a",
                            simple_echo_flow("flow-a", ["result"]))
        flow_b = write_flow(tmp_path, "flow-b",
                            simple_echo_flow("flow-b", ["result"]))

        compile_chain([flow_a, flow_b], [])

        # flows/ directory should still be empty
        assert list(flows_dir.iterdir()) == []
