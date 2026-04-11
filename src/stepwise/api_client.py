"""HTTP client for Stepwise server API.

Used by CLI when a server is running, instead of direct SQLite access.
"""

from __future__ import annotations

import json
import time
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

    def patch_job(
        self,
        job_id: str,
        notify_url: str | None = None,
        notify_context: dict | None = None,
    ) -> dict:
        """Update mutable fields on an existing job."""
        body: dict = {}
        if notify_url is not None:
            body["notify_url"] = notify_url
        if notify_context is not None:
            body["notify_context"] = notify_context
        return self._request("PATCH", f"/api/jobs/{job_id}", body)

    def cancel(self, job_id: str) -> dict:
        """Cancel a job."""
        return self._request("POST", f"/api/jobs/{job_id}/cancel")

    def approve(self, job_id: str) -> dict:
        """Approve a job awaiting approval."""
        return self._request("POST", f"/api/jobs/{job_id}/approve")

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

        max_retries = 8
        not_found_count = 0

        while True:
            try:
                status = self.status(job_id)
            except StepwiseAPIError as e:
                if e.status == 404 and not_found_count < max_retries:
                    not_found_count += 1
                    time.sleep(min(0.5 * (2 ** (not_found_count - 1)), 8))
                    continue
                raise

            not_found_count = 0  # reset on success
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

    def wait_many(self, job_ids: list[str], mode: str = "all") -> dict:
        """Poll until jobs reach terminal state or suspension.

        Args:
            job_ids: List of job IDs to wait on.
            mode: 'all' (wait for all) or 'any' (wait for first).

        Returns dict with keys: mode, status, jobs (list of per-job results), summary.
        """
        pending: dict[str, dict | None] = {jid: None for jid in job_ids}
        not_found_counts: dict[str, int] = {jid: 0 for jid in job_ids}
        max_retries = 8

        while True:
            for jid in list(pending):
                if pending[jid] is not None:
                    continue

                try:
                    status = self.status(jid)
                except StepwiseAPIError as e:
                    if e.status == 404 and not_found_counts[jid] < max_retries:
                        not_found_counts[jid] += 1
                        time.sleep(min(0.5 * (2 ** (not_found_counts[jid] - 1)), 8))
                        continue
                    if e.status == 404:
                        pending[jid] = {"job_id": jid, "status": "error",
                                        "error": f"Job {jid} not found after {max_retries} retries"}
                        if mode == "any":
                            return self._build_wait_many_result(mode, [pending[jid]])
                        continue
                    raise

                not_found_counts[jid] = 0
                job_status = status.get("status", "")

                if job_status in ("completed", "failed", "cancelled"):
                    pending[jid] = {**status, "job_id": jid, "status": job_status}
                else:
                    steps = status.get("steps", [])
                    has_suspended = any(s["status"] == "suspended" for s in steps)
                    has_active = any(s["status"] in ("running", "delegated") for s in steps)
                    if has_suspended and not has_active:
                        pending[jid] = {**status, "job_id": jid, "status": "suspended"}

                if pending[jid] is not None and mode == "any":
                    return self._build_wait_many_result(mode, [pending[jid]])

            if mode == "all" and all(r is not None for r in pending.values()):
                return self._build_wait_many_result(mode, list(pending.values()))

            time.sleep(0.5)

    def _build_wait_many_result(self, mode: str, results: list[dict]) -> dict:
        """Build multi-job result dict."""
        summary = {"total": len(results), "completed": 0, "failed": 0,
                   "cancelled": 0, "suspended": 0, "error": 0}
        for r in results:
            s = r.get("status", "error")
            if s in summary:
                summary[s] += 1
            else:
                summary["error"] += 1

        if summary["failed"] or summary["error"]:
            overall = "failed"
        elif summary["cancelled"]:
            overall = "cancelled"
        elif summary["suspended"]:
            overall = "suspended"
        else:
            overall = "completed"

        return {"mode": mode, "status": overall, "jobs": results, "summary": summary}

    # ── Schedules ───────────────────────────────────────────────────

    def list_schedules(
        self,
        status: str | None = None,
        schedule_type: str | None = None,
    ) -> list[dict]:
        """List schedules."""
        params = {}
        if status:
            params["status"] = status
        if schedule_type:
            params["type"] = schedule_type
        return self._request("GET", "/api/schedules", params=params)

    def create_schedule(self, body: dict) -> dict:
        """Create a schedule."""
        return self._request("POST", "/api/schedules", body)

    def get_schedule(self, schedule_id: str) -> dict:
        """Get schedule detail + stats."""
        return self._request("GET", f"/api/schedules/{schedule_id}")

    def update_schedule(self, schedule_id: str, body: dict) -> dict:
        """Update schedule fields."""
        return self._request("PATCH", f"/api/schedules/{schedule_id}", body)

    def delete_schedule(self, schedule_id: str) -> dict:
        """Delete a schedule."""
        return self._request("DELETE", f"/api/schedules/{schedule_id}")

    def pause_schedule(self, schedule_id: str, reason: str | None = None) -> dict:
        """Pause a schedule."""
        body = {}
        if reason:
            body["reason"] = reason
        return self._request("POST", f"/api/schedules/{schedule_id}/pause", body or None)

    def resume_schedule(self, schedule_id: str) -> dict:
        """Resume a paused schedule."""
        return self._request("POST", f"/api/schedules/{schedule_id}/resume")

    def trigger_schedule(self, schedule_id: str) -> dict:
        """Manually trigger a schedule."""
        return self._request("POST", f"/api/schedules/{schedule_id}/trigger")

    def schedule_ticks(
        self,
        schedule_id: str,
        limit: int = 50,
        offset: int = 0,
        outcome: str | None = None,
    ) -> list[dict]:
        """Get tick history for a schedule."""
        params: dict = {"limit": str(limit), "offset": str(offset)}
        if outcome:
            params["outcome"] = outcome
        return self._request("GET", f"/api/schedules/{schedule_id}/ticks", params=params)

    def schedule_stats(self, schedule_id: str) -> dict:
        """Get aggregated stats for a schedule."""
        return self._request("GET", f"/api/schedules/{schedule_id}/stats")

    def schedule_jobs(self, schedule_id: str, limit: int = 50) -> list[dict]:
        """Get jobs launched by a schedule."""
        return self._request("GET", f"/api/schedules/{schedule_id}/jobs", params={"limit": str(limit)})
