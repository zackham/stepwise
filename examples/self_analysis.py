#!/usr/bin/env python3
"""Run a real multi-step workflow that analyzes the stepwise codebase itself.

Exercises: agent executor (Claude CLI), script executor, exit rules with loop,
StepLimits, data flow between steps, mixed sync/async execution.

Flow:
  gather_stats (script) → analyze (agent) → validate (script) → write_report (agent)
                               ↑                  |
                               └── loop if bad ────┘
"""

import json
import sys
import urllib.request
import urllib.error

API = "http://localhost:8340"


def api_post(path: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}")
        sys.exit(1)
STEPWISE_DIR = "/home/zack/work/stepwise"

workflow = {
    "steps": {
        # ── Step 1: Gather codebase stats (script, sync) ─────────────
        "gather_stats": {
            "name": "gather_stats",
            "outputs": ["file_count", "total_lines", "top_files"],
            "executor": {
                "type": "script",
                "config": {
                    "command": f"""python3 -c "
import os, json
base = '{STEPWISE_DIR}/src/stepwise'
files = []
total = 0
for f in sorted(os.listdir(base)):
    if f.endswith('.py'):
        path = os.path.join(base, f)
        with open(path) as fh:
            n = len(fh.readlines())
        files.append({{'name': f, 'lines': n}})
        total += n
files.sort(key=lambda x: -x['lines'])
print(json.dumps({{'file_count': len(files), 'total_lines': total, 'top_files': files[:5]}}))
"
""",
                },
                "decorators": [],
            },
            "inputs": [],
            "after": [],
            "exit_rules": [],
            "idempotency": "idempotent",
        },

        # ── Step 2: Agent analyzes the codebase (async) ──────────────
        "analyze": {
            "name": "analyze",
            "outputs": ["patterns", "complexity_score", "top_concern"],
            "executor": {
                "type": "agent",
                "config": {
                    "prompt": (
                        "You are analyzing the Stepwise workflow engine codebase.\n\n"
                        "Stats: $file_count Python files, $total_lines total lines.\n"
                        "Top files by size: $top_files\n\n"
                        "Read src/stepwise/engine.py and src/stepwise/models.py.\n\n"
                        "Then create a file called `analysis.json` in the current directory "
                        "with exactly this structure:\n"
                        "```json\n"
                        '{\n'
                        '  "patterns": [\n'
                        '    {"name": "pattern name", "description": "1-2 sentence description"}\n'
                        '  ],\n'
                        '  "complexity_score": 7,\n'
                        '  "top_concern": "one sentence about the biggest complexity concern"\n'
                        '}\n'
                        "```\n\n"
                        "Requirements:\n"
                        "- Include at least 3 architectural patterns you observe\n"
                        "- complexity_score must be 1-10 (integer)\n"
                        "- Be specific and technical, not generic\n"
                        "- Write ONLY analysis.json, nothing else"
                    ),
                    "output_mode": "file",
                    "output_path": "analysis.json",
                    "working_dir": STEPWISE_DIR,
                    "allowed_tools": ["Read", "Glob", "Grep", "Write"],
                    "max_turns": 10,
                },
                "decorators": [],
            },
            "inputs": [
                {"local_name": "file_count", "source_step": "gather_stats", "source_field": "file_count"},
                {"local_name": "total_lines", "source_step": "gather_stats", "source_field": "total_lines"},
                {"local_name": "top_files", "source_step": "gather_stats", "source_field": "top_files"},
            ],
            "after": [],
            "exit_rules": [],
            "idempotency": "idempotent",
            "limits": {"max_cost_usd": 2.00, "max_duration_minutes": 5},
        },

        # ── Step 3: Validate the analysis (script, sync) ────────────
        "validate": {
            "name": "validate",
            "outputs": ["valid", "feedback", "pattern_count"],
            "executor": {
                "type": "script",
                "config": {
                    "command": """python3 -c "
import json, os
inp = json.load(open(os.environ['JOB_ENGINE_INPUTS']))
patterns = inp.get('patterns', [])
score = inp.get('complexity_score', 0)
concern = inp.get('top_concern', '')
ok_patterns = isinstance(patterns, list) and len(patterns) >= 3
ok_score = isinstance(score, (int, float)) and 1 <= score <= 10
ok_concern = isinstance(concern, str) and len(concern) > 10
valid = ok_patterns and ok_score and ok_concern
issues = []
if not ok_patterns: issues.append(f'need >=3 patterns, got {len(patterns) if isinstance(patterns, list) else 0}')
if not ok_score: issues.append(f'score must be 1-10 integer, got {score}')
if not ok_concern: issues.append(f'top_concern too short or missing')
feedback = 'PASS' if valid else 'FAIL: ' + '; '.join(issues)
print(json.dumps({'valid': valid, 'feedback': feedback, 'pattern_count': len(patterns) if isinstance(patterns, list) else 0}))
"
""",
                },
                "decorators": [],
            },
            "inputs": [
                {"local_name": "patterns", "source_step": "analyze", "source_field": "patterns"},
                {"local_name": "complexity_score", "source_step": "analyze", "source_field": "complexity_score"},
                {"local_name": "top_concern", "source_step": "analyze", "source_field": "top_concern"},
            ],
            "after": [],
            "exit_rules": [
                {
                    "name": "quality_pass",
                    "type": "field_match",
                    "config": {"field": "valid", "value": True, "action": "advance"},
                    "priority": 10,
                },
                {
                    "name": "quality_fail",
                    "type": "field_match",
                    "config": {
                        "field": "valid",
                        "value": False,
                        "action": "loop",
                        "target": "analyze",
                        "max_iterations": 2,
                    },
                    "priority": 5,
                },
            ],
            "idempotency": "idempotent",
        },

        # ── Step 4: Agent writes final report (async) ────────────────
        "write_report": {
            "name": "write_report",
            "outputs": ["result"],
            "executor": {
                "type": "agent",
                "config": {
                    "prompt": (
                        "You just completed an analysis of the Stepwise workflow engine.\n\n"
                        "Analysis found $pattern_count architectural patterns. "
                        "Validation result: $feedback\n\n"
                        "Write a concise 3-4 sentence engineering summary of the codebase's "
                        "architectural health. Be specific and technical. "
                        "Output ONLY the summary text, nothing else."
                    ),
                    "output_mode": "stream_result",
                    "working_dir": STEPWISE_DIR,
                    "allowed_tools": ["Read"],
                    "max_turns": 3,
                },
                "decorators": [],
            },
            "inputs": [
                {"local_name": "pattern_count", "source_step": "validate", "source_field": "pattern_count"},
                {"local_name": "feedback", "source_step": "validate", "source_field": "feedback"},
            ],
            "after": ["validate"],
            "exit_rules": [],
            "idempotency": "idempotent",
            "limits": {"max_cost_usd": 1.00, "max_duration_minutes": 3},
        },
    }
}


def main():
    # Create job
    print("Creating job...")
    job = api_post("/api/jobs", {
        "objective": "Stepwise Self-Analysis: analyze own codebase architecture",
        "workflow": workflow,
        "workspace_path": STEPWISE_DIR,
    })
    job_id = job["id"]
    print(f"Created job: {job_id}")

    # Start job
    print("Starting job...")
    api_post(f"/api/jobs/{job_id}/start", {})

    print(f"\nJob started! Watch it at: http://stepwise.localhost")
    print(f"Job ID: {job_id}")
    print(f"\nWorkflow:")
    print(f"  1. gather_stats (script) - count source files and lines")
    print(f"  2. analyze (agent) - Claude reads engine.py + models.py, writes analysis.json")
    print(f"  3. validate (script) - check analysis quality, loop back if bad")
    print(f"  4. write_report (agent) - Claude writes final architecture summary")
    print(f"\nExit rules: validate loops to analyze (max 2) if quality check fails")
    print(f"Limits: analyze=$2/5min, write_report=$1/3min")


if __name__ == "__main__":
    main()
