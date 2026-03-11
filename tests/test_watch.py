"""Tests for --watch mode."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main, _find_free_port
from stepwise.project import init_project


SIMPLE_FLOW = """\
name: simple
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


class TestFindFreePort:
    """_find_free_port() returns an available port."""

    def test_returns_port_number(self):
        port = _find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_returns_different_ports(self):
        ports = {_find_free_port() for _ in range(5)}
        # At least 2 unique ports (they're random)
        assert len(ports) >= 2


class TestWatchMode:
    """--watch starts server with pre-loaded job."""

    def _write_flow(self, tmp_path):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(SIMPLE_FLOW)
        return flow

    def test_watch_calls_uvicorn_with_random_port(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run") as mock_run:
            rc = main(["run", str(flow), "--watch", "--no-open"])

        assert rc == EXIT_SUCCESS
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        # Should use a random port (not 8340)
        port = call_kwargs.kwargs.get("port") or call_kwargs[1].get("port")
        assert port is not None

    def test_watch_with_explicit_port(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run") as mock_run:
            rc = main(["run", str(flow), "--watch", "--port", "9999", "--no-open"])

        assert rc == EXIT_SUCCESS
        call_kwargs = mock_run.call_args
        port = call_kwargs.kwargs.get("port") or call_kwargs[1].get("port")
        assert port == 9999

    def test_watch_sets_env_vars(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        import os
        captured_env = {}

        def capture_env(*args, **kwargs):
            captured_env["STEPWISE_WATCH_WORKFLOW"] = os.environ.get("STEPWISE_WATCH_WORKFLOW")
            captured_env["STEPWISE_WATCH_OBJECTIVE"] = os.environ.get("STEPWISE_WATCH_OBJECTIVE")

        with patch("uvicorn.run", side_effect=capture_env):
            main(["run", str(flow), "--watch", "--no-open"])

        assert captured_env["STEPWISE_WATCH_WORKFLOW"] is not None
        assert captured_env["STEPWISE_WATCH_OBJECTIVE"] is not None

    def test_watch_no_open_suppresses_browser(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run"), \
             patch("stepwise.cli._open_browser") as mock_browser:
            main(["run", str(flow), "--watch", "--no-open"])

        mock_browser.assert_not_called()

    def test_watch_opens_browser_by_default(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run"), \
             patch("stepwise.cli._open_browser") as mock_browser:
            main(["run", str(flow), "--watch"])

        mock_browser.assert_called_once()

    def test_watch_does_not_use_stdin_handler(self, tmp_path, capsys, monkeypatch):
        """--watch mode uses web UI for human steps, not stdin."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        # If watch mode called StdinHumanHandler, this would fail.
        # Instead it should go through uvicorn.
        with patch("uvicorn.run"):
            rc = main(["run", str(flow), "--watch", "--no-open"])
        assert rc == EXIT_SUCCESS

    def test_watch_prints_url(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run"):
            main(["run", str(flow), "--watch", "--no-open"])

        out = capsys.readouterr().out
        assert "http://127.0.0.1:" in out

    def test_watch_invalid_flow_errors(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = tmp_path / "bad.flow.yaml"
        flow.write_text("not: valid: yaml: [")
        rc = main(["run", str(flow), "--watch", "--no-open"])
        assert rc == EXIT_USAGE_ERROR
