"""Tests for top-level flow commands (get, share, search)."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.project import init_project


SIMPLE_FLOW = """\
name: downloaded
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


class TestFlowGet:
    """get <url> downloads YAML to cwd."""

    def test_get_url_downloads_yaml(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Mock urlretrieve
        def mock_retrieve(url, filename):
            Path(filename).write_text(SIMPLE_FLOW)

        with patch("urllib.request.urlretrieve", side_effect=mock_retrieve):
            rc = main(["get", "https://example.com/test.flow.yaml"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Downloaded" in out
        assert (tmp_path / "test.flow.yaml").exists()

    def test_get_non_yaml_url_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["get", "https://example.com/readme.txt"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "YAML" in err

    def test_get_existing_file_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "exists.flow.yaml").write_text("already here")
        rc = main(["get", "https://example.com/exists.flow.yaml"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_get_url_failed_download(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import urllib.error

        with patch("urllib.request.urlretrieve", side_effect=urllib.error.URLError("404")):
            rc = main(["get", "https://example.com/missing.flow.yaml"])

        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "Failed" in err

    def test_get_name_not_found(self, tmp_path, capsys, monkeypatch):
        """get <name> (not URL) tries registry, fails if not found."""
        from stepwise.registry_client import RegistryError
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: (_ for _ in ()).throw(RegistryError("Flow 'pr-review' not found in registry", 404)),
        )
        rc = main(["get", "pr-review"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "not found" in err.lower()

    def test_get_name_from_registry(self, tmp_path, capsys, monkeypatch):
        """get <name> fetches from registry and saves to directory."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: {"name": "downloaded", "slug": "downloaded", "author": "alice",
                                "yaml": SIMPLE_FLOW, "steps": 1, "downloads": 42},
        )
        rc = main(["get", "downloaded"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "downloaded" in combined.lower()
        assert (tmp_path / ".stepwise" / "registry" / "@alice" / "downloaded" / "FLOW.yaml").exists()

    def test_get_yml_extension_accepted(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def mock_retrieve(url, filename):
            Path(filename).write_text(SIMPLE_FLOW)

        with patch("urllib.request.urlretrieve", side_effect=mock_retrieve):
            rc = main(["get", "https://example.com/test.yml"])

        assert rc == EXIT_SUCCESS
        assert (tmp_path / "test.yml").exists()


class TestFlowShare:
    """share publishes to registry."""

    def test_share_no_args(self, capsys):
        rc = main(["share"])
        assert rc == EXIT_USAGE_ERROR

    def test_share_with_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
        # Set up auth for publish
        auth_file = tmp_path / "auth.json"
        import json
        auth_file.write_text(json.dumps({"auth_token": "tok_test", "github_username": "test", "registry_url": "https://stepwise.run"}))
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)

        flow = tmp_path / "test.flow.yaml"
        flow.write_text(SIMPLE_FLOW)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "slug": "downloaded",
            "name": "downloaded",
            "update_token": "stw_tok_abc",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        rc = main(["share", str(flow)])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "downloaded" in combined.lower()

    def test_share_missing_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["share", "nonexistent.flow.yaml"])
        assert rc == EXIT_USAGE_ERROR


class TestFlowSearch:
    """search queries the registry."""

    def test_search_no_args(self, capsys, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"flows": [], "total": 0}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        rc = main(["search"])
        assert rc == EXIT_SUCCESS

    def test_search_with_query(self, capsys, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "flows": [{"slug": "pr-review", "author": "alice", "steps": 3, "downloads": 5, "tags": ["code"]}],
            "total": 1,
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        rc = main(["search", "pr", "review"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "pr-review" in combined.lower()
