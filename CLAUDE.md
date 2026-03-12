# Stepwise

Portable workflow orchestration for agents and humans.
Package: `stepwise-run` (not `stepwise` — that's taken on PyPI). CLI command: `stepwise`.
Entry point: `stepwise.cli:cli_main` (defined in `pyproject.toml` `[project.scripts]`).

**Push to master = users get it on next `stepwise update`.** There is no staging branch.

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
| `stepwise run <file>` | Headless execution, 0.1s tick loop, exits on job complete/fail |
| `stepwise run --watch <file>` | Launches FastAPI server with auto-created job, opens web UI |
| `stepwise serve` | Persistent web server on port 8340 (REST + WebSocket) |

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

Tick-based loop: `engine.tick()` iterates active jobs, polls running executors, checks step readiness, launches ready steps.

- **Tick interval:** 0.1s in runner (`runner.py`), 2s active / 10s idle in server (`server.py`)
- **Step readiness** (`_is_step_ready()`): no active run + no current completed run (or loop guard) + all deps have current completed runs
- **Currency** (`_is_current()`): latest run for step is COMPLETED and all dep runs are also current (recursive)
- **Executor dispatch:** `registry.create(ExecutorRef)` → factory lookup → decorator wrapping
- **Exit rules** after step completion: `advance` (continue DAG), `loop` (re-launch target step), `escalate` (pause job), `abandon` (fail job) — defined via `models.py:ExitRule`
- **Input resolution:** `_resolve_inputs()` navigates artifact fields via dot-path (`"step_name.field.nested"`)

Do not duplicate readiness checks outside `engine.py`. Test input resolution via `register_step_fn()`, not by mocking engine internals.

### Executors

All registered in `src/stepwise/registry_factory.py:create_default_registry()`:

| Type name | Class | Source | Behavior |
|---|---|---|---|
| `script` | `ScriptExecutor` | `executors.py` | Synchronous shell command, parses stdout as JSON |
| `human` | `HumanExecutor` | `executors.py` | Immediately suspends for human input via API |
| `llm` | `LLMExecutor` | `executors.py` | OpenRouter API call (only registered if API key configured) |
| `mock_llm` | `MockLLMExecutor` | `executors.py` | Test-only LLM stub with configurable failure/latency |
| `agent` | `AgentExecutor` | `agent.py` | ACP agent via acpx subprocess with tool dispatch |
| `delegating` | `DelegatingExecutor` | `server.py` | Creates a sub-job from a child workflow |

Decorators (`src/stepwise/decorators.py`): `TimeoutDecorator`, `RetryDecorator`, `NotificationDecorator`, `FallbackDecorator` — applied via `ExecutorRef.decorators` list.

### Executor return types

Every executor's `start()` returns an `ExecutorResult` with one of these `type` values:

| `type` | Meaning | What to set |
|---|---|---|
| `"data"` | Synchronous completion | `envelope=HandoffEnvelope(artifact={...}, sidecar=Sidecar(), workspace=..., timestamp=_now())` |
| `"watch"` | Suspend for external input | `watch=WatchSpec(mode="human"\|"poll", ...)` |
| `"sub_job"` | Delegate to child workflow | `sub_job_def=SubJobDefinition(...)` |
| `"async"` | Long-running, poll via `check_status()` | `executor_state={...}` (opaque dict persisted by engine) |

**Failure signaling:** Set `executor_state={"failed": True, "error": "..."}` and `envelope.executor_meta={"failed": True}`. The engine checks `executor_state["failed"]` in `check_status()`.

**Artifact keys must match the step's `outputs` list** in the YAML definition. The engine validates this via `_validate_artifact()`.

### Server (`src/stepwise/server.py`)

- FastAPI REST at `/api/*`, WebSocket at `/ws` for live tick updates and agent output streaming
- `ThreadSafeStore` subclass lives here (not in `store.py`) — wraps SQLiteStore with `check_same_thread=False`
- Agent output: NDJSON file tailing → broadcast to all WebSocket clients
- Web UI served from `src/stepwise/_web/` via static file mount

### Web UI (`web/src/`)

- Routes in `web/src/router.tsx` via `createRoute()`. Layout: `components/layout/AppLayout.tsx`
- Pages in `web/src/pages/`: JobDashboard, JobDetailPage, JobEventsPage, JobTreePage, BuilderPage
- All API calls through `lib/api.ts` — never use raw `fetch()` elsewhere
- React Query hooks in `hooks/useStepwise.ts`. WebSocket: `hooks/useStepwiseWebSocket.ts`. Agent stream: `hooks/useAgentStream.ts`
- Dark mode only. Tailwind 4 + shadcn/ui for all styling — do not add CSS files or inline styles
- Dev proxy: Vite forwards `/api` and `/ws` to `localhost:8340` (`web/vite.config.ts`)

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

---

## Testing

### Python (pytest, `tests/`)

Fixtures in `tests/conftest.py`:

| Fixture | Provides |
|---|---|
| `store()` | In-memory `SQLiteStore(":memory:")` |
| `registry()` | `ExecutorRegistry` with `callable`, `script`, `human`, `mock_llm` registered |
| `engine(store, registry)` | `Engine` instance |
| `cleanup_step_fns` | Autouse — clears `CallableExecutor` registry after each test |

**CallableExecutor pattern** — register a Python function, reference from workflow config:

```python
from tests.conftest import register_step_fn

def test_my_feature(engine):
    register_step_fn("double", lambda inputs: {"result": inputs["n"] * 2})

    wf = WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "double"}),
            inputs=[InputBinding("n", "$job", "n")],
            outputs=["result"],
        ),
    })
    job = engine.create_job(wf, objective="test", inputs={"n": 5})
    engine.start_job(job.id)
    for _ in range(10):
        engine.tick()
        job = engine.store.load_job(job.id)
        if job.status != JobStatus.RUNNING:
            break
    assert job.status == JobStatus.COMPLETED
    runs = engine.store.runs_for_job(job.id)
    assert runs[0].result.artifact["result"] == 10
```

Other patterns:
- Inline `Executor` subclasses for failure/edge-case testing (no external mock library)
- `tempfile.mkdtemp()` for workspace and DB paths
- Manual tick loops: `for _ in range(N): engine.tick(); if done: break`

### Web (Vitest + jsdom + @testing-library/react)

- `vi.fn()`, `vi.mock()`, `createWrapper()` for QueryClient, `makeStepDef`/`makeRun` factories
- Config in `web/vite.config.ts` (`test.environment: 'jsdom'`, `setupFiles: ['./src/test/setup.ts']`)

---

## Key files

**Engine:** `engine.py` (tick loop, readiness, launching), `models.py` (all dataclasses), `executors.py` (ABC + built-in executors), `store.py` (SQLite), `events.py` (event type constants), `decorators.py` (retry, timeout, fallback, notification)

**CLI/Runner:** `cli.py` (all CLI commands), `runner.py` (headless `stepwise run`), `runner_bg.py` (background mode), `agent.py` (AgentExecutor + AcpxBackend), `agent_help.py` (agent instruction generation)

**Server:** `server.py` (FastAPI REST + WS + ThreadSafeStore + DelegatingExecutor), `registry_factory.py` (shared executor registration)

**Config/Parsing:** `yaml_loader.py` (.flow.yaml parser), `project.py` (.stepwise/ directory), `config.py` (StepwiseConfig + model aliases), `context.py` (LLM context chain compilation), `report.py` (HTML job reports), `openrouter.py` (OpenRouter API client), `llm_client.py` (LLM client ABC), `registry_client.py` (stepwise.run registry client)

**Web:** `lib/api.ts` (all fetch calls), `hooks/useStepwise.ts` (React Query), `hooks/useStepwiseWebSocket.ts` (WS connection), `hooks/useAgentStream.ts` (NDJSON stream parser), `lib/dag-layout.ts` (Dagre layout engine), `router.tsx` (route definitions), `components/layout/AppLayout.tsx` (root layout)

**Tests:** `tests/conftest.py` (fixtures: store, registry, engine, CallableExecutor, register_step_fn)

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
4. Add React Query hook in `web/src/hooks/useStepwise.ts`

### Add a new web page

1. Create page component in `web/src/pages/MyPage.tsx`
2. Register route in `web/src/router.tsx` with `createRoute()` and add to `routeTree`
3. Add navigation link in `web/src/components/layout/AppLayout.tsx` if needed

### Add a new test

1. Create `tests/test_mymodule.py`
2. Use fixtures from `conftest.py` (`engine`, `store`, `registry`)
3. Build `WorkflowDefinition` inline (see test example above), create job, tick engine, assert on `runs[].result.artifact`
4. For executor-specific tests: subclass `Executor` inline or use `register_step_fn()`

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
