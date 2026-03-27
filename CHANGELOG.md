# Changelog

All notable changes to Stepwise are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.21.0] — 2026-03-27

**Full-Stack Orchestration Platform** — from engine primitives to production-grade job management, a comprehensive web UI, and hardened agent reliability. 297 commits spanning 15 internal versions since 0.6.0.

### Added

#### Engine
- **Job staging** — `STAGED` status, job groups, dependency edges, cross-job data wiring via `$job_ref` inputs, approval gates (`AWAITING_APPROVAL`)
- **Derived outputs** — compute output fields from executor results using expressions
- **Job metadata** — `--meta key=value` flag, metadata column, event envelopes with hook env vars
- **Step result caching** — `cache:` config on steps, `stepwise cache` CLI commands, for-each batch cache, `--rerun` flag to bypass cache
- **Multi-job wait** — `stepwise wait` with `--all` and `--any` flags for blocking on multiple jobs
- **Orphan recovery** — auto-adopt orphaned CLI jobs on server startup and periodically
- **Agent concurrency** — configurable `max_concurrent_agents` with stagger delay, semaphore-based dispatch
- **Transient retry** — auto-applied retry decorator for agent executors with error classification and exponential backoff
- **Server identity** — cross-project confusion prevention, global server registry with warnings
- **DAG validation** — `stepwise check` with cycle detection, unreachable step detection, non-zero exit
- **`stepwise validate --fix`** — auto-fix common YAML issues; `stepwise test-fixture` for test flow generation
- **Premature launch detection** — warn on steps downstream of loop bodies that could fire too early
- **`on_error: continue`** for parallel steps (not just for-each)
- **Stall detection** — validate warns on steps that can never reach completion

#### CLI
- **`stepwise job`** subcommand group — `create`, `show`, `run`, `dep`, `cancel`, `rm` for job staging
- **`stepwise tail`** — live event stream for running jobs
- **`stepwise logs`** — chronological event dump for debugging
- **`stepwise output`** — retrieve per-step outputs with positional step name
- **`stepwise docs`** — browse bundled documentation with keyword search fallback
- **`stepwise server log`** — view server log output
- **`stepwise extensions list`** — show installed executor plugins
- **`stepwise login/logout`** — Device Flow authentication for registry
- **`stepwise preflight`** — ready-to-run assessment for flows
- **`stepwise info`** — flow metadata and config init scaffolding
- **`stepwise uninstall`** — clean removal command
- **`stepwise help`** — interactive assistant
- **`stepwise version`** — alias for `--version`

#### Config System
- **Config variables** — `ConfigVar` with types, defaults, descriptions, sensitive flag, choice options
- **Flow requirements** — declare external dependencies with install hints and URLs
- **`config.local.yaml`** — per-machine overrides, auto-excluded from bundles and git
- **Project-level `notify_url`** — webhook notifications without per-run flags
- **JSON Schema generation** for flow config inputs

#### Web UI
- **Orchestrator Canvas** — mini-DAG job cards in responsive CSS grid layout
- **Virtualized job list** — handles thousands of jobs via `@tanstack/react-virtual`
- **Command palette** — `Cmd+K` / `Ctrl+K` quick navigation
- **Timeline/waterfall view** — step execution timing visualization
- **Diff viewer** — output changes across retry attempts
- **Log search** — regex filtering across all log viewers
- **Breadcrumb navigation** — hierarchical page navigation
- **Error recovery suggestions** — actionable fix hints on failure pages
- **Live duration** — real-time elapsed time on running steps
- **Browser notifications** — alerts for suspended steps
- **Light/dark theme toggle** — full light mode support across all components
- **Toast notifications** via sonner
- **WebSocket status indicator** — connection health in header
- **Status count badges** — aggregate counts on filter pills and nav links
- **Quick-launch section** — recently-run flows on dashboard (later removed)
- **Keyboard navigation** — arrow keys + Enter on job list
- **Sort controls** — name, status, date sorting on job list
- **Per-job action menu** — Cancel, Retry, Delete from job list
- **Relative time grouping** — "Today", "Yesterday", "This week" in job list
- **URL-persisted filters** — `?q=&status=` search params
- **Error summary banner** — failed job diagnostics
- **Collapsible for-each groups** — expandable step groups in DAG
- **DAG polish** — rich tooltips, executor accents, animated edges, shareable screenshot export
- **Responsive mobile layout** — full mobile support across all pages
- **React error boundary** — prevents white-screen crashes
- **404/Not Found page** — proper routing for missing resources
- **Tabbed right sidebar** — unified editor panels
- **Raw log viewer** — script step stdout/stderr
- **Flow-not-found** message in editor
- **Welcome banner** — shown when no jobs exist
- **Cost attribution** — `$0 (Max)` display for subscription billing

#### Eval Framework
- **eval-1.0** — 16-step evaluation flow with preflight, discovery, security, migration, data integrity, quality testing, scoring, and HTML report generation

### Changed
- **Rename `executor: human` → `executor: external`** — breaking change; update all `.flow.yaml` files
- **Rename `sequencing:` → `after:`** in flow YAML — breaking change; update all flow definitions
- **Rename `--var`/`--var-file` → `--input`** with `@file` prefix detection
- **Input names must be valid identifiers** (`[A-Za-z_][A-Za-z0-9_]*`) — use underscores instead of hyphens
- **`$var` placeholders auto shell-quoted** — do not pre-quote placeholders in `command`/`check_command`
- **Exit rule default: fail on no-match** when explicit `advance` rules exist but none match
- **Remove default concurrency limit** — all jobs start immediately unless configured otherwise
- **Jobs list endpoint** returns summary payload (1.8MB → 13KB) with limit parameter
- **Require auth for `stepwise share`** — Device Flow login required for registry publishing
- Install script uses `--force --reinstall` for reliable upgrades
- Server defaults to detached start; binds 0.0.0.0 for container accessibility
- `.step-io/` moved under `.stepwise/step-io/`

### Removed
- **Route system** (`RouteSpec`, `RouteDefinition`, `_launch_route`) — replaced by `when`-based branching
- **`stepwise chain`** subcommand — replaced by `--after` + `--input` job staging
- **QuickLaunch** from web dashboard

### Fixed
- Orphaned CLI jobs recovered on server restart instead of failing them
- React Error #31 from object values rendered as JSX children
- Light mode CSS variants across all web components
- Terminal step detection for sub-flow loop cycles
- Currentness invalidation cycles in circular dependency chains
- Agent session name collisions across concurrent jobs
- `acpx` session cleanup on job completion, failure, and cancellation
- Script step output recovery on server restart
- OpenRouter 400 errors from incorrect `tool_choice` on single-output LLM steps
- Double-escaped JSON strings in LLM executor output
- Expression namespace: `true`/`false`/`null` aliases for Python builtins
- For-each all-fail correct behavior
- Follow-flow mode ensures full input dialog visible for suspended steps
- Canvas page crash from `job_group` accessed from wrong path
- Harden exit rule and interpolated config rendering against unexpected types
- Zombie job cleanup with PID verification
- `stepwise update` blocked when running from editable install

### Security
- AST validation on exit rule, `when`, and derived output expressions — blocks `__class__`/`__bases__`/`__globals__` traversal
- Shell-escape user input values in `command`/`check_command` via `shlex.quote()`
- Namespace step inputs under `STEPWISE_INPUT_` prefix in environment variables
- Reject output file paths that escape working directory in AgentExecutor

### Deprecated
- Bare input environment variables (`$url`). Use `$STEPWISE_INPUT_url` instead. Bare names still exported during deprecation period.

### Development
- 1980 Python tests, 310 frontend tests (2290 total, up from ~700 in 0.6.0)

## [0.6.0] — 2026-03-17

**Optional Inputs, Session Continuity, Webhook Notifications** — smarter loops, agent memory, and async event delivery.

### Added
- **Optional inputs** — `{from: "step.field", optional: true}` weak-reference bindings that resolve to `None` when the source dep is unavailable. Enables feeding data backward across loops, first-run defaults, and graceful degradation. Cycles in the dependency graph are valid if every cycle contains at least one optional edge.
- **Session continuity** — `continue_session: true` on agent/LLM steps reuses the same agent session across loop iterations instead of starting fresh. `loop_prompt` provides an alternate prompt for attempt > 1. `max_continuous_attempts` acts as a circuit breaker, forcing a fresh session with chain context backfill after N iterations.
- **Cross-step session sharing** — Agent steps with `continue_session` auto-emit `_session_id` as a typed output. Downstream steps receive it via optional input bindings to continue the same conversation. Engine serializes concurrent access via `_SessionLockManager`.
- **Webhook notifications** — `stepwise run --async --notify <url> --notify-context '{...}'` delivers HTTP POST callbacks on job suspend, complete, and fail events. Context dict is passed through to every webhook payload.

### Changed
- **Exit rule default behavior** — When explicit `advance` rules exist but none match, the step now **fails** instead of silently advancing. This prevents unhandled output cases from progressing through the DAG. Steps with only loop/escalate/abandon rules still implicitly advance when unmatched.

### Fixed
- **Ctrl+C during human input** — presents suspend/cancel menu instead of crashing
- **External fulfill detection** — AsyncEngine polls for state changes every 5 seconds, picking up fulfills from other processes without waiting for an internal event

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
