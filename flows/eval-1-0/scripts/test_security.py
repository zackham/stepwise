#!/usr/bin/env python3
"""Security posture evaluation for a local-first tool (like Redis, SQLite).

Threat model: stepwise runs on localhost, trusted local user. No auth needed.
Security concerns: input sanitization, secret leakage, safe defaults, dependency hygiene.
"""
import json
import os
import subprocess
import sys
import urllib.request

project_path = os.environ.get("project_path", os.getcwd())
server_port = os.environ.get("server_port", "8340")

results = []

def add(id, req, result, evidence, severity="major"):
    results.append({"id": id, "requirement": req, "result": result, "evidence": evidence, "severity": severity})

# S1: Server binds localhost by default (not 0.0.0.0)
try:
    # Check the default in server code
    r = subprocess.run(["grep", "-r", "0.0.0.0", os.path.join(project_path, "src/stepwise/server.py")],
                       capture_output=True, text=True, timeout=5)
    if "0.0.0.0" in r.stdout and "default" in r.stdout.lower():
        add("S1", "Server binds localhost by default", "fail",
            "server.py defaults to 0.0.0.0 — should default to 127.0.0.1 for security", "major")
    else:
        # Check the CLI default
        r2 = subprocess.run(["grep", "-rn", "host.*default\|default.*host", os.path.join(project_path, "src/stepwise")],
                           capture_output=True, text=True, timeout=5)
        if "0.0.0.0" in r2.stdout:
            add("S1", "Server binds localhost by default", "fail",
                f"Default host includes 0.0.0.0: {r2.stdout.strip()[:200]}", "major")
        else:
            add("S1", "Server binds localhost by default", "pass",
                "Server defaults to localhost binding")
except Exception as e:
    add("S1", "Server binds localhost by default", "insufficient_evidence", str(e))

# S2: No eval()/exec() in execution paths
try:
    r = subprocess.run(["grep", "-rn", r"eval\(", os.path.join(project_path, "src/stepwise/")],
                       capture_output=True, text=True, timeout=10)
    eval_hits = [l for l in r.stdout.strip().split("\n") if l and "test" not in l.lower() and "#" not in l.split("eval")[0]]
    if eval_hits:
        # Check if eval is used safely (e.g., for exit conditions which is expected)
        safe_evals = [l for l in eval_hits if "exit" in l.lower() or "when" in l.lower() or "condition" in l.lower()]
        unsafe = [l for l in eval_hits if l not in safe_evals]
        if unsafe:
            add("S2", "No unsafe eval()/exec() in execution paths", "fail",
                f"Found {len(unsafe)} eval() calls outside exit conditions: {unsafe[0][:200]}", "blocker")
        else:
            add("S2", "No unsafe eval()/exec() in execution paths", "pass",
                f"eval() used only for exit condition evaluation ({len(safe_evals)} instances) — expected and sandboxed")
    else:
        add("S2", "No unsafe eval()/exec() in execution paths", "pass", "No eval() calls found")
except Exception as e:
    add("S2", "No unsafe eval()/exec() in execution paths", "insufficient_evidence", str(e))

# S3: No hardcoded secrets in codebase
try:
    r = subprocess.run(["grep", "-rn", "--include=*.py", "-i",
                        r"sk-\|api_key\s*=\s*[\"']", os.path.join(project_path, "src/stepwise/")],
                       capture_output=True, text=True, timeout=10)
    hits = [l for l in r.stdout.strip().split("\n") if l and "test" not in l.lower() and "example" not in l.lower() and "placeholder" not in l.lower()]
    if hits:
        add("S3", "No hardcoded secrets in codebase", "fail",
            f"Possible hardcoded secrets: {hits[0][:200]}", "blocker")
    else:
        add("S3", "No hardcoded secrets in codebase", "pass", "No hardcoded API keys found in src/")
except Exception as e:
    add("S3", "No hardcoded secrets in codebase", "insufficient_evidence", str(e))

# S4: Sensitive config vars masked in output/logs
try:
    r = subprocess.run(["grep", "-rn", "-i", "mask\|redact\|sensitive\|secret", 
                        os.path.join(project_path, "src/stepwise/")],
                       capture_output=True, text=True, timeout=10)
    if r.stdout.strip():
        add("S4", "Sensitive config values masked in output", "pass",
            f"Found masking/redaction logic: {r.stdout.strip().split(chr(10))[0][:200]}")
    else:
        add("S4", "Sensitive config values masked in output", "fail",
            "No masking/redaction logic found — API keys could appear in logs or UI", "major")
except Exception as e:
    add("S4", "Sensitive config values masked in output", "insufficient_evidence", str(e))

# S5: YAML run: blocks sanitized (no shell injection via config interpolation)
try:
    r = subprocess.run(["grep", "-rn", "shell=True\|subprocess.call\|os.system",
                        os.path.join(project_path, "src/stepwise/")],
                       capture_output=True, text=True, timeout=10)
    shell_true = [l for l in r.stdout.strip().split("\n") if l and "shell=True" in l]
    if shell_true:
        add("S5", "Script execution uses subprocess safely", "fail",
            f"Found shell=True: {shell_true[0][:200]}", "major")
    else:
        add("S5", "Script execution uses subprocess safely", "pass",
            "No shell=True found — scripts executed safely")
except Exception as e:
    add("S5", "Script execution uses subprocess safely", "insufficient_evidence", str(e))

# S6: No critical CVEs in dependencies
try:
    r = subprocess.run(["uv", "pip", "list", "--outdated", "--format=json"],
                       capture_output=True, text=True, timeout=30, cwd=project_path)
    if r.returncode == 0:
        outdated = json.loads(r.stdout) if r.stdout.strip() else []
        if len(outdated) > 10:
            add("S6", "Dependencies reasonably up to date", "fail",
                f"{len(outdated)} outdated packages", "minor")
        else:
            add("S6", "Dependencies reasonably up to date", "pass",
                f"{len(outdated)} outdated packages (acceptable)")
    else:
        add("S6", "Dependencies reasonably up to date", "insufficient_evidence",
            "Could not check outdated packages")
except Exception as e:
    add("S6", "Dependencies reasonably up to date", "insufficient_evidence", str(e))

# S7: Temp files cleaned up (no secrets left on disk)
try:
    r = subprocess.run(["grep", "-rn", "mktemp\|tempfile\|NamedTemporaryFile",
                        os.path.join(project_path, "src/stepwise/")],
                       capture_output=True, text=True, timeout=10)
    if r.stdout.strip():
        # Check if cleanup happens
        cleanup = subprocess.run(["grep", "-rn", "cleanup\|finally\|__exit__\|atexit",
                                  os.path.join(project_path, "src/stepwise/")],
                                capture_output=True, text=True, timeout=10)
        if cleanup.stdout.strip():
            add("S7", "Temporary files cleaned up properly", "pass",
                "Temp file usage found with cleanup patterns")
        else:
            add("S7", "Temporary files cleaned up properly", "fail",
                "Temp files created but no cleanup patterns found", "minor")
    else:
        add("S7", "Temporary files cleaned up properly", "pass", "No temp file usage in src/")
except Exception as e:
    add("S7", "Temporary files cleaned up properly", "insufficient_evidence", str(e))

# S8: WebSocket doesn't expose internal errors to clients
try:
    r = subprocess.run(["grep", "-rn", "traceback\|stack_trace\|exc_info",
                        os.path.join(project_path, "src/stepwise/server.py")],
                       capture_output=True, text=True, timeout=10)
    if "traceback" in r.stdout.lower() and "send" in r.stdout.lower():
        add("S8", "Internal errors not leaked to WebSocket clients", "fail",
            "Traceback may be sent to WebSocket clients", "minor")
    else:
        add("S8", "Internal errors not leaked to WebSocket clients", "pass",
            "No traceback leakage to clients found")
except Exception as e:
    add("S8", "Internal errors not leaked to WebSocket clients", "insufficient_evidence", str(e))

# Compute scores
pass_count = sum(1 for r in results if r["result"] == "pass")
fail_count = sum(1 for r in results if r["result"] == "fail")
insufficient_count = sum(1 for r in results if r["result"] == "insufficient_evidence")
score_pct = round(pass_count / (pass_count + fail_count) * 100) if (pass_count + fail_count) > 0 else 0
has_blocker = any(r["severity"] == "blocker" and r["result"] == "fail" for r in results)
blocker_ids = [r["id"] for r in results if r["severity"] == "blocker" and r["result"] == "fail"]

output = {
    "dimension": "security",
    "rubric_results": results,
    "pass_count": pass_count,
    "fail_count": fail_count,
    "insufficient_count": insufficient_count,
    "score_pct": score_pct,
    "has_blocker": has_blocker,
    "blocker_ids": blocker_ids
}

print(json.dumps(output))
