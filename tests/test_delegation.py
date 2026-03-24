"""Tests for WebSocket delegation and --wait/--async server delegation."""

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ExternalExecutor
from stepwise.models import (
    ExecutorRef,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.io import PlainAdapter, create_adapter
from stepwise.runner import (
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    EXIT_SUSPENDED,
    _is_blocked_by_suspension_from_runs,
    _ws_url_from_server,
    _build_tree_from_dicts,
    _fetch_job_state,
)
from stepwise.server import app
from stepwise.store import SQLiteStore

from tests.conftest import CallableExecutor, register_step_fn


# ── Helpers ──────────────────────────────────────────────────────────


def _simple_wf():
    return WorkflowDefinition(steps={
        "a": StepDefinition(
            name="a",
            outputs=["result"],
            executor=ExecutorRef("callable", {"fn_name": "identity"}),
        ),
    })


def _mock_response(json_data=None, status_code=200):
    """Create a mock httpx response with sync .json() and .raise_for_status()."""
    resp = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    resp.status_code = status_code
    return resp


def _mock_async_client(url_responses: dict):
    """Create a mock httpx client where .get/.post return awaitable responses."""
    async def _get(url):
        return url_responses[url]

    async def _post(url, **kwargs):
        return url_responses.get(url, _mock_response(json_data={"status": "ok"}))

    client = MagicMock()
    client.get = _get
    client.post = _post
    return client


def _make_project(tmp_path):
    from stepwise.project import StepwiseProject
    dot_dir = tmp_path / ".stepwise"
    dot_dir.mkdir(exist_ok=True)
    jobs_dir = dot_dir / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    templates_dir = dot_dir / "templates"
    templates_dir.mkdir(exist_ok=True)
    return StepwiseProject(
        root=tmp_path,
        dot_dir=dot_dir,
        db_path=dot_dir / "stepwise.db",
        jobs_dir=jobs_dir,
        templates_dir=templates_dir,
    )


# ── Unit tests ───────────────────────────────────────────────────────


class TestWsUrlConversion:
    def test_http_to_ws(self):
        assert _ws_url_from_server("http://localhost:8340") == "ws://localhost:8340/ws"

    def test_https_to_wss(self):
        assert _ws_url_from_server("https://example.com:8340") == "wss://example.com:8340/ws"

    def test_trailing_slash(self):
        assert _ws_url_from_server("http://localhost:8340/") == "ws://localhost:8340/ws"

    def test_trailing_slashes(self):
        assert _ws_url_from_server("http://localhost:8340///") == "ws://localhost:8340/ws"


class TestBlockedBySuspensionFromRuns:
    def test_suspended_no_active(self):
        runs = [{"status": "suspended"}, {"status": "completed"}]
        assert _is_blocked_by_suspension_from_runs(runs) is True

    def test_suspended_with_active(self):
        runs = [{"status": "suspended"}, {"status": "running"}]
        assert _is_blocked_by_suspension_from_runs(runs) is False

    def test_no_suspended(self):
        runs = [{"status": "running"}, {"status": "completed"}]
        assert _is_blocked_by_suspension_from_runs(runs) is False

    def test_empty(self):
        assert _is_blocked_by_suspension_from_runs([]) is False

    def test_delegated_counts_as_active(self):
        runs = [{"status": "suspended"}, {"status": "delegated"}]
        assert _is_blocked_by_suspension_from_runs(runs) is False


class TestBuildTreeFromDicts:
    def test_builds_completed_node(self):
        runs = [
            {"id": "r1", "status": "completed", "step_name": "a",
             "started_at": "2026-01-01T00:00:00", "completed_at": "2026-01-01T00:00:01"},
        ]
        tree = _build_tree_from_dicts(runs)
        assert len(tree) == 1
        assert tree[0].name == "a"
        assert tree[0].status == "completed"
        assert tree[0].duration is not None

    def test_builds_failed_node(self):
        runs = [{"id": "r1", "status": "failed", "step_name": "a", "error": "boom"}]
        tree = _build_tree_from_dicts(runs)
        assert len(tree) == 1
        assert tree[0].status == "failed"
        assert tree[0].error == "boom"

    def test_builds_multiple_nodes(self):
        runs = [
            {"id": "r1", "status": "completed", "step_name": "a",
             "started_at": "2026-01-01T00:00:00", "completed_at": "2026-01-01T00:00:01"},
            {"id": "r2", "status": "running", "step_name": "b"},
        ]
        tree = _build_tree_from_dicts(runs)
        assert len(tree) == 2
        assert tree[0].status == "completed"
        assert tree[1].status == "running"


class TestFetchJobState:
    def test_fetches_job_and_runs(self):
        async def _run():
            client = _mock_async_client({
                "/api/jobs/j1": _mock_response(json_data={"id": "j1", "status": "running"}),
                "/api/jobs/j1/runs": _mock_response(json_data=[{"id": "r1", "status": "running"}]),
            })
            job_data, runs = await _fetch_job_state(client, "j1")
            assert job_data["id"] == "j1"
            assert len(runs) == 1
        asyncio.run(_run())


# ── Server endpoint tests ────────────────────────────────────────────
# Use file-based SQLite because TestClient runs in a different thread.


class TestJobCostEndpoint:
    def test_returns_cost(self):
        import stepwise.server as srv

        register_step_fn("identity", lambda inputs: {"result": "ok"})
        with TestClient(app, raise_server_exceptions=False) as c:
            # After lifespan, override engine with our own
            engine = srv._engine
            job = engine.create_job("test", _simple_wf())
            engine.start_job(job.id)
            # Tick via the engine (lifespan engine is async, but get_job still works)
            # Just fetch — the engine task is running in the background
            import time
            for _ in range(50):
                j = engine.get_job(job.id)
                if j.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.05)

            resp = c.get(f"/api/jobs/{job.id}/cost")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == job.id
            assert "cost_usd" in data

    def test_not_found(self):
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/jobs/nonexistent/cost")
            assert resp.status_code == 404


class TestJobSuspendedEndpoint:
    def test_returns_suspended_details(self):
        import stepwise.server as srv

        with TestClient(app, raise_server_exceptions=False) as c:
            engine = srv._engine
            wf = WorkflowDefinition(steps={
                "ask": StepDefinition(
                    name="ask",
                    outputs=["response"],
                    executor=ExecutorRef("external", {"prompt": "Enter value"}),
                ),
            })
            job = engine.create_job("test", wf)
            engine.start_job(job.id)
            import time
            suspended = False
            for _ in range(100):
                runs = engine.get_runs(job.id)
                if any(r.status == StepRunStatus.SUSPENDED for r in runs):
                    suspended = True
                    break
                time.sleep(0.1)
            assert suspended, "Step never reached SUSPENDED status"

            resp = c.get(f"/api/jobs/{job.id}/suspended")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == job.id
            assert len(data["suspended_steps"]) == 1
            assert data["suspended_steps"][0]["step"] == "ask"
            assert data["suspended_steps"][0]["prompt"] == "Enter value"

    def test_no_suspended(self):
        import stepwise.server as srv

        register_step_fn("identity", lambda inputs: {"result": "ok"})
        with TestClient(app, raise_server_exceptions=False) as c:
            engine = srv._engine
            job = engine.create_job("test", _simple_wf())
            engine.start_job(job.id)
            import time
            for _ in range(50):
                j = engine.get_job(job.id)
                if j.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.05)

            resp = c.get(f"/api/jobs/{job.id}/suspended")
            assert resp.status_code == 200
            data = resp.json()
            assert data["suspended_steps"] == []


# ── --async delegation tests ─────────────────────────────────────────


class TestDelegatedAsync:
    def test_creates_and_starts_job(self, capsys):
        from stepwise.runner import _delegated_run_async

        wf = _simple_wf()
        mock_responses = [
            _mock_response(json_data={"id": "job-123"}),
            _mock_response(json_data={"status": "started"}),
        ]

        with patch("stepwise.runner.httpx.post", side_effect=mock_responses):
            result = _delegated_run_async(
                "http://localhost:8340", wf, "test-obj", {"x": 1}, None,
            )

        assert result == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out.strip())
        assert out["job_id"] == "job-123"
        assert out["status"] == "running"

    def test_server_unreachable(self, capsys):
        from stepwise.runner import _delegated_run_async

        wf = _simple_wf()
        with patch("stepwise.runner.httpx.post", side_effect=Exception("Connection refused")):
            result = _delegated_run_async(
                "http://localhost:9999", wf, "test-obj", None, None,
            )

        assert result == EXIT_JOB_FAILED
        out = json.loads(capsys.readouterr().out.strip())
        assert out["status"] == "error"
        assert "Connection refused" in out["error"]


# ── --local flag tests ───────────────────────────────────────────────


class TestLocalFlagSkipsDelegation:
    def test_wait_skips_delegation(self, tmp_path):
        from stepwise.runner import run_wait

        flow = tmp_path / "test.flow.yaml"
        flow.write_text("name: test\nsteps:\n  a:\n    run: echo '{\"result\": 1}'\n    outputs: [result]\n")
        project = _make_project(tmp_path)

        with patch("stepwise.server_detect.detect_server") as mock_detect:
            mock_detect.return_value = "http://localhost:8340"
            result = run_wait(
                flow_path=flow,
                project=project,
                force_local=True,
            )
            mock_detect.assert_not_called()

    def test_async_skips_delegation(self, tmp_path):
        from stepwise.runner import run_async

        flow = tmp_path / "test.flow.yaml"
        flow.write_text("name: test\nsteps:\n  a:\n    run: echo '{\"result\": 1}'\n    outputs: [result]\n")
        project = _make_project(tmp_path)

        with patch("stepwise.server_detect.detect_server") as mock_detect:
            mock_detect.return_value = "http://localhost:8340"
            with patch("subprocess.Popen"):
                result = run_async(
                    flow_path=flow,
                    project=project,
                    force_local=True,
                )
            mock_detect.assert_not_called()


# ── Delegated --wait/WS loop tests ──────────────────────────────────
# These test the core async loop logic without asyncio.run() (which
# installs signal handlers and hangs in pytest). Instead, test the
# building-block helpers directly and the delegation entry points.


class TestDelegatedRunWait:
    def test_completed_via_delegation(self, capsys):
        """_delegated_run_wait creates job, starts it, and returns result."""
        from stepwise.runner import _delegated_run_wait

        # Mock _delegated_create_and_start to return a job_id
        # Mock _delegated_wait_ws_loop to return EXIT_SUCCESS with JSON
        with patch("stepwise.runner._delegated_create_and_start") as mock_create, \
             patch("stepwise.runner.asyncio.run") as mock_run:
            mock_create.return_value = ("j1", None)
            mock_run.return_value = EXIT_SUCCESS
            result = _delegated_run_wait(
                "http://localhost:8340", _simple_wf(), "test", None, None, None,
            )

        assert result == EXIT_SUCCESS
        mock_create.assert_called_once()

    def test_create_failure(self, capsys):
        """_delegated_run_wait handles creation failure."""
        from stepwise.runner import _delegated_run_wait

        with patch("stepwise.runner._delegated_create_and_start") as mock_create:
            mock_create.return_value = (None, "Connection refused")
            result = _delegated_run_wait(
                "http://localhost:8340", _simple_wf(), "test", None, None, None,
            )

        assert result == EXIT_JOB_FAILED
        out = json.loads(capsys.readouterr().out.strip())
        assert "Connection refused" in out["error"]


class TestDelegatedCreateAndStart:
    def test_success(self):
        from stepwise.runner import _delegated_create_and_start

        responses = [
            _mock_response(json_data={"id": "j1"}),
            _mock_response(json_data={"status": "started"}),
        ]
        with patch("stepwise.runner.httpx.post", side_effect=responses):
            job_id, err = _delegated_create_and_start(
                "http://localhost:8340", _simple_wf(), "test", None, None,
            )
        assert job_id == "j1"
        assert err is None

    def test_create_fails(self):
        from stepwise.runner import _delegated_create_and_start

        with patch("stepwise.runner.httpx.post", side_effect=Exception("boom")):
            job_id, err = _delegated_create_and_start(
                "http://localhost:8340", _simple_wf(), "test", None, None,
            )
        assert job_id is None
        assert "boom" in err

    def test_start_fails(self):
        from stepwise.runner import _delegated_create_and_start

        responses = [
            _mock_response(json_data={"id": "j1"}),
            MagicMock(raise_for_status=MagicMock(side_effect=Exception("500"))),
        ]
        with patch("stepwise.runner.httpx.post", side_effect=responses):
            job_id, err = _delegated_create_and_start(
                "http://localhost:8340", _simple_wf(), "test", None, None,
            )
        assert job_id is None
        assert "500" in err


class TestDelegatedWsLoopHelpers:
    """Test the WS loop building blocks via direct asyncio calls (no signal handlers)."""

    def test_fetch_job_state(self):
        async def _run():
            client = _mock_async_client({
                "/api/jobs/j1": _mock_response(json_data={"id": "j1", "status": "completed"}),
                "/api/jobs/j1/runs": _mock_response(json_data=[{"id": "r1", "status": "completed"}]),
            })
            job_data, runs = await _fetch_job_state(client, "j1")
            assert job_data["status"] == "completed"
            assert len(runs) == 1
        asyncio.run(_run())

    def test_fetch_job_state_retries_on_404(self):
        """_fetch_job_state retries up to 3 times when server returns 404."""
        call_count = 0

        async def _get(url):
            nonlocal call_count
            if url == "/api/jobs/j1":
                call_count += 1
                if call_count < 3:
                    resp = _mock_response(status_code=404)
                    resp.raise_for_status = MagicMock(
                        side_effect=httpx.HTTPStatusError(
                            "Not Found", request=MagicMock(), response=resp
                        )
                    )
                    return resp
                return _mock_response(json_data={"id": "j1", "status": "running"})
            if url == "/api/jobs/j1/runs":
                return _mock_response(json_data=[])
            raise ValueError(f"unexpected url: {url}")

        client = MagicMock()
        client.get = _get

        async def _run():
            job_data, runs = await _fetch_job_state(client, "j1")
            assert job_data["id"] == "j1"
            assert call_count == 3

        with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            asyncio.run(_run())
            assert mock_sleep.await_count == 2
            mock_sleep.assert_awaited_with(1)

    def test_fetch_job_state_raises_after_retries_exhausted(self):
        """_fetch_job_state raises after 3 failed 404 attempts."""
        call_count = 0

        async def _get(url):
            nonlocal call_count
            if url == "/api/jobs/j1":
                call_count += 1
            resp = _mock_response(status_code=404)
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Not Found", request=MagicMock(), response=resp
                )
            )
            return resp

        client = MagicMock()
        client.get = _get

        async def _run():
            with pytest.raises(httpx.HTTPStatusError):
                await _fetch_job_state(client, "j1")

        with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            asyncio.run(_run())
            assert call_count == 3
            assert mock_sleep.await_count == 2

    def test_suspension_detection(self):
        """_is_blocked_by_suspension_from_runs detects when job is blocked."""
        runs_blocked = [{"status": "suspended"}, {"status": "completed"}]
        runs_active = [{"status": "suspended"}, {"status": "running"}]
        assert _is_blocked_by_suspension_from_runs(runs_blocked) is True
        assert _is_blocked_by_suspension_from_runs(runs_active) is False
