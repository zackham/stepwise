"""Tests for flow archive/unarchive/delete functionality."""

from pathlib import Path

import pytest

from stepwise.flow_resolution import is_archived, set_flow_archived


MINIMAL_FLOW = "name: test-flow\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"


class TestHelpers:
    """Unit tests for is_archived() and set_flow_archived()."""

    def test_is_archived_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(f"name: x\narchived: true\n{MINIMAL_FLOW.split(chr(10), 1)[1]}")
        assert is_archived(p) is True

    def test_is_archived_false_missing(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert is_archived(p) is False

    def test_is_archived_false_explicit(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: x\narchived: false\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert is_archived(p) is False

    def test_is_archived_broken_yaml(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(": : : invalid :::")
        assert is_archived(p) is False

    def test_is_archived_nonexistent(self, tmp_path):
        p = tmp_path / "nope.flow.yaml"
        assert is_archived(p) is False

    def test_set_archived_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert set_flow_archived(p, True) is True
        content = p.read_text()
        assert "archived: true" in content

    def test_set_archived_false(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: test-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert set_flow_archived(p, False) is True
        content = p.read_text()
        assert "archived" not in content

    def test_set_archived_idempotent_true(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text("name: test-flow\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n")
        assert set_flow_archived(p, True) is False

    def test_set_archived_idempotent_false(self, tmp_path):
        p = tmp_path / "flow.flow.yaml"
        p.write_text(MINIMAL_FLOW)
        assert set_flow_archived(p, False) is False

    def test_round_trip_preserves_content(self, tmp_path):
        original = "name: test-flow  # important comment\ndescription: keep this\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [result]\n"
        p = tmp_path / "flow.flow.yaml"
        p.write_text(original)

        set_flow_archived(p, True)
        assert "archived: true" in p.read_text()
        assert "# important comment" in p.read_text()

        set_flow_archived(p, False)
        restored = p.read_text()
        assert restored == original
