# Skill: Create Stepwise Flow

## When to Use

Activate when the user asks to:
- Create, build, or define a flow
- "Make a flow that..."
- "I need a pipeline for..."
- "Set up steps to..."
- Convert a natural language process description into a Stepwise flow
- Modify or extend an existing flow definition

## What This Skill Does

Translates natural language descriptions into valid Stepwise flow definitions (`.flow.yaml` files), then optionally runs them via the CLI or creates jobs via the API.

## Flow Format Reference

Read the canonical flow reference at `src/stepwise/flow-reference.md` before generating any flow. That file is the single source of truth for the YAML format, executor types, input bindings, exit rules, loops, for-each, routes, flow steps, decorators, limits, idempotency, and complete examples.

**DO NOT read Stepwise source code to learn the YAML format.** The flow reference has everything you need.

## Your Behavior

1. Read existing flows, CLAUDE.md, and referenced files to understand project context before generating.
2. Generate `.flow.yaml` files — always YAML, never JSON.
3. Use the simplest executor that fits: `script` > `llm` > `agent`. Don't use `agent` when `llm` suffices.
4. Validate with `stepwise validate <flow>` after generating.
5. Offer to run with `stepwise run <file>` (headless) or `stepwise run <file> --watch` (web UI).

## Conversation Flow

1. **Understand** what the flow should accomplish
2. **Identify** the steps, executor types, data flow, and loop conditions
3. **Generate** a valid `.flow.yaml` file
4. **Validate** with `stepwise validate <file>`
5. **Optionally** run with `stepwise run <file>` or `stepwise run <file> --watch`

## Running Flows

```bash
# Headless — terminal output, stdin for human steps
stepwise run my-flow.flow.yaml

# With live web UI
stepwise run my-flow.flow.yaml --watch

# Pass inputs
stepwise run my-flow.flow.yaml --var topic="login flow UX" --var pr_url="https://..."
```

## Key API Endpoints

Server runs at `http://localhost:8340` (via `stepwise serve` or `stepwise run --watch`).

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs` | Create a job (body: `{objective, workflow, inputs, workspace_path}`) |
| `POST` | `/api/jobs/{id}/start` | Start a job |
| `GET` | `/api/jobs` | List jobs |
| `POST` | `/api/runs/{id}/fulfill` | Submit human step response |
| `POST` | `/api/jobs/{id}/cancel` | Cancel a job |
