"""Tests for flow config system: ConfigVar, FlowRequirement, parsing, loading, and warnings."""

import pytest

from stepwise.models import ConfigVar, FlowRequirement, WorkflowDefinition
from stepwise.yaml_loader import YAMLLoadError, load_workflow_string, load_workflow_yaml


# ── ConfigVar Dataclass ──────────────────────────────────────────────


class TestConfigVarDataclass:
    def test_config_var_to_dict_from_dict_roundtrip(self):
        cv = ConfigVar(
            name="persona",
            description="Your persona",
            type="str",
            required=True,
            example="You are...",
        )
        restored = ConfigVar.from_dict(cv.to_dict())
        assert restored.name == cv.name
        assert restored.description == cv.description
        assert restored.type == cv.type
        assert restored.required == cv.required
        assert restored.example == cv.example

    def test_config_var_defaults(self):
        cv = ConfigVar(name="x")
        assert cv.type == "str"
        assert cv.required is True
        assert cv.default is None
        assert cv.description == ""

    def test_config_var_choice_to_dict(self):
        cv = ConfigVar(
            name="style",
            type="choice",
            options=["a", "b"],
            default="a",
            required=False,
        )
        d = cv.to_dict()
        assert d["options"] == ["a", "b"]
        assert d["default"] == "a"
        assert d.get("required") is False

    def test_flow_requirement_roundtrip(self):
        fr = FlowRequirement(name="ffmpeg", check="ffmpeg -version")
        restored = FlowRequirement.from_dict(fr.to_dict())
        assert restored.name == fr.name
        assert restored.check == fr.check

    def test_config_var_invalid_type(self):
        with pytest.raises(ValueError, match="invalid type"):
            ConfigVar.from_dict({"name": "x", "type": "invalid"})


# ── Config Parsing ───────────────────────────────────────────────────


class TestConfigParsing:
    def test_parse_config_basic(self):
        wf = load_workflow_string("""
name: test
config:
  persona:
    description: "Your persona"
    type: str
    required: true
    example: "You are..."
  max_rounds:
    description: "Max iterations"
    type: number
    default: 5
steps:
  a:
    run: echo ok
    outputs: [result]
    inputs:
      persona: $job.persona
      max_rounds: $job.max_rounds
""")
        assert len(wf.config_vars) == 2
        assert wf.config_vars[0].name == "persona"
        assert wf.config_vars[0].required is True
        assert wf.config_vars[1].name == "max_rounds"
        assert wf.config_vars[1].default == 5
        assert wf.config_vars[1].required is False  # has default → not required

    def test_parse_config_choice_requires_options(self):
        with pytest.raises(YAMLLoadError):
            load_workflow_string("""
name: test
config:
  style:
    type: choice
steps:
  a:
    run: echo ok
    outputs: [r]
""")

    def test_parse_config_invalid_type(self):
        with pytest.raises(YAMLLoadError):
            load_workflow_string("""
name: test
config:
  x:
    type: invalid
steps:
  a:
    run: echo ok
    outputs: [r]
""")

    def test_parse_config_invalid_identifier(self):
        with pytest.raises(YAMLLoadError):
            load_workflow_string("""
name: test
config:
  123bad:
    description: "bad name"
steps:
  a:
    run: echo ok
    outputs: [r]
""")

    def test_parse_config_inferred_required(self):
        wf = load_workflow_string("""
name: test
config:
  with_default:
    default: hello
  no_default:
    description: "needs a value"
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      with_default: $job.with_default
      no_default: $job.no_default
""")
        by_name = {v.name: v for v in wf.config_vars}
        assert by_name["with_default"].required is False
        assert by_name["no_default"].required is True

    def test_parse_config_empty_spec(self):
        """Config var with null spec should get defaults."""
        wf = load_workflow_string("""
name: test
config:
  simple:
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      simple: $job.simple
""")
        assert len(wf.config_vars) == 1
        assert wf.config_vars[0].name == "simple"
        assert wf.config_vars[0].type == "str"


# ── Requires Parsing ─────────────────────────────────────────────────


class TestRequiresParsing:
    def test_parse_requires_structured(self):
        wf = load_workflow_string("""
name: test
requires:
  - name: ffmpeg
    description: Audio processing
    check: "ffmpeg -version"
  - name: camofox
steps:
  a:
    run: echo ok
    outputs: [r]
""")
        assert len(wf.requires) == 2
        assert wf.requires[0].name == "ffmpeg"
        assert wf.requires[0].check == "ffmpeg -version"
        assert wf.requires[1].name == "camofox"
        assert wf.requires[1].check == ""

    def test_parse_requires_shorthand(self):
        wf = load_workflow_string("""
name: test
requires:
  - ffmpeg
  - camofox
steps:
  a:
    run: echo ok
    outputs: [r]
""")
        assert wf.requires[0].name == "ffmpeg"
        assert wf.requires[1].name == "camofox"


# ── Readme Loading ───────────────────────────────────────────────────


class TestReadmeLoading:
    def test_readme_inline(self):
        wf = load_workflow_string("""
name: test
readme: |
  # My Flow
  This flow does great things.
steps:
  a:
    run: echo ok
    outputs: [r]
""")
        assert "My Flow" in wf.readme

    def test_readme_from_file(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(
            "name: test\nsteps:\n  a:\n    run: echo ok\n    outputs: [r]\n"
        )
        (flow_dir / "README.md").write_text("# My Flow\nDetails here.")
        wf = load_workflow_yaml(str(flow_dir / "FLOW.yaml"))
        assert "Details here" in wf.readme

    def test_readme_inline_overrides_file(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(
            "name: test\nreadme: |\n  Inline content.\n"
            "steps:\n  a:\n    run: echo ok\n    outputs: [r]\n"
        )
        (flow_dir / "README.md").write_text("File content.")
        wf = load_workflow_yaml(str(flow_dir / "FLOW.yaml"))
        assert "Inline content" in wf.readme
        assert "File content" not in wf.readme


# ── Config Warnings ──────────────────────────────────────────────────


class TestConfigWarnings:
    def test_warning_orphan_job_input(self):
        wf = load_workflow_string("""
name: test
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      foo: $job.foo
""")
        # No config block but has $job.foo — this should NOT warn
        # because warnings only fire when config_vars is non-empty
        warns = wf.warnings()
        assert not any("not declared in config" in w for w in warns)

    def test_warning_orphan_job_input_with_config(self):
        wf = load_workflow_string("""
name: test
config:
  bar:
    description: declared
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      foo: $job.foo
      bar: $job.bar
""")
        warns = wf.warnings()
        assert any("foo" in w and "not declared in config" in w for w in warns)

    def test_warning_unused_config_var(self):
        wf = load_workflow_string("""
name: test
config:
  bar:
    description: unused
steps:
  a:
    run: echo ok
    outputs: [r]
""")
        warns = wf.warnings()
        assert any("bar" in w and "never referenced" in w for w in warns)

    def test_no_warning_when_config_matches_inputs(self):
        wf = load_workflow_string("""
name: test
config:
  x:
    description: input x
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      x: $job.x
""")
        warns = wf.warnings()
        assert not any("not declared" in w or "never referenced" in w for w in warns)

    def test_warning_no_default_or_example(self):
        wf = load_workflow_string("""
name: test
config:
  bare:
    description: "needs something"
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      bare: $job.bare
""")
        warns = wf.warnings()
        assert any("bare" in w and "no default or example" in w for w in warns)


# ── Config Value Resolution ──────────────────────────────────────────


class TestConfigValueResolution:
    def test_load_flow_config_directory(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text("""
name: test
config:
  a:
    default: "1"
  b:
    description: no default
steps:
  s:
    run: echo ok
    outputs: [r]
    inputs:
      a: $job.a
      b: $job.b
""")
        (flow_dir / "config.local.yaml").write_text("b: hello\n")
        wf = load_workflow_yaml(str(flow_dir / "FLOW.yaml"))
        from stepwise.runner import load_flow_config
        result = load_flow_config(flow_dir / "FLOW.yaml", wf)
        assert result == {"a": "1", "b": "hello"}

    def test_load_flow_config_single_file(self, tmp_path):
        flow_file = tmp_path / "mine.flow.yaml"
        flow_file.write_text("""
name: test
config:
  a:
    default: "1"
steps:
  s:
    run: echo ok
    outputs: [r]
    inputs:
      a: $job.a
""")
        (tmp_path / "mine.config.local.yaml").write_text("a: override\n")
        wf = load_workflow_yaml(str(flow_file))
        from stepwise.runner import load_flow_config
        result = load_flow_config(flow_file, wf)
        assert result["a"] == "override"

    def test_load_flow_config_no_file(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text("""
name: test
config:
  x:
    default: "d"
steps:
  s:
    run: echo ok
    outputs: [r]
    inputs:
      x: $job.x
""")
        wf = load_workflow_yaml(str(flow_dir / "FLOW.yaml"))
        from stepwise.runner import load_flow_config
        result = load_flow_config(flow_dir / "FLOW.yaml", wf)
        assert result == {"x": "d"}

    def test_merge_priority(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text("""
name: test
config:
  a:
    default: "1"
  b:
    default: "2"
steps:
  s:
    run: echo ok
    outputs: [r]
    inputs:
      a: $job.a
      b: $job.b
""")
        (flow_dir / "config.local.yaml").write_text("b: 3\nc: 4\n")
        wf = load_workflow_yaml(str(flow_dir / "FLOW.yaml"))
        from stepwise.runner import load_flow_config
        result = load_flow_config(flow_dir / "FLOW.yaml", wf)
        # config default a=1, config.local.yaml overrides b=3 and adds c=4
        assert result == {"a": "1", "b": 3, "c": 4}


# ── Backward Compatibility ───────────────────────────────────────────


class TestBackwardCompatibility:
    def test_flow_without_config_loads_normally(self):
        wf = load_workflow_string("""
name: test
steps:
  a:
    run: echo ok
    outputs: [r]
""")
        assert wf.config_vars == []
        assert wf.requires == []
        assert wf.readme == ""

    def test_workflow_definition_roundtrip_with_config(self):
        wf = load_workflow_string("""
name: test
config:
  persona:
    description: "Your persona"
    example: "You are..."
requires:
  - name: ffmpeg
    check: "ffmpeg -version"
readme: |
  # Readme
  Content here.
steps:
  a:
    run: echo ok
    outputs: [r]
    inputs:
      persona: $job.persona
""")
        d = wf.to_dict()
        restored = WorkflowDefinition.from_dict(d)
        assert len(restored.config_vars) == 1
        assert restored.config_vars[0].name == "persona"
        assert restored.config_vars[0].description == "Your persona"
        assert len(restored.requires) == 1
        assert restored.requires[0].name == "ffmpeg"
        assert "Readme" in restored.readme
