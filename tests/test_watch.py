"""Tests for --watch mode."""

import json
import pytest
from unittest.mock import patch, MagicMock, ANY
from pathlib import Path

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main, _find_free_port, _submit_watch_job
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
    """--watch starts server and submits job via API."""

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

    def test_watch_submits_job_via_api(self, tmp_path, capsys, monkeypatch):
        """--watch submits the job via API in a background thread."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        # Capture the background thread function
        threads_started = []
        original_thread_init = __import__("threading").Thread.__init__

        with patch("uvicorn.run"), \
             patch("stepwise.cli._submit_job_when_ready") as mock_submit:
            main(["run", str(flow), "--watch", "--no-open"])

        mock_submit.assert_called_once()
        # Verify workflow data was passed
        call_args = mock_submit.call_args
        workflow_arg = call_args[0][3] if len(call_args[0]) > 3 else call_args.kwargs.get("workflow")
        assert workflow_arg is not None

    def test_watch_no_open_suppresses_browser(self, tmp_path, capsys, monkeypatch):
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        with patch("uvicorn.run"), \
             patch("stepwise.cli._submit_job_when_ready"), \
             patch("stepwise.cli._open_browser") as mock_browser:
            main(["run", str(flow), "--watch", "--no-open"])

        mock_browser.assert_not_called()

    def test_watch_does_not_use_stdin_handler(self, tmp_path, capsys, monkeypatch):
        """--watch mode uses web UI for external steps, not stdin."""
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

    def test_watch_reuses_existing_server(self, tmp_path, capsys, monkeypatch):
        """If a server is already running, submit job there instead of starting a new one."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        # Simulate an existing server via detect_server
        with patch("stepwise.server_detect.detect_server", return_value="http://localhost:8340"), \
             patch("stepwise.cli._submit_watch_job", return_value=EXIT_SUCCESS) as mock_submit, \
             patch("uvicorn.run") as mock_uvicorn:
            rc = main(["run", str(flow), "--watch", "--no-open"])

        assert rc == EXIT_SUCCESS
        mock_submit.assert_called_once()
        mock_uvicorn.assert_not_called()  # should NOT start a new server

    def test_watch_writes_pidfile(self, tmp_path, capsys, monkeypatch):
        """--watch writes and cleans up a pidfile."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = self._write_flow(tmp_path)

        pidfile = tmp_path / ".stepwise" / "server.pid"

        def check_pidfile(*args, **kwargs):
            assert pidfile.exists()

        with patch("uvicorn.run", side_effect=check_pidfile):
            main(["run", str(flow), "--watch", "--no-open"])

        # Pidfile should be cleaned up after server stops
        assert not pidfile.exists()


class TestSubmitWatchJobProjectPath:
    """_submit_watch_job shows project path from server health endpoint."""

    def _make_mock_workflow(self):
        wf = MagicMock()
        wf.to_dict.return_value = {"steps": {}}
        return wf

    def _make_args(self):
        args = MagicMock()
        args.no_open = True
        return args

    def _mock_urlopen_responses(self, health_body=None, health_error=False):
        """Return a side_effect function for urlopen that handles create, start, and health."""
        call_count = [0]

        def side_effect(req_or_url, **kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None

            if call_count[0] == 1:
                # Create job response
                mock_resp.read.return_value = json.dumps({"id": "job-123"}).encode()
            elif call_count[0] == 2:
                # Start job response
                mock_resp.read.return_value = b"{}"
            elif call_count[0] == 3:
                # Health response
                if health_error:
                    raise ConnectionError("server down")
                body = health_body or {}
                mock_resp.read.return_value = json.dumps(body).encode()
            return mock_resp

        return side_effect

    def test_shows_project_path(self, capsys):
        home = str(Path.home())
        health = {"status": "ok", "project_path": f"{home}/work/my-project"}

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen_responses(health)):
            rc = _submit_watch_job(
                "http://localhost:8340",
                self._make_mock_workflow(),
                "test", {}, self._make_args(),
            )

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "~/work/my-project" in out
        assert "job submitted to running server (~/work/my-project)" in out

    def test_no_project_path_in_health(self, capsys):
        health = {"status": "ok"}

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen_responses(health)):
            rc = _submit_watch_job(
                "http://localhost:8340",
                self._make_mock_workflow(),
                "test", {}, self._make_args(),
            )

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "job submitted to running server" in out
        assert "(" not in out.split("server")[1].split("\n")[0]  # no parenthetical

    def test_health_request_fails_gracefully(self, capsys):
        with patch("urllib.request.urlopen",
                   side_effect=self._mock_urlopen_responses(health_error=True)):
            rc = _submit_watch_job(
                "http://localhost:8340",
                self._make_mock_workflow(),
                "test", {}, self._make_args(),
            )

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "job submitted to running server" in out
        assert "(" not in out.split("server")[1].split("\n")[0]

    def test_non_home_path_shown_as_is(self, capsys):
        health = {"status": "ok", "project_path": "/opt/projects/my-app"}

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen_responses(health)):
            rc = _submit_watch_job(
                "http://localhost:8340",
                self._make_mock_workflow(),
                "test", {}, self._make_args(),
            )

        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "(/opt/projects/my-app)" in out
