"""Tests for flow subcommands (get, share, search)."""

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
    """flow get <url> downloads YAML to cwd."""

    def test_get_url_downloads_yaml(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Mock urlretrieve
        def mock_retrieve(url, filename):
            Path(filename).write_text(SIMPLE_FLOW)

        with patch("urllib.request.urlretrieve", side_effect=mock_retrieve):
            rc = main(["flow", "get", "https://example.com/test.flow.yaml"])

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Downloaded" in out
        assert (tmp_path / "test.flow.yaml").exists()

    def test_get_non_yaml_url_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["flow", "get", "https://example.com/readme.txt"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "YAML" in err

    def test_get_existing_file_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "exists.flow.yaml").write_text("already here")
        rc = main(["flow", "get", "https://example.com/exists.flow.yaml"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_get_url_failed_download(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import urllib.error

        with patch("urllib.request.urlretrieve", side_effect=urllib.error.URLError("404")):
            rc = main(["flow", "get", "https://example.com/missing.flow.yaml"])

        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "Failed" in err

    def test_get_name_shows_coming_soon(self, tmp_path, capsys, monkeypatch):
        """flow get <name> (not URL) shows registry stub."""
        monkeypatch.chdir(tmp_path)
        rc = main(["flow", "get", "pr-review"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "coming soon" in out.lower()

    def test_get_yml_extension_accepted(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def mock_retrieve(url, filename):
            Path(filename).write_text(SIMPLE_FLOW)

        with patch("urllib.request.urlretrieve", side_effect=mock_retrieve):
            rc = main(["flow", "get", "https://example.com/test.yml"])

        assert rc == EXIT_SUCCESS
        assert (tmp_path / "test.yml").exists()


class TestFlowShare:
    """flow share prints coming soon stub."""

    def test_share_no_args(self, capsys):
        rc = main(["flow", "share"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "coming soon" in out.lower()

    def test_share_with_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        rc = main(["flow", "share", str(flow)])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "coming soon" in out.lower()

    def test_share_missing_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["flow", "share", "nonexistent.flow.yaml"])
        assert rc == EXIT_USAGE_ERROR


class TestFlowSearch:
    """flow search prints coming soon stub."""

    def test_search_no_args(self, capsys):
        rc = main(["flow", "search"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "coming soon" in out.lower()

    def test_search_with_query(self, capsys):
        rc = main(["flow", "search", "pr", "review"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "coming soon" in out.lower()
        assert "pr review" in out.lower()
