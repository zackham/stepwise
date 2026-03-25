"""Tests for multi-job wait (--all, --any) and sentinel files."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from stepwise.api_client import StepwiseClient, StepwiseAPIError
from stepwise.runner import (
    EXIT_CANCELLED,
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    EXIT_SUSPENDED,
    _aggregate_exit_code,
    _build_multi_result,
    _write_sentinel,
)


# ---------------------------------------------------------------------------
# _aggregate_exit_code tests
# ---------------------------------------------------------------------------

class TestAggregateExitCode:
    def test_all_completed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "completed"}
        ]) == EXIT_SUCCESS

    def test_one_failed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "failed"}
        ]) == EXIT_JOB_FAILED

    def test_one_cancelled(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "cancelled"}
        ]) == EXIT_CANCELLED

    def test_one_suspended(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}
        ]) == EXIT_SUSPENDED

    def test_failed_beats_suspended(self):
        """Failed has highest priority over suspended and cancelled."""
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}, {"status": "failed"}
        ]) == EXIT_JOB_FAILED

    def test_cancelled_beats_suspended(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}, {"status": "cancelled"}
        ]) == EXIT_CANCELLED

    def test_error_counts_as_failed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "error"}
        ]) == EXIT_JOB_FAILED


# ---------------------------------------------------------------------------
# _build_multi_result tests
# ---------------------------------------------------------------------------

class TestBuildMultiResult:
    def test_all_mode_completed(self):
        results = [{"job_id": "j1", "status": "completed"},
                   {"job_id": "j2", "status": "completed"}]
        out = _build_multi_result("all", results, 10.0)
        assert out["mode"] == "all"
        assert out["status"] == "completed"
        assert out["summary"]["total"] == 2
        assert out["summary"]["completed"] == 2
        assert out["duration_seconds"] == 10.0

    def test_any_mode_single_result(self):
        results = [{"job_id": "j1", "status": "failed"}]
        out = _build_multi_result("any", results, 5.0)
        assert out["mode"] == "any"
        assert out["status"] == "failed"
        assert len(out["jobs"]) == 1

    def test_mixed_statuses(self):
        results = [
            {"job_id": "j1", "status": "completed"},
            {"job_id": "j2", "status": "failed"},
            {"job_id": "j3", "status": "suspended"},
        ]
        out = _build_multi_result("all", results, 15.5)
        assert out["status"] == "failed"  # worst wins
        assert out["summary"]["completed"] == 1
        assert out["summary"]["failed"] == 1
        assert out["summary"]["suspended"] == 1
        assert out["summary"]["total"] == 3


# ---------------------------------------------------------------------------
# _write_sentinel tests
# ---------------------------------------------------------------------------

class TestWriteSentinel:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed")
            sentinel = project_dir / "completed" / "job-abc.json"
            assert sentinel.exists()
            data = json.loads(sentinel.read_text())
            assert data["job_id"] == "job-abc"
            assert data["status"] == "completed"
            assert "completed_at" in data

    def test_no_tmp_files_left(self):
        """Atomic write should not leave .tmp files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed")
            completed_dir = project_dir / "completed"
            tmp_files = list(completed_dir.glob("*.tmp"))
            assert tmp_files == []

    def test_noop_when_no_project_dir(self):
        """Should silently do nothing when project_dir is None."""
        _write_sentinel(None, "job-abc", "completed")  # should not raise

    def test_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed",
                           extra={"outputs": {"result": 42}})
            data = json.loads((project_dir / "completed" / "job-abc.json").read_text())
            assert data["outputs"] == {"result": 42}

    def test_multiple_sentinels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-1", "completed")
            _write_sentinel(project_dir, "job-2", "failed")
            assert (project_dir / "completed" / "job-1.json").exists()
            assert (project_dir / "completed" / "job-2.json").exists()
            data2 = json.loads((project_dir / "completed" / "job-2.json").read_text())
            assert data2["status"] == "failed"


# ---------------------------------------------------------------------------
# StepwiseClient.wait_many() tests
# ---------------------------------------------------------------------------

class TestClientWaitMany:
    def test_all_mode_both_complete(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            return {"status": "completed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["mode"] == "all"
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 2
        assert result["summary"]["total"] == 2

    def test_any_mode_first_completes(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "running", "steps": [{"status": "running"}]}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="any")

        assert result["status"] == "completed"
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["job_id"] == "j1"

    def test_all_mode_one_failed(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "failed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "failed"
        assert result["summary"]["failed"] == 1
        assert result["summary"]["completed"] == 1

    def test_retry_per_job_404(self):
        """One job 404s twice then succeeds, other succeeds immediately."""
        client = StepwiseClient("http://localhost:8340")
        j1_calls = 0

        def mock_status(job_id):
            nonlocal j1_calls
            if job_id == "j1":
                j1_calls += 1
                if j1_calls <= 2:
                    raise StepwiseAPIError(404, "Not found")
                return {"status": "completed", "steps": []}
            return {"status": "completed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 2

    def test_all_mode_suspended(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "running",
                    "steps": [{"status": "suspended"}]}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "suspended"
        assert result["summary"]["suspended"] == 1

    def test_any_mode_cancelled(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "cancelled", "steps": []}
            return {"status": "running", "steps": [{"status": "running"}]}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="any")

        assert result["status"] == "cancelled"
        assert len(result["jobs"]) == 1

    def test_404_exhaustion_marks_error(self):
        """A job that 404s past max_retries is marked as error."""
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                raise StepwiseAPIError(404, "Not found")
            return {"status": "completed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "failed"  # error counts as failed
        assert result["summary"]["error"] == 1
        assert result["summary"]["completed"] == 1


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------

class TestWaitArgParsing:
    def test_single_job_parses(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "job-abc"])
        assert args.job_ids == ["job-abc"]
        assert getattr(args, "wait_mode", None) is None

    def test_multiple_jobs_with_all(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "--all", "j1", "j2", "j3"])
        assert args.job_ids == ["j1", "j2", "j3"]
        assert args.wait_mode == "all"

    def test_multiple_jobs_with_any(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "--any", "j1", "j2"])
        assert args.job_ids == ["j1", "j2"]
        assert args.wait_mode == "any"

    def test_all_and_any_mutually_exclusive(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["wait", "--all", "--any", "j1"])

    def test_single_job_with_all_flag(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "--all", "j1"])
        assert args.job_ids == ["j1"]
        assert args.wait_mode == "all"
