"""Tests for stepwise uninstall command."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from stepwise.cli import (
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    _stop_server_for_project,
    main,
)
from stepwise.models import Job, JobStatus, WorkflowDefinition
from stepwise.project import DOT_DIR_NAME, init_project
from stepwise.store import SQLiteStore


def _create_running_job(db_path: Path) -> None:
    """Insert a RUNNING job into the given database."""
    store = SQLiteStore(str(db_path))
    job = Job(
        id="test-running-1",
        objective="test",
        workflow=WorkflowDefinition(steps={}),
        status=JobStatus.RUNNING,
    )
    store.save_job(job)


class TestUninstallRemovesDotDir:
    def test_uninstall_removes_dot_dir(self, tmp_path, capsys):
        init_project(tmp_path)
        assert (tmp_path / DOT_DIR_NAME).is_dir()

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes"])
        assert rc == EXIT_SUCCESS
        assert not (tmp_path / DOT_DIR_NAME).exists()

    def test_uninstall_no_project_exits_cleanly(self, tmp_path, capsys):
        rc = main(["--project-dir", str(tmp_path), "uninstall"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "No .stepwise/ project found" in combined

    def test_uninstall_aborts_on_running_jobs(self, tmp_path, capsys):
        project = init_project(tmp_path)
        _create_running_job(project.db_path)

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes"])
        assert rc == EXIT_USAGE_ERROR
        assert (tmp_path / DOT_DIR_NAME).is_dir()

    def test_uninstall_force_overrides_running_jobs(self, tmp_path, capsys):
        project = init_project(tmp_path)
        _create_running_job(project.db_path)

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes", "--force"])
        assert rc == EXIT_SUCCESS
        assert not (tmp_path / DOT_DIR_NAME).exists()

    def test_uninstall_cleans_gitignore(self, tmp_path, capsys):
        init_project(tmp_path)
        # Add extra lines to .gitignore
        gitignore = tmp_path / ".gitignore"
        content = gitignore.read_text()
        gitignore.write_text(content + "node_modules/\n*.pyc\n")

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes"])
        assert rc == EXIT_SUCCESS

        result = gitignore.read_text()
        assert ".stepwise/" not in result
        assert "config.local.yaml" not in result
        assert "*.config.local.yaml" not in result
        assert "node_modules/" in result
        assert "*.pyc" in result

    def test_uninstall_gitignore_missing_no_error(self, tmp_path, capsys):
        """No error if .gitignore doesn't exist."""
        init_project(tmp_path)
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            gitignore.unlink()

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes"])
        assert rc == EXIT_SUCCESS
        assert not (tmp_path / DOT_DIR_NAME).exists()


class TestUninstallOptionalRemovals:
    def test_uninstall_removes_flows_when_flagged(self, tmp_path, capsys):
        init_project(tmp_path)
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        (flows_dir / "test.flow.yaml").write_text("name: test\n")

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes", "--remove-flows"])
        assert rc == EXIT_SUCCESS
        assert not flows_dir.exists()

    def test_uninstall_keeps_flows_by_default(self, tmp_path, capsys):
        init_project(tmp_path)
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        (flows_dir / "test.flow.yaml").write_text("name: test\n")

        rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes"])
        assert rc == EXIT_SUCCESS
        assert flows_dir.is_dir()

    def test_uninstall_cli_flag_calls_subprocess(self, tmp_path, capsys):
        init_project(tmp_path)

        with patch("stepwise.cli._detect_install_method", return_value="uv"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = main(["--project-dir", str(tmp_path), "uninstall", "--yes", "--cli"])

        assert rc == EXIT_SUCCESS
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["uv", "tool", "uninstall", "stepwise-run"]


class TestStopServerForProject:
    def test_no_server_returns_false(self, tmp_path):
        from stepwise.io import create_adapter
        io = create_adapter(quiet=True)

        dot_dir = tmp_path / DOT_DIR_NAME
        dot_dir.mkdir()

        with patch("stepwise.server_detect.read_pidfile", return_value={}):
            result = _stop_server_for_project(dot_dir, io)
        assert result is False

    def test_sends_sigterm(self, tmp_path):
        from stepwise.io import create_adapter
        io = create_adapter(quiet=True)

        dot_dir = tmp_path / DOT_DIR_NAME
        dot_dir.mkdir()

        with patch("stepwise.server_detect.read_pidfile", return_value={"pid": 12345}), \
             patch("stepwise.server_detect._pid_alive", side_effect=[True, False]), \
             patch("stepwise.server_detect.remove_pidfile") as mock_remove, \
             patch("os.kill") as mock_kill:
            result = _stop_server_for_project(dot_dir, io)

        assert result is True
        mock_kill.assert_called_once()
        import signal
        mock_kill.assert_called_with(12345, signal.SIGTERM)
        mock_remove.assert_called_once_with(dot_dir)

    def test_stale_pidfile_cleaned_up(self, tmp_path):
        from stepwise.io import create_adapter
        io = create_adapter(quiet=True)

        dot_dir = tmp_path / DOT_DIR_NAME
        dot_dir.mkdir()

        with patch("stepwise.server_detect.read_pidfile", return_value={"pid": 99999}), \
             patch("stepwise.server_detect._pid_alive", return_value=False), \
             patch("stepwise.server_detect.remove_pidfile") as mock_remove:
            result = _stop_server_for_project(dot_dir, io)

        assert result is False
        mock_remove.assert_called_once_with(dot_dir)
