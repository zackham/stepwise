"""Tests for stepwise extensions discovery and CLI command."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.cli import main, EXIT_SUCCESS
from stepwise.extensions import scan_extensions, Extension, CACHE_TTL_SECONDS


# ── Helpers ──────────────────────────────────────────────────────────────────


def _capture_stdout(argv: list[str]) -> tuple[int, str]:
    """Run CLI and capture stdout."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = main(argv)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, output


def _make_executable(path: Path, content: str) -> Path:
    """Write a script file and make it executable."""
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_manifest_extension(dir: Path, name: str, manifest: dict) -> Path:
    """Create a stepwise-<name> script that returns JSON manifest."""
    script = dir / f"stepwise-{name}"
    manifest_json = json.dumps(manifest)
    content = f"""#!/bin/sh
if [ "$1" = "--manifest" ]; then
  echo '{manifest_json}'
  exit 0
fi
echo "extension {name} running"
"""
    return _make_executable(script, content)


def _make_simple_extension(dir: Path, name: str) -> Path:
    """Create a stepwise-<name> script with no --manifest support."""
    script = dir / f"stepwise-{name}"
    content = """#!/bin/sh
echo "no manifest support"
exit 1
"""
    return _make_executable(script, content)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal stepwise project."""
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir()
    (dot_dir / "db.sqlite").touch()
    (dot_dir / "templates").mkdir()
    (dot_dir / "jobs").mkdir()
    return tmp_path


@pytest.fixture
def ext_dir(tmp_path):
    """A temp directory for extension executables."""
    d = tmp_path / "extensions"
    d.mkdir()
    return d


# ── scan_extensions() tests ───────────────────────────────────────────────────


class TestScanExtensions:
    def test_empty_path_returns_empty(self, tmp_path):
        """No stepwise-* executables → empty list."""
        with patch.dict(os.environ, {"PATH": str(tmp_path)}):
            result = scan_extensions(dot_dir=None)
        assert result == []

    def test_finds_manifest_extension(self, ext_dir):
        """Extension supporting --manifest is fully populated."""
        _make_manifest_extension(ext_dir, "telegram", {
            "name": "telegram",
            "version": "0.2.0",
            "description": "Route events to Telegram",
        })
        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert len(result) == 1
        ext = result[0]
        assert ext.name == "telegram"
        assert ext.version == "0.2.0"
        assert ext.description == "Route events to Telegram"
        assert ext.path.endswith("stepwise-telegram")

    def test_finds_extension_without_manifest(self, ext_dir):
        """Extension without --manifest falls back to name+path only."""
        _make_simple_extension(ext_dir, "myext")
        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert len(result) == 1
        ext = result[0]
        assert ext.name == "myext"
        assert ext.version is None
        assert ext.description is None
        assert str(ext_dir) in ext.path

    def test_finds_multiple_extensions_sorted(self, ext_dir):
        """Multiple extensions are returned sorted by name."""
        _make_manifest_extension(ext_dir, "zebra", {"name": "zebra", "version": "1.0.0"})
        _make_manifest_extension(ext_dir, "alpha", {"name": "alpha", "version": "1.0.0"})
        _make_simple_extension(ext_dir, "mango")

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert [e.name for e in result] == ["alpha", "mango", "zebra"]

    def test_ignores_non_stepwise_executables(self, ext_dir):
        """Executables not named stepwise-* are ignored."""
        _make_executable(ext_dir / "other-tool", "#!/bin/sh\necho ok\n")
        _make_manifest_extension(ext_dir, "real", {"name": "real", "version": "1.0.0"})

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert len(result) == 1
        assert result[0].name == "real"

    def test_ignores_non_executable_files(self, ext_dir):
        """Files named stepwise-* but not executable are ignored."""
        non_exec = ext_dir / "stepwise-notexec"
        non_exec.write_text("#!/bin/sh\necho hi\n")
        # Do NOT chmod +x

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert result == []

    def test_first_path_entry_wins(self, tmp_path):
        """When the same name appears in multiple PATH dirs, first wins."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        _make_manifest_extension(dir1, "plugin", {"name": "plugin", "version": "1.0.0", "description": "first"})
        _make_manifest_extension(dir2, "plugin", {"name": "plugin", "version": "2.0.0", "description": "second"})

        path = f"{dir1}{os.pathsep}{dir2}"
        with patch.dict(os.environ, {"PATH": path}):
            result = scan_extensions(dot_dir=None)

        assert len(result) == 1
        assert result[0].version == "1.0.0"
        assert result[0].description == "first"

    def test_cache_is_written(self, ext_dir, tmp_path):
        """After scan, cache file is written to dot_dir."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        _make_manifest_extension(ext_dir, "bot", {"name": "bot", "version": "1.0.0"})

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            scan_extensions(dot_dir=dot_dir)

        cache = dot_dir / "extensions.json"
        assert cache.exists()
        data = json.loads(cache.read_text())
        assert "timestamp" in data
        assert len(data["extensions"]) == 1
        assert data["extensions"][0]["name"] == "bot"

    def test_cache_is_used_within_ttl(self, ext_dir, tmp_path):
        """Results served from cache within TTL without re-scanning."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()

        # Pre-populate cache with a fake extension not on PATH
        cache = dot_dir / "extensions.json"
        cache.write_text(json.dumps({
            "timestamp": time.time(),
            "extensions": [
                {"name": "cached-ext", "path": "/fake/path", "version": "9.9.9",
                 "description": "from cache", "capabilities": None, "config_keys": None}
            ],
        }))

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=dot_dir)

        assert len(result) == 1
        assert result[0].name == "cached-ext"
        assert result[0].version == "9.9.9"

    def test_expired_cache_triggers_rescan(self, ext_dir, tmp_path):
        """Expired cache triggers fresh scan."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        _make_manifest_extension(ext_dir, "fresh", {"name": "fresh", "version": "2.0.0"})

        # Write an expired cache with stale data
        cache = dot_dir / "extensions.json"
        old_ts = time.time() - CACHE_TTL_SECONDS - 1
        cache.write_text(json.dumps({
            "timestamp": old_ts,
            "extensions": [
                {"name": "stale", "path": "/old/path", "version": "0.0.1",
                 "description": None, "capabilities": None, "config_keys": None}
            ],
        }))

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=dot_dir)

        assert len(result) == 1
        assert result[0].name == "fresh"

    def test_refresh_bypasses_cache(self, ext_dir, tmp_path):
        """--refresh forces re-scan even when cache is fresh."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        _make_manifest_extension(ext_dir, "live", {"name": "live", "version": "3.0.0"})

        # Fresh cache with wrong data
        cache = dot_dir / "extensions.json"
        cache.write_text(json.dumps({
            "timestamp": time.time(),
            "extensions": [
                {"name": "cached", "path": "/old", "version": "1.0.0",
                 "description": None, "capabilities": None, "config_keys": None}
            ],
        }))

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=dot_dir, refresh=True)

        assert len(result) == 1
        assert result[0].name == "live"

    def test_manifest_with_capabilities(self, ext_dir):
        """Capabilities and config_keys from manifest are preserved."""
        _make_manifest_extension(ext_dir, "full", {
            "name": "full",
            "version": "1.0.0",
            "description": "Full-featured ext",
            "capabilities": ["event_consumer", "fulfillment"],
            "config_keys": ["api_key", "chat_id"],
        })

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert result[0].capabilities == ["event_consumer", "fulfillment"]
        assert result[0].config_keys == ["api_key", "chat_id"]

    def test_no_cache_without_dot_dir(self, ext_dir, tmp_path):
        """When dot_dir=None, no cache file is written anywhere."""
        _make_manifest_extension(ext_dir, "nocache", {"name": "nocache", "version": "1.0.0"})

        with patch.dict(os.environ, {"PATH": str(ext_dir)}):
            result = scan_extensions(dot_dir=None)

        assert result[0].name == "nocache"
        # No cache files created in ext_dir or tmp_path
        assert not any((tmp_path / ".stepwise" / "extensions.json").exists()
                       for _ in [None])


# ── CLI command tests ─────────────────────────────────────────────────────────


class TestExtensionsCLI:
    def test_no_extensions_helpful_message(self, tmp_project):
        """When no extensions found, print helpful message."""
        with patch.dict(os.environ, {"PATH": ""}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
            ])

        assert code == EXIT_SUCCESS
        assert "No extensions found" in output
        assert "stepwise-<name>" in output

    def test_lists_extension_with_manifest(self, tmp_project, ext_dir):
        """Extensions with manifest are shown with all columns."""
        _make_manifest_extension(ext_dir, "telegram", {
            "name": "telegram",
            "version": "0.1.0",
            "description": "Route events to Telegram",
        })

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "telegram" in output
        assert "0.1.0" in output
        assert "Route events to Telegram" in output
        assert "stepwise-telegram" in output

    def test_lists_extension_without_manifest(self, tmp_project, ext_dir):
        """Extensions without manifest show name and path, dashes for missing fields."""
        _make_simple_extension(ext_dir, "myext")

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "myext" in output
        assert "stepwise-myext" in output

    def test_extensions_list_subcommand(self, tmp_project, ext_dir):
        """'stepwise extensions list' works the same as 'stepwise extensions'."""
        _make_manifest_extension(ext_dir, "demo", {
            "name": "demo",
            "version": "1.0.0",
            "description": "Demo extension",
        })

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions", "list",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "demo" in output
        assert "1.0.0" in output

    def test_table_has_header(self, tmp_project, ext_dir):
        """Output includes a header row with column names."""
        _make_manifest_extension(ext_dir, "x", {"name": "x", "version": "1.0.0"})

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "NAME" in output
        assert "VERSION" in output
        assert "DESCRIPTION" in output
        assert "PATH" in output

    def test_refresh_flag_bypasses_cache(self, tmp_project, ext_dir):
        """--refresh causes fresh scan even if cache is present."""
        dot_dir = tmp_project / ".stepwise"

        # Write a stale cache with fake data
        cache = dot_dir / "extensions.json"
        cache.write_text(json.dumps({
            "timestamp": time.time(),
            "extensions": [
                {"name": "cached-fake", "path": "/fake", "version": "0.0.0",
                 "description": None, "capabilities": None, "config_keys": None}
            ],
        }))

        _make_manifest_extension(ext_dir, "realext", {
            "name": "realext",
            "version": "1.0.0",
            "description": "The real extension",
        })

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "realext" in output
        assert "cached-fake" not in output

    def test_multiple_extensions_displayed(self, tmp_project, ext_dir):
        """Multiple extensions are all shown in the table."""
        _make_manifest_extension(ext_dir, "aaa", {"name": "aaa", "version": "1.0.0"})
        _make_manifest_extension(ext_dir, "bbb", {"name": "bbb", "version": "2.0.0"})
        _make_simple_extension(ext_dir, "ccc")

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(os.environ, {"PATH": path}):
            code, output = _capture_stdout([
                "--project-dir", str(tmp_project),
                "extensions",
                "--refresh",
            ])

        assert code == EXIT_SUCCESS
        assert "aaa" in output
        assert "bbb" in output
        assert "ccc" in output

    def test_extensions_without_project(self, tmp_path, ext_dir):
        """Command works even when not inside a stepwise project (no caching)."""
        _make_manifest_extension(ext_dir, "noproj", {
            "name": "noproj",
            "version": "1.0.0",
            "description": "Works without project",
        })

        path = f"{ext_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        # Use a dir with no .stepwise/
        with patch.dict(os.environ, {"PATH": path}):
            # Can't easily test without --project-dir because find_project
            # walks up from cwd. Just verify the module scan still works.
            result = scan_extensions(dot_dir=None)

        names = [e.name for e in result]
        assert "noproj" in names
