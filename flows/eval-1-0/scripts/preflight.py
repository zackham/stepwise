#!/usr/bin/env python3
"""Preflight checks and ground truth calibration for eval-1.0.

Checks:
1. stepwise --version exits 0
2. Server health at configured port returns 200
3. Known-good flow validates successfully
4. Ground truth: known-bad.flow.yaml fails validation
5. Project path exists with expected directories
6. pyproject.toml readable with version field
"""

import json
import os
import subprocess
import sys
from urllib.request import urlopen
from urllib.error import URLError

def run_check(check_id, description, fn):
    """Run a single check, return result dict."""
    try:
        passed, evidence = fn()
        return {
            "id": check_id,
            "description": description,
            "passed": passed,
            "evidence": evidence,
        }
    except Exception as e:
        return {
            "id": check_id,
            "description": description,
            "passed": False,
            "evidence": f"Exception: {e}",
        }

def check_version(stepwise_path):
    """PF1: stepwise --version exits 0."""
    result = subprocess.run(
        ["uv", "run", "stepwise", "--version"],
        capture_output=True, text=True, timeout=30,
        cwd=stepwise_path,
    )
    if result.returncode == 0:
        return True, f"Version: {result.stdout.strip()}"
    return False, f"Exit code {result.returncode}: {result.stderr.strip()}"

def check_server_health(server_port):
    """PF2: Server health endpoint returns 200."""
    url = f"http://localhost:{server_port}/api/health"
    try:
        resp = urlopen(url, timeout=5)
        if resp.status == 200:
            return True, f"GET {url} returned 200"
        return False, f"GET {url} returned {resp.status}"
    except URLError as e:
        return False, f"GET {url} failed: {e}"

def check_known_good_validates(stepwise_path):
    """PF3: Known-good flow validates successfully."""
    flow_path = os.path.join(stepwise_path, "flows", "demo", "FLOW.yaml")
    if not os.path.exists(flow_path):
        return False, f"Known-good flow not found at {flow_path}"
    result = subprocess.run(
        ["uv", "run", "stepwise", "validate", flow_path],
        capture_output=True, text=True, timeout=30,
        cwd=stepwise_path,
    )
    if result.returncode == 0:
        return True, f"Validated {flow_path} successfully"
    return False, f"Validation failed (exit {result.returncode}): {result.stderr.strip()}"

def check_known_bad_fails(stepwise_path, flow_dir):
    """PF4: Ground truth calibration — known-bad.flow.yaml must fail validation."""
    bad_flow = os.path.join(flow_dir, "data", "known-bad.flow.yaml")
    if not os.path.exists(bad_flow):
        return False, f"Known-bad flow not found at {bad_flow}"
    result = subprocess.run(
        ["uv", "run", "stepwise", "validate", bad_flow],
        capture_output=True, text=True, timeout=30,
        cwd=stepwise_path,
    )
    if result.returncode != 0:
        return True, f"Validator correctly rejected known-bad flow (exit {result.returncode})"
    return False, "Validator accepted known-bad flow — ground truth calibration FAILED"

def check_project_structure(stepwise_path):
    """PF5: Project path has expected directories."""
    expected_dirs = ["src/stepwise", "tests", "web/src"]
    missing = []
    for d in expected_dirs:
        full = os.path.join(stepwise_path, d)
        if not os.path.isdir(full):
            missing.append(d)
    if not missing:
        return True, f"All expected directories present: {expected_dirs}"
    return False, f"Missing directories: {missing}"

def check_pyproject(stepwise_path):
    """PF6: pyproject.toml readable with version field."""
    toml_path = os.path.join(stepwise_path, "pyproject.toml")
    if not os.path.exists(toml_path):
        return False, f"pyproject.toml not found at {toml_path}"
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    version = data.get("project", {}).get("version")
    if version:
        return True, f"Version: {version}"
    return False, "No version field found in pyproject.toml"

def main():
    stepwise_path = os.environ.get("stepwise_path", "")
    server_port = os.environ.get("server_port", "8340")
    flow_dir = os.environ.get("STEPWISE_FLOW_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if not stepwise_path:
        print(json.dumps({
            "preflight_passed": False,
            "preflight_checks": [],
            "abort_reason": "stepwise_path not set",
        }))
        sys.exit(1)

    checks = [
        run_check("PF1", "stepwise --version", lambda: check_version(stepwise_path)),
        run_check("PF2", "Server health", lambda: check_server_health(server_port)),
        run_check("PF3", "Known-good flow validates", lambda: check_known_good_validates(stepwise_path)),
        run_check("PF4", "Ground truth: known-bad rejected", lambda: check_known_bad_fails(stepwise_path, flow_dir)),
        run_check("PF5", "Project structure", lambda: check_project_structure(stepwise_path)),
        run_check("PF6", "pyproject.toml version", lambda: check_pyproject(stepwise_path)),
    ]

    all_passed = all(c["passed"] for c in checks)
    failed = [c for c in checks if not c["passed"]]
    abort_reason = ""
    if not all_passed:
        abort_reason = "; ".join(f'{c["id"]}: {c["evidence"]}' for c in failed)

    output = {
        "preflight_passed": all_passed,
        "preflight_checks": checks,
        "abort_reason": abort_reason,
    }
    print(json.dumps(output))

    if not all_passed:
        sys.exit(1)

if __name__ == "__main__":
    main()
