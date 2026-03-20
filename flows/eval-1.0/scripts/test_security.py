#!/usr/bin/env python3
"""Security posture tests for eval-1.0 (HARD GATE, severity model).

Rubric S1-S10 with severity classification: blocker, major, minor.
Any blocker finding = automatic NO-GO regardless of percentage score.
"""

import json
import os
import re
import subprocess
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


def rubric_item(item_id, requirement, result, evidence, severity="major"):
    """Create a security rubric item with severity."""
    return {
        "id": item_id,
        "requirement": requirement,
        "result": result,
        "evidence": evidence,
        "severity": severity,
    }


def grep_files(project_path, pattern, extensions=None, exclude_dirs=None):
    """Search for pattern in project files. Returns list of (file, line_num, line)."""
    exclude_dirs = exclude_dirs or ["node_modules", ".venv", "venv", "__pycache__", ".git"]
    matches = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in files:
            if extensions and not any(fname.endswith(ext) for ext in extensions):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line):
                            rel = os.path.relpath(fpath, project_path)
                            matches.append((rel, i, line.strip()))
            except (OSError, UnicodeDecodeError):
                pass
    return matches


def api_get(port, path, timeout=5):
    """HTTP GET. Returns (status, body) or raises."""
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

    # S1: API endpoints reject unauthenticated requests (blocker)
    # Stepwise doesn't have auth by default — this IS the finding
    try:
        status, body = api_get(server_port, "/api/jobs")
        if status == 200:
            results.append(rubric_item("S1",
                "API endpoints reject unauthenticated requests",
                "fail",
                "GET /api/jobs returned 200 without any authentication — no auth mechanism exists",
                severity="blocker"))
        else:
            results.append(rubric_item("S1",
                "API endpoints reject unauthenticated requests",
                "pass",
                f"GET /api/jobs returned {status}",
                severity="blocker"))
    except Exception as e:
        results.append(rubric_item("S1",
            "API endpoints reject unauthenticated requests",
            "insufficient_evidence",
            f"Could not reach server: {e}",
            severity="blocker"))

    # S2: WebSocket requires auth (major)
    try:
        import websocket
        ws = websocket.create_connection(f"ws://localhost:{server_port}/ws", timeout=5)
        ws.close()
        results.append(rubric_item("S2",
            "WebSocket requires authentication",
            "fail",
            "WebSocket connected without authentication",
            severity="major"))
    except ImportError:
        results.append(rubric_item("S2",
            "WebSocket requires authentication",
            "insufficient_evidence",
            "websocket-client library not installed",
            severity="major"))
    except Exception as e:
        # Connection refused or auth rejection = pass
        results.append(rubric_item("S2",
            "WebSocket requires authentication",
            "pass",
            f"Connection rejected: {e}",
            severity="major"))

    # S3: Sensitive config vars masked in logs/API (blocker)
    # Check if API key values are exposed in config endpoint
    try:
        status, body = api_get(server_port, "/api/config")
        data = json.loads(body)
        body_lower = body.lower()
        # Check for raw API key exposure
        has_exposed_key = False
        if isinstance(data, dict):
            for key, val in data.items():
                if "key" in key.lower() and isinstance(val, str) and len(val) > 10:
                    has_exposed_key = True
                    break
        if has_exposed_key:
            results.append(rubric_item("S3",
                "Sensitive config vars masked in API responses",
                "fail",
                "API key value appears unmasked in /api/config response",
                severity="blocker"))
        else:
            results.append(rubric_item("S3",
                "Sensitive config vars masked in API responses",
                "pass",
                "No raw API key values detected in /api/config response",
                severity="blocker"))
    except Exception as e:
        results.append(rubric_item("S3",
            "Sensitive config vars masked in API responses",
            "insufficient_evidence",
            f"Could not check config endpoint: {e}",
            severity="blocker"))

    # S4: YAML run blocks can't inject via config interpolation (blocker)
    # Check if script executor sanitizes input values used in shell commands
    src_dir = os.path.join(project_path, "src", "stepwise")
    executor_path = os.path.join(src_dir, "executors.py")
    if os.path.exists(executor_path):
        with open(executor_path, "r") as f:
            content = f.read()
        # Check for shell=True with user input interpolation
        uses_shell_true = "shell=True" in content or "shell = True" in content
        # Check if there's input sanitization
        has_shlex = "shlex" in content
        if uses_shell_true and not has_shlex:
            results.append(rubric_item("S4",
                "Script executor prevents shell injection via inputs",
                "fail",
                f"executors.py uses shell=True without shlex sanitization",
                severity="blocker"))
        elif uses_shell_true and has_shlex:
            results.append(rubric_item("S4",
                "Script executor prevents shell injection via inputs",
                "pass",
                "executors.py uses shell=True but applies shlex sanitization",
                severity="blocker"))
        else:
            results.append(rubric_item("S4",
                "Script executor prevents shell injection via inputs",
                "pass",
                "executors.py does not use shell=True for command execution",
                severity="blocker"))
    else:
        results.append(rubric_item("S4",
            "Script executor prevents shell injection via inputs",
            "insufficient_evidence",
            "executors.py not found",
            severity="blocker"))

    # S5: No eval()/exec() in execution paths (blocker)
    dangerous_calls = grep_files(
        os.path.join(project_path, "src", "stepwise"),
        r'\b(eval|exec)\s*\(',
        extensions=[".py"],
    )
    # Filter out safe uses (e.g., in comments, string literals for exit rule eval)
    # exit rule evaluation is expected to use eval — check if it's sandboxed
    real_dangers = []
    for fpath, line_num, line in dangerous_calls:
        # Skip comments
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Exit rule eval is expected but should be noted
        real_dangers.append(f"{fpath}:{line_num}: {line[:100]}")

    if not real_dangers:
        results.append(rubric_item("S5",
            "No eval()/exec() in execution paths",
            "pass",
            "No eval/exec calls found in src/stepwise/",
            severity="blocker"))
    else:
        results.append(rubric_item("S5",
            "No eval()/exec() in execution paths",
            "fail",
            f"Found {len(real_dangers)} eval/exec calls: {'; '.join(real_dangers[:5])}",
            severity="blocker"))

    # S6: Agent can't access files outside working_dir (major)
    agent_path = os.path.join(src_dir, "agent.py")
    if os.path.exists(agent_path):
        with open(agent_path, "r") as f:
            agent_content = f.read()
        # Check if working_dir is enforced / sandboxed
        has_cwd = "cwd" in agent_content or "working_dir" in agent_content
        has_chdir = "chdir" in agent_content
        if has_cwd or has_chdir:
            results.append(rubric_item("S6",
                "Agent executor sets working directory",
                "pass",
                "agent.py references working directory configuration",
                severity="major"))
        else:
            results.append(rubric_item("S6",
                "Agent executor sets working directory",
                "fail",
                "agent.py does not appear to enforce working directory",
                severity="major"))
    else:
        results.append(rubric_item("S6",
            "Agent executor sets working directory",
            "insufficient_evidence",
            "agent.py not found",
            severity="major"))

    # S7: No hardcoded secrets in codebase (major)
    secret_patterns = [
        r'(?i)(api[_-]?key|secret[_-]?key|password|token)\s*=\s*["\'][a-zA-Z0-9]{20,}["\']',
        r'sk-[a-zA-Z0-9]{20,}',
        r'ghp_[a-zA-Z0-9]{20,}',
    ]
    all_secrets = []
    for pattern in secret_patterns:
        matches = grep_files(
            os.path.join(project_path, "src"),
            pattern,
            extensions=[".py", ".ts", ".tsx", ".js"],
        )
        all_secrets.extend(matches)

    if not all_secrets:
        results.append(rubric_item("S7",
            "No hardcoded secrets in source code",
            "pass",
            "No hardcoded API keys, tokens, or passwords found in src/",
            severity="major"))
    else:
        locs = [f"{f}:{n}" for f, n, _ in all_secrets[:5]]
        results.append(rubric_item("S7",
            "No hardcoded secrets in source code",
            "fail",
            f"Found {len(all_secrets)} potential secrets: {', '.join(locs)}",
            severity="major"))

    # S8: No critical CVEs in dependencies (major)
    # Check with pip audit or similar
    try:
        r = subprocess.run(
            ["uv", "run", "pip", "audit"],
            capture_output=True, text=True, timeout=60,
            cwd=project_path,
        )
        if r.returncode == 0:
            results.append(rubric_item("S8",
                "No critical CVEs in dependencies",
                "pass",
                "pip audit found no vulnerabilities",
                severity="major"))
        else:
            # Check if pip audit is not available
            if "No module named" in r.stderr or "not found" in r.stderr.lower():
                results.append(rubric_item("S8",
                    "No critical CVEs in dependencies",
                    "insufficient_evidence",
                    "pip-audit not installed",
                    severity="major"))
            else:
                results.append(rubric_item("S8",
                    "No critical CVEs in dependencies",
                    "fail",
                    f"pip audit found issues: {r.stdout[:300]}",
                    severity="major"))
    except Exception as e:
        results.append(rubric_item("S8",
            "No critical CVEs in dependencies",
            "insufficient_evidence",
            f"Could not run dependency audit: {e}",
            severity="major"))

    # S9: Server binds localhost by default (minor)
    server_path = os.path.join(src_dir, "server.py")
    if os.path.exists(server_path):
        with open(server_path, "r") as f:
            server_content = f.read()
        # Check default host binding
        if '0.0.0.0' in server_content:
            # Check if it's the default or configurable
            if 'host' in server_content.lower():
                results.append(rubric_item("S9",
                    "Server binds localhost by default",
                    "fail",
                    "server.py references 0.0.0.0 — may bind to all interfaces by default",
                    severity="minor"))
            else:
                results.append(rubric_item("S9",
                    "Server binds localhost by default",
                    "fail",
                    "server.py uses 0.0.0.0",
                    severity="minor"))
        elif '127.0.0.1' in server_content or 'localhost' in server_content:
            results.append(rubric_item("S9",
                "Server binds localhost by default",
                "pass",
                "Server defaults to localhost/127.0.0.1 binding",
                severity="minor"))
        else:
            results.append(rubric_item("S9",
                "Server binds localhost by default",
                "insufficient_evidence",
                "Could not determine default bind address from server.py",
                severity="minor"))
    else:
        results.append(rubric_item("S9",
            "Server binds localhost by default",
            "insufficient_evidence",
            "server.py not found",
            severity="minor"))

    # S10: Webhook URLs validated — no SSRF (major)
    hooks_path = os.path.join(src_dir, "hooks.py")
    if os.path.exists(hooks_path):
        with open(hooks_path, "r") as f:
            hooks_content = f.read()
        # Check if webhook/notify URLs are validated
        has_url_validation = any(term in hooks_content for term in
            ["urlparse", "validate_url", "allowed_hosts", "localhost", "127.0.0.1"])
        if has_url_validation:
            results.append(rubric_item("S10",
                "Webhook URLs validated (no SSRF)",
                "pass",
                "hooks.py contains URL validation logic",
                severity="major"))
        else:
            results.append(rubric_item("S10",
                "Webhook URLs validated (no SSRF)",
                "fail",
                "hooks.py does not validate webhook URLs — potential SSRF risk",
                severity="major"))
    else:
        # Check config.py or server.py for notify_url handling
        config_path = os.path.join(src_dir, "config.py")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg_content = f.read()
            if "notify_url" in cfg_content:
                has_validation = "urlparse" in cfg_content or "validate" in cfg_content
                results.append(rubric_item("S10",
                    "Webhook URLs validated (no SSRF)",
                    "pass" if has_validation else "fail",
                    f"config.py has notify_url {'with' if has_validation else 'without'} URL validation",
                    severity="major"))
            else:
                results.append(rubric_item("S10",
                    "Webhook URLs validated (no SSRF)",
                    "insufficient_evidence",
                    "No webhook/notify_url handling found",
                    severity="major"))
        else:
            results.append(rubric_item("S10",
                "Webhook URLs validated (no SSRF)",
                "insufficient_evidence",
                "hooks.py and config.py not found",
                severity="major"))

    # Compute scores
    pass_count = sum(1 for r in results if r["result"] == "pass")
    fail_count = sum(1 for r in results if r["result"] == "fail")
    insufficient_count = sum(1 for r in results if r["result"] == "insufficient_evidence")
    denominator = pass_count + fail_count
    score_pct = round(pass_count / denominator * 100) if denominator > 0 else 0

    blocker_items = [r for r in results if r["severity"] == "blocker" and r["result"] == "fail"]
    has_blocker = len(blocker_items) > 0
    blocker_ids = [r["id"] for r in blocker_items]

    output = {
        "dimension": "security",
        "rubric_results": results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
        "score_pct": score_pct,
        "has_blocker": has_blocker,
        "blocker_ids": blocker_ids,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
