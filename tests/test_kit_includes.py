"""Tests for kit include resolution and version matching."""

import json
from pathlib import Path

import pytest

from stepwise.flow_resolution import (
    FLOW_DIR_MARKER,
    KIT_DIR_MARKER,
    FlowResolutionError,
    IncludedFlow,
    IncludeRef,
    discover_kits,
    parse_include_ref,
    resolve_flow,
    resolve_kit_includes,
)
from stepwise.version import (
    VersionError,
    parse_constraint,
    parse_version,
    version_matches,
)


# ── Version matching ──────────────────────────────────────────────


class TestVersionParsing:
    def test_caret_major(self):
        assert version_matches("1.5.0", "^1.0.0")
        assert version_matches("1.99.99", "^1.0.0")
        assert not version_matches("2.0.0", "^1.0.0")
        assert not version_matches("0.9.0", "^1.0.0")

    def test_caret_minor(self):
        assert version_matches("0.2.5", "^0.2.0")
        assert not version_matches("0.3.0", "^0.2.0")
        assert not version_matches("1.0.0", "^0.2.0")

    def test_caret_patch(self):
        assert version_matches("0.0.3", "^0.0.3")
        assert not version_matches("0.0.4", "^0.0.3")

    def test_tilde(self):
        assert version_matches("1.2.9", "~1.2.3")
        assert not version_matches("1.3.0", "~1.2.3")
        assert not version_matches("1.2.2", "~1.2.3")

    def test_exact(self):
        assert version_matches("1.2.3", "1.2.3")
        assert not version_matches("1.2.4", "1.2.3")

    def test_star(self):
        assert version_matches("999.999.999", "*")
        assert version_matches("0.0.1", "*")

    def test_pep440_passthrough(self):
        assert version_matches("1.5.0", ">=1.0,<2.0")
        assert not version_matches("2.0.0", ">=1.0,<2.0")

    def test_invalid_constraint(self):
        with pytest.raises(VersionError):
            parse_constraint("not-a-version!!!")

    def test_caret_short_version(self):
        # ^1.0 should work like ^1.0.0
        assert version_matches("1.5.0", "^1.0")
        assert not version_matches("2.0.0", "^1.0")


# ── Include parsing ──────────────────────────────────────────────


class TestIncludeParsing:
    def test_registry_simple(self):
        ref = parse_include_ref("@community:code-review")
        assert ref.ref_type == "registry"
        assert ref.author == "community"
        assert ref.slug == "code-review"
        assert ref.version_constraint == ""
        assert ref.flow_name == "code-review"

    def test_registry_with_version(self):
        ref = parse_include_ref("@zack:test-runner@^1.0")
        assert ref.ref_type == "registry"
        assert ref.author == "zack"
        assert ref.slug == "test-runner"
        assert ref.version_constraint == "^1.0"
        assert ref.flow_name == "test-runner"

    def test_registry_exact_version(self):
        ref = parse_include_ref("@bob:helper@1.2.3")
        assert ref.version_constraint == "1.2.3"

    def test_kit_flow(self):
        ref = parse_include_ref("podcast/podcast-deep")
        assert ref.ref_type == "kit"
        assert ref.kit == "podcast"
        assert ref.flow == "podcast-deep"
        assert ref.flow_name == "podcast-deep"

    def test_standalone(self):
        ref = parse_include_ref("my-flow")
        assert ref.ref_type == "standalone"
        assert ref.flow_name == "my-flow"

    def test_empty_raises(self):
        with pytest.raises(FlowResolutionError):
            parse_include_ref("")

    def test_invalid_registry_no_colon(self):
        with pytest.raises(FlowResolutionError):
            parse_include_ref("@badref")

    def test_invalid_kit_double_slash(self):
        with pytest.raises(FlowResolutionError):
            parse_include_ref("a/b/c")


# ── Include resolution ──────────────────────────────────────────


@pytest.fixture
def include_project(tmp_path):
    """Project with multiple kits and standalone flows for include testing."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".stepwise").mkdir()
    flows = project / "flows"
    flows.mkdir()

    def _make_flow(parent, name):
        d = parent / name
        d.mkdir(parents=True, exist_ok=True)
        (d / FLOW_DIR_MARKER).write_text(
            f"name: {name}\ndescription: '{name} flow'\n"
            f"steps:\n  s:\n    run: echo ok\n    outputs: [x]\n"
        )

    # Kit A: includes flows from kit B and standalone
    kit_a = flows / "kit-a"
    kit_a.mkdir()
    (kit_a / KIT_DIR_MARKER).write_text(
        "name: kit-a\n"
        "description: Kit A\n"
        "include:\n"
        "  - kit-b/helper\n"
        "  - standalone-util\n"
    )
    _make_flow(kit_a, "main-flow")

    # Kit B: has a helper flow
    kit_b = flows / "kit-b"
    kit_b.mkdir()
    (kit_b / KIT_DIR_MARKER).write_text(
        "name: kit-b\n"
        "description: Kit B\n"
    )
    _make_flow(kit_b, "helper")
    _make_flow(kit_b, "other")

    # Standalone flow
    _make_flow(flows, "standalone-util")

    return project


class TestIncludeResolution:
    def test_resolve_local_kit_include(self, include_project):
        includes = ["kit-b/helper"]
        resolved = resolve_kit_includes(
            "kit-a", includes, include_project, {"main-flow"}, auto_fetch=False,
        )
        assert len(resolved) == 1
        assert resolved[0].name == "helper"
        assert resolved[0].source_type == "kit"
        assert resolved[0].path.exists()

    def test_resolve_standalone_include(self, include_project):
        includes = ["standalone-util"]
        resolved = resolve_kit_includes(
            "kit-a", includes, include_project, {"main-flow"}, auto_fetch=False,
        )
        assert len(resolved) == 1
        assert resolved[0].name == "standalone-util"
        assert resolved[0].source_type == "standalone"

    def test_collision_with_bundled_raises(self, include_project):
        includes = ["kit-b/helper"]
        with pytest.raises(FlowResolutionError, match="conflicts with bundled"):
            resolve_kit_includes(
                "kit-a", includes, include_project,
                {"helper"},  # pretend helper is bundled
                auto_fetch=False,
            )

    def test_duplicate_include_raises(self, include_project):
        # Two includes that resolve to the same name
        includes = ["kit-b/helper", "kit-b/helper"]
        with pytest.raises(FlowResolutionError, match="duplicate"):
            resolve_kit_includes(
                "kit-a", includes, include_project, set(), auto_fetch=False,
            )

    def test_missing_include_warns_not_errors(self, include_project):
        includes = ["nonexistent-flow"]
        # Should not raise — warns and skips
        resolved = resolve_kit_includes(
            "kit-a", includes, include_project, set(), auto_fetch=False,
        )
        assert len(resolved) == 0


class TestDiscoverKitsWithIncludes:
    def test_discover_resolves_includes(self, include_project):
        kits = discover_kits(include_project)
        kit_a = next(k for k in kits if k.name == "kit-a")
        assert "main-flow" in kit_a.flow_names  # bundled
        assert len(kit_a.included_flows) == 2
        inc_names = {f.name for f in kit_a.included_flows}
        assert "helper" in inc_names
        assert "standalone-util" in inc_names

    def test_all_flow_names_includes_both(self, include_project):
        kits = discover_kits(include_project)
        kit_a = next(k for k in kits if k.name == "kit-a")
        all_names = kit_a.all_flow_names
        assert "main-flow" in all_names
        assert "helper" in all_names
        assert "standalone-util" in all_names

    def test_resolve_included_flow_via_kit_slash(self, include_project):
        """kit-a/helper should resolve through includes."""
        path = resolve_flow("kit-a/helper", include_project)
        assert path.exists()
        assert "kit-b" in str(path)  # the actual file is in kit-b
