---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage FLOW.yaml workflows. Activate when user mentions flows, workflows, pipelines, stepwise, FLOW.yaml, or asks "what flows do we have".
---

# Stepwise

1. **Run `stepwise agent-help`** to discover available flows and how to use them. This is always current.
2. **Run a flow:** `stepwise run <name> --wait --var k=v` — blocks until done, returns JSON.
3. **Handle suspensions:** When a flow pauses for input (exit code 5), use `stepwise fulfill <run-id> '{"field": "value"}'` to continue.
4. **Create flows:** `stepwise new <name>`, then read `FLOW_REFERENCE.md` in this directory for the YAML format.
5. **Do NOT modify this file.** It gets overwritten on upgrades. Add project-specific guidance to your CLAUDE.md.
