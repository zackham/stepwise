---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage FLOW.yaml workflows. Activate when user mentions flows, workflows, pipelines, stepwise, FLOW.yaml, or asks "what flows do we have".
---

# Stepwise

1. **Run `stepwise agent-help`** to discover available flows and how to use them. This is always current.
2. **Run a flow:** `stepwise run <name> --wait --input k=v` — **always run as a background process** (e.g., `run_in_background=True` in Bash tool). This gives you automatic notification on completion/failure/suspension with full JSON results while keeping your session free for conversation. Never block your session with a foreground `--wait`.
3. **Handle suspensions:** When a flow pauses for input (exit code 5), use `stepwise fulfill <run-id> '{"field": "value"}'` to continue.
4. **Create or modify flows:** Read `FLOW_REFERENCE.md` in this directory — it's the complete YAML spec for understanding, creating, and modifying flows. New flows: `stepwise new <name>`.
5. **Do NOT modify this file.** It gets overwritten on upgrades. Add project-specific guidance to your CLAUDE.md.
