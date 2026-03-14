---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage .flow.yaml workflows. Use when working with multi-step pipelines, DAGs, or agent/human/LLM workflows.
---

# Stepwise

Workflow orchestration for agents and humans. Runs multi-step pipelines with LLM, agent, human, and script executors.

## When to Use

Activate when:
- User mentions stepwise, flows, workflows, or pipelines
- Working with `.flow.yaml` or `FLOW.yaml` files
- User wants to run, create, or manage a workflow
- User asks about step orchestration, DAGs, or multi-step processes

## Discovering Flows

**IMPORTANT: Always use `stepwise agent-help` to find available flows.** Do NOT search for files manually — flows live in multiple locations that only the CLI knows about (local directories, registry cache, global installs). The command outputs a complete catalog with inputs, outputs, and exact run commands.

```bash
stepwise agent-help
```

### Flow locations

Flows use directory format: `flows/<name>/FLOW.yaml` with co-located scripts and prompts alongside.

```
flows/
  my-flow/
    FLOW.yaml              # flow definition
    analyze.py             # co-located script
    prompts/system.md      # prompt file
```

The CLI resolves bare flow names across: project root → `flows/` → `.stepwise/flows/` → `~/.stepwise/flows/`.

Registry flows (downloaded via `stepwise get @author:name`) are cached in `.stepwise/registry/@author/slug/FLOW.yaml`. Run them with `stepwise run @author:flow-name`.

### Interaction Modes

1. **Automated** — `stepwise run <flow> --wait --var k=v` → JSON result on stdout
2. **Mediated** — `--wait` returns exit 5 on suspension → `fulfill <run-id> '{}' --wait` → resumes
3. **Monitoring** — `status <job-id> --output json` (DAG view), `list --suspended --output json` (inbox)
4. **Data Grab** — `output <job-id> --step a,b` (per-step), `--inputs` (step inputs), `--run <run-id>`
5. **Takeover** — `cancel <job-id> --output json`, `wait <job-id>`

### CLI Quick Reference

```
stepwise run <flow> --wait --var k=v          # run, block, get JSON result
stepwise run <flow> --watch                   # run with live web UI
stepwise run <flow> --async                   # fire-and-forget, returns job_id
stepwise new <name>                           # create flows/<name>/FLOW.yaml scaffold
stepwise status <job-id> --output json        # resolved flow status (DAG view)
stepwise output <job-id>                      # retrieve terminal outputs
stepwise output <job-id> --step a,b           # per-step outputs
stepwise output <job-id> --step a --inputs    # step inputs
stepwise output --run <run-id>                # direct run output
stepwise fulfill <run-id> '{"field": "val"}'  # satisfy human step
stepwise fulfill <run-id> '{}' --wait         # fulfill and block on job
stepwise list --suspended --output json       # global suspension inbox
stepwise wait <job-id>                        # block on existing job
stepwise cancel <job-id> --output json        # cancel with step details
stepwise schema <flow>                        # input/output schema as JSON
stepwise validate <flow>                      # syntax check
stepwise run @author:flow-name                # run a registry flow (auto-fetches)
stepwise share <flow>                         # publish to registry
stepwise get @author:flow-name                # download from registry
stepwise search "query"                       # search registry
stepwise info <name>                          # registry flow details
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Completed successfully |
| 1 | Flow execution failed |
| 2 | Input validation error |
| 3 | Timeout (--wait mode) |
| 4 | Cancelled (--wait mode) |
| 5 | Suspended (all progress blocked by human steps) |

### Output Format (--wait mode)

Success (exit 0):
```json
{"status": "completed", "job_id": "...", "outputs": {...}, "cost_usd": 0.05, "duration_seconds": 45}
```

Failure (exit 1):
```json
{"status": "failed", "job_id": "...", "error": "...", "failed_step": "...", "completed_outputs": {...}}
```

Suspended (exit 5):
```json
{"status": "suspended", "job_id": "...", "suspended_steps": [{"step": "...", "run_id": "...", "prompt": "...", "fields": [...]}]}
```

Timeout (exit 3):
```json
{"status": "timeout", "job_id": "...", "timeout_seconds": 300, "suspended_at_step": "..."}
```

## Registry Flows

Flows fetched from the registry (via `stepwise get @author:name`) are cached in `.stepwise/registry/@author/slug/`. These are **read-only** — never modify them in place.

**To run a registry flow:** `stepwise run @author:flow-name`

**To fork a registry flow for modification:**
1. Copy the directory from `.stepwise/registry/@author/slug/` to `flows/your-name/`
2. Set `author:` to the current user
3. Add `forked_from: "@author:original-name"` to the YAML metadata
4. Modify freely — it's now a local flow

**Important:** Bare flow names (e.g. `stepwise run my-flow`) only resolve to local flows. Registry flows always require the `@author:name` format.

## Creating & Modifying Flows

Read `FLOW_REFERENCE.md` in this skill directory for the complete YAML format specification including:
- Step definitions, executor types (script, human, poll, llm, agent)
- Input bindings, exit rules, loop patterns
- For-each (fan-out/fan-in), route steps (conditional dispatch)
- Decorators, limits, idempotency modes
- Complete examples and validation checklist
