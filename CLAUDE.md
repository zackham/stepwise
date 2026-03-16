# Stepwise

Portable workflow orchestration for agents and humans.
Package: `stepwise-run` (not `stepwise` — that's taken on PyPI). CLI command: `stepwise`.
Entry point: `stepwise.cli:cli_main` (defined in `pyproject.toml` `[project.scripts]`).

**Push to master = users get it on next `stepwise update`.** There is no staging branch.

**Commit after every meaningful change.** Don't batch unrelated work into one commit. Each commit should be a single logical unit (a feature, a fix, a refactor). Write concise commit messages that explain what changed. This keeps the git log useful for changelog generation and makes reverts safe.

---

## Quick start

```bash
# Python backend
uv sync                                    # install deps
uv run pytest tests/                       # run all tests
uv run pytest tests/test_engine.py         # run one test file
uv run pytest tests/test_engine.py::TestLinearWorkflow::test_linear_a_b_c  # one test
uv run stepwise --help                     # run CLI from dev checkout

# Web frontend (from repo root)
cd web && npm install && npm run dev       # dev server (proxies /api + /ws to localhost:8340)
cd web && npm run test                     # vitest
cd web && npm run lint                     # eslint

# Build & bundle
make build-web                             # npm build → copies web/dist/ → src/stepwise/_web/
```

| CLI mode | What it does |
|---|---|
| `stepwise run <file>` | Headless execution, event-driven engine, exits on job complete/fail. Delegates to running server if one is detected (use `--local` to force standalone). |
| `stepwise run --wait <file>` | Blocking JSON output mode. Delegates to server if available (use `--local` to force standalone). |
| `stepwise run --async <file>` | Fire-and-forget. Delegates to server if available (no subprocess needed); falls back to detached background process with `--local`. |
| `stepwise run --watch <file>` | Launches FastAPI server with auto-created job, opens web UI |
| `stepwise serve` | Persistent web server on port 8340 (REST + WebSocket) |

All delegation modes (`run`, `--wait`, `--async`) use WebSocket notifications from the server for low-latency updates, falling back to REST polling at 2s intervals if WS connection fails.

---

## Architecture

Python backend (engine, CLI, FastAPI server) + React frontend bundled into the Python package at `src/stepwise/_web/`.

### Module dependency DAG (strict — no circular imports allowed)

```
models → llm_client → executors → engine → server
                                → agent
```

`models.py` must never import from `engine`, `executors`, or `agent`. `executors.py` must never import from `engine`.

### Engine (`src/stepwise/engine.py`)

Two engine classes: `AsyncEngine` (primary, event-driven) and `Engine` (legacy, tick-based).

**AsyncEngine** — event-driven with `asyncio.Queue`. Executors run in the thread pool via `asyncio.to_thread()`. Steps complete → push result event → engine dispatches newly ready steps. Poll watches are driven by `_schedule_poll_watch()` which creates an asyncio task that pushes `poll_check` events at the configured interval.

**Engine** (legacy) — tick-based `engine.tick()` loop. Still used by some tests. All business logic (readiness, exit rules, input resolution) is shared between both engines.

- **Step readiness** (`_is_step_ready()`): no active run + no current completed run (or loop guard) + all deps have current completed runs
- **Currency** (`_is_current()`): latest run for step is COMPLETED and all dep runs are also current (recursive)
- **Executor dispatch:** `registry.create(ExecutorRef)` → factory lookup → decorator wrapping → `to_thread(executor.start)` (AsyncEngine) or direct call (Engine)
- **Exit rules** after step completion: `advance` (continue DAG, optional `target` for conditional branching), `loop` (re-launch target step), `escalate` (pause job), `abandon` (fail job) — defined via `models.py:ExitRule`
- **Skip propagation**: When `advance` has a `target`, non-targeted downstream steps get SKIPPED runs. Transitive propagation marks dead branches. `any_of` inputs only skip when ALL sources are dead.
- **Input resolution:** `_resolve_inputs()` navigates artifact fields via dot-path (`"step_name.field.nested"`)

Do not duplicate readiness checks outside `engine.py`. Test input resolution via `register_step_fn()`, not by mocking engine internals.

### Executors

All registered in `src/stepwise/registry_factory.py:create_default_registry()`:

| Type name | Class | Source | Behavior |
|---|---|---|---|
| `script` | `ScriptExecutor` | `executors.py` | Synchronous shell command, parses stdout as JSON |
| `human` | `HumanExecutor` | `executors.py` | Immediately suspends for human input via API |
| `poll` | `PollExecutor` | `executors.py` | Suspends with poll watch — engine runs `check_command` at `interval_seconds`; JSON dict on stdout = fulfilled |
| `llm` | `LLMExecutor` | `executors.py` | OpenRouter API call (only registered if API key configured) |
| `mock_llm` | `MockLLMExecutor` | `executors.py` | Test-only LLM stub with configurable failure/latency |
| `agent` | `AgentExecutor` | `agent.py` | ACP agent via acpx — supports `emit_flow: true` for dynamic flow emission |

Decorators (`src/stepwise/decorators.py`): `TimeoutDecorator`, `RetryDecorator`, `FallbackDecorator` — applied via `ExecutorRef.decorators` list.

### Executor return types

Every executor's `start()` returns an `ExecutorResult` with one of these `type` values:

| `type` | Meaning | What to set |
|---|---|---|
| `"data"` | Synchronous completion | `envelope=HandoffEnvelope(artifact={...}, sidecar=Sidecar(), workspace=..., timestamp=_now())` |
| `"watch"` | Suspend for external input | `watch=WatchSpec(mode="human"\|"poll", ...)` |
| `"async"` | Legacy: long-running, poll via `check_status()` | `executor_state={...}` (opaque dict persisted by engine). No built-in executor uses this — prefer blocking in `start()` (safe in AsyncEngine's thread pool). |
| `"delegate"` | Dynamic sub-flow | `sub_job_def=SubJobDefinition(...)` — engine creates sub-job, run transitions to DELEGATED |

**Failure signaling:** Set `executor_state={"failed": True, "error": "..."}` and `envelope.executor_meta={"failed": True}`. For `type="data"` failures, the engine routes through `_fail_run()` which evaluates exit rules.

**Artifact keys must match the step's `outputs` list** in the YAML definition. The engine validates this via `_validate_artifact()`.

### Server (`src/stepwise/server.py`)

- FastAPI REST at `/api/*`, WebSocket at `/ws` for live updates and agent output streaming
- `ThreadSafeStore` subclass lives here (not in `store.py`) — wraps SQLiteStore with `_LockedConnection` proxy (serializes all sqlite3 calls via `threading.Lock`)
- `AsyncEngine` runs as an `asyncio.Task` in the server lifespan — no tick loop
- `_observe_external_jobs()` loop: polls for state changes in CLI-owned jobs, broadcasts stale job warnings via WebSocket
- Agent output: NDJSON file tailing → broadcast to all WebSocket clients
- Web UI served from `src/stepwise/_web/` via static file mount

**API endpoint groups:**
- `/api/jobs/*` — CRUD, adopt, stale detection, rerun, cancel, resume, fulfill
- `/api/config/*` — Model labels, API keys, default model, OpenRouter model search
- `/api/editor/*` — Chat endpoint (streaming NDJSON via `editor_llm.py`)
- `/api/flows/*` — Local flow listing, YAML parse/save, step CRUD, workspace file management
- `/api/registry/*` — Search, fetch, install flows from stepwise.run registry

### Web UI (`web/src/`)

- Routes in `web/src/router.tsx` via `createRoute()`. Layout: `components/layout/AppLayout.tsx`
- Pages in `web/src/pages/`: JobDashboard, JobDetailPage, JobEventsPage, JobTreePage, EditorPage, SettingsPage
- All API calls through `lib/api.ts` — never use raw `fetch()` elsewhere
- Dark mode only. Tailwind 4 + shadcn/ui for all styling — do not add CSS files or inline styles
- Dev proxy: Vite forwards `/api` and `/ws` to `localhost:8340` (`web/vite.config.ts`)

**Hooks** (split by domain):
- `hooks/useStepwise.ts` — React Query hooks for jobs, runs, events, fulfillment
- `hooks/useStepwiseWebSocket.ts` — WebSocket connection for live updates
- `hooks/useAgentStream.ts` — NDJSON stream parser for agent output
- `hooks/useEditor.ts` — Flow file CRUD, YAML parsing, step editing, registry search/install
- `hooks/useEditorChat.ts` — Agent-assisted flow editing (streaming chat with YAML generation)
- `hooks/useConfig.ts` — Config management (model labels, API keys, default model, OpenRouter search)
- `hooks/useAutoSelectSuspended.ts` — Auto-select first suspended human step

**Component directories:**
- `components/dag/` — Interactive DAG visualization: `FlowDagView` (pan/zoom/follow-flow), `StepNode`, `DagEdges` (animated intake/loopback edges), `ExpandedStepContainer` (sub-flow rendering), `ForEachExpandedContainer` (fan-out instances), `HumanInputPanel`, `DataFlowPanel`, `TypedField`
- `components/editor/` — Visual flow editor: `YamlEditor` (CodeMirror), `ChatPanel` (agent-assisted editing with Claude/Codex/Simple modes), `StepDefinitionPanel`, `AddStepDialog`, `RegistryBrowser`, `FlowFileTree`, `EditorToolbar`
- `components/jobs/` — Job detail views: `StepDetailPanel`, `JobDetailSidebar`, `AgentStreamView`, `HandoffEnvelopeView`, `FulfillWatchDialog` (schema-driven human input form), `JobTreeView`, `JobList`, `CreateJobDialog`
- `components/ui/` — shadcn/ui primitives

**Libraries:**
- `lib/api.ts` — All fetch calls (jobs, config, editor, registry, flow files)
- `lib/dag-layout.ts` — Dagre layout engine
- `lib/types.ts` — TypeScript interfaces for all backend models
- `lib/status-colors.ts` — Centralized color schemes for job/step statuses
- `lib/validate-fields.ts` — Output field validation against `OutputFieldSchema`
- `lib/utils.ts` — Tailwind class merger (`cn()`)

---

## Data layer

- SQLite with WAL mode, foreign keys enabled (`src/stepwise/store.py:SQLiteStore`)
- DB location: `STEPWISE_DB` env var, or `.stepwise/stepwise.db`
- Tables: `jobs`, `step_runs`, `events`, `step_events`
- Raw SQL via Store methods — no ORM. Do not introduce an ORM or repository abstraction.

### Core model chain (`src/stepwise/models.py`)

```
Job → WorkflowDefinition → StepDefinition (with InputBinding, ExitRule, ExecutorRef)
Job ← StepRun ← HandoffEnvelope (with artifact dict, Sidecar, executor_meta)
```

- All dataclasses have `to_dict()`/`from_dict()` serialization pair — new dataclasses must too
- StepRun states: `RUNNING` → `COMPLETED` | `FAILED` | `SUSPENDED` | `DELEGATED`
- Job ownership: `created_by` (`"server"` or `"cli:<pid>"`), `runner_pid`, `heartbeat_at` — used for stale detection and adoption
- Events: append-only log, type constants in `src/stepwise/events.py`
- YAML parsing: `yaml_loader.py:load_workflow_yaml()` → `WorkflowDefinition`

### YAML workflow format

```yaml
name: my-workflow          # kebab-case
steps:
  fetch-data:              # kebab-case step names
    run: |                 # shorthand for executor: script
      curl -s "$url" | jq '.'
    inputs:
      url: $job.target_url        # $job.param for job-level inputs
    outputs: [raw_data]           # must match JSON keys in stdout

  analyze:
    executor: llm
    config:
      model: anthropic/claude-sonnet-4-20250514
      prompt: "Analyze: $raw_data"
    inputs:
      raw_data: fetch-data.raw_data    # source_step.field for upstream bindings
    outputs: [analysis, quality_score]  # underscore output field names
    exits:
      - name: good-enough
        when: "float(outputs.quality_score) >= 0.8"
        action: advance
      - name: retry
        when: "attempt < 3"
        action: loop
        target: analyze
```

Poll step (wait for external condition):

```yaml
steps:
  wait-for-review:
    executor: poll
    check_command: |
      gh pr view $pr_number --json reviewDecision \
        --jq 'select(.reviewDecision != "") | {decision: .reviewDecision}'
    interval_seconds: 30
    prompt: "Waiting for PR #$pr_number review"
    inputs:
      pr_number: create-pr.pr_number
    outputs: [decision]
```

The `check_command` runs every `interval_seconds`. Empty stdout or non-zero exit = not ready. JSON dict on stdout = fulfilled (dict becomes the artifact). `$var` placeholders in `check_command` and `prompt` are interpolated from inputs.

Agent step with dynamic flow emission:

```yaml
steps:
  implement:
    executor: agent
    prompt: "Implement: $spec"
    emit_flow: true               # agent can write .stepwise/emit.flow.yaml
    inputs:
      spec: $job.spec
    outputs: [result]
```

Iterative delegation (agent loops with sub-flow results):

```yaml
steps:
  agent-phase:
    executor: agent
    prompt: |
      Implement: $spec
      Previous result: $prev_result
    emit_flow: true
    inputs:
      spec: $job.spec
      prev_result: agent-phase.result    # self-reference for iteration
    outputs: [result]
    exits:
      - name: continue
        when: "outputs.get('_delegated', False)"
        action: loop
        target: agent-phase
        max_iterations: 5
```

---

## Testing

### Python (pytest, `tests/`)

Fixtures in `tests/conftest.py`:

| Fixture | Provides |
|---|---|
| `store()` | In-memory `SQLiteStore(":memory:")` |
| `registry()` | `ExecutorRegistry` with `callable`, `script`, `human`, `mock_llm` registered |
| `engine(store, registry)` | Legacy `Engine` instance (tick-based) |
| `async_engine(store, registry)` | `AsyncEngine` instance (event-driven, preferred for new tests) |
| `cleanup_step_fns` | Autouse — clears `CallableExecutor` registry after each test |

**`run_job_sync()` helper** — preferred way to run a job to completion in tests:

```python
from tests.conftest import register_step_fn, run_job_sync

def test_my_feature(async_engine):
    register_step_fn("double", lambda inputs: {"result": inputs["n"] * 2})

    wf = WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "double"}),
            inputs=[InputBinding("n", "$job", "n")],
            outputs=["result"],
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf, inputs={"n": 5})
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    assert runs[0].result.artifact["result"] == 10
```

Other patterns:
- Inline `Executor` subclasses for failure/edge-case testing (no external mock library)
- `tempfile.mkdtemp()` for workspace and DB paths
- Legacy tick loops (older tests): `for _ in range(N): engine.tick(); if done: break`

### Web (Vitest + jsdom + @testing-library/react)

- `vi.fn()`, `vi.mock()`, `createWrapper()` for QueryClient, `makeStepDef`/`makeRun` factories
- Config in `web/vite.config.ts` (`test.environment: 'jsdom'`, `setupFiles: ['./src/test/setup.ts']`)

---

## Key files

**Engine:** `engine.py` (AsyncEngine event queue + legacy Engine tick loop, readiness, launching), `models.py` (all dataclasses), `executors.py` (ABC + built-in executors), `store.py` (SQLite + heartbeat + stale detection), `events.py` (event type constants), `decorators.py` (retry, timeout, fallback), `hooks.py` (project hooks — fires shell scripts on engine events like suspend, complete, fail)

**CLI/Runner:** `cli.py` (all CLI commands), `runner.py` (headless `stepwise run` + server delegation), `runner_bg.py` (background mode), `agent.py` (AgentExecutor + AcpxBackend), `agent_help.py` (agent instruction generation), `server_detect.py` (PID file + health probe for server detection), `io.py` (terminal I/O adapter — TerminalAdapter/PlainAdapter/QuietAdapter for rendering flows and collecting input), `api_client.py` (HTTP client for CLI→server delegation)

**Server:** `server.py` (FastAPI REST + WS + ThreadSafeStore + observation loop + adoption + config/editor/registry endpoints), `registry_factory.py` (shared executor registration), `editor_llm.py` (agent-assisted flow editing via acpx or OpenRouter, streaming NDJSON)

**Config/Parsing:** `yaml_loader.py` (.flow.yaml parser), `project.py` (.stepwise/ directory), `config.py` (StepwiseConfig + model aliases), `context.py` (LLM context chain compilation), `report.py` (HTML job reports), `openrouter.py` (OpenRouter API client), `openrouter_models.py` (model catalog fetcher + cache), `llm_client.py` (LLM client ABC), `cli_llm_client.py` (LLM via acpx fallback), `registry_client.py` (stepwise.run registry client), `flow_resolution.py` (flow discovery + name→path resolution), `bundle.py` (collect/unpack flow directories for sharing), `schema.py` (JSON tool contract generation from flow definitions)

**Web:** `router.tsx` (route definitions), `components/layout/AppLayout.tsx` (root layout), `lib/api.ts` (all fetch calls), `lib/dag-layout.ts` (Dagre layout engine), `lib/types.ts` (TypeScript model interfaces), `lib/status-colors.ts` (status color schemes), `lib/validate-fields.ts` (output field validation)

**Tests:** `tests/conftest.py` (fixtures: store, registry, engine, async_engine, run_job_sync, CallableExecutor, register_step_fn). ~40 test files covering engine, executors, models, CLI, server endpoints, editor, config, delegation, for-each, agent emit flow, streaming, etc.

---

## Guardrails

1. **Module DAG is strict:** `models → executors → engine → server`. Do not import from `engine` in `models.py` or `executors.py`.
2. **No `print()` in library code** — use `logging`. Stdout is reserved for JSON output in CLI mode.
3. **No `fetch()` outside `web/src/lib/api.ts`** in the frontend.
4. **`httpx` stays in core `[project.dependencies]`** — not optional extras.
5. **All model dataclasses must have `to_dict()`/`from_dict()` pair.**
6. **Push to master = immediate user release** via `stepwise update`. Tests must pass first: `uv run pytest tests/`
7. **No ORM or repository abstraction** — the store uses raw SQL intentionally.
8. **No CSS files or inline styles in web** — Tailwind classes and shadcn/ui components only.
9. **Dark mode only** — no light/color theme toggle.
10. **Register production executors in `registry_factory.py`** only — test code uses its own registry in `conftest.py`.
11. **Web routes go in `web/src/router.tsx`** — no file-based routing.

---

## Recipes

### Add a new executor type

1. Subclass `Executor` in `src/stepwise/executors.py`:

```python
class HttpExecutor(Executor):
    def __init__(self, url: str) -> None:
        self.url = url

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        import httpx
        resp = httpx.post(self.url, json=inputs)
        resp.raise_for_status()
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact=resp.json(),       # keys must match step's outputs list
                sidecar=Sidecar(),
                workspace=context.workspace_path,
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")  # sync executor

    def cancel(self, state: dict) -> None:
        pass
```

2. Register in `src/stepwise/registry_factory.py`:
```python
from stepwise.executors import HttpExecutor
registry.register("http", lambda cfg: HttpExecutor(url=cfg["url"]))
```

3. Use in YAML: `executor: http` with `config: { url: "https://..." }`

### Add a new CLI command

1. Add handler in `src/stepwise/cli.py`: `def cmd_mycommand(args: Namespace) -> int`
2. Register subparser in `cli_main()`: `subparsers.add_parser("mycommand", help="...").set_defaults(handler=cmd_mycommand)`
3. Return `EXIT_SUCCESS` / `EXIT_JOB_FAILED` / etc. (constants at top of `cli.py`)

### Add a new API endpoint

1. Add Pydantic request model in `src/stepwise/server.py`
2. Add FastAPI route: `@app.post("/api/my-endpoint")`
3. Add fetch function in `web/src/lib/api.ts`
4. Add React Query hook in the appropriate hooks file: `useStepwise.ts` (jobs/runs), `useEditor.ts` (flows/registry), `useConfig.ts` (settings/models)

### Add a new web page

1. Create page component in `web/src/pages/MyPage.tsx`
2. Register route in `web/src/router.tsx` with `createRoute()` and add to `routeTree`
3. Add navigation link in `web/src/components/layout/AppLayout.tsx` if needed

### Add a new test

1. Create `tests/test_mymodule.py`
2. Use fixtures from `conftest.py` (`async_engine`, `store`, `registry`)
3. Build `WorkflowDefinition` inline (see test example above), create job, use `run_job_sync()`, assert on `runs[].result.artifact`
4. For executor-specific tests: subclass `Executor` inline or use `register_step_fn()`

### Agent-emitted flow

Agent steps with `emit_flow: true` can dynamically create sub-workflows by writing `.stepwise/emit.flow.yaml` to their working directory. The engine launches the emitted flow as a sub-job and propagates results back.

**Basic pattern:** Agent analyzes task, writes flow, engine executes it:
```yaml
steps:
  implement:
    executor: agent
    prompt: "Break this into steps and emit a flow: $spec"
    emit_flow: true
    inputs:
      spec: $job.spec
    outputs: [result]
```

**Iterative pattern:** Exit rules loop the agent with sub-flow results:
```yaml
steps:
  agent-phase:
    executor: agent
    prompt: "Continue: $spec\nPrevious: $prev_result"
    emit_flow: true
    inputs:
      spec: $job.spec
      prev_result: agent-phase.result
    outputs: [result]
    exits:
      - name: continue
        when: "outputs.get('_delegated', False)"
        action: loop
        target: agent-phase
        max_iterations: 5
```

The `_delegated: True` marker is injected into the artifact when a sub-flow completes, allowing exit rules to distinguish delegation from direct completion.

### Distribution & Releases

```bash
# How users install
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh

# Which runs:
uv tool install stepwise-run@git+https://github.com/zackham/stepwise.git
```

No PyPI publishing. The install script and `update` both pull from `master`.

### Release workflow

Every push to master is a release — but only version bumps trigger user-visible upgrade notifications.

**When to bump version:**
- New features or milestones → bump **minor** (0.2.0 → 0.3.0)
- Bug fixes or polish → bump **patch** (0.2.0 → 0.2.1)
- Pre-1.0: no major bumps yet. 1.0.0 = stable API commitment.

**Release steps:**
1. Ensure all tests pass: `uv run pytest tests/` + `cd web && npm run test`
2. Update `version` in `pyproject.toml`
3. Add a `## [X.Y.Z] — YYYY-MM-DD` section to `CHANGELOG.md` (above `[Unreleased]`)
4. Commit: `git commit -m "release: vX.Y.Z"`
5. Tag: `git tag vX.Y.Z`
6. Push: `git push origin master --tags`

**How upgrades surface to users:**
- `stepwise serve` prints a one-liner on startup if a newer version exists (cached check, once/day, non-blocking)
- `stepwise update` shows "Already up to date" or installs + prints the changelog diff between old and new version
- Version check fetches `pyproject.toml` from GitHub raw, caches in `~/.cache/stepwise/version-check.json`

**Key files:** `_get_version()`, `_fetch_remote_version()`, `_check_for_upgrade()`, `_fetch_changelog_sections()` in `cli.py`. `CHANGELOG.md` must use `## [X.Y.Z]` headers for the diff parser to work.
