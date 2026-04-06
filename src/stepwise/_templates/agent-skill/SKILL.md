---
name: stepwise
description: Stepwise workflow orchestration — run, create, and manage FLOW.yaml workflows. Activate when user mentions flows, workflows, pipelines, stepwise, FLOW.yaml, or asks "what flows do we have".
---

# Stepwise

`stepwise` is a standalone CLI installed on PATH. Do not prefix with `uv run`, `python -m`, or any wrapper.

## Flow Discovery

Run `stepwise agent-help` once per session to get the complete, always-current reference: available flows with their inputs/outputs, full CLI reference, and job staging examples.

**`agent-help` is the source of truth for dispatch** — it covers every flow's description, inputs, outputs, and the full CLI reference. You do not need to read FLOW.yaml files, run `--help` on subcommands, or research flows through any other means.

**Kits** group related flows (e.g., `swdev` contains plan-light, plan, plan-strong, implement, fast-implement). `agent-help` shows kit names with usage/composition guidance. Use `stepwise agent-help <kit>` for full flow details within a kit. Reference kit flows as `kit/flow` — e.g., `stepwise run swdev/plan-light --wait`.

## Before You Dispatch

**Read flow descriptions first.** Each flow's description (from `agent-help`) explains what the flow handles autonomously and what the caller provides. If a flow says it explores the codebase, it explores the codebase. If it says it handles research, it handles research. Match your prep work to what the flow actually needs from you.

**Write specs from what you already know.** The `spec` and `topic` inputs are the most important things you control. Draw from conversation context — vision docs, roadmap docs, prior discussion, user input. Include requirements, constraints, known design decisions, and references to relevant documents. The conversation is the spec source; the flow handles everything else. A thin spec produces thin results.

**Don't pre-explore when flows self-ground.** Check the flow's description: if it says it handles codebase exploration or research, write your spec from conversation knowledge and let the flow's agents do the grounding. Only pre-explore if the flow expects caller-provided codebase context, or the user directs you to.

**Always pass `--name`.** Format: `"verb: noun"` — e.g., `"research: phonics pedagogy"`, `"plan: auth overhaul"`, `"impl: spelling autonomy"`.

**Phase multi-job work when data doesn't auto-wire.** If a downstream flow needs upstream results folded into its spec (not just a file path), stage in phases: run upstream first, review output, then stage downstream with enriched specs. Don't pre-wire everything when the data path requires human judgment.

**Do NOT:**
- Prefix `stepwise` with `uv run`, `python -m`, or any wrapper — it's on PATH
- Read FLOW.yaml files — `agent-help` has everything needed for dispatch
- Run `--help` on stepwise subcommands — `agent-help` covers the full CLI
- Explore the target codebase before dispatching if the flow handles exploration itself — check the flow's description first

## Running a Single Flow

Always run with `--wait` as a **background process** so your session stays free:

```bash
stepwise run <flow> --wait --name "verb: noun" --input key=value
```

Use `run_in_background=True` (or equivalent) in your shell tool. You get automatic notification with full JSON results on completion, failure, or suspension.

## Multi-Job DAGs (research → plan → implement)

When a task has multiple phases or parallel workstreams, use job staging. **Run commands interactively, one at a time** — don't write bash scripts. Interactive execution lets you see output, catch errors, and adapt.

```bash
# 1. Create each job — note the ID from the JSON output
stepwise job create <flow> \
  --input k=v --group mygroup --name "verb: noun" \
  --output json
# Returns {"id": "abc123", ...}

# 2. Wire dependencies (two methods)
# a. Data wiring — passes data AND auto-creates dependency:
stepwise job create <flow> \
  --input plan_file=<upstream-job-id>.plan_file \
  --group mygroup --name "impl: feature" --output json

# b. Ordering only (no data flow):
stepwise job dep <downstream-id> --after <upstream-id>

# 3. Review and release
stepwise job show --group mygroup
stepwise job run --group mygroup --wait
```

If you need IDs in shell variables: `... --output json | jq -r .id`

### Data wiring

- `--input plan_file=<upstream-job-id>.plan_file` — passes data AND auto-creates dependency
- `stepwise job dep A --after B` — ordering only, no data flow

Jobs in the same group with no dependencies run in parallel.

### Job run flags

`stepwise job run` supports the same control flags as `stepwise run`:

- `--wait` — block until completion, JSON output on stdout (for groups, waits for ALL jobs)
- `--async` — fire-and-forget, returns job IDs immediately
- `--notify URL` — webhook notification on completion/failure
- `--notify-context JSON` — extra context to include in webhook payload
- `--max-concurrent N` — limit concurrent jobs in a group (requires `--group`)

Use `--wait` with `run_in_background=True` for non-blocking dispatch with automatic notification.

## Suspensions

When a flow pauses for human input (exit code 5):
```bash
stepwise fulfill <run-id> '{"field": "value"}' --wait
```

## Debugging & Recovery

### Inspect a failed job
```bash
stepwise job show <job-id>     # overview with step statuses
stepwise events <job-id>       # full event log
```

### Restart a single failed step (without rerunning the whole job)
```bash
# Via API — web UI exposes this as a button
curl -X POST http://localhost:8340/api/jobs/<job-id>/steps/<step-name>/rerun
```

### Cancel zombie runs on a failed job
```bash
stepwise cancel <job-id> --force         # cancel all RUNNING runs on a terminal job
stepwise cancel --run <run-id>           # cancel a specific step run by ID
```

### Run modes comparison
| Context | Command |
|---------|---------|
| CLI session (blocking) | `stepwise run <flow> --wait` |
| CLI session (background) | `stepwise run <flow> --wait` with `run_in_background=True` |
| Telegram/async context | `stepwise run <flow> --async --notify <webhook>` |
| Pre-staged job | `stepwise job run <id> --wait` |
| Job group | `stepwise job run --group <name> --wait` |

## Creating or Modifying Flows

Read `FLOW_REFERENCE.md` in this directory — the complete YAML spec for flow authoring. New flows: `stepwise new <name>`.

---

*Do NOT modify this file — it gets overwritten on upgrades. Add project-specific guidance to your CLAUDE.md.*
