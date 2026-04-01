"""Tests for FlowMetadata and YAML metadata parsing."""

import pytest
from pathlib import Path

from stepwise.models import FlowMetadata, WorkflowDefinition
from stepwise.yaml_loader import (
    load_workflow_yaml,
    load_workflow_string,
    get_author,
    _parse_metadata,
    YAMLLoadError,
)


class TestFlowMetadata:
    """FlowMetadata dataclass serialization and defaults."""

    def test_empty_metadata(self):
        m = FlowMetadata()
        assert m.name == ""
        assert m.description == ""
        assert m.author == ""
        assert m.version == ""
        assert m.visibility == "interactive"

    def test_to_dict_omits_empty(self):
        m = FlowMetadata()
        assert m.to_dict() == {}
        # visibility=interactive is the default, so omitted
        assert "visibility" not in m.to_dict()

    def test_to_dict_includes_set_fields(self):
        m = FlowMetadata(name="test", author="zack")
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["author"] == "zack"
        assert "description" not in d
        assert "version" not in d

    def test_to_dict_includes_non_default_visibility(self):
        m = FlowMetadata(name="bg", visibility="background")
        d = m.to_dict()
        assert d["visibility"] == "background"

    def test_to_dict_omits_interactive_visibility(self):
        m = FlowMetadata(name="test", visibility="interactive")
        d = m.to_dict()
        assert "visibility" not in d

    def test_from_dict_full(self):
        d = {
            "name": "my-flow",
            "description": "A test flow",
            "author": "zack",
            "version": "1.0",
        }
        m = FlowMetadata.from_dict(d)
        assert m.name == "my-flow"
        assert m.description == "A test flow"
        assert m.author == "zack"
        assert m.version == "1.0"
        assert m.visibility == "interactive"

    def test_from_dict_with_visibility(self):
        d = {"name": "x", "visibility": "internal"}
        m = FlowMetadata.from_dict(d)
        assert m.visibility == "internal"

    def test_from_dict_empty(self):
        m = FlowMetadata.from_dict({})
        assert m.name == ""
        assert m.visibility == "interactive"

    def test_round_trip(self):
        original = FlowMetadata(
            name="roundtrip",
            description="Test round trip",
            author="tester",
            version="2.0",
        )
        restored = FlowMetadata.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.author == original.author
        assert restored.version == original.version
        assert restored.visibility == original.visibility

    def test_round_trip_visibility(self):
        original = FlowMetadata(name="bg", visibility="background")
        restored = FlowMetadata.from_dict(original.to_dict())
        assert restored.visibility == "background"


class TestWorkflowDefinitionMetadata:
    """WorkflowDefinition includes metadata in serialization."""

    def test_to_dict_includes_metadata(self):
        wf = WorkflowDefinition(
            steps={},
            metadata=FlowMetadata(name="test"),
        )
        d = wf.to_dict()
        assert "metadata" in d
        assert d["metadata"]["name"] == "test"

    def test_to_dict_omits_empty_metadata(self):
        wf = WorkflowDefinition(steps={})
        d = wf.to_dict()
        assert "metadata" not in d

    def test_from_dict_restores_metadata(self):
        d = {
            "steps": {},
            "metadata": {"name": "restored"},
        }
        wf = WorkflowDefinition.from_dict(d)
        assert wf.metadata.name == "restored"


class TestYAMLMetadataParsing:
    """YAML loader parses metadata from top-level fields."""

    FLOW_WITH_METADATA = """\
name: my-demo
description: A demo workflow
author: zack
version: "1.0"
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

    FLOW_WITHOUT_METADATA = """\
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

    FLOW_PARTIAL_METADATA = """\
name: partial
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

    def test_parses_full_metadata(self, tmp_path):
        flow = tmp_path / "demo.flow.yaml"
        flow.write_text(self.FLOW_WITH_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.name == "my-demo"
        assert wf.metadata.description == "A demo workflow"
        assert wf.metadata.author == "zack"
        assert wf.metadata.version == "1.0"

    def test_backward_compatible_no_metadata(self, tmp_path):
        flow = tmp_path / "bare.flow.yaml"
        flow.write_text(self.FLOW_WITHOUT_METADATA)
        wf = load_workflow_yaml(flow)
        # No metadata fields → defaults
        assert wf.metadata.description == ""
        assert wf.metadata.author == ""
        # But name should default from filename
        assert wf.metadata.name == "bare"

    def test_name_defaults_from_filename(self, tmp_path):
        flow = tmp_path / "my-cool-flow.flow.yaml"
        flow.write_text(self.FLOW_WITHOUT_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.name == "my-cool-flow"

    def test_name_defaults_from_plain_yaml(self, tmp_path):
        flow = tmp_path / "simple.yaml"
        flow.write_text(self.FLOW_WITHOUT_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.name == "simple"

    def test_explicit_name_overrides_filename(self, tmp_path):
        flow = tmp_path / "other.flow.yaml"
        flow.write_text(self.FLOW_PARTIAL_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.name == "partial"

    def test_partial_metadata(self, tmp_path):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(self.FLOW_PARTIAL_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.name == "partial"
        assert wf.metadata.description == ""

    def test_string_source_no_filename_fallback(self):
        wf = load_workflow_yaml(self.FLOW_WITHOUT_METADATA)
        # No file path → name stays empty
        assert wf.metadata.name == ""

    def test_flow_yaml_extension_recognized(self, tmp_path):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(self.FLOW_WITH_METADATA)
        wf = load_workflow_yaml(str(flow))
        assert wf.metadata.name == "my-demo"

    def test_plain_yaml_extension_works(self, tmp_path):
        flow = tmp_path / "test.yaml"
        flow.write_text(self.FLOW_WITH_METADATA)
        wf = load_workflow_yaml(str(flow))
        assert wf.metadata.name == "my-demo"

    def test_visibility_defaults_to_interactive(self, tmp_path):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(self.FLOW_WITHOUT_METADATA)
        wf = load_workflow_yaml(flow)
        assert wf.metadata.visibility == "interactive"

    def test_visibility_background(self):
        yaml_str = """\
name: bg-flow
visibility: background
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""
        wf = load_workflow_yaml(yaml_str)
        assert wf.metadata.visibility == "background"

    def test_visibility_internal(self):
        yaml_str = """\
name: internal-flow
visibility: internal
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""
        wf = load_workflow_yaml(yaml_str)
        assert wf.metadata.visibility == "internal"

    def test_visibility_interactive_explicit(self):
        yaml_str = """\
name: explicit-flow
visibility: interactive
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""
        wf = load_workflow_yaml(yaml_str)
        assert wf.metadata.visibility == "interactive"

    def test_visibility_invalid_rejected(self):
        yaml_str = """\
name: bad-flow
visibility: secret
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""
        with pytest.raises(YAMLLoadError, match="Invalid visibility 'secret'"):
            load_workflow_yaml(yaml_str)


class TestGetAuthor:
    """Author auto-population from git config."""

    def test_returns_string(self):
        author = get_author()
        assert isinstance(author, str)
        assert len(author) > 0

    def test_git_fallback_to_user(self, monkeypatch):
        """When git is not available, falls back to $USER."""
        import subprocess

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setenv("USER", "testuser")
        assert get_author() == "testuser"

    def test_fallback_to_anonymous(self, monkeypatch):
        """When neither git nor $USER available, returns 'anonymous'."""
        import subprocess

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        assert get_author() == "anonymous"


class TestParseMetadata:
    """Internal _parse_metadata helper."""

    def test_with_source_path_flow_yaml(self):
        data = {}
        m = _parse_metadata(data, Path("/tmp/my-flow.flow.yaml"))
        assert m.name == "my-flow"

    def test_with_source_path_plain_yaml(self):
        data = {}
        m = _parse_metadata(data, Path("/tmp/simple.yaml"))
        assert m.name == "simple"

    def test_explicit_name_wins(self):
        data = {"name": "explicit"}
        m = _parse_metadata(data, Path("/tmp/different.flow.yaml"))
        assert m.name == "explicit"

    def test_no_source_path(self):
        data = {}
        m = _parse_metadata(data, None)
        assert m.name == ""

    def test_visibility_parsed(self):
        data = {"visibility": "background"}
        m = _parse_metadata(data, None)
        assert m.visibility == "background"

    def test_visibility_defaults_interactive(self):
        data = {}
        m = _parse_metadata(data, None)
        assert m.visibility == "interactive"
