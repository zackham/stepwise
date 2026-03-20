#!/usr/bin/env python3
"""Data integrity tests for eval-1.0 (HARD GATE, NEW).

Rubric DI1-DI6: SQLite WAL mode, foreign keys, artifact round-trip,
step run retrieval, no orphaned runs, event log ordering.
"""

import json
import os
import sqlite3
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


def rubric_item(item_id, requirement, result, evidence):
    return {
        "id": item_id,
        "requirement": requirement,
        "result": result,
        "evidence": evidence,
    }


def api_get(port, path, timeout=10):
    url = f"http://localhost:{port}{path}"
    resp = urlopen(Request(url), timeout=timeout)
    return resp.status, resp.read().decode()


def api_post(port, path, data=None, timeout=10):
    import urllib.request
    url = f"http://localhost:{port}{path}"
    body = json.dumps(data).encode() if data else b""
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, resp.read().decode()


def find_db_path(project_path):
    """Find the stepwise database file."""
    candidates = [
        os.path.join(project_path, ".stepwise", "stepwise.db"),
        os.environ.get("STEPWISE_DB", ""),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def main():
    project_path = os.environ.get("project_path", "")
    server_port = os.environ.get("server_port", "8340")

    if not project_path:
        print(json.dumps({"error": "project_path not set"}))
        sys.exit(1)

    results = []
    db_path = find_db_path(project_path)

    # DI1: SQLite WAL mode enabled
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            conn.close()
            results.append(rubric_item("DI1",
                "SQLite WAL mode enabled",
                "pass" if mode == "wal" else "fail",
                f"journal_mode = {mode}"))
        except Exception as e:
            results.append(rubric_item("DI1",
                "SQLite WAL mode enabled",
                "fail",
                f"Could not query PRAGMA journal_mode: {e}"))
    else:
        results.append(rubric_item("DI1",
            "SQLite WAL mode enabled",
            "insufficient_evidence",
            "Database file not found"))

    # DI2: Foreign keys enforced
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.execute("PRAGMA foreign_keys")
            fk = cursor.fetchone()[0]
            conn.close()
            # Note: PRAGMA foreign_keys returns 0 in a read-only connection
            # Check the store.py source to verify it's enabled at connection time
            store_path = os.path.join(project_path, "src", "stepwise", "store.py")
            if os.path.exists(store_path):
                with open(store_path, "r") as f:
                    store_content = f.read()
                has_fk_pragma = "foreign_keys" in store_content and ("ON" in store_content or "1" in store_content)
                if has_fk_pragma:
                    results.append(rubric_item("DI2",
                        "Foreign keys enforced",
                        "pass",
                        f"store.py enables foreign_keys PRAGMA; current read-only value: {fk}"))
                else:
                    results.append(rubric_item("DI2",
                        "Foreign keys enforced",
                        "fail",
                        f"store.py does not appear to enable foreign_keys; current value: {fk}"))
            else:
                results.append(rubric_item("DI2",
                    "Foreign keys enforced",
                    "pass" if fk == 1 else "fail",
                    f"PRAGMA foreign_keys = {fk}"))
        except Exception as e:
            results.append(rubric_item("DI2",
                "Foreign keys enforced",
                "fail",
                f"Could not query PRAGMA foreign_keys: {e}"))
    else:
        results.append(rubric_item("DI2",
            "Foreign keys enforced",
            "insufficient_evidence",
            "Database file not found"))

    # DI3: Job artifact round-trip
    # Create a job via API, wait for it to reach a state, verify data
    try:
        status, body = api_post(server_port, "/api/jobs", {
            "flow": "welcome",
            "inputs": {"team_name": "di-test"},
        })
        data = json.loads(body)
        job_id = data.get("id")
        if job_id:
            # Wait for job to reach suspended state (welcome flow has human step)
            time.sleep(3)
            status, body = api_get(server_port, f"/api/jobs/{job_id}")
            job_data = json.loads(body)
            # Verify we can read back the job with its definition
            has_workflow = "workflow" in job_data or "objective" in job_data
            has_id = job_data.get("id") == job_id
            if has_id and job_data.get("status") in ("running", "suspended", "completed"):
                results.append(rubric_item("DI3",
                    "Job data round-trip through store",
                    "pass",
                    f"Created job {job_id}, read back with status={job_data.get('status')}"))
            else:
                results.append(rubric_item("DI3",
                    "Job data round-trip through store",
                    "fail",
                    f"Job {job_id} read back with unexpected state: {job_data.get('status')}"))
            # Cleanup
            try:
                api_post(server_port, f"/api/jobs/{job_id}/cancel")
            except Exception:
                pass
        else:
            results.append(rubric_item("DI3",
                "Job data round-trip through store",
                "fail",
                "Could not create test job"))
    except Exception as e:
        results.append(rubric_item("DI3",
            "Job data round-trip through store",
            "fail",
            f"Exception: {e}"))

    # DI4: Step run results retrievable
    try:
        status, body = api_get(server_port, "/api/jobs")
        jobs = json.loads(body)
        found_runs = False
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            try:
                status, body = api_get(server_port, f"/api/jobs/{job_id}/runs")
                runs = json.loads(body)
                if isinstance(runs, list) and runs:
                    # Check that runs have expected structure
                    first_run = runs[0]
                    has_fields = all(k in first_run for k in ["id", "step_name", "status"])
                    if has_fields:
                        found_runs = True
                        results.append(rubric_item("DI4",
                            "Step run results retrievable via API",
                            "pass",
                            f"Job {job_id} has {len(runs)} runs with proper structure"))
                        break
            except Exception:
                continue

        if not found_runs:
            results.append(rubric_item("DI4",
                "Step run results retrievable via API",
                "insufficient_evidence",
                "No jobs with completed step runs found"))
    except Exception as e:
        results.append(rubric_item("DI4",
            "Step run results retrievable via API",
            "fail",
            f"Exception: {e}"))

    # DI5: No orphaned step runs
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.execute("""
                SELECT COUNT(*) FROM step_runs sr
                LEFT JOIN jobs j ON sr.job_id = j.id
                WHERE j.id IS NULL
            """)
            orphan_count = cursor.fetchone()[0]
            conn.close()
            results.append(rubric_item("DI5",
                "No orphaned step runs (all have valid job_id)",
                "pass" if orphan_count == 0 else "fail",
                f"Found {orphan_count} orphaned step runs"))
        except Exception as e:
            results.append(rubric_item("DI5",
                "No orphaned step runs (all have valid job_id)",
                "fail",
                f"Query failed: {e}"))
    else:
        results.append(rubric_item("DI5",
            "No orphaned step runs (all have valid job_id)",
            "insufficient_evidence",
            "Database file not found"))

    # DI6: Event log append-only integrity (sequential ordering)
    try:
        status, body = api_get(server_port, "/api/jobs")
        jobs = json.loads(body)
        checked = False
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            try:
                status, body = api_get(server_port, f"/api/jobs/{job_id}/events")
                events = json.loads(body)
                if isinstance(events, list) and len(events) >= 2:
                    # Verify events are in sequential order
                    timestamps = []
                    for evt in events:
                        ts = evt.get("timestamp") or evt.get("created_at", "")
                        timestamps.append(ts)
                    # Check monotonic ordering
                    is_ordered = all(timestamps[i] <= timestamps[i+1] for i in range(len(timestamps)-1))
                    results.append(rubric_item("DI6",
                        "Event log has sequential ordering",
                        "pass" if is_ordered else "fail",
                        f"Job {job_id}: {len(events)} events, ordered={is_ordered}"))
                    checked = True
                    break
            except Exception:
                continue

        if not checked:
            results.append(rubric_item("DI6",
                "Event log has sequential ordering",
                "insufficient_evidence",
                "No jobs with enough events to verify ordering"))
    except Exception as e:
        results.append(rubric_item("DI6",
            "Event log has sequential ordering",
            "fail",
            f"Exception: {e}"))

    # Compute scores
    pass_count = sum(1 for r in results if r["result"] == "pass")
    fail_count = sum(1 for r in results if r["result"] == "fail")
    insufficient_count = sum(1 for r in results if r["result"] == "insufficient_evidence")
    denominator = pass_count + fail_count
    score_pct = round(pass_count / denominator * 100) if denominator > 0 else 0

    output = {
        "dimension": "data_integrity",
        "rubric_results": results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
        "score_pct": score_pct,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
