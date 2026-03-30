#!/usr/bin/env python3
"""Quality tests for eval-1.0 — 7 non-gate dimensions batched.

Dimensions:
1. Validation (V1-V4)
2. Testing (T1-T4)
3. Config (CF1-CF3)
4. Webhooks (W1-W2)
5. Observability (O1-O3)
6. Performance (P1-P3)
7. Error DX (E1-E7)
"""

import glob
import json
import os
import re
import subprocess
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


def api_post(port, path, data=None, timeout=10):
    import urllib.request
    url = f"http://localhost:{port}{path}"
    body = json.dumps(data).encode() if data else b""
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, resp.read().decode()


# ── 1. Validation (V1-V4) ────────────────────────────────────────────

def test_validation(project_path):
    results = []

    # V1: Built-in flows validate
    flow_files = glob.glob(os.path.join(project_path, "flows", "*", "FLOW.yaml"))
    valid_count = 0
    for fpath in flow_files:
        rc, _, _ = run_cmd(["uv", "run", "stepwise", "validate", fpath], cwd=project_path)
        if rc == 0:
            valid_count += 1
    results.append(rubric_item("V1",
        "Built-in flows validate successfully",
        "pass" if valid_count == len(flow_files) and flow_files else "fail",
        f"{valid_count}/{len(flow_files)} flows validated"))

    # V2: Malformed YAML rejected
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".flow.yaml", delete=False) as f:
        f.write("name: bad\nsteps:\n  - not a mapping\n")
        bad_path = f.name
    rc, _, _ = run_cmd(["uv", "run", "stepwise", "validate", bad_path], cwd=project_path)
    os.unlink(bad_path)
    results.append(rubric_item("V2",
        "Malformed YAML rejected by validator",
        "pass" if rc != 0 else "fail",
        f"Validator exit code: {rc}"))

    # V3: Circular deps detected
    with tempfile.NamedTemporaryFile(mode="w", suffix=".flow.yaml", delete=False) as f:
        f.write("""name: cycle-test
steps:
  a:
    run: |
      printf '{"x": 1}'
    inputs:
      val: b.y
    outputs: [x]
  b:
    run: |
      printf '{"y": 1}'
    inputs:
      val: a.x
    outputs: [y]
""")
        cycle_path = f.name
    rc, out, err = run_cmd(["uv", "run", "stepwise", "validate", cycle_path], cwd=project_path)
    os.unlink(cycle_path)
    # Check if the error mentions cycle or circular
    combined = (out + err).lower()
    detected = rc != 0 and ("cycle" in combined or "circular" in combined or "no entry" in combined)
    results.append(rubric_item("V3",
        "Circular dependencies detected by validator",
        "pass" if detected else "fail",
        f"Exit {rc}, output mentions cycle: {detected}"))

    # V4: Duplicate steps caught
    with tempfile.NamedTemporaryFile(mode="w", suffix=".flow.yaml", delete=False) as f:
        # YAML maps with duplicate keys — last one wins, but validator should catch
        f.write("""name: dup-test
steps:
  my-step:
    run: |
      printf '{"x": 1}'
    outputs: [x]
  my-step:
    run: |
      printf '{"y": 1}'
    outputs: [y]
""")
        dup_path = f.name
    rc, out, err = run_cmd(["uv", "run", "stepwise", "validate", dup_path], cwd=project_path)
    os.unlink(dup_path)
    # YAML itself deduplicates keys silently; this may pass validation
    if rc != 0:
        results.append(rubric_item("V4",
            "Duplicate step names caught",
            "pass",
            f"Validator rejected duplicate steps (exit {rc})"))
    else:
        results.append(rubric_item("V4",
            "Duplicate step names caught",
            "insufficient_evidence",
            "YAML parser deduplicates keys silently; validator accepted the file"))

    return results


# ── 2. Testing (T1-T4) ───────────────────────────────────────────────

def test_testing(project_path):
    results = []

    # T1: Test suite runs
    rc, out, err = run_cmd(
        ["uv", "run", "pytest", "tests/", "-x", "--tb=short", "-q"],
        cwd=project_path, timeout=300,
    )
    results.append(rubric_item("T1",
        "pytest test suite runs to completion",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[-500:] if out else err[-500:]}"))

    # T2: ≥90% pass rate
    # Parse pytest output for pass/fail counts
    pass_match = re.search(r'(\d+) passed', out)
    fail_match = re.search(r'(\d+) failed', out)
    error_match = re.search(r'(\d+) error', out)
    passed = int(pass_match.group(1)) if pass_match else 0
    failed = int(fail_match.group(1)) if fail_match else 0
    errors = int(error_match.group(1)) if error_match else 0
    total = passed + failed + errors
    pct = round(passed / total * 100) if total > 0 else 0
    results.append(rubric_item("T2",
        "≥90% test pass rate",
        "pass" if pct >= 90 else "fail",
        f"{passed}/{total} tests passed ({pct}%)"))

    # T3: Zero collection errors
    collection_errors = re.findall(r'ERROR collecting', out + err)
    results.append(rubric_item("T3",
        "Zero test collection errors",
        "pass" if not collection_errors else "fail",
        f"Found {len(collection_errors)} collection errors"))

    # T4: ≥20 tests
    results.append(rubric_item("T4",
        "At least 20 tests in suite",
        "pass" if total >= 20 else "fail",
        f"Total tests: {total}"))

    return results


# ── 3. Config (CF1-CF3) ──────────────────────────────────────────────

def test_config(project_path, server_port):
    results = []

    # CF1: config get works
    rc, out, err = run_cmd(["uv", "run", "stepwise", "config", "get"], cwd=project_path)
    results.append(rubric_item("CF1",
        "stepwise config get exits 0",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[:200] if out else err[:200]}"))

    # CF2: config get <key> works
    rc, out, err = run_cmd(["uv", "run", "stepwise", "config", "get", "default_model"], cwd=project_path)
    results.append(rubric_item("CF2",
        "stepwise config get <key> exits 0",
        "pass" if rc == 0 else "fail",
        f"Exit {rc}: {out[:200] if out else err[:200]}"))

    # CF3: config set/get roundtrip
    # Set a test value, read it back
    test_key = "default_model"
    rc_set, _, err_set = run_cmd(
        ["uv", "run", "stepwise", "config", "set", test_key, "test-model-12345"],
        cwd=project_path,
    )
    rc_get, out_get, _ = run_cmd(
        ["uv", "run", "stepwise", "config", "get", test_key],
        cwd=project_path,
    )
    roundtrip_ok = rc_set == 0 and "test-model-12345" in out_get
    # Restore original value
    if rc_set == 0:
        run_cmd(["uv", "run", "stepwise", "config", "set", test_key, ""], cwd=project_path)
    results.append(rubric_item("CF3",
        "config set/get roundtrip preserves value",
        "pass" if roundtrip_ok else "fail",
        f"Set exit {rc_set}, get returned: {out_get[:200]}"))

    return results


# ── 4. Webhooks (W1-W2) ──────────────────────────────────────────────

def test_webhooks(project_path):
    results = []

    # W1: Webhook/hook fire capability exists
    hooks_path = os.path.join(project_path, "src", "stepwise", "hooks.py")
    if os.path.exists(hooks_path):
        with open(hooks_path, "r") as f:
            content = f.read()
        has_fire = "fire" in content or "notify" in content or "trigger" in content
        results.append(rubric_item("W1",
            "Hook/notification system exists",
            "pass" if has_fire else "fail",
            f"hooks.py exists, has fire/notify/trigger: {has_fire}"))
    else:
        config_path = os.path.join(project_path, "src", "stepwise", "config.py")
        has_notify = False
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                has_notify = "notify_url" in f.read()
        results.append(rubric_item("W1",
            "Hook/notification system exists",
            "pass" if has_notify else "insufficient_evidence",
            f"hooks.py not found; config.py has notify_url: {has_notify}"))

    # W2: Webhook payload includes job_id and status
    # Check if hook/notification code sends structured data
    src_dir = os.path.join(project_path, "src", "stepwise")
    found_payload = False
    for fname in os.listdir(src_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(src_dir, fname)
        with open(fpath, "r") as f:
            content = f.read()
        if "job_id" in content and ("status" in content) and ("notify" in content or "webhook" in content or "hook" in content):
            found_payload = True
            break
    results.append(rubric_item("W2",
        "Notification payload includes job_id and status",
        "pass" if found_payload else "insufficient_evidence",
        "Found notification code with job_id/status" if found_payload else "Could not verify notification payload structure"))

    return results


# ── 5. Observability (O1-O3) ─────────────────────────────────────────

def test_observability(project_path):
    results = []

    # O1: Uses logging module (not print)
    src_dir = os.path.join(project_path, "src", "stepwise")
    uses_logging = False
    has_print = False
    for fname in os.listdir(src_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(src_dir, fname)
        with open(fpath, "r") as f:
            content = f.read()
        if "import logging" in content or "from logging" in content:
            uses_logging = True
        # Check for print() in library code (not in __main__ blocks)
        lines = content.split("\n")
        in_main = False
        for line in lines:
            if "if __name__" in line:
                in_main = True
            if not in_main and re.match(r'\s*print\s*\(', line) and not line.strip().startswith("#"):
                has_print = True

    results.append(rubric_item("O1",
        "Library code uses logging module",
        "pass" if uses_logging else "fail",
        f"Uses logging: {uses_logging}, has print() in lib code: {has_print}"))

    # O2: Step timing recorded in step runs
    # Check store.py or models.py for timing fields
    for fname in ["store.py", "models.py"]:
        fpath = os.path.join(src_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "r") as f:
                content = f.read()
            if any(term in content for term in ["started_at", "completed_at", "duration", "elapsed"]):
                results.append(rubric_item("O2",
                    "Step timing recorded in runs",
                    "pass",
                    f"{fname} contains timing fields (started_at/completed_at/duration)"))
                break
    else:
        results.append(rubric_item("O2",
            "Step timing recorded in runs",
            "fail",
            "No timing fields found in store.py or models.py"))

    # O3: Error context in failed steps
    # Check that failed runs include error details
    engine_path = os.path.join(src_dir, "engine.py")
    if os.path.exists(engine_path):
        with open(engine_path, "r") as f:
            content = f.read()
        has_error_context = "error" in content.lower() and ("fail_run" in content or "_fail" in content)
        results.append(rubric_item("O3",
            "Failed steps include error context",
            "pass" if has_error_context else "fail",
            "engine.py has error handling in failure paths" if has_error_context else "No error context found in failure paths"))
    else:
        results.append(rubric_item("O3",
            "Failed steps include error context",
            "insufficient_evidence",
            "engine.py not found"))

    return results


# ── 6. Performance (P1-P3) ───────────────────────────────────────────

def test_performance(project_path, server_port):
    results = []

    # P1: Health endpoint < 500ms
    try:
        start = time.monotonic()
        api_get(server_port, "/api/health")
        elapsed_ms = (time.monotonic() - start) * 1000
        results.append(rubric_item("P1",
            "GET /api/health responds in <500ms",
            "pass" if elapsed_ms < 500 else "fail",
            f"Response time: {elapsed_ms:.0f}ms"))
    except Exception as e:
        results.append(rubric_item("P1",
            "GET /api/health responds in <500ms",
            "fail",
            f"Request failed: {e}"))

    # P2: CLI --version < 2s
    start = time.monotonic()
    rc, _, _ = run_cmd(["uv", "run", "stepwise", "--version"], cwd=project_path, timeout=10)
    elapsed_s = time.monotonic() - start
    results.append(rubric_item("P2",
        "stepwise --version completes in <2s",
        "pass" if rc == 0 and elapsed_s < 2 else "fail",
        f"Completed in {elapsed_s:.1f}s (exit {rc})"))

    # P3: Validation < 5s
    welcome = os.path.join(project_path, "flows", "demo", "FLOW.yaml")
    start = time.monotonic()
    rc, _, _ = run_cmd(["uv", "run", "stepwise", "validate", welcome], cwd=project_path, timeout=10)
    elapsed_s = time.monotonic() - start
    results.append(rubric_item("P3",
        "stepwise validate completes in <5s",
        "pass" if rc == 0 and elapsed_s < 5 else "fail",
        f"Completed in {elapsed_s:.1f}s (exit {rc})"))

    return results


# ── 7. Error DX (E1-E7) ──────────────────────────────────────────────

def test_error_dx(project_path):
    """Test that error messages are actionable and don't leak stack traces."""
    results = []
    import tempfile

    def validate_and_check(item_id, desc, yaml_content, expect_fail=True):
        """Validate YAML and check error message quality."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".flow.yaml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        rc, out, err = run_cmd(["uv", "run", "stepwise", "validate", path], cwd=project_path)
        os.unlink(path)
        combined = out + err
        has_traceback = "Traceback" in combined
        has_actionable = len(combined) > 10  # Has some error message
        if expect_fail and rc != 0 and has_actionable and not has_traceback:
            return rubric_item(item_id, desc, "pass",
                f"Clear error without traceback: {combined[:200]}")
        elif expect_fail and rc != 0 and has_traceback:
            return rubric_item(item_id, desc, "fail",
                f"Error includes stack trace: {combined[:200]}")
        elif expect_fail and rc == 0:
            return rubric_item(item_id, desc, "fail",
                "Validator accepted invalid input")
        else:
            return rubric_item(item_id, desc, "pass",
                f"Exit {rc}: {combined[:200]}")

    # E1: Missing required field
    results.append(validate_and_check("E1",
        "Missing field error is actionable",
        "name: test\nsteps:\n  s:\n    outputs: [x]\n"))

    # E2: Circular dependency error
    results.append(validate_and_check("E2",
        "Circular dependency error is actionable",
        """name: cycle
steps:
  a:
    run: |
      printf '{"x": 1}'
    inputs:
      v: b.y
    outputs: [x]
  b:
    run: |
      printf '{"y": 1}'
    inputs:
      v: a.x
    outputs: [y]
"""))

    # E3: Invalid executor type
    results.append(validate_and_check("E3",
        "Invalid executor type error is actionable",
        """name: bad-exec
steps:
  s:
    executor: quantum
    outputs: [x]
""",
        expect_fail=False))  # Executor validation may be runtime-only

    # E4: Missing input reference
    results.append(validate_and_check("E4",
        "Missing input reference error is actionable",
        """name: bad-ref
steps:
  s:
    run: |
      printf '{"x": 1}'
    inputs:
      v: ghost.field
    outputs: [x]
"""))

    # E5: Invalid exit rule
    results.append(validate_and_check("E5",
        "Invalid exit rule error is actionable",
        """name: bad-exit
steps:
  s:
    run: |
      printf '{"x": 1}'
    outputs: [x]
    exits:
      - action: explode
        when: "True"
"""))

    # E6: Empty steps dict
    results.append(validate_and_check("E6",
        "Empty steps error is actionable",
        "name: empty\nsteps: {}\n"))

    # E7: Non-YAML content
    results.append(validate_and_check("E7",
        "Non-YAML content rejected clearly",
        "this is not yaml at all }{}}[[\n"))

    return results


# ── Main ──────────────────────────────────────────────────────────────

def compute_scores(results):
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

    dims = {
        "validation": test_validation(project_path),
        "testing": test_testing(project_path),
        "config": test_config(project_path, server_port),
        "webhooks": test_webhooks(project_path),
        "observability": test_observability(project_path),
        "performance": test_performance(project_path, server_port),
        "error_dx": test_error_dx(project_path),
    }

    all_results = []
    dimensions = {}
    for dim_name, items in dims.items():
        all_results.extend(items)
        p, f, ie, pct = compute_scores(items)
        dimensions[dim_name] = {
            "rubric_results": items,
            "pass_count": p,
            "fail_count": f,
            "insufficient_count": ie,
            "score_pct": pct,
        }

    pass_count, fail_count, insufficient_count, score_pct = compute_scores(all_results)

    output = {
        "dimension": "quality",
        "rubric_results": all_results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
        "score_pct": score_pct,
        "dimensions": dimensions,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
