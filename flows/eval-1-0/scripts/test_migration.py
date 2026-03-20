#!/usr/bin/env python3
"""Backwards compatibility tests for eval-1.0 (HARD GATE).

Rubric M1-M5: existing flows parse, CLI commands present, API endpoints
respond, CHANGELOG exists, migration docs or command exists.
"""

import glob
import json
import os
import subprocess
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError


def rubric_item(item_id, requirement, result, evidence):
    return {
        "id": item_id,
        "requirement": requirement,
        "result": result,
        "evidence": evidence,
    }


def run_cmd(args, cwd=None, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {args[0]}"


def api_get(port, path, timeout=5):
    url = f"http://localhost:{port}{path}"
    resp = urlopen(Request(url), timeout=timeout)
    return resp.status, resp.read().decode()


def main():
    project_path = os.environ.get("project_path", "")
    server_port = os.environ.get("server_port", "8340")

    if not project_path:
        print(json.dumps({"error": "project_path not set"}))
        sys.exit(1)

    results = []

    # M1: All existing flows in repo parse without error
    flow_files = glob.glob(os.path.join(project_path, "flows", "*", "FLOW.yaml"))
    flow_files += glob.glob(os.path.join(project_path, "examples", "**", "*.flow.yaml"), recursive=True)
    if flow_files:
        failed_flows = []
        for fpath in flow_files:
            rc, out, err = run_cmd(
                ["uv", "run", "stepwise", "validate", fpath],
                cwd=project_path, timeout=30,
            )
            if rc != 0:
                rel = os.path.relpath(fpath, project_path)
                failed_flows.append(rel)
        if not failed_flows:
            results.append(rubric_item("M1",
                "All existing flows parse without error",
                "pass",
                f"Validated {len(flow_files)} flows successfully"))
        else:
            results.append(rubric_item("M1",
                "All existing flows parse without error",
                "fail",
                f"{len(failed_flows)}/{len(flow_files)} flows failed: {', '.join(failed_flows[:5])}"))
    else:
        results.append(rubric_item("M1",
            "All existing flows parse without error",
            "insufficient_evidence",
            "No flow files found in repo"))

    # M2: No CLI commands removed (check --help for expected commands)
    expected_commands = ["run", "validate", "info", "jobs", "config", "server", "diagram", "schema"]
    rc, help_out, err = run_cmd(["uv", "run", "stepwise", "--help"], cwd=project_path)
    if rc == 0:
        missing_cmds = [cmd for cmd in expected_commands if cmd not in help_out]
        if not missing_cmds:
            results.append(rubric_item("M2",
                "No expected CLI commands removed",
                "pass",
                f"All {len(expected_commands)} expected commands present in --help"))
        else:
            results.append(rubric_item("M2",
                "No expected CLI commands removed",
                "fail",
                f"Missing commands: {', '.join(missing_cmds)}"))
    else:
        results.append(rubric_item("M2",
            "No expected CLI commands removed",
            "fail",
            f"stepwise --help failed (exit {rc}): {err[:200]}"))

    # M3: Key API endpoints still respond
    endpoints = ["/api/health", "/api/jobs", "/api/flows", "/api/config"]
    endpoint_results = []
    for ep in endpoints:
        try:
            status, _ = api_get(server_port, ep)
            endpoint_results.append((ep, status == 200))
        except Exception as e:
            endpoint_results.append((ep, False))

    all_ok = all(ok for _, ok in endpoint_results)
    failed_eps = [ep for ep, ok in endpoint_results if not ok]
    if all_ok:
        results.append(rubric_item("M3",
            "Key API endpoints respond",
            "pass",
            f"All {len(endpoints)} endpoints returned 200"))
    else:
        results.append(rubric_item("M3",
            "Key API endpoints respond",
            "fail",
            f"Failed endpoints: {', '.join(failed_eps)}"))

    # M4: CHANGELOG.md exists with version sections
    changelog_path = os.path.join(project_path, "CHANGELOG.md")
    if os.path.exists(changelog_path):
        with open(changelog_path, "r") as f:
            content = f.read()
        import re
        version_sections = re.findall(r'##\s*\[[\d.]+\]', content)
        if version_sections:
            results.append(rubric_item("M4",
                "CHANGELOG.md exists with version sections",
                "pass",
                f"CHANGELOG.md has {len(version_sections)} version sections"))
        else:
            results.append(rubric_item("M4",
                "CHANGELOG.md exists with version sections",
                "fail",
                "CHANGELOG.md exists but has no ## [X.Y.Z] version sections"))
    else:
        results.append(rubric_item("M4",
            "CHANGELOG.md exists with version sections",
            "fail",
            "CHANGELOG.md not found"))

    # M5: Migration docs or stepwise migrate command exists
    has_migrate_cmd = False
    rc, help_out, _ = run_cmd(["uv", "run", "stepwise", "--help"], cwd=project_path)
    if rc == 0 and "migrate" in help_out:
        has_migrate_cmd = True

    has_migration_docs = False
    for doc_pattern in ["docs/*migrat*", "docs/*upgrad*", "MIGRATION*", "UPGRADING*"]:
        if glob.glob(os.path.join(project_path, doc_pattern)):
            has_migration_docs = True
            break

    if has_migrate_cmd:
        results.append(rubric_item("M5",
            "Migration docs or migrate command exists",
            "pass",
            "stepwise migrate command available"))
    elif has_migration_docs:
        results.append(rubric_item("M5",
            "Migration docs or migrate command exists",
            "pass",
            "Migration documentation found"))
    else:
        results.append(rubric_item("M5",
            "Migration docs or migrate command exists",
            "insufficient_evidence",
            "Neither migrate command nor migration docs found"))

    # Compute scores
    pass_count = sum(1 for r in results if r["result"] == "pass")
    fail_count = sum(1 for r in results if r["result"] == "fail")
    insufficient_count = sum(1 for r in results if r["result"] == "insufficient_evidence")
    denominator = pass_count + fail_count
    score_pct = round(pass_count / denominator * 100) if denominator > 0 else 0

    output = {
        "dimension": "migration",
        "rubric_results": results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
        "score_pct": score_pct,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
