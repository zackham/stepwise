---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage .flow.yaml workflows. Use when working with multi-step pipelines, DAGs, or agent/human/LLM workflows.
---

# Stepwise

Workflow orchestration for agents and humans. Runs multi-step pipelines with LLM, agent, human, and script executors.

## When to Use

Activate when:
- User mentions stepwise, flows, workflows, or pipelines
- Working with `.flow.yaml` files
- User wants to run, create, or manage a workflow
- User asks about step orchestration, DAGs, or multi-step processes

## Using Flows

To discover available flows and how to run them:

```bash
stepwise agent-help
```

This outputs the current flow catalog with inputs, outputs, and exact run commands.

Flows can be single files (`my-flow.flow.yaml`) or directories (`my-flow/FLOW.yaml` with co-located scripts and prompts). The CLI accepts flow names — it resolves them across `flows/`, `.stepwise/flows/`, and the project root.

### CLI Quick Reference

```
stepwise run <flow> --wait --var k=v          # run, block, get JSON result
stepwise run <flow> --watch                   # run with live web UI
stepwise run <flow> --async                   # fire-and-forget, returns job_id
stepwise new <name>                           # create flows/<name>/FLOW.yaml scaffold
stepwise output <job-id>                      # retrieve outputs
stepwise fulfill <run-id> '{"field": "val"}'  # satisfy human step
stepwise status <job-id>                      # check progress
stepwise schema <flow>                        # input/output schema as JSON
stepwise validate <flow>                      # syntax check
stepwise share <flow>                         # publish to registry
stepwise get <name>                           # download from registry
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

### Output Format (--wait mode)

Success (exit 0):
```json
{"status": "completed", "job_id": "...", "outputs": [...], "cost_usd": 0.05, "duration_seconds": 45}
```

Failure (exit 1):
```json
{"status": "failed", "job_id": "...", "error": "...", "failed_step": "...", "completed_outputs": [...]}
```

Timeout (exit 3):
```json
{"status": "timeout", "job_id": "...", "timeout_seconds": 300, "suspended_at_step": "..."}
```

## Creating & Modifying Flows

Read `FLOW_REFERENCE.md` in this skill directory for the complete YAML format specification including:
- Step definitions, executor types (script, human, llm, agent)
- Input bindings, exit rules, loop patterns
- For-each (fan-out/fan-in), route steps (conditional dispatch)
- Decorators, limits, idempotency modes
- Complete examples and validation checklist
