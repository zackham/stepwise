---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage FLOW.yaml workflows. Activate when user mentions flows, workflows, pipelines, stepwise, FLOW.yaml, or asks "what flows do we have".
---

# Stepwise — Rules

1. **To find flows, ALWAYS run `stepwise agent-help`.** Never glob/search for flow files — flows live in multiple locations (local, registry cache, global) that only the CLI resolves.
2. **To create a flow, use `stepwise new <name>`.** This creates `flows/<name>/FLOW.yaml`.
3. **To run a flow:** `stepwise run <name> --wait --var k=v` — blocks until done, returns JSON. No server needed.
   For concurrent jobs, use `--async` and poll with `stepwise status <job-id>`.
4. **After listing flows, be helpful.** Offer to run them. Always mention what inputs (`--var`) are needed.
5. **Handle suspended steps.** When a job suspends (external step waiting for input):
   ```bash
   stepwise status <job-id>                          # see suspended step
   stepwise fulfill <run-id> '{"field": "value"}'    # provide input
   ```
6. **Do NOT modify this file.** It gets overwritten on `stepwise init` and upgrades. For project-specific flow guidance, add it to your project's CLAUDE.md or equivalent.

# Stepwise

Workflow orchestration for agents and humans. Runs multi-step pipelines with LLM, agent, external, poll, and script executors. Works out of the box — no server, no setup.

For the web DAG viewer, webhook notifications, or HTTP API access, see `stepwise server start --help`.

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

### CLI Quick Reference

```
# Running flows
stepwise run <flow> --wait --var k=v          # run, block, get JSON result
stepwise run <flow> --async                   # background, returns job_id
stepwise run <flow> --watch                   # run with live web UI
stepwise chain <flow1> <flow2> --var k=v      # compose flows into pipeline

# Monitoring
stepwise status <job-id> --output json        # job status (DAG view)
stepwise output <job-id>                      # terminal outputs
stepwise output <job-id> <step-name>          # single step output
stepwise tail <job-id>                        # stream live events
stepwise logs <job-id>                        # full event history

# Interaction
stepwise fulfill <run-id> '{"field": "val"}'  # satisfy external step
stepwise list --suspended --output json       # global suspension inbox
stepwise wait <job-id>                        # block on existing job
stepwise cancel <job-id>                      # cancel a running job

# Flow management
stepwise new <name>                           # create flow scaffold
stepwise validate <flow>                      # syntax check
stepwise schema <flow>                        # input/output schema

# Registry
stepwise run @author:flow-name                # run registry flow
stepwise share <flow>                         # publish to registry
stepwise login                                # authenticate with GitHub
stepwise get @author:flow-name                # download from registry
stepwise search "query"                       # search registry
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Completed successfully |
| 1 | Flow execution failed |
| 2 | Input validation error |
| 3 | Timeout (--wait mode) |
| 4 | Cancelled (--wait mode) |
| 5 | Suspended (all progress blocked by external steps) |

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

## Registry Flows

Flows fetched from the registry (via `stepwise get @author:name`) are cached in `.stepwise/registry/@author/slug/`. These are **read-only** — never modify them in place.

**To fork a registry flow:**
1. Copy from `.stepwise/registry/@author/slug/` to `flows/your-name/`
2. Modify freely — it's now a local flow

## Creating & Modifying Flows

Read `FLOW_REFERENCE.md` in this skill directory for the complete YAML format specification including:
- Step definitions, executor types (script, external, poll, llm, agent)
- Input bindings, exit rules, loop patterns
- For-each (fan-out/fan-in), route steps (conditional dispatch)
- Decorators, limits, idempotency modes
- Complete examples and validation checklist
