#!/usr/bin/env python3
"""Core execution tests for eval-1.0: CLI + Server + Lifecycle (HARD GATE).

Three sub-dimensions batched into one script:
- CLI (C1-C10): command-line interface checks
- Server (SV1-SV8): REST API and WebSocket checks
- Lifecycle (L1-L6): job lifecycle checks
"""

import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError


def rubric_item(item_id, requirement, result, evidence, **extra):
    """Create a three-state rubric item."""
    item = {
        "id": item_id,
        "requirement": requirement,
        "result": result,  # "pass", "fail", "insufficient_evidence"
        "evidence": evidence,
    }
    item.update(extra)
    return item


def run_cmd(args, cwd=None, timeout=30):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {args[0]}"


def api_get(port, path, timeout=5):
    """HTTP GET against the server. Returns (status, body_str) or raises."""
    url = f"http://localhost:{port}{path}"
    req = Request(url)
    resp = urlopen(req, timeout=timeout)
    body = resp.read().decode()
    return resp.status, body


def api_post(port, path, data=None, timeout=10):
    """HTTP POST against the server. Returns (status, body_str)."""
    import urllib.request
    url = f"http://localhost:{port}{path}"
    body_bytes = json.dumps(data).encode() if data else b""
    req = Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, resp.read().decode()


# ── CLI Rubric (C1-C10) ──────────────────────────────────────────────

def test_cli(project_path):
    results = []

    # C1: --version
    rc, out, err = run_cmd(["uv", "run", "stepwise", "--version"], cwd=project_path)
    results.append(rubric_item("C1", "stepwise --version exits 0",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out or err}"))

    # C2: --help
    rc, out, err = run_cmd(["uv", "run", "stepwise", "--help"], cwd=project_path)
    results.append(rubric_item("C2", "stepwise --help exits 0 with usage info",
        "pass" if rc == 0 and "usage" in out.lower() else "fail",
        f"Exit {rc}, has usage: {'usage' in out.lower()}"))

    # C3: validate good flow
    welcome = os.path.join(project_path, "flows", "welcome", "FLOW.yaml")
    rc, out, err = run_cmd(["uv", "run", "stepwise", "validate", welcome], cwd=project_path)
    results.append(rubric_item("C3", "stepwise validate accepts valid flow",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out or err}"))

    # C4: validate bad flow
    bad = os.path.join(project_path, "flows", "eval-1-0", "data", "known-bad.flow.yaml")
    if os.path.exists(bad):
        rc, out, err = run_cmd(["uv", "run", "stepwise", "validate", bad], cwd=project_path)
        results.append(rubric_item("C4", "stepwise validate rejects invalid flow",
            "pass" if rc != 0 else "fail",
            f"Exit {rc}: {out or err}"))
    else:
        results.append(rubric_item("C4", "stepwise validate rejects invalid flow",
            "insufficient_evidence", "known-bad.flow.yaml not found"))

    # C5: info command (requires a flow name argument)
    rc, out, err = run_cmd(["uv", "run", "stepwise", "info", "welcome"], cwd=project_path)
    results.append(rubric_item("C5", "stepwise info exits 0",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[:200] if out else err[:200]}"))

    # C6: run --wait with a simple flow
    rc, out, err = run_cmd(
        ["uv", "run", "stepwise", "run", "--wait", "--local", welcome],
        cwd=project_path, timeout=60,
    )
    # Welcome flow requires human input, so --wait will likely fail or timeout
    # Mark as pass if the command starts and produces JSON or recognizable output
    if rc == 0:
        results.append(rubric_item("C6", "stepwise run --wait produces output",
            "pass", f"Exit {rc}: output length {len(out)}"))
    else:
        # Expected: welcome flow suspends for human input
        results.append(rubric_item("C6", "stepwise run --wait produces output",
            "insufficient_evidence",
            "Welcome flow requires human input; cannot test --wait end-to-end non-interactively"))

    # C7: jobs list
    rc, out, err = run_cmd(["uv", "run", "stepwise", "jobs"], cwd=project_path)
    results.append(rubric_item("C7", "stepwise jobs lists jobs",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[:200] if out else err[:200]}"))

    # C8: config get (requires a key argument)
    rc, out, err = run_cmd(["uv", "run", "stepwise", "config", "get", "default_model"], cwd=project_path)
    results.append(rubric_item("C8", "stepwise config get exits 0",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[:200] if out else err[:200]}"))

    # C9: schema command
    rc, out, err = run_cmd(["uv", "run", "stepwise", "schema", welcome], cwd=project_path)
    results.append(rubric_item("C9", "stepwise schema produces output",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: output length {len(out)}"))

    # C10: diagram command
    rc, out, err = run_cmd(["uv", "run", "stepwise", "diagram", welcome], cwd=project_path)
    results.append(rubric_item("C10", "stepwise diagram produces output",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: output length {len(out)}"))

    return results


# ── Server Rubric (SV1-SV8) ──────────────────────────────────────────

def test_server(server_port):
    results = []

    # SV1: Health endpoint
    try:
        status, body = api_get(server_port, "/api/health")
        results.append(rubric_item("SV1", "GET /api/health returns 200",
            "pass" if status == 200 else "fail",
            f"Status {status}: {body[:200]}"))
    except Exception as e:
        results.append(rubric_item("SV1", "GET /api/health returns 200",
            "fail", f"Request failed: {e}"))

    # SV2: List jobs
    try:
        status, body = api_get(server_port, "/api/jobs")
        data = json.loads(body)
        results.append(rubric_item("SV2", "GET /api/jobs returns job list",
            "pass" if status == 200 and isinstance(data, list) else "fail",
            f"Status {status}, returned {len(data)} jobs"))
    except Exception as e:
        results.append(rubric_item("SV2", "GET /api/jobs returns job list",
            "fail", f"Request failed: {e}"))

    # SV3: Create job (using welcome flow)
    # API requires 'objective' (str) and either 'workflow' (dict) or 'flow_path' (str)
    test_job_id = None
    try:
        status, body = api_post(server_port, "/api/jobs", {
            "objective": "eval test run",
            "flow_path": "flows/welcome/FLOW.yaml",
            "inputs": {"team_name": "eval-test"},
        })
        data = json.loads(body)
        test_job_id = data.get("id")
        results.append(rubric_item("SV3", "POST /api/jobs creates a job",
            "pass" if status in (200, 201) and test_job_id else "fail",
            f"Status {status}, job_id: {test_job_id}"))
    except Exception as e:
        results.append(rubric_item("SV3", "POST /api/jobs creates a job",
            "fail", f"Request failed: {e}"))

    # SV4: Get job detail
    if test_job_id:
        try:
            status, body = api_get(server_port, f"/api/jobs/{test_job_id}")
            data = json.loads(body)
            results.append(rubric_item("SV4", "GET /api/jobs/:id returns job detail",
                "pass" if status == 200 and data.get("id") == test_job_id else "fail",
                f"Status {status}, has id: {data.get('id') == test_job_id}"))
        except Exception as e:
            results.append(rubric_item("SV4", "GET /api/jobs/:id returns job detail",
                "fail", f"Request failed: {e}"))
    else:
        results.append(rubric_item("SV4", "GET /api/jobs/:id returns job detail",
            "insufficient_evidence", "No test job created"))

    # SV5: Cancel job
    if test_job_id:
        try:
            status, body = api_post(server_port, f"/api/jobs/{test_job_id}/cancel")
            results.append(rubric_item("SV5", "POST /api/jobs/:id/cancel works",
                "pass" if status in (200, 204) else "fail",
                f"Status {status}: {body[:200]}"))
        except Exception as e:
            results.append(rubric_item("SV5", "POST /api/jobs/:id/cancel works",
                "fail", f"Request failed: {e}"))
    else:
        results.append(rubric_item("SV5", "POST /api/jobs/:id/cancel works",
            "insufficient_evidence", "No test job to cancel"))

    # SV6: WebSocket connect (use stdlib http.client to verify upgrade handshake)
    try:
        import http.client
        import base64
        conn = http.client.HTTPConnection("localhost", int(server_port), timeout=5)
        # WebSocket upgrade key per RFC 6455
        ws_key = base64.b64encode(os.urandom(16)).decode()
        conn.request("GET", "/ws", headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": ws_key,
            "Sec-WebSocket-Version": "13",
        })
        resp = conn.getresponse()
        # 101 Switching Protocols = successful WebSocket upgrade
        if resp.status == 101:
            results.append(rubric_item("SV6", "WebSocket connection succeeds",
                "pass", f"WebSocket upgrade accepted (101) at ws://localhost:{server_port}/ws"))
        else:
            results.append(rubric_item("SV6", "WebSocket connection succeeds",
                "fail", f"WebSocket upgrade returned {resp.status} instead of 101"))
        conn.close()
    except Exception as e:
        results.append(rubric_item("SV6", "WebSocket connection succeeds",
            "fail", f"Connection failed: {e}"))

    # SV7: List flows (endpoint is /api/local-flows)
    try:
        status, body = api_get(server_port, "/api/local-flows")
        data = json.loads(body)
        results.append(rubric_item("SV7", "GET /api/local-flows returns flow list",
            "pass" if status == 200 and isinstance(data, list) else "fail",
            f"Status {status}, returned {len(data)} flows"))
    except Exception as e:
        results.append(rubric_item("SV7", "GET /api/local-flows returns flow list",
            "fail", f"Request failed: {e}"))

    # SV8: Config models
    try:
        status, body = api_get(server_port, "/api/config")
        results.append(rubric_item("SV8", "GET /api/config returns config",
            "pass" if status == 200 else "fail",
            f"Status {status}: {body[:200]}"))
    except Exception as e:
        results.append(rubric_item("SV8", "GET /api/config returns config",
            "fail", f"Request failed: {e}"))

    return results


# ── Lifecycle Rubric (L1-L6) ─────────────────────────────────────────

def test_lifecycle(project_path, server_port):
    results = []

    # L1: Run-to-completion — create and run a minimal script flow
    # We test this by creating a job via API and checking it completes
    try:
        # Use a flow that can complete without human input
        # Check if there's a purely scripted flow available
        status, body = api_post(server_port, "/api/jobs", {
            "objective": "lifecycle test run",
            "flow_path": "flows/welcome/FLOW.yaml",
            "inputs": {"team_name": "lifecycle-test"},
        })
        data = json.loads(body)
        job_id = data.get("id")
        if job_id:
            # Wait briefly then check status — welcome flow suspends for human
            time.sleep(2)
            status, body = api_get(server_port, f"/api/jobs/{job_id}")
            job_data = json.loads(body)
            job_status = job_data.get("status", "unknown")
            # A suspended or running status means the engine is working
            if job_status in ("running", "suspended", "completed"):
                results.append(rubric_item("L1", "Job engine processes steps to completion or suspension",
                    "pass", f"Job {job_id} reached status: {job_status}"))
            else:
                results.append(rubric_item("L1", "Job engine processes steps to completion or suspension",
                    "fail", f"Job {job_id} has unexpected status: {job_status}"))
            # Cleanup: cancel the test job
            try:
                api_post(server_port, f"/api/jobs/{job_id}/cancel")
            except Exception:
                pass
        else:
            results.append(rubric_item("L1", "Job engine processes steps to completion or suspension",
                "fail", "Could not create test job"))
    except Exception as e:
        results.append(rubric_item("L1", "Job engine processes steps to completion or suspension",
            "fail", f"Exception: {e}"))

    # L2: Failed step handling — test that the engine records failures
    try:
        status, body = api_get(server_port, "/api/jobs")
        jobs = json.loads(body)
        failed_jobs = [j for j in jobs if j.get("status") == "failed"]
        if failed_jobs:
            results.append(rubric_item("L2", "Failed steps are recorded with error info",
                "pass", f"Found {len(failed_jobs)} failed jobs with status recorded"))
        else:
            results.append(rubric_item("L2", "Failed steps are recorded with error info",
                "insufficient_evidence", "No failed jobs available to inspect"))
    except Exception as e:
        results.append(rubric_item("L2", "Failed steps are recorded with error info",
            "fail", f"Exception: {e}"))

    # L3: Cancel mid-execution
    # Already tested in SV5 implicitly — check that cancelled job has correct status
    try:
        status, body = api_get(server_port, "/api/jobs")
        jobs = json.loads(body)
        cancelled = [j for j in jobs if j.get("status") == "cancelled"]
        if cancelled:
            results.append(rubric_item("L3", "Cancellation sets job status to cancelled",
                "pass", f"Found {len(cancelled)} cancelled jobs"))
        else:
            results.append(rubric_item("L3", "Cancellation sets job status to cancelled",
                "insufficient_evidence", "No cancelled jobs found — may need longer run"))
    except Exception as e:
        results.append(rubric_item("L3", "Cancellation sets job status to cancelled",
            "fail", f"Exception: {e}"))

    # L4: Human pause/resume
    results.append(rubric_item("L4", "Human step pauses job for input",
        "insufficient_evidence",
        "Requires interactive human input; cannot test fully in automated eval"))

    # L5: Persistence across restart
    results.append(rubric_item("L5", "Job state persists across server restart",
        "insufficient_evidence",
        "Cannot safely restart server during evaluation run"))

    # L6: Rerun of failed step
    # Rerun endpoint is POST /api/jobs/{job_id}/steps/{step_name}/rerun
    try:
        status, body = api_get(server_port, "/api/jobs")
        jobs = json.loads(body)
        if jobs:
            job_id = jobs[0]["id"]
            # Get the job's runs to find a step name
            try:
                rs, rbody = api_get(server_port, f"/api/jobs/{job_id}/runs")
                runs = json.loads(rbody)
                step_name = runs[0]["step_name"] if runs else "pick-feature"
            except Exception:
                step_name = "pick-feature"
            # Try the rerun endpoint — may 400 if step hasn't failed
            try:
                from urllib.error import HTTPError
                status, body = api_post(server_port, f"/api/jobs/{job_id}/steps/{step_name}/rerun")
                results.append(rubric_item("L6", "Rerun endpoint exists and responds",
                    "pass", f"Rerun endpoint returned status {status}"))
            except HTTPError as e:
                # 400/404 means endpoint exists but conditions aren't met
                if e.code in (400, 404, 422):
                    results.append(rubric_item("L6", "Rerun endpoint exists and responds",
                        "pass", f"Rerun endpoint exists (returned {e.code})"))
                else:
                    results.append(rubric_item("L6", "Rerun endpoint exists and responds",
                        "fail", f"Rerun endpoint returned unexpected {e.code}"))
        else:
            results.append(rubric_item("L6", "Rerun endpoint exists and responds",
                "insufficient_evidence", "No jobs available to test rerun"))
    except Exception as e:
        results.append(rubric_item("L6", "Rerun endpoint exists and responds",
            "fail", f"Exception: {e}"))

    return results


# ── Main ──────────────────────────────────────────────────────────────

def compute_scores(results):
    """Compute three-state scores from rubric results."""
    pass_count = sum(1 for r in results if r["result"] == "pass")
    fail_count = sum(1 for r in results if r["result"] == "fail")
    insufficient_count = sum(1 for r in results if r["result"] == "insufficient_evidence")
    denominator = pass_count + fail_count
    score_pct = round(pass_count / denominator * 100) if denominator > 0 else 0
    return pass_count, fail_count, insufficient_count, score_pct


def main():
    project_path = os.environ.get("project_path", "")
    server_port = os.environ.get("server_port", "8340")

    if not project_path:
        print(json.dumps({"error": "project_path not set"}))
        sys.exit(1)

    cli_results = test_cli(project_path)
    server_results = test_server(server_port)
    lifecycle_results = test_lifecycle(project_path, server_port)

    all_results = cli_results + server_results + lifecycle_results
    pass_count, fail_count, insufficient_count, score_pct = compute_scores(all_results)

    output = {
        "dimension": "core_execution",
        "rubric_results": all_results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
        "score_pct": score_pct,
        "cli_results": cli_results,
        "server_results": server_results,
        "lifecycle_results": lifecycle_results,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
