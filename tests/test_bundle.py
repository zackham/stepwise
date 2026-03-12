"""Tests for bundle support — collect for sharing, unpack on get."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from stepwise.bundle import (
    ALLOWED_EXTENSIONS,
    BLOCKED_DIRS,
    BLOCKED_FILES,
    MAX_BUNDLE_SIZE,
    MAX_FILE_COUNT,
    BundleError,
    collect_bundle,
    unpack_bundle,
)
from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main


SIMPLE_FLOW = """\
name: my-flow
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


# ── collect_bundle tests ──────────────────────────────────────────────


class TestCollectBundle:
    def test_collects_py_and_md_files(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / "helper.py").write_text("print('hello')")
        (flow_dir / "README.md").write_text("# My Flow")

        files = collect_bundle(flow_dir)

        assert "helper.py" in files
        assert "README.md" in files
        assert files["helper.py"] == "print('hello')"
        assert files["README.md"] == "# My Flow"

    def test_skips_flow_yaml(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / "helper.py").write_text("x = 1")

        files = collect_bundle(flow_dir)

        assert "FLOW.yaml" not in files
        assert "helper.py" in files

    def test_skips_blocked_directories(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        for blocked in ["__pycache__", ".git", "node_modules"]:
            d = flow_dir / blocked
            d.mkdir()
            (d / "file.py").write_text("blocked")

        (flow_dir / "helper.py").write_text("allowed")

        files = collect_bundle(flow_dir)

        assert "helper.py" in files
        assert len(files) == 1

    def test_raises_on_blocked_files(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / ".env").write_text("SECRET=abc")

        with pytest.raises(BundleError, match="Blocked file"):
            collect_bundle(flow_dir)

    def test_skips_non_allowed_extensions(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / "image.png").write_bytes(b"\x89PNG")
        (flow_dir / "data.csv").write_text("a,b,c")
        (flow_dir / "helper.py").write_text("x = 1")

        files = collect_bundle(flow_dir)

        assert "helper.py" in files
        assert "image.png" not in files
        assert "data.csv" not in files

    def test_skips_binary_files_silently(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        # Write a .py file with invalid UTF-8
        (flow_dir / "binary.py").write_bytes(b"\x80\x81\x82")
        (flow_dir / "good.py").write_text("x = 1")

        files = collect_bundle(flow_dir)

        assert "good.py" in files
        assert "binary.py" not in files

    def test_raises_on_too_many_files(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        for i in range(MAX_FILE_COUNT + 1):
            (flow_dir / f"file{i}.py").write_text(f"x = {i}")

        with pytest.raises(BundleError, match="Too many files"):
            collect_bundle(flow_dir)

    def test_raises_on_too_large_bundle(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        # Write a file larger than MAX_BUNDLE_SIZE
        (flow_dir / "big.txt").write_text("x" * (MAX_BUNDLE_SIZE + 1))

        with pytest.raises(BundleError, match="Bundle too large"):
            collect_bundle(flow_dir)

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        files = collect_bundle(flow_dir)

        assert files == {}

    def test_skips_hidden_files(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / ".hidden.py").write_text("secret")
        (flow_dir / "visible.py").write_text("public")

        files = collect_bundle(flow_dir)

        assert "visible.py" in files
        assert ".hidden.py" not in files

    def test_allows_origin_json(self, tmp_path):
        """The .origin.json file is explicitly allowed despite being hidden."""
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / ".origin.json").write_text('{"source": "test"}')

        files = collect_bundle(flow_dir)

        assert ".origin.json" in files

    def test_collects_subdirectory_files(self, tmp_path):
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        sub = flow_dir / "lib"
        sub.mkdir()
        (sub / "utils.py").write_text("def helper(): pass")

        files = collect_bundle(flow_dir)

        assert "lib/utils.py" in files

    def test_not_a_directory_raises(self, tmp_path):
        f = tmp_path / "not-a-dir.txt"
        f.write_text("nope")

        with pytest.raises(BundleError, match="Not a directory"):
            collect_bundle(f)


# ── unpack_bundle tests ──────────────────────────────────────────────


class TestUnpackBundle:
    def test_creates_flow_yaml(self, tmp_path):
        target = tmp_path / "my-flow"
        flow_path = unpack_bundle(target, SIMPLE_FLOW)

        assert flow_path == target / "FLOW.yaml"
        assert flow_path.read_text() == SIMPLE_FLOW

    def test_creates_subdirectories_for_files(self, tmp_path):
        target = tmp_path / "my-flow"
        files = {
            "helper.py": "x = 1",
            "lib/utils.py": "def helper(): pass",
        }
        unpack_bundle(target, SIMPLE_FLOW, files=files)

        assert (target / "helper.py").read_text() == "x = 1"
        assert (target / "lib" / "utils.py").read_text() == "def helper(): pass"

    def test_writes_origin_json(self, tmp_path):
        target = tmp_path / "my-flow"
        origin = {"registry": "https://stepwise.run", "slug": "my-flow", "version": 1}
        unpack_bundle(target, SIMPLE_FLOW, origin=origin)

        origin_path = target / ".origin.json"
        assert origin_path.exists()
        data = json.loads(origin_path.read_text())
        assert data["slug"] == "my-flow"

    def test_works_without_files(self, tmp_path):
        target = tmp_path / "my-flow"
        flow_path = unpack_bundle(target, SIMPLE_FLOW)

        assert flow_path.exists()
        assert flow_path.read_text() == SIMPLE_FLOW
        # No extra files besides FLOW.yaml
        all_files = list(target.rglob("*"))
        assert len([f for f in all_files if f.is_file()]) == 1

    def test_creates_target_dir_if_missing(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "my-flow"
        assert not target.exists()

        flow_path = unpack_bundle(target, SIMPLE_FLOW)

        assert flow_path.exists()
        assert target.is_dir()


# ── CLI integration tests ────────────────────────────────────────────


class TestShareWithBundle:
    def test_share_directory_flow_bundles_files(self, tmp_path, capsys, monkeypatch):
        """stepwise share with a directory flow collects and bundles files."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        # Create a directory flow
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / "helper.py").write_text("x = 1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "slug": "my-flow",
            "name": "my-flow",
            "update_token": "stw_tok_abc",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)
        # Auto-confirm the bundle prompt
        monkeypatch.setattr("stepwise.cli._prompt", lambda msg: "y")

        rc = main(["share", str(flow_dir)])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Bundling 1 co-located file(s)" in out
        assert "helper.py" in out

        # Verify the files were passed to the registry
        call_args = mock_client.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else None
        # The payload is passed as keyword arg json=...
        assert payload is not None
        assert "files" in payload
        assert "helper.py" in payload["files"]

    def test_share_single_file_no_bundle(self, tmp_path, capsys, monkeypatch):
        """stepwise share with a single-file flow does not attempt bundling."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        flow = tmp_path / "test.flow.yaml"
        flow.write_text(SIMPLE_FLOW)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "slug": "my-flow",
            "name": "my-flow",
            "update_token": "stw_tok_abc",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        rc = main(["share", str(flow)])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Bundling" not in out

    def test_share_directory_flow_no_extra_files_skips_prompt(self, tmp_path, capsys, monkeypatch):
        """Directory flow with only FLOW.yaml doesn't prompt for bundle confirmation."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "slug": "my-flow",
            "name": "my-flow",
            "update_token": "stw_tok_abc",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        # _prompt should NOT be called — no bundle files means no confirmation
        prompt_called = []
        monkeypatch.setattr("stepwise.cli._prompt", lambda msg: prompt_called.append(msg) or "y")

        rc = main(["share", str(flow_dir)])
        assert rc == EXIT_SUCCESS
        assert len(prompt_called) == 0

    def test_share_bundle_cancelled(self, tmp_path, capsys, monkeypatch):
        """User declining bundle confirmation cancels the share."""
        monkeypatch.chdir(tmp_path)

        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flow_dir / "helper.py").write_text("x = 1")

        monkeypatch.setattr("stepwise.cli._prompt", lambda msg: "n")

        rc = main(["share", str(flow_dir)])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Cancelled" in out


class TestGetWithBundle:
    def test_get_with_files_creates_directory(self, tmp_path, capsys, monkeypatch):
        """get with bundled files creates a directory structure."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: {
                "name": "my-flow",
                "slug": "my-flow",
                "author": "alice",
                "yaml": SIMPLE_FLOW,
                "steps": 1,
                "downloads": 42,
                "version": 2,
                "files": {"helper.py": "x = 1", "lib/utils.py": "def f(): pass"},
            },
        )

        rc = main(["get", "my-flow"])
        assert rc == EXIT_SUCCESS

        # Verify directory structure
        target_dir = tmp_path / "flows" / "my-flow"
        assert (target_dir / "FLOW.yaml").exists()
        assert (target_dir / "FLOW.yaml").read_text() == SIMPLE_FLOW
        assert (target_dir / "helper.py").read_text() == "x = 1"
        assert (target_dir / "lib" / "utils.py").read_text() == "def f(): pass"

        # Verify .origin.json
        origin = json.loads((target_dir / ".origin.json").read_text())
        assert origin["slug"] == "my-flow"
        assert origin["author"] == "alice"
        assert "content_hash" in origin

        out = capsys.readouterr().out
        assert "Downloaded" in out
        assert "2 file(s)" in out

    def test_get_without_files_creates_directory(self, tmp_path, capsys, monkeypatch):
        """get without bundled files still creates a directory structure."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: {
                "name": "my-flow",
                "slug": "my-flow",
                "author": "alice",
                "yaml": SIMPLE_FLOW,
                "steps": 1,
                "downloads": 10,
            },
        )

        rc = main(["get", "my-flow"])
        assert rc == EXIT_SUCCESS

        target_dir = tmp_path / "flows" / "my-flow"
        assert (target_dir / "FLOW.yaml").exists()
        assert (target_dir / ".origin.json").exists()

    def test_get_existing_dir_errors_without_force(self, tmp_path, capsys, monkeypatch):
        """get with existing target dir errors without --force."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: {
                "name": "my-flow",
                "slug": "my-flow",
                "author": "alice",
                "yaml": SIMPLE_FLOW,
                "steps": 1,
                "downloads": 10,
            },
        )

        # Pre-create target dir
        (tmp_path / "flows" / "my-flow").mkdir(parents=True)

        rc = main(["get", "my-flow"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_get_with_force_overwrites(self, tmp_path, capsys, monkeypatch):
        """get with --force overwrites existing directory."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: {
                "name": "my-flow",
                "slug": "my-flow",
                "author": "alice",
                "yaml": SIMPLE_FLOW,
                "steps": 1,
                "downloads": 10,
                "files": {"new.py": "y = 2"},
            },
        )

        # Pre-create target dir with old content
        target = tmp_path / "flows" / "my-flow"
        target.mkdir(parents=True)
        (target / "FLOW.yaml").write_text("old content")

        rc = main(["get", "--force", "my-flow"])
        assert rc == EXIT_SUCCESS

        # Should have new content
        assert (target / "FLOW.yaml").read_text() == SIMPLE_FLOW
        assert (target / "new.py").read_text() == "y = 2"

    def test_get_at_author_ref(self, tmp_path, capsys, monkeypatch):
        """get @author:slug format works with bundles."""
        monkeypatch.chdir(tmp_path)
        fetched_slugs = []

        def mock_fetch(slug, **kw):
            fetched_slugs.append(slug)
            return {
                "name": "Cool Flow",
                "slug": slug,
                "author": "alice",
                "yaml": SIMPLE_FLOW,
                "steps": 1,
                "downloads": 5,
            }

        monkeypatch.setattr("stepwise.registry_client.fetch_flow", mock_fetch)

        rc = main(["get", "@alice:cool-flow"])
        assert rc == EXIT_SUCCESS
        assert fetched_slugs == ["cool-flow"]
        assert (tmp_path / "flows" / "cool-flow" / "FLOW.yaml").exists()

    def test_get_url_still_works(self, tmp_path, capsys, monkeypatch):
        """URL-based downloads still use the old path (not bundle)."""
        monkeypatch.chdir(tmp_path)

        def mock_retrieve(url, filename):
            Path(filename).write_text(SIMPLE_FLOW)

        from unittest.mock import patch
        with patch("urllib.request.urlretrieve", side_effect=mock_retrieve):
            rc = main(["get", "https://example.com/test.flow.yaml"])

        assert rc == EXIT_SUCCESS
        # URL downloads go to cwd as single files (unchanged behavior)
        assert (tmp_path / "test.flow.yaml").exists()
