"""HTTP client for Stepwise server API.

Used by CLI when a server is running, instead of direct SQLite access.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


class StepwiseAPIError(Exception):
    """Error from the Stepwise API."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"API error {status}: {detail}")


class StepwiseClient:
    """HTTP client wrapping Stepwise server endpoints."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        """Make an HTTP request and return parsed JSON response."""
        url = f"{self.base_url}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url = f"{url}?{qs}"

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail", str(e))
            except Exception:
                detail = str(e)
            raise StepwiseAPIError(e.code, detail)
        except urllib.error.URLError as e:
            raise StepwiseAPIError(0, f"Connection failed: {e.reason}")

    # ── Jobs ─────────────────────────────────────────────────────────

    def jobs(
        self,
        status: str | None = None,
        top_level: bool = True,
    ) -> list[dict]:
        """List jobs."""
        params = {}
        if status:
            params["status"] = status
        if top_level:
            params["top_level"] = "true"
        return self._request("GET", "/api/jobs", params=params)

    def create_job(
        self,
        objective: str,
        workflow: dict,
        inputs: dict | None = None,
        name: str | None = None,
        metadata: dict | None = None,
        status: str | None = None,
        job_group: str | None = None,
    ) -> dict:
        """Create and optionally start a job."""
        body: dict = {
            "objective": objective,
            "workflow": workflow,
            "inputs": inputs,
        }
        if name:
            body["name"] = name
        if metadata:
            body["metadata"] = metadata
        if status:
            body["status"] = status
        if job_group:
            body["job_group"] = job_group
        return self._request("POST", "/api/jobs", body)

    def status(self, job_id: str) -> dict:
        """Get resolved flow status for a job."""
        return self._request("GET", f"/api/jobs/{job_id}/status")

    def output(
        self,
        job_id: str,
        step: str | None = None,
        inputs: bool = False,
    ) -> dict:
        """Get job outputs, optionally per-step."""
        params = {}
        if step:
            params["step"] = step
        if inputs:
            params["inputs"] = "true"
        return self._request("GET", f"/api/jobs/{job_id}/output", params=params)

    def events(self, job_id: str) -> list[dict]:
        """Get all events for a job."""
        return self._request("GET", f"/api/jobs/{job_id}/events")

    def cancel(self, job_id: str) -> dict:
        """Cancel a job."""
        return self._request("POST", f"/api/jobs/{job_id}/cancel")

    def fulfill(self, run_id: str, payload: dict) -> dict:
        """Fulfill a suspended step."""
        return self._request("POST", f"/api/runs/{run_id}/fulfill", {
            "payload": payload,
        })

    def list_suspended(
        self,
        since: str | None = None,
        flow: str | None = None,
    ) -> dict:
        """Get global suspension inbox."""
        params = {}
        if since:
            params["since"] = since
        if flow:
            params["flow"] = flow
        return self._request("GET", "/api/jobs/suspended", params=params)

    def health(self) -> dict:
        """Check server health."""
        return self._request("GET", "/api/health")

    def wait(self, job_id: str) -> dict:
        """Long-poll until job reaches terminal state or suspension.

        Note: The server doesn't have a native wait endpoint yet,
        so this polls status until terminal/suspended.
        """
        import time

        while True:
            status = self.status(job_id)
            job_status = status.get("status", "")

            if job_status in ("completed", "failed", "cancelled"):
                return status

            # Check for suspension (all progress blocked)
            steps = status.get("steps", [])
            has_suspended = any(s["status"] == "suspended" for s in steps)
            has_active = any(s["status"] in ("running", "delegated") for s in steps)
            if has_suspended and not has_active:
                return status

            time.sleep(0.5)
