# Changelog

All notable changes to Stepwise are documented here. Versions are tagged milestones, not semver releases (yet).

## [Unreleased]

- `httpx` is now a core dependency (the `[llm]` extra has been removed)
- `install.sh` — universal `curl | sh` installer that bootstraps `uv` + `stepwise`
- `stepwise self-update` — upgrade to the latest version (auto-detects uv/pipx/pip)
- `stepwise serve` auto-picks a random port when 8340 is already in use

## [M9] — 2026-03-12

**Flow Sharing** — publish, discover, and reuse flows via the stepwise.run registry.

### Added
- `stepwise flow share <file>` — publish a flow to the registry (validates first, saves update token)
- `stepwise flow get <name>` — download a flow by registry name (saves to cwd as `<name>.flow.yaml`)
- `stepwise flow search [query]` — search the registry with optional `--tag` and `--sort` filters
- `stepwise flow info <name>` — show metadata for a published flow without downloading
- `--update` flag for `flow share` — update a previously published flow using stored token
- `--author` flag for `flow share` — override the author name
- `--force` flag for `flow get` — overwrite existing local file
- `--output json` flag for `flow search` — machine-readable output
- `registry_client.py` — full registry client module (fetch, publish, search, update, cache, tokens)
- Parse-time `@author:name` resolution — registry refs in `routes:` and `for_each:` are fetched and baked inline at YAML load time (engine never sees registry refs)
- Author verification — `@alice:fast-pipeline` checks that the fetched flow's author matches `alice`
- `flow_ref` field on `RouteSpec` — preserves the original `@author:name` string for provenance after resolution
- Disk cache at `~/.cache/stepwise/flows/` — avoids repeated network calls for the same flow
- Token management at `~/.config/stepwise/tokens.json` (file mode 0600)
- `STEPWISE_REGISTRY_URL` env var — override the default `https://stepwise.run` registry
- 22 new tests (client, cache, tokens, parse-time resolution, author mismatch, round-trip serialization)

### Changed
- `stepwise flow get <url>` unchanged — URLs still download directly
- Route and for-each flow resolution now tries `@` refs as registry lookups before raising errors
- Old "coming soon" stubs in CLI replaced with real implementations

## [M8] — 2026-03-12

**Route Steps** — conditional sub-flow dispatch.

### Added
- `routes:` block on step definitions — dispatch to different sub-flows based on upstream output
- First-match semantics with `when:` expressions and optional `default` route
- Three flow source types: inline blocks, local file paths (loaded at parse time), registry refs (`@author:name`, implemented in M9)
- File ref cycle detection using immutable set branching (sibling routes can share files)
- Output contract validation — every terminal step of each sub-flow must independently cover declared outputs
- `attempt` available in route `when:` expressions (from `store.next_attempt()`)
- Route events: `route.matched`, `route.no_match`, `route.eval_error`
- Expression errors fail the step immediately (no fallthrough to next route)
- Try/except around sub-job creation to prevent orphaned DELEGATED runs
- For-each steps now also support file path flow references
- `RouteSpec`, `RouteDefinition` data model classes
- `_launch_route()`, `_resolve_flow_ref()` engine methods
- `load_workflow_yaml()` now accepts `base_dir` and `loading_files` params for recursive loading
- 46 new tests (parsing, validation, cycle detection, execution, serialization, events)

## [M7b] — 2026-03-11

**Flows as Tools** — make flows callable by external agents via CLI.

### Added
- `stepwise schema <flow>` — generate JSON tool contracts (inputs, outputs, human steps)
- `stepwise run --wait` — blocking mode that prints a single JSON object to stdout
- `stepwise run --async` — fire-and-forget via detached background process (no server required)
- `stepwise run --output json` — headless mode with JSON result on completion
- `stepwise output <job-id>` — retrieve job outputs (`--scope full` for per-step details + cost)
- `stepwise fulfill <run-id> '<json>'` — satisfy suspended human steps from the command line
- `stepwise agent-help` — generate markdown instructions for CLAUDE.md (`--update` for in-place)
- `--var-file key=path` flag for passing large inputs without shell escaping
- `--timeout` flag for `--wait` mode with structured timeout response
- `--stdin` flag for `stepwise fulfill` (read payload from stdin)
- `--flows-dir` flag for `stepwise agent-help` (override flow discovery directory)
- Actionable error messages — missing inputs include exact `--var` flags to fix it
- Stdout purity — `--wait` prints only JSON to stdout, all logging to stderr
- Exit codes for agent callers: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled
- Partial outputs on failure — `completed_outputs` from steps that finished before the error
- Engine methods: `terminal_outputs()`, `completed_outputs()`, `suspended_step_details()`, `job_cost()`
- Agent integration guide (`docs/agent-integration.md`)
- 42 new tests (schema, CLI tools, --wait, --async, fulfill, agent-help, --var-file, --timeout)

## [M7a] — 2026-03-11

**Context Chains** — session continuity across agent steps.

### Added
- `context.py` — compile prior step conversation transcripts into XML context blocks
- `chains:` block in YAML for declaring chain groups with token budgets
- `chain` and `chain_label` fields on step definitions
- Topological ordering ensures deterministic context regardless of parallel execution
- Overflow strategies: `drop_oldest`, `drop_middle` (whole transcripts, never mid-conversation)
- Accumulation modes: `full` (all prior transcripts), `latest` (most recent only)
- Transcript capture via `acpx sessions show` in AgentExecutor
- 68 new tests

## [M6] — 2026-03-11

**HTML Reports** — self-contained execution traces.

### Added
- `stepwise run --report` generates a self-contained HTML report on completion
- `--report-output` flag for custom report file path
- SVG DAG visualization, step timeline, expandable detail panels
- Inputs, outputs, sidecar, executor metadata, and errors per step
- Cost summary across all steps
- 22 new tests

## [M5] — 2026-03-11

**For-Each Steps** — fan-out over lists.

### Added
- `for_each` step type — iterate over a list, running an embedded sub-flow per item
- `as` field for naming the current item
- `on_error: continue` or `fail_fast` control
- Each iteration runs as an independent sub-job; results collected in source order
- Parallel execution of iterations
- 28 new tests (covering serial, parallel, error handling, nested flows)

## [M4] — 2026-03-11

**Agent Executor + Async + Limits** — autonomous AI agent steps.

### Added
- `AgentExecutor` with `AcpxBackend` — run agents via ACP protocol with async polling
- `StepLimits` — cap cost, duration, or iterations per step
- `step_events` table for fine-grained observability
- `ErrorCategory` enum for structured error classification
- `cancel` API endpoint for running jobs
- WebSocket agent output streaming
- `AgentStreamView` component in web UI
- Loop guards: `_dep_will_be_superseded()` prevents premature downstream launch

### Changed
- `terminal_steps()` now excludes unconditional loop-internal steps
- `_is_step_ready()` includes loop guard to prevent infinite re-triggering

## [M3] — 2026-03-10

**LLM Executor** — single LLM calls via OpenRouter.

### Added
- `LLMExecutor` — structured output extraction from LLM API calls
- `MockLLMExecutor` — deterministic mock for testing
- OpenRouter integration with model registry and tier support
- Config management: `stepwise config set/get` for API keys and default model
- `~/.config/stepwise/config.json` configuration file

## [M2] — 2026-03-10

**Web UI + YAML + CLI** — visual execution and workflow definition.

### Added
- React web frontend (Vite, TanStack Router + Query, Tailwind 4, shadcn/ui)
- DAG visualization with dagre.js layout
- Step detail panels with real-time status updates
- Workflow builder (visual drag-and-drop)
- WebSocket for live tick updates
- YAML workflow loader (347 lines) — `.flow.yaml` format
- `stepwise run <flow>` — headless execution with terminal reporter
- `stepwise run --watch` — ephemeral server with browser UI
- `stepwise serve` — persistent server mode
- `stepwise validate` — syntax and structural validation
- `stepwise jobs`, `stepwise status`, `stepwise cancel` — job management
- `stepwise templates` — list available templates
- `stepwise flow get/share/search` — flow sharing stubs (fully implemented in M9)
- `.stepwise/` project directory (like `.git/`) with SQLite DB
- Signal handling — Ctrl+C cleanly cancels active jobs
- Flow metadata: name, description, author, version, tags
- 5 web routes: /jobs, /jobs/:id, /jobs/:id/events, /jobs/:id/tree, /builder

## [M1] — 2026-03-10

**Core Engine** — the foundation.

### Added
- DAG-based workflow engine with tick loop
- `Job`, `Step`, `StepRun`, `ExitRule`, `InputBinding` models
- `ScriptExecutor` — run shell commands, parse JSON output
- `HumanExecutor` — suspend for human input via `WatchSpec`
- Expression-based exit rules with `advance`, `loop`, `escalate`, `abandon` actions
- Loop management via supersession (new runs invalidate previous, cascading downstream)
- Parallel execution of independent steps
- `sequencing` for pure ordering without data dependencies
- `HandoffEnvelope` — structured step output (artifact + sidecar + executor metadata)
- SQLite persistence with WAL mode and crash recovery
- Decorators: timeout, retry, fallback, notification (composable per-step)
- Sub-job delegation for hierarchical workflows
- FastAPI server with REST API (27 endpoints) and WebSocket
- 235 tests

## [0.0.1] — 2026-03-08

### Added
- Initial project setup with uv
- MIT license
