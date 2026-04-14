"""Tests for kit discovery, KIT.yaml parsing, and kit-aware flow resolution."""

import pytest
from pathlib import Path

from stepwise.flow_resolution import (
    FlowInfo,
    FlowResolutionError,
    KitInfo,
    discover_flows,
    discover_kits,
    resolve_flow,
)
from stepwise.models import KitDefinition
from stepwise.yaml_loader import KitLoadError, load_kit_yaml


SIMPLE_FLOW = """\
name: test
author: test
steps:
  hello:
    run: 'echo "hello"'
    outputs: [msg]
"""

MINIMAL_KIT = """\
name: {name}
author: test
description: "Test kit"
"""

FULL_KIT = """\
name: {name}
description: "Full test kit"
author: tester
category: testing
usage: |
  ## When to use
  Always.
include:
  - "@community:helper@^1.0"
tags: [test, demo]
"""


def _make_kit(project: Path, kit_name: str, flow_names: list[str],
              kit_yaml: str | None = None) -> Path:
    """Create a kit directory with member flows under flows/."""
    kit_dir = project / "flows" / kit_name
    kit_dir.mkdir(parents=True, exist_ok=True)
    yaml = kit_yaml or MINIMAL_KIT.format(name=kit_name)
    (kit_dir / "KIT.yaml").write_text(yaml)
    for flow_name in flow_names:
        flow_dir = kit_dir / flow_name
        flow_dir.mkdir(exist_ok=True)
        (flow_dir / "FLOW.yaml").write_text(
            SIMPLE_FLOW.replace("name: test", f"name: {flow_name}")
        )
    return kit_dir


def _make_standalone_flow(project: Path, name: str) -> Path:
    """Create a standalone directory flow under flows/."""
    flow_dir = project / "flows" / name
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "FLOW.yaml").write_text(
        SIMPLE_FLOW.replace("name: test", f"name: {name}")
    )
    return flow_dir


class TestKitDefinition:
    """KitDefinition dataclass serialization."""

    def test_round_trip_minimal(self):
        k = KitDefinition(name="test", description="A test kit")
        d = k.to_dict()
        assert d == {"name": "test", "description": "A test kit"}
        k2 = KitDefinition.from_dict(d)
        assert k2.name == "test"
        assert k2.description == "A test kit"
        assert k2.author == ""
        assert k2.tags == []

    def test_round_trip_full(self):
        k = KitDefinition(
            name="swdev", description="Dev kit", author="zack",
            category="development", usage="Use wisely",
            include=["@bob:review"],
            tags=["dev", "plan"],
        )
        k2 = KitDefinition.from_dict(k.to_dict())
        assert k2.name == k.name
        assert k2.include == k.include
        assert k2.tags == k.tags

    def test_to_dict_omits_defaults(self):
        k = KitDefinition(name="test", description="Test")
        d = k.to_dict()
        assert "author" not in d
        assert "tags" not in d
        assert "include" not in d

    def test_from_dict_missing_keys_uses_defaults(self):
        k = KitDefinition.from_dict({"name": "test"})
        assert k.description == ""
        assert k.tags == []


class TestLoadKitYaml:
    """KIT.yaml parsing and validation."""

    def test_minimal_kit(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: mykit\ndescription: Test\n")
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "mykit"
        assert result.description == "Test"

    def test_full_kit(self, tmp_path):
        kit_dir = tmp_path / "swdev"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(FULL_KIT.format(name="swdev"))
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "swdev"
        assert result.author == "tester"
        assert result.category == "testing"
        assert "Always" in result.usage
        assert result.include == ["@community:helper@^1.0"]
        assert result.tags == ["test", "demo"]

    def test_missing_name_errors(self, tmp_path):
        kit_dir = tmp_path / "bad"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("description: No name\n")
        with pytest.raises(KitLoadError, match="name.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_missing_description_errors(self, tmp_path):
        kit_dir = tmp_path / "bad"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: bad\n")
        with pytest.raises(KitLoadError, match="description.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_name_dir_mismatch_errors(self, tmp_path):
        kit_dir = tmp_path / "actual-dir"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: wrong-name\ndescription: Test\n")
        with pytest.raises(KitLoadError, match="does not match directory"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_invalid_name_errors(self, tmp_path):
        kit_dir = tmp_path / "bad kit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: bad kit\ndescription: Test\n")
        with pytest.raises(KitLoadError, match="Invalid kit name"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_unknown_keys_tolerated(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(
            "name: mykit\ndescription: Test\nfuture_field: value\n"
        )
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "mykit"

    def test_include_must_be_list(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(
            "name: mykit\ndescription: Test\ninclude: not-a-list\n"
        )
        with pytest.raises(KitLoadError, match="include.*must be a list"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_malformed_yaml_errors(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(": : : invalid yaml :::")
        with pytest.raises(KitLoadError, match="Failed to parse"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_empty_yaml_errors(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("")
        with pytest.raises(KitLoadError, match="name.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")


class TestDiscoverKits:
    """discover_kits() finds all kits in a project."""

    def test_finds_kit_in_flows_dir(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["flow-a", "flow-b"])
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].name == "mykit"

    def test_kit_members_discovered(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["alpha", "beta"])
        kits = discover_kits(tmp_path)
        assert sorted(kits[0].flow_names) == ["alpha", "beta"]
        assert len(kits[0].flow_paths) == 2
        assert all(p.name == "FLOW.yaml" for p in kits[0].flow_paths)

    def test_empty_kit_has_no_members(self, tmp_path):
        _make_kit(tmp_path, "empty-kit", [])
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].flow_names == []

    def test_multiple_kits(self, tmp_path):
        _make_kit(tmp_path, "alpha-kit", ["f1"])
        _make_kit(tmp_path, "beta-kit", ["f2"])
        kits = discover_kits(tmp_path)
        names = [k.name for k in kits]
        assert sorted(names) == ["alpha-kit", "beta-kit"]

    def test_kit_in_project_root(self, tmp_path):
        """Kit can exist at project root, not just flows/."""
        kit_dir = tmp_path / "rootkit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: rootkit\ndescription: Test\n")
        flow_dir = kit_dir / "myflow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        kits = discover_kits(tmp_path)
        assert any(k.name == "rootkit" for k in kits)

    def test_kit_and_standalone_coexist(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["kit-flow"])
        _make_standalone_flow(tmp_path, "solo")
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].name == "mykit"

    def test_dir_with_flow_yaml_is_not_kit(self, tmp_path):
        """A directory with FLOW.yaml but no KIT.yaml is a flow, not a kit."""
        _make_standalone_flow(tmp_path, "not-a-kit")
        kits = discover_kits(tmp_path)
        assert len(kits) == 0

    def test_deduplicates_by_resolved_path(self, tmp_path):
        """Same kit reachable from multiple search dirs is only listed once."""
        _make_kit(tmp_path, "mykit", ["f1"])
        kits = discover_kits(tmp_path)
        assert sum(1 for k in kits if k.name == "mykit") == 1


class TestDiscoverFlowsWithKits:
    """discover_flows() correctly handles kit member flows."""

    def test_kit_members_have_kit_name(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["flow-a", "flow-b"])
        flows = discover_flows(tmp_path)
        kit_flows = [f for f in flows if f.kit_name == "mykit"]
        assert len(kit_flows) == 2
        assert sorted(f.name for f in kit_flows) == ["flow-a", "flow-b"]

    def test_standalone_has_null_kit_name(self, tmp_path):
        _make_standalone_flow(tmp_path, "solo")
        flows = discover_flows(tmp_path)
        solo = [f for f in flows if f.name == "solo"]
        assert len(solo) == 1
        assert solo[0].kit_name is None

    def test_kit_members_not_duplicated(self, tmp_path):
        """Kit member flows don't also appear as standalone flows."""
        _make_kit(tmp_path, "mykit", ["plan", "implement"])
        _make_standalone_flow(tmp_path, "solo")
        flows = discover_flows(tmp_path)
        plan_flows = [f for f in flows if f.name == "plan"]
        assert len(plan_flows) == 1
        assert plan_flows[0].kit_name == "mykit"

    def test_rglob_skips_kit_internals(self, tmp_path):
        """rglob phase doesn't independently discover kit member flows."""
        _make_kit(tmp_path, "mykit", ["deep-flow"])
        flows = discover_flows(tmp_path)
        deep = [f for f in flows if f.name == "deep-flow"]
        assert len(deep) == 1
        assert deep[0].kit_name == "mykit"

    def test_mixed_kit_and_standalone(self, tmp_path):
        """Both kit member flows and standalone flows appear in results."""
        _make_kit(tmp_path, "mykit", ["kit-flow"])
        _make_standalone_flow(tmp_path, "standalone")
        flows = discover_flows(tmp_path)
        names = {f.name for f in flows}
        assert names == {"kit-flow", "standalone"}

    def test_dir_with_both_kit_and_flow_yaml_treated_as_kit(self, tmp_path):
        """If a directory has both KIT.yaml and FLOW.yaml, KIT.yaml wins."""
        weird_dir = tmp_path / "flows" / "ambiguous"
        weird_dir.mkdir(parents=True)
        (weird_dir / "KIT.yaml").write_text("name: ambiguous\ndescription: Test\n")
        (weird_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        member = weird_dir / "child"
        member.mkdir()
        (member / "FLOW.yaml").write_text(SIMPLE_FLOW)
        flows = discover_flows(tmp_path)
        assert not any(f.name == "ambiguous" and f.kit_name is None for f in flows)
        assert any(f.name == "child" and f.kit_name == "ambiguous" for f in flows)

    def test_existing_discovery_tests_still_pass(self, tmp_path):
        """Basic non-kit discovery still works (regression guard)."""
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        df = flows_dir / "dir-flow"
        df.mkdir()
        (df / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flows_dir / "file-flow.flow.yaml").write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        names = {f.name for f in result}
        assert names == {"dir-flow", "file-flow"}
        assert all(f.kit_name is None for f in result)


class TestResolveKitFlow:
    """Kit-qualified flow resolution and strict-with-hints."""

    def test_kit_slash_flow_resolves(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["my-flow"])
        result = resolve_flow("mykit/my-flow", project_dir=tmp_path)
        expected = tmp_path / "flows" / "mykit" / "my-flow" / "FLOW.yaml"
        assert result == expected

    def test_bare_name_doesnt_resolve_kit_member(self, tmp_path):
        """Bare 'my-flow' does NOT resolve into a kit."""
        _make_kit(tmp_path, "mykit", ["my-flow"])
        with pytest.raises(FlowResolutionError):
            resolve_flow("my-flow", project_dir=tmp_path)

    def test_bare_name_hint_single_kit(self, tmp_path):
        """Error message suggests kit-qualified name when flow exists in one kit."""
        _make_kit(tmp_path, "mykit", ["plan"])
        with pytest.raises(FlowResolutionError, match="Did you mean.*mykit/plan"):
            resolve_flow("plan", project_dir=tmp_path)

    def test_bare_name_hint_multiple_kits(self, tmp_path):
        """Error suggests multiple options when flow exists in multiple kits."""
        _make_kit(tmp_path, "alpha", ["shared"])
        _make_kit(tmp_path, "beta", ["shared"])
        with pytest.raises(FlowResolutionError, match="Did you mean.*alpha/shared.*beta/shared"):
            resolve_flow("shared", project_dir=tmp_path)

    def test_nonexistent_kit_errors(self, tmp_path):
        (tmp_path / "flows").mkdir(exist_ok=True)
        with pytest.raises(FlowResolutionError, match="Kit.*not found"):
            resolve_flow("nokit/flow", project_dir=tmp_path)

    def test_nonexistent_flow_in_kit_errors(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["real-flow"])
        with pytest.raises(FlowResolutionError, match="not found in kit.*mykit"):
            resolve_flow("mykit/no-such-flow", project_dir=tmp_path)

    def test_nonexistent_flow_lists_available(self, tmp_path):
        """Error for missing flow in kit lists available flows."""
        _make_kit(tmp_path, "mykit", ["alpha", "beta"])
        with pytest.raises(FlowResolutionError, match="alpha.*beta"):
            resolve_flow("mykit/missing", project_dir=tmp_path)

    def test_nested_slash_rejected(self, tmp_path):
        """'a/b/c' is rejected (no nested kits)."""
        with pytest.raises(FlowResolutionError, match="one level only"):
            resolve_flow("a/b/c", project_dir=tmp_path)

    def test_existing_standalone_not_affected(self, tmp_path):
        """Standalone flow resolution still works when kits exist."""
        _make_kit(tmp_path, "mykit", ["kit-only-flow"])
        _make_standalone_flow(tmp_path, "solo")
        result = resolve_flow("solo", project_dir=tmp_path)
        assert result == tmp_path / "flows" / "solo" / "FLOW.yaml"

    def test_kit_name_as_bare_name_gives_kit_error(self, tmp_path):
        """'stepwise run swdev' when swdev is a kit gives helpful error."""
        _make_kit(tmp_path, "swdev", ["plan", "implement"])
        with pytest.raises(FlowResolutionError, match="is a kit.*not a flow"):
            resolve_flow("swdev", project_dir=tmp_path)

    def test_kit_name_as_bare_name_lists_flows(self, tmp_path):
        """Error for kit-as-flow includes available flow names."""
        _make_kit(tmp_path, "swdev", ["plan", "implement"])
        with pytest.raises(FlowResolutionError, match="implement.*plan"):
            resolve_flow("swdev", project_dir=tmp_path)

    def test_yaml_extension_still_rejects_with_slash(self, tmp_path):
        """'kit/flow.yaml' is still treated as path lookup, not kit ref."""
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("kit/flow.yaml", project_dir=tmp_path)

    def test_dir_without_kit_yaml_not_treated_as_kit(self, tmp_path):
        """A directory with subdir/FLOW.yaml but no KIT.yaml is not a kit."""
        parent = tmp_path / "flows" / "notkit"
        parent.mkdir(parents=True)
        child = parent / "child"
        child.mkdir()
        (child / "FLOW.yaml").write_text(SIMPLE_FLOW)
        with pytest.raises(FlowResolutionError, match="not a kit.*no KIT.yaml"):
            resolve_flow("notkit/child", project_dir=tmp_path)
