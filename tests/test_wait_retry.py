"""Tests for 404 retry/backoff in --wait and StepwiseClient.wait()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stepwise.runner import JobNotFoundError, _fetch_job_state
from stepwise.api_client import StepwiseClient, StepwiseAPIError


# ---------------------------------------------------------------------------
# _fetch_job_state tests
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data: dict | list | None = None):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


@pytest.mark.asyncio
async def test_fetch_job_state_retries_on_404():
    """404 on job endpoint retries with exponential backoff then succeeds."""
    job_ok = _mock_response(200, {"id": "j1", "status": "running"})
    runs_ok = _mock_response(200, [])
    not_found = _mock_response(404)

    client = AsyncMock()
    # First two calls: 404, third: 200 (job), fourth: 200 (runs)
    client.get = AsyncMock(side_effect=[not_found, not_found, job_ok, runs_ok])

    with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        job, runs = await _fetch_job_state(client, "j1")

    assert job == {"id": "j1", "status": "running"}
    assert runs == []
    # Two retries → sleep(0.5), sleep(1.0)
    assert mock_sleep.await_count == 2
    mock_sleep.assert_any_await(0.5)
    mock_sleep.assert_any_await(1.0)


@pytest.mark.asyncio
async def test_fetch_job_state_exhausts_retries():
    """All 404s → JobNotFoundError with diagnostic message."""
    not_found = _mock_response(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=not_found)

    with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(JobNotFoundError, match="not found after 8 retries"):
            await _fetch_job_state(client, "j1")


@pytest.mark.asyncio
async def test_fetch_job_state_no_retry_on_non_404():
    """Non-404 errors propagate immediately without retries."""
    error_resp = _mock_response(500)
    client = AsyncMock()
    client.get = AsyncMock(return_value=error_resp)

    with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(Exception, match="HTTP 500"):
            await _fetch_job_state(client, "j1")

    # No retries — sleep never called
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_job_state_runs_endpoint_retries():
    """Job endpoint succeeds, runs endpoint retries on 404 then succeeds."""
    job_ok = _mock_response(200, {"id": "j1", "status": "running"})
    runs_ok = _mock_response(200, [{"step": "a", "status": "completed"}])
    not_found = _mock_response(404)

    client = AsyncMock()
    # job: 200, runs: 404, 404, 200
    client.get = AsyncMock(side_effect=[job_ok, not_found, not_found, runs_ok])

    with patch("stepwise.runner.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        job, runs = await _fetch_job_state(client, "j1")

    assert runs == [{"step": "a", "status": "completed"}]
    assert mock_sleep.await_count == 2


# ---------------------------------------------------------------------------
# StepwiseClient.wait() tests
# ---------------------------------------------------------------------------

def test_client_wait_retries_on_404():
    """StepwiseClient.wait() retries 404s then succeeds."""
    client = StepwiseClient("http://localhost:8340")

    call_count = 0
    def mock_status(job_id):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise StepwiseAPIError(404, "Not found")
        return {"status": "completed", "steps": []}

    with patch.object(client, "status", side_effect=mock_status):
        with patch("time.sleep") as mock_sleep:
            result = client.wait("j1")

    assert result["status"] == "completed"
    assert mock_sleep.call_count == 2


def test_client_wait_exhausts_retries():
    """StepwiseClient.wait() re-raises after max 404 retries."""
    client = StepwiseClient("http://localhost:8340")

    def mock_status(job_id):
        raise StepwiseAPIError(404, "Not found")

    with patch.object(client, "status", side_effect=mock_status):
        with patch("time.sleep"):
            with pytest.raises(StepwiseAPIError) as exc_info:
                client.wait("j1")

    assert exc_info.value.status == 404


def test_client_wait_no_retry_on_non_404():
    """Non-404 API errors propagate immediately."""
    client = StepwiseClient("http://localhost:8340")

    def mock_status(job_id):
        raise StepwiseAPIError(500, "Internal error")

    with patch.object(client, "status", side_effect=mock_status):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(StepwiseAPIError) as exc_info:
                client.wait("j1")

    assert exc_info.value.status == 500
    mock_sleep.assert_not_called()
