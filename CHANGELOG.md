# Changelog

All notable changes to Stepwise are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-03-17

**Server Management, Config Interpolation, Expression Fixes** — structured server commands and executor parameterization.

### Added
- **`stepwise server` subcommands** — `start`, `stop`, `restart`, `status` replace the old `stepwise serve`. `--detach` for background mode, status shows PID/port/uptime/log path
- **Config interpolation** — executor config string values support `$variable` interpolation from resolved inputs, parameterizing model, command, system message, etc.
- **`stepwise diagram`** — DOT source no longer pollutes flow resolution (uses `pipe()`)

### Fixed
- **Single-output LLM steps** — skip `tool_choice` for steps with one output to prevent truncation; model responds naturally with JSON/text fallback
- **Cycle detection** — now accounts for loop back-edges, preventing false positives on valid loop targets
- **Expression namespace** — `true`/`false`/`null` aliases added (Python `True`/`False`/`None`)
- **For-each all-fail** — correct behavior when all fan-out instances fail
- **Cost reporting** — accurate token cost aggregation
- **Diagram port labels** — HTML line breaks in port node labels, no more overlap

## [0.4.0] — 2026-03-16

**Pull-Based Branching, Poll Executor, Smooth DAG Camera** — conditional workflows and a polished live view.

### Added
- **`when` conditions** — steps declare their own activation condition evaluated against resolved inputs. Mutually exclusive branches, conditional gates, and skip propagation without explicit routing
- **`any_of` input bindings** — steps can depend on any one of multiple upstream steps, enabling merge points after conditional branches
- **`SKIPPED` step status** — steps that never activate are marked SKIPPED at job settlement, with a `STEP_SKIPPED` event
- **Poll executor** — `executor: poll` runs `check_command` at `interval_seconds`; JSON dict on stdout = fulfilled. For waiting on external conditions (PR reviews, deploys, etc.)
- **DAG camera** — critically damped spring with dead zone and target blending for smooth auto-follow. Zoom-to-fit active nodes (70–100%), slower pan lerp, extended active rect for human input popovers
- **Animated layout transitions** — DAG nodes interpolate position when the layout changes (expand/collapse, new steps)
- **Flows page** — dedicated page for browsing local flows, split from the editor
- **Local flow info panel** — three-column layout showing flow metadata, executor types, and description
- **Zombie job cleanup** — server fails jobs owned by dead processes on startup
- **`acpx` auto-install** — install script installs acpx when npm is available
- **`stepwise diagram`** — CLI command to render flow DAGs via graphviz

### Changed
- **Branching model rewrite** — removed the entire route system (`RouteSpec`, `RouteDefinition`, `_launch_route`). Replaced with pure-pull `when`-based branching. Exit rule `advance` with `target` replaced by `when` conditions on downstream steps
- Welcome flow rewritten using DAG branching primitives
- EditorPage simplified after FlowsPage extraction

### Fixed
- NaN/Infinity in SVG from layout transition and dagre
- Container port labels overlapping header and clipping at bottom
- Output port labels overflowing expanded container bottom
- `any_of` input bindings handled correctly in DAG layout
- Null job status handled gracefully in store

## [0.3.1] — 2026-03-14

**CLI display overhaul** — rich, readable terminal output.

### Added
- **Live block rendering** — active steps redraw in place, completed steps scroll up permanently. No more interleaving of parallel for-each items
- **Output previews** — completed steps show `→ key: value` inline
- **For-each item labels** — `[data-model]`, `[api-routes]`, etc. group sub-steps under their item
- **Loop icon** — `⟳` for retry attempts
- **`stepwise welcome`** — interactive post-install demo prompt

### Changed
- Install script uses `--force --reinstall` for reliable upgrades
- Cleaner post-install message with copy-pasteable commands

## [0.3.0] — 2026-03-14

**Async Engine, Live DAG, Agent Emit Flow** — real-time execution with dynamic workflows.

### Added
- **AsyncEngine** — event-driven engine replaces tick-based polling. Parallel step dispatch via `asyncio.Queue`, executor runs in thread pool. No more tick interval
- **Agent Emit Flow** — agent steps with `emit_flow: true` can dynamically create sub-workflows by writing `.stepwise/emit.flow.yaml`. Engine launches emitted flow as sub-job and propagates results back. Supports iterative delegation with exit rule loops
- **CLI Server Delegation** — `stepwise run` auto-delegates to a running server for lower latency. `--wait` and `--async` modes use WebSocket for live updates, falling back to REST polling
- **Job Ownership** — `created_by`, `runner_pid`, `heartbeat_at` fields track who owns each job. Stale detection for orphaned CLI jobs. Server adoption via `POST /api/jobs/{id}/adopt`
- **Typed Human Inputs** — `OutputFieldSchema` with `type: choice|number|text`, validation, auto-generated UI controls in web and CLI
- **Follow-Flow Mode** — DAG view auto-pans to track active steps at 100% zoom
- **Welcome Flow** — interactive product tour: plan, implement (for-each), test (retry loops), review (route steps), deploy. Available as `@stepwise:welcome` from registry
- **`STEPWISE_ATTEMPT`** — attempt number exposed as env var to script executors
- **Inline Human Input** — human step panels render directly below suspended DAG nodes
- **Auto-Expand Sub-Jobs** — delegated sub-flows and for-each instances expand automatically in the DAG
- **Animated DAG Edges** — intake and loopback edges animate with glow and flowing dashes for active steps
- **Data Flow Labels** — artifact values shown on DAG edges with hover tooltips
- **Settings Page** — model labels, API keys, default model configuration in the web UI
- **Billing Mode** — `billing: subscription` skips cost limit enforcement for subscription users
- **IOAdapter** — unified CLI output abstraction (PlainAdapter, QuietAdapter, TerminalAdapter)
- 500+ new tests (1144 total)

### Changed
- Server uses `AsyncEngine` instead of tick-based `Engine`
- `ThreadSafeStore` with `_LockedConnection` proxy serializes all SQLite calls
- Removed `/api/tick` endpoint (no longer needed)
- Install and README quickstart now lead with `@stepwise:welcome` demo

## [0.2.0] — 2026-03-12

**Editor, Visual Editing, Registry Browser, AI Chat** — full flow authoring experience.

### Added
- **Flow Editor (M10)** — CodeMirror 6 YAML editor with syntax highlighting, live DAG visualization side-by-side, flow file list with search/filter, toolbar with Save/Discard/Ctrl+S, dirty state tracking, unsaved changes warning
- **Visual Step Editing (M12b)** — click DAG nodes to open StepDefinitionPanel with editable fields (prompt, model, command, outputs). Add Step dialog with executor type picker. Delete step with confirm. Server-side AST-preserving YAML patches via ruamel.yaml round-trip
- **Registry Browser (M11)** — search/browse stepwise.run registry from the editor sidebar. Preview flow DAGs, view metadata (author, downloads, tags, executor types). One-click install to local project. Graceful offline handling ("Registry unavailable")
- **AI Chat (M13)** — LLM-assisted flow creation/modification via streaming chat panel. YAML code blocks with Apply buttons. Context-aware quick actions. OpenRouter integration with system prompt containing Stepwise YAML format reference
- **Flow directories** — flows can now be directories containing `FLOW.yaml` alongside co-located scripts, prompts, and docs. Single-file `.flow.yaml` still works everywhere
- **Name-based flow resolution** — CLI commands accept flow names: `stepwise run my-flow` resolves across project root, `flows/`, `.stepwise/flows/`
- **`stepwise new <name>`** — scaffolds a flow directory from a minimal template
- **`prompt_file:`** — load prompt content from file relative to flow directory at parse time
- **Script path resolution** — `run:` paths resolve relative to flow directory for directory flows
- **Registry bundles** — `stepwise share` bundles directory flows as structured JSON with size/count limits
- **`.origin.json`** — provenance tracking when flows are installed from the registry
- `flow_resolution.py`, `bundle.py`, `editor_llm.py` — new modules
- Editor API endpoints: `/api/local-flows`, `/api/flows/local/{path}`, `/api/flows/parse`, `/api/flows/patch-step`, `/api/flows/add-step`, `/api/flows/delete-step`, `/api/editor/chat`, `/api/registry/*`
- 10 new web components, 3 new hooks, 200+ new tests

### Changed
- **Flat CLI** — `stepwise share/get/search/info` are top-level commands (removed `stepwise flow` subgroup)
- Builder page replaced by Editor page
- `WorkflowDagView` → `FlowDagView`, `WorkflowBuilder` → `FlowBuilder` (renamed)
- Web routes: `/builder` removed, `/editor` and `/editor/$flowName` added

## [0.1.0] — 2026-03-12

**Core engine through Flow Sharing** — the complete orchestration platform.

### Added
- **Core Engine (M1)** — DAG-based workflow engine with tick loop, step readiness, parallel execution, loop management via supersession, expression-based exit rules (advance/loop/escalate/abandon), HandoffEnvelope structured output, SQLite persistence with WAL mode, decorators (timeout/retry/fallback/notification), sub-job delegation, FastAPI server with 27 REST endpoints + WebSocket
- **Web UI (M2)** — React frontend (Vite, TanStack Router + Query, Tailwind 4, shadcn/ui), DAG visualization with dagre.js, step detail panels with real-time status, YAML workflow loader, `stepwise run/serve/validate` CLI commands, `.stepwise/` project directory
- **LLM Executor (M3)** — OpenRouter integration with model registry and tier support, `stepwise config` for API keys
- **Agent Executor (M4)** — ACP protocol with async polling, StepLimits (cost/duration/iterations), step_events table, WebSocket agent output streaming, AgentStreamView component
- **For-Each (M5)** — fan-out over lists with parallel sub-jobs, `on_error: continue|fail_fast`
- **HTML Reports (M6)** — `stepwise run --report` generates self-contained HTML execution traces with SVG DAG, step timeline, cost summary
- **Context Chains (M7a)** — session continuity across agent steps, `chains:` YAML block, overflow strategies (drop_oldest/drop_middle), transcript capture
- **Flows as Tools (M7b)** — `stepwise schema/run --wait/run --async/output/fulfill/agent-help` for agent integration, structured exit codes, stdout purity
- **Route Steps (M8)** — conditional sub-flow dispatch with `routes:` block, first-match semantics, file ref cycle detection, output contract validation
- **Flow Sharing (M9)** — `stepwise share/get/search/info`, registry client with disk cache and token management, parse-time `@author:name` resolution
- `install.sh` — universal `curl | sh` installer
- `stepwise update` — upgrade to latest version
- 640+ Python tests, 77+ frontend tests

## [0.0.1] — 2026-03-08

### Added
- Initial project setup with uv
- MIT license
