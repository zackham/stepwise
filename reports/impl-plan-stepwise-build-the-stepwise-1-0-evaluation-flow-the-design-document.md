---
title: "Implementation Plan: Stepwise 1.0 Evaluation Flow"
date: "2026-03-20T22:30:00"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Stepwise 1.0 Evaluation Flow

## Overview

Build a 16-step self-hosted evaluation flow at `flows/eval-1.0/FLOW.yaml` that assesses Stepwise's 1.0 readiness by running on Stepwise itself. The flow implements council-revised v2 design with key refinements: compression from 26 to 16 steps via script batching and multi-rater merging, a security severity model (Blocker/Major/Minor with auto-veto), three-state rubrics (pass/fail/insufficient\_evidence), a new Data Integrity hard gate, ground truth calibration in preflight, adaptive early stopping across runs, and strictly observable rubric items.

## Requirements

### R1: Flow Structure
- **AC-1a**: `flows/eval-1.0/FLOW.yaml` exists and is discovered by `stepwise run eval-1.0` via the directory flow convention (`flow_resolution.py:52-68` searches `flows/` subdirectory for `FLOW.yaml` marker)
- **AC-1b**: `stepwise validate flows/eval-1.0/FLOW.yaml` exits 0 with zero warnings (validated against `models.py:WorkflowDefinition.validate()` at line 525 and `warnings()` at line 570)
- **AC-1c**: `stepwise run eval-1.0 --watch` launches the flow, opens web UI, and shows all 16 steps in the DAG visualization

### R2: Step Compression (26 → 16)
- **AC-2a**: Phase 2 script steps batched into 5 steps: `test-core` (merges v2's `test-cli` + `test-server` + `test-lifecycle`), `test-security`, `test-migration`, `test-data-integrity` (new), `test-quality` (merges v2's `test-validation` + `test-suite` + `test-config` + `test-webhooks` + `test-observability` + `test-performance` + `test-error-dx`)
- **AC-2b**: Phase 3 multi-rater agent pairs (A+B × 3 = 6 steps) merged into 3 single agent steps with built-in rigor instructions and explicit "be conservative — score True only when clearly met" prompting
- **AC-2c**: `grep -c "^  [a-z]" flows/eval-1.0/FLOW.yaml` (top-level step definitions) returns exactly 16

### R3: Security Severity Model
- **AC-3a**: `test-security` script outputs rubric items with `"severity": "blocker"|"major"|"minor"` field on every result
- **AC-3b**: `aggregate-scores` checks `has_blocker` flag from test-security output — if `true`, sets `security_veto: true` and recommendation to NO-GO regardless of percentage score
- **AC-3c**: Severity classification matches spec: S1 (API auth), S3 (secret masking), S4 (shell injection), S5 (no eval/exec) are `blocker`; S2, S6-S8, S10 are `major`; S9 is `minor`

### R4: Three-State Rubrics
- **AC-4a**: Every rubric item in every script outputs `"result": "pass"|"fail"|"insufficient_evidence"` (never boolean `true`/`false`)
- **AC-4b**: Scoring formula implemented in `aggregate.py`: `score = pass_count / (pass_count + fail_count) * 100` — items with `insufficient_evidence` excluded from both numerator and denominator
- **AC-4c**: Warning emitted by aggregate if `insufficient_count / total > 0.3` for any dimension; warning appears in `insufficient_evidence_warnings` output field
- **AC-4d**: If all items are `insufficient_evidence` (denominator = 0), score is 0 and dimension is flagged

### R5: Data Integrity Hard Gate (NEW)
- **AC-5a**: `test-data-integrity` step exists with 6 rubric items (DI1-DI6)
- **AC-5b**: DI1 verifies SQLite WAL mode by querying `PRAGMA journal_mode` on the stepwise DB (located via `STEPWISE_DB` env var or `.stepwise/stepwise.db`, per `store.py:45-47`)
- **AC-5c**: DI2 verifies foreign keys via `PRAGMA foreign_keys` (enabled at `store.py:62`)
- **AC-5d**: DI3-DI6 test artifact round-trip, step run retrieval, orphan detection, and event ordering via the REST API (`/api/jobs/*` endpoints in `server.py`)
- **AC-5e**: Aggregation includes `data_integrity` in hard gate checks with ≥80% threshold

### R6: Ground Truth Calibration
- **AC-6a**: `data/known-bad.flow.yaml` exists with at least 3 deliberate defects that `stepwise validate` must catch: missing `steps:` key, circular non-optional dependency, and a reference to a nonexistent upstream output
- **AC-6b**: Preflight runs `stepwise validate` on the known-bad file and asserts exit code ≠ 0
- **AC-6c**: Preflight runs `stepwise validate` on `flows/welcome/FLOW.yaml` and asserts exit code = 0
- **AC-6d**: If either calibration check fails, preflight exits with non-zero status, which fails the step and halts the job (engine marks step FAILED → job FAILED per `engine.py:_fail_run()`)

### R7: Adaptive Early Stopping
- **AC-7a**: `run-eval-3x.sh` runs `stepwise run eval-1.0 --wait --var eval_run_number=N` for N=1,2,3
- **AC-7b**: After Run 1, harness parses the `--wait` JSON output (`{"status": "completed", "outputs": [...]}` per `runner.py:709-715`) and checks `outputs[0].all_gates_passed`
- **AC-7c**: If `all_gates_passed == false` or `security_veto == true` in Run 1, Runs 2 and 3 are skipped with a printed reason
- **AC-7d**: Final output includes variance analysis: for each dimension, report `[run1_score, run2_score, run3_score]` and flag if max-min > 15%

### R8: Observable & Falsifiable Rubrics
- **AC-8a**: Every rubric item in every script specifies: (1) the exact command or API call, (2) the expected observable outcome (exit code, HTTP status, string match), and (3) a deterministic pass/fail criterion
- **AC-8b**: Zero rubric items use subjective language ("well-structured", "clean", "good quality") — all items are checkable by grepping, HTTP calls, or subprocess exit codes
- **AC-8c**: Agent synthesis steps (Phase 3) receive evidence packets as inputs and produce qualitative summaries, but the script-determined pass/fail/insufficient\_evidence is authoritative for scoring

### R9: Human Approval Gate
- **AC-9a**: `human-approval` step has `output_schema` with `human_decision` (type: `choice`, options: `[approve, reject, override]`) and `human_notes` (type: `text`, required: false)
- **AC-9b**: Prompt displays scorecard, gate results, and remediation list via `$variable` interpolation from aggregate-scores outputs
- **AC-9c**: `publish-report` reads `human_decision` and skips report generation if `reject`, includes override note if `override`

### R10: Self-Contained Flow
- **AC-10a**: All Python scripts live under `flows/eval-1.0/scripts/`, all prompt files under `flows/eval-1.0/prompts/`, all test data under `flows/eval-1.0/data/`
- **AC-10b**: No `import` statements reference modules outside Python stdlib and the `stepwise` package itself — verified by grepping `from vita` or `from reports` in scripts
- **AC-10c**: `publish.py` generates HTML using only `string.Template` from stdlib — no Jinja2, no external report generators
- **AC-10d**: Flow runs to completion (excluding agent steps that need API keys) on a fresh machine with only stepwise installed and server running

## Assumptions

| # | Assumption | Verification | File:Line |
|---|-----------|-------------|-----------|
| A1 | `prompt_file:` resolves relative to flow directory at parse time and works for agent, LLM, and human executors | Verified: `_resolve_prompt_file()` reads content at parse time, injects as `config["prompt"]`. Tests cover all executor types. | `yaml_loader.py:179-216`, `test_m10_flow_dir.py:247-380` (7 tests covering LLM, agent, human, directory flows) |
| A2 | `sequencing:` enforces ordering without data dependency | Verified: separate field on StepDefinition, added to dep graph in `_build_dep_graph()` | `models.py:390`, `engine.py:536-541` |
| A3 | `limits.max_duration_minutes` is enforced by the engine via `_check_limits()` | Verified: checks elapsed time, emits STEP_LIMIT_EXCEEDED event, fails the run. Note: AsyncEngine checks on completion, not continuously during execution. | `engine.py:2000-2038` (check logic), `engine.py:636-654` (tick-loop enforcement) |
| A4 | `limits.max_cost_usd` is enforced only in `billing_mode == "api_key"` | Verified: conditional check in `_check_limits()`. In subscription mode, field is stored but not enforced at runtime. | `engine.py:2024-2036` |
| A5 | Script steps with `run: python3 scripts/foo.py` auto-resolve paths via `flow_dir` | Verified: `_resolve_command()` checks if relative path exists under `flow_dir`, converts to absolute. Also sets `STEPWISE_FLOW_DIR` env var. | `executors.py:144-187` (path resolution), `executors.py:210-212` (env var), `engine.py:1106-1107` (flow_dir injection) |
| A6 | Script inputs are delivered as env vars (strings) plus JSON file at `$JOB_ENGINE_INPUTS` | Verified: `start()` writes inputs to JSON file, also sets each input as an env var (strings direct, dicts/lists JSON-encoded). | `executors.py:189-220` |
| A7 | `--wait` JSON output includes `outputs` field with terminal step artifacts | Verified: `_json_stdout({"status": "completed", "outputs": engine.terminal_outputs(job.id), ...})`. `terminal_outputs()` returns list of artifact dicts from terminal steps. | `runner.py:709-715`, `engine.py:442-455` |
| A8 | `--var KEY=VALUE` passes job-level inputs accessible as `$job.KEY` in flow | Verified: `--var` parsed at CLI, merged into `inputs` dict passed to `engine.create_job()`. | `cli.py:3131-3132` (argparse), `cli.py:1309-1312` (parsing) |
| A9 | The welcome flow exists and completes without human input for preflight validation | **NEEDS VERIFICATION AT RUNTIME**: welcome flow starts with a `human` step (`pick-feature`), so `stepwise validate` will pass but `run --wait` will suspend. Preflight should only `validate`, not `run`, the welcome flow. | `flows/welcome/FLOW.yaml:19-34` |
| A10 | No `max_concurrent` flow-level setting exists; all ready steps run concurrently | Verified: not in `StepDefinition` or `WorkflowDefinition`. Engine dispatches all ready steps. | `models.py:385-403` (StepDefinition), `models.py:487-512` (WorkflowDefinition) |

## Out of Scope

| Exclusion | Rationale |
|-----------|-----------|
| **Fixing defects found by the evaluation** | This flow measures the 0.6→1.0 gap; remediation is a separate workstream driven by the flow's output. Coupling measurement and remediation in one flow would create circular dependencies and conflate the eval signal. |
| **Web UI visual testing** (Playwright/Cypress) | Playwright adds ~200MB of browser binary dependencies and requires a running display server or Xvfb. The eval runs in headless/CI contexts. UX assessment via agent code inspection (reading `web/src/` component files) covers functional completeness without infrastructure burden. Visual regression testing is a separate concern. |
| **Multi-model agent diversity within a single run** | Running 3 different models per synthesis step would 3× agent costs ($36→$108/run) for marginal accuracy improvement. The 3-run variance analysis in the harness (R7) provides a better signal on score stability. If single-model variance is >15% across runs, the rubric is the problem, not the model. |
| **Playwright-based end-to-end server tests** | test-core uses HTTP API calls and subprocess checks, which test the actual server endpoints. Adding Playwright for browser testing adds complexity without improving the signal for hard gate dimensions. |
| **0.6-specific backwards compatibility corpus** | There is no frozen 0.6 flow corpus to test against. M1 validates all flows currently in the repo, which is the practical backwards compat surface. If a formal 0.6 corpus is needed, that's a prerequisites task, not part of this flow. |
| **The actual 0.6→1.0 upgrade work** | The flow produces a prioritized remediation roadmap (R4's P0/P1/P2 priorities); executing that roadmap is tracked separately. |
| **PyPI publishing or release automation** | Stepwise distributes via `git+https://` (per CLAUDE.md "Distribution & Releases"). Release automation is orthogonal to readiness evaluation. |

## Architecture

### Directory Structure

```
flows/eval-1.0/
├── FLOW.yaml                    # 16-step flow definition (~200 lines)
├── scripts/
│   ├── preflight.py             # Phase 0: smoke tests + ground truth calibration
│   ├── discover.py              # Phase 1: codebase metadata collection
│   ├── test_core.py             # Phase 2: CLI + Server + Lifecycle (HARD GATE)
│   ├── test_security.py         # Phase 2: Security posture (HARD GATE, severity model)
│   ├── test_migration.py        # Phase 2: Backwards compat (HARD GATE)
│   ├── test_data_integrity.py   # Phase 2: Data integrity (HARD GATE, NEW)
│   ├── test_quality.py          # Phase 2: 7 non-gate dimensions batched
│   ├── aggregate.py             # Phase 4: Scoring + gates + three-state logic
│   └── publish.py               # Phase 6: HTML report generation (self-contained)
├── prompts/
│   ├── adversarial-probe.md     # Phase 2b: Chaos engineering agent prompt
│   ├── new-user-test.md         # Phase 2b: New user simulation prompt
│   ├── synthesize-docs.md       # Phase 3: Documentation synthesis
│   ├── synthesize-code.md       # Phase 3: Code quality synthesis
│   ├── synthesize-ux.md         # Phase 3: UX synthesis
│   └── generate-report.md       # Phase 4b: LLM report generation prompt
├── data/
│   └── known-bad.flow.yaml      # Ground truth: deliberately broken flow (3+ defects)
└── run-eval-3x.sh               # Outer harness: 3-run with adaptive early stopping
```

### How It Fits Existing Patterns

**Directory flow convention** (`flow_resolution.py:52-68`): `eval-1.0/FLOW.yaml` is discovered when user runs `stepwise run eval-1.0`. The flow resolution searches `flows/` subdirectory for a directory matching the name, then looks for `FLOW.yaml` marker inside.

**Script path resolution** (`executors.py:144-187`): `run: python3 scripts/preflight.py` is resolved by `ScriptExecutor._resolve_command()` which checks if `scripts/preflight.py` exists under `flow_dir` and converts to absolute path. The engine injects `flow_dir` from `WorkflowDefinition.source_dir` at `engine.py:1106-1107`.

**Prompt file loading** (`yaml_loader.py:179-216`): `prompt_file: prompts/adversarial-probe.md` is resolved relative to the YAML file's directory at parse time. Content replaces `config["prompt"]`. Tested for agent executors at `test_m10_flow_dir.py:318-334`.

**Input delivery to scripts** (`executors.py:189-220`): Each input is available as an env var (string inputs direct, dict/list inputs JSON-encoded). Also available as JSON file at `$JOB_ENGINE_INPUTS`. Scripts read inputs via `os.environ["stepwise_path"]` or `json.load(open(os.environ["JOB_ENGINE_INPUTS"]))`.

**Human step output schema** (`models.py:357-380`, `yaml_loader.py:860-900`): `OutputFieldSpec` supports typed outputs (`choice`, `text`, `number`, `bool`) with options lists. Used by `flows/welcome/FLOW.yaml:30-34` for the `pick-feature` step. The web UI renders these as form inputs in `FulfillWatchDialog.tsx`.

**--wait JSON output** (`runner.py:709-715`): Terminal step artifacts are collected by `engine.terminal_outputs()` at `engine.py:442-455` and included in the `outputs` field. The harness parses this to check gate results.

### Step DAG (16 steps)

```
preflight                         Phase 0: ground truth calibration + smoke test
    │ (sequencing)
    ▼
discover                          Phase 1: codebase metadata
    │ (input: project_path)
    ├──→ test-core          ┐
    ├──→ test-security      │
    ├──→ test-migration     │     Phase 2: parallel automated testing
    ├──→ test-data-integrity│     (5 scripts + 2 agents, all launch concurrently)
    ├──→ test-quality       │
    ├──→ adversarial-probe  │
    └──→ new-user-test      ┘
              │ (inputs: evidence from Phase 2)
    ├──→ synthesize-docs  ──┐
    ├──→ synthesize-code  ──┤     Phase 3: qualitative synthesis (3 agents)
    └──→ synthesize-ux    ──┘
              │ (inputs: all scores)
              ▼
       aggregate-scores           Phase 4: hard gates + three-state scoring
              │
              ▼
       generate-report            Phase 4b: LLM markdown report
              │
              ▼
       human-approval             Phase 5: human gate (approve/reject/override)
              │
              ▼
       publish-report             Phase 6: HTML generation + gate pass-through
```

**Dependency wiring:**
- `discover` → `sequencing: [preflight]` (no data dep, just ordering)
- Phase 2 scripts → `inputs: { project_path: discover.project_path, server_port: $job.server_port }`
- Phase 2 agents → `inputs: { project_path: discover.project_path }` + `working_dir` from discover
- Phase 3 agents → inputs from Phase 2 script evidence + probe results
- `aggregate-scores` → inputs from all Phase 2 scripts + Phase 3 agents (`score_pct` fields)
- `generate-report` → inputs from aggregate + probe/new-user raw data
- `human-approval` → inputs from aggregate (scorecard, recommendation)
- `publish-report` → inputs from generate-report + human-approval + aggregate (gate pass-through)

### Evidence Packet Schema (Three-State)

Every script step outputs rubric items in this format:

```json
{
  "id": "S1",
  "requirement": "API endpoints reject unauthenticated requests",
  "result": "pass",
  "evidence": "GET /api/jobs returned 401 without token",
  "file": "src/stepwise/server.py:142"
}
```

Where `result` is one of: `"pass"`, `"fail"`, `"insufficient_evidence"`.

For the security dimension only, each item also includes:
```json
{ "severity": "blocker" }
```

Dimension-level output structure:
```json
{
  "dimension": "security",
  "rubric_results": [...],
  "pass_count": 7,
  "fail_count": 1,
  "insufficient_count": 2,
  "score_pct": 87,
  "has_blocker": true,
  "blocker_ids": ["S4"]
}
```

### Hard Gate Dimensions (4)

| Gate | Dimension(s) | Threshold | Veto Rule |
|------|-------------|-----------|-----------|
| Security | Security Posture | ≥80% | Any `blocker` severity = auto NO-GO |
| Core Execution | CLI + Server + Lifecycle | ≥80% (combined avg) | — |
| Migration | Backwards Compat | ≥80% | — |
| Data Integrity | Store + Artifacts | ≥80% | — |

### Scoring Formula

```
dimension_score = pass_count / (pass_count + fail_count) * 100
  # insufficient_evidence items excluded from denominator
  # warning if insufficient_count / total_count > 0.3
  # if pass_count + fail_count == 0: score = 0 (all insufficient)

overall_score = mean(all dimension scores)
  # GO requires: all 4 hard gates ≥80% AND overall ≥75% AND no security blockers
```

### Key Design Decisions

1. **Scripts as external `.py` files** (not inline heredocs): Keeps FLOW.yaml at ~200 lines (vs. 2000+ in the design doc). Scripts are independently testable (`python3 scripts/foo.py` with env vars). Uses the M10 flow\_dir resolution (`executors.py:144-187`) verified by `test_m10_flow_dir.py`.

2. **Single agent per synthesis dimension** (not A+B pairs): Halves agent cost from ~$24 to ~$12/run. Multi-rater variance signal moves to the 3-run harness (cross-run variance > within-run agent disagreement). Each agent prompt includes explicit "be conservative — score True only when clearly met" instruction.

3. **Self-contained HTML report**: `publish.py` uses `string.Template` from stdlib with an inline HTML/CSS template. No Jinja2, no external generators. Color-coded scorecard (green ≥80%, yellow 60-79%, red <60%).

4. **Ground truth calibration**: Purpose-built `known-bad.flow.yaml` with 3+ deliberate defects. Preflight confirms the validator catches them before running expensive Phase 2. This validates that the testing infrastructure works — the "test of the tests."

5. **publish-report passes through gate data**: Terminal step outputs include `all_gates_passed`, `security_veto`, `overall_avg` alongside `report_path` and `status` — enabling the harness to parse `--wait` JSON output (`runner.py:712: "outputs": engine.terminal_outputs(job.id)`) for adaptive early stopping.

## Implementation Steps

### Step 1: Create directory structure (15 min)

Create the flow directory tree:
```bash
mkdir -p flows/eval-1.0/{scripts,prompts,data}
```

**Depends on**: nothing
**Produces**: empty directory structure for subsequent steps

### Step 2: Write `data/known-bad.flow.yaml` — ground truth test data (15 min)

A deliberately broken flow with 3+ defects:
1. **No `steps:` key** — triggers "Workflow must have steps" error in `models.py:WorkflowDefinition.validate()` at line 529
2. **Circular dependency** (a→b→a with no optional edge) — caught by cycle detection in `models.py:_detect_cycles()` at line 588
3. **Reference to nonexistent upstream output** — caught by input validation in `models.py:validate()` at line 549

Verify: `stepwise validate flows/eval-1.0/data/known-bad.flow.yaml` must exit non-zero.

Note: each defect should be in a separate known-bad file (since the first error may halt parsing). Alternatively, one file with the first detected error is sufficient for the ground truth check — preflight just needs to confirm validator catches *something*. Use a single file with the circular dependency defect (most robust — it parses but fails validation).

**Files**: `flows/eval-1.0/data/known-bad.flow.yaml`
**Depends on**: Step 1

### Step 3: Write `scripts/preflight.py` (30 min)

Six checks, all must pass:

| Check | Method | Pass criterion |
|-------|--------|---------------|
| CLI exists | `subprocess.run(["stepwise", "--version"])` | exit code 0 |
| Server alive | `urllib.request.urlopen(f"http://localhost:{port}/api/health")` | HTTP 200 |
| Validator works (good) | `subprocess.run(["stepwise", "validate", welcome_flow])` | exit code 0 |
| Validator works (bad) | `subprocess.run(["stepwise", "validate", known_bad_flow])` | exit code ≠ 0 |
| Project structure | `os.path.isdir()` for `src/stepwise`, `tests`, `web/src` | all exist |
| pyproject.toml | `tomllib.load()` succeeds, `version` key present | no exception |

Input env vars: `stepwise_path`, `server_port` (delivered per `executors.py:214-220`).
Known-bad flow path: resolved via `STEPWISE_FLOW_DIR` env var (`executors.py:212`) as `$STEPWISE_FLOW_DIR/data/known-bad.flow.yaml`.

Output JSON: `{"preflight_checks": [...], "preflight_passed": bool, "abort_reason": str|null}`
On failure: `sys.exit(1)` → engine marks step FAILED → job halts.

**Files**: `flows/eval-1.0/scripts/preflight.py`
**Depends on**: Steps 1-2

### Step 4: Write `scripts/discover.py` (20 min)

Collects codebase metadata. Reads `stepwise_path` from env.

| Output field | Source |
|-------------|--------|
| `version` | `tomllib.load("pyproject.toml")["project"]["version"]` |
| `project_path` | `os.path.expanduser(stepwise_path)` |
| `file_counts` | `os.walk()` counting `.py`, `.ts`, `.tsx`, `.yaml`, `.md` |
| `python_loc` | Line count of `src/stepwise/**/*.py` |
| `typescript_loc` | Line count of `web/src/**/*.{ts,tsx}` |
| `flows` | `glob.glob("flows/*/FLOW.yaml") + glob.glob("examples/**/*.flow.yaml")` |
| `test_files` | `glob.glob("tests/**/test_*.py")` |
| `doc_files` | `glob.glob("docs/*.md") + ["README.md"]` filtered by existence |

Straight port from design doc's discover step with env var input instead of heredoc interpolation.

**Files**: `flows/eval-1.0/scripts/discover.py`
**Depends on**: Step 1

### Step 5: Write `scripts/test_core.py` — CLI + Server + Lifecycle hard gate (1.5 hrs)

Batches 3 v2 steps. Each sub-section produces rubric items in three-state format.

**CLI rubric (C1-C10)**:

| ID | Check | Command | Pass criterion |
|----|-------|---------|---------------|
| C1 | `--version` returns semver | `stepwise --version` | exit 0, stdout matches `\d+\.\d+` |
| C2 | `--help` shows all commands | `stepwise --help` | exit 0, stdout contains "run", "validate", "server" |
| C3 | Validate good flow | `stepwise validate {welcome_flow}` | exit 0 |
| C4 | Validate bad flow | `stepwise validate {bad_flow}` | exit ≠ 0 |
| C5 | `info` shows steps | `stepwise info {flow}` | exit 0, stdout > 10 chars |
| C6 | `run --wait` completes | `stepwise run --wait --local {flow}` | exit 0 — uses `--local` to avoid server delegation for isolation |
| C7 | `jobs` lists jobs | `stepwise jobs` | exit 0 |
| C8 | `config get` works | `stepwise config get` | exit 0 |
| C9 | `schema` outputs JSON | `stepwise schema {flow}` | exit 0, `json.loads(stdout)` succeeds |
| C10 | `diagram` outputs DOT | `stepwise diagram {flow} -f dot` | exit 0, "digraph" in stdout |

Note C6: uses `--local` flag to prevent the eval flow's own server from being re-entered, which would cause contention. The flow to run must be a simple script-only flow — welcome flow has a human step so it would suspend. Use a minimal inline flow: write a temp file with a single echo step.

**Server rubric (SV1-SV8)**: HTTP calls via `urllib.request` to `http://localhost:{port}/api/*`. WebSocket test (SV6) uses `insufficient_evidence` if `websocket-client` is not importable.

**Lifecycle rubric (L1-L5)**: Run-to-completion, cancel mid-execution, job list queryable, step run persistence. L4 (pause/resume on human steps) and L5 (persistence across server restart) are marked `insufficient_evidence` with reason — they require manual interaction or destructive server restart that could interfere with the running eval.

Outputs: `dimension`, `rubric_results`, `pass_count`, `fail_count`, `insufficient_count`, `score_pct`, `cli_results`, `server_results`, `lifecycle_results`.

**Files**: `flows/eval-1.0/scripts/test_core.py`
**Depends on**: Steps 1, 2

### Step 6: Write `scripts/test_security.py` — severity model (1.5 hrs)

Each rubric item has `result` (three-state) AND `severity` (blocker/major/minor).

| ID | Check | Severity | Method |
|----|-------|----------|--------|
| S1 | API auth enforcement | blocker | Check if any auth middleware exists in `server.py` — stepwise currently has no auth, so this is a known `fail` with severity `blocker` |
| S2 | WebSocket auth | major | Attempt unauthenticated WS connect — if accepted, `fail` |
| S3 | Sensitive vars masked | blocker | Set a config var with `sensitive: true`, grep server logs and API response for the value |
| S4 | Shell injection via config | blocker | Create flow with `run: echo $var`, set var to `; echo INJECTED`, check if INJECTED appears in output |
| S5 | No `eval()`/`exec()` in execution paths | blocker | `re.search(r'\beval\s*\(', line)` across `src/stepwise/**/*.py` excluding comments and test files |
| S6 | Agent sandboxing | major | `insufficient_evidence` — requires running an actual agent which is expensive; note as needs-manual-verification |
| S7 | No hardcoded secrets | major | Regex scan for `sk-[a-zA-Z0-9]{20,}`, `AKIA[A-Z0-9]{16}`, `password\s*=\s*["'][^"']+` |
| S8 | No critical CVEs | major | `subprocess.run(["uv", "run", "pip", "audit"])` — `insufficient_evidence` if command not available |
| S9 | Server binds localhost | minor | Read `server.py`, check for `"0.0.0.0"` string vs `"127.0.0.1"`/`"localhost"` |
| S10 | SSRF prevention | major | POST to `/api/jobs` with `notify_url: "http://169.254.169.254/"` — check if rejected |

S1 finding: stepwise has no auth by default. This is `result: "fail", severity: "blocker"`. The flow honestly reports this as a 1.0 gap.

**Files**: `flows/eval-1.0/scripts/test_security.py`
**Depends on**: Step 1

### Step 7: Write `scripts/test_migration.py` (30 min)

| ID | Check | Method | Insufficient when |
|----|-------|--------|-------------------|
| M1 | All repo flows parse | `stepwise validate` each flow in `flows/` + `examples/` | No flows found |
| M2 | CLI commands stable | `stepwise --help`, check for "run", "validate", "info", "jobs", "config", "server" | — |
| M3 | API endpoints respond | HTTP GET to `/api/health`, `/api/jobs`, `/api/flows` | Server unreachable |
| M4 | CHANGELOG exists | `os.path.exists("CHANGELOG.md")` | — |
| M5 | Migration docs exist | Check for "migrat" in docs/*.md or `stepwise --help` | Neither command nor docs mention migration |

**Files**: `flows/eval-1.0/scripts/test_migration.py`
**Depends on**: Step 1

### Step 8: Write `scripts/test_data_integrity.py` — NEW hard gate (45 min)

| ID | Check | Method |
|----|-------|--------|
| DI1 | WAL mode enabled | `sqlite3.connect(db_path)`, `cursor.execute("PRAGMA journal_mode")`, assert `"wal"` |
| DI2 | Foreign keys on | `cursor.execute("PRAGMA foreign_keys")`, assert `1` |
| DI3 | Job artifact round-trip | POST `/api/jobs` (create) → GET `/api/jobs/{id}` → verify fields match |
| DI4 | Step runs retrievable | GET `/api/jobs/{id}/runs` → verify response is list with expected fields |
| DI5 | No orphaned step runs | `SELECT COUNT(*) FROM step_runs WHERE job_id NOT IN (SELECT id FROM jobs)` — requires DB access |
| DI6 | Event log ordering | GET `/api/jobs/{id}/events` → verify timestamps are monotonically non-decreasing |

DB path discovery: `os.environ.get("STEPWISE_DB")` or `os.path.join(project_path, ".stepwise/stepwise.db")` per `store.py:45-47`.

DI5 requires direct SQLite access. If the DB is locked by the server (WAL mode allows concurrent readers, so this should work), execute read-only query. If connection fails, mark `insufficient_evidence`.

DI3-DI4, DI6 use the REST API. DI3 creates a job but doesn't run it (just tests store persistence). No flow execution needed.

**Files**: `flows/eval-1.0/scripts/test_data_integrity.py`
**Depends on**: Step 1

### Step 9: Write `scripts/test_quality.py` — 7 non-gate dimensions (1.5 hrs)

Largest script. Each sub-dimension is an independent function returning its own rubric results.

| Sub-dimension | Items | Key checks |
|--------------|-------|------------|
| Validation (V1-V4) | 4 | All flows validate; malformed rejected; circular deps caught; invalid refs caught |
| Testing (T1-T4) | 4 | `uv run pytest tests/ -q --no-header` — parse summary line for passed/failed/error counts |
| Config (CF1-CF3) | 3 | `stepwise config get`, `config get server.port`, set/get roundtrip |
| Webhooks (W1-W2) | 2 | Spin up `http.server.HTTPServer` on ephemeral port, run flow with `--notify`, check payload received |
| Observability (O1-O3) | 3 | Grep `src/stepwise/` for `import logging`; check `engine.py` for `duration`/`elapsed`; runtime error context |
| Performance (P1-P3) | 3 | `time.time()` around health endpoint (<500ms), `--version` (<2s), validate (<5s) |
| Error DX (E1-E7) | 7 | Submit broken YAML files, verify errors are actionable (no Traceback), mention the specific problem |

Total: 26 rubric items. Combined `score_pct` is mean of sub-dimension scores.

Testing sub-dimension (T1-T4) runs `uv run pytest` which takes ~60-120s. Set internal timeout of 300s. The step has `limits.max_duration_minutes: 15` to cap total runtime.

**Files**: `flows/eval-1.0/scripts/test_quality.py`
**Depends on**: Step 1

### Step 10: Write agent prompt files (1 hr)

Six `.md` files in `prompts/`. Each follows the pattern: context → task → rubric → output format.

**`adversarial-probe.md`**: 4 attack categories (malformed YAML, injection, resource exhaustion, edge cases), 14 tests total. Each test result classified by `severity: blocker|major|minor`. Output: `adversarial_results`, `critical_findings` (blockers), `handled_well`, `silent_failures`. Agent gets `working_dir` set to project path but no codebase access instruction.

**`new-user-test.md`**: Restricted to `docs/` + `README.md` only. Builds fetch→summarize flow. Reports confusion points with severity. Output: `new_user_flow_yaml`, `new_user_succeeded`, `new_user_validation_attempts`, `new_user_confusion_points`, `new_user_docs_consulted`, `new_user_evidence`.

**`synthesize-docs.md`**: Receives `new_user_confusion_points` from new-user-test. Evaluates D1, D5-D8. Instruction: "Score True only when the requirement is clearly and unambiguously met. When evidence is ambiguous, score False." Output: `docs_rubric`, `docs_qualitative`, `docs_score_pct`.

**`synthesize-code.md`**: Receives `security_evidence` and `adversarial_critical`. Evaluates CQ1-CQ6. Must cite specific file:line. Output: `code_rubric`, `code_qualitative`, `code_score_pct`.

**`synthesize-ux.md`**: Evaluates UX1-UX8. Must read `web/src/components/` files. Output: `ux_rubric`, `ux_qualitative`, `ux_score_pct`.

**`generate-report.md`**: Receives full scorecard. Produces structured markdown. Not scored — presentation only.

**Files**: `flows/eval-1.0/prompts/*.md`
**Depends on**: Steps 1, 5-9 (to know exact output field names)

### Step 11: Write FLOW.yaml — full 16-step definition (1 hr)

The FLOW.yaml wires together all scripts and prompts. Key patterns:

```yaml
name: eval-1.0
config:
  stepwise_path: { type: str, default: "~/work/stepwise" }
  server_port: { type: str, default: "8340" }
  eval_run_number: { type: str, default: "1" }

steps:
  preflight:
    run: python3 scripts/preflight.py
    inputs: { stepwise_path: $job.stepwise_path, server_port: $job.server_port }
    outputs: [preflight_checks, preflight_passed, abort_reason]
    limits: { max_duration_minutes: 2 }

  discover:
    run: python3 scripts/discover.py
    sequencing: [preflight]
    inputs: { stepwise_path: $job.stepwise_path }
    outputs: [version, project_path, ...]

  test-core:
    run: python3 scripts/test_core.py
    inputs: { project_path: discover.project_path, server_port: $job.server_port }
    outputs: [dimension, rubric_results, ..., score_pct]
    limits: { max_duration_minutes: 5 }

  # ... (similar pattern for test-security, test-migration, etc.)

  adversarial-probe:
    executor: agent
    prompt_file: prompts/adversarial-probe.md
    working_dir: $project_path  # resolved from discover.project_path
    inputs: { project_path: discover.project_path, server_port: $job.server_port }
    outputs: [adversarial_results, critical_findings, handled_well, silent_failures]
    limits: { max_cost_usd: 5.00, max_duration_minutes: 20 }

  # ... synthesis agents, aggregate, report, human, publish
```

Human approval step uses `output_schema` (per `yaml_loader.py:860-900`):
```yaml
  human-approval:
    executor: human
    prompt: |
      ## Recommendation: $recommendation
      ...
    outputs:
      human_decision:
        type: choice
        options: [approve, reject, override]
      human_notes:
        type: text
        required: false
```

`publish-report` passes through gate data for harness consumption:
```yaml
    outputs: [report_path, status, human_decision, run_number,
              all_gates_passed, security_veto, overall_avg]
```

**Files**: `flows/eval-1.0/FLOW.yaml`
**Depends on**: Steps 2-10 (all scripts and prompts must be written first to confirm exact output field names)

### Step 12: Write `scripts/aggregate.py` (45 min)

Input env vars: `core_score_pct`, `security_score_pct`, `migration_score_pct`, `data_integrity_score_pct`, `quality_score_pct`, `docs_score_pct`, `code_score_pct`, `ux_score_pct`, plus `security_has_blocker`, `security_blocker_ids`.

Logic:
1. Parse all score inputs from env vars (delivered as strings per `executors.py:216`)
2. Hard gate checks: security ≥80% AND no blockers, core ≥80%, migration ≥80%, data\_integrity ≥80%
3. Overall score: mean of all 8 dimension scores
4. Recommendation: NO-GO if any gate fails OR `security_veto` OR overall <75%
5. Remediation: sorted by P0 (failed hard gate), P1 (<60%), P2 (<80%)
6. Insufficient evidence warnings: from upstream rubric data (passed via JSON env vars)

Output: `scorecard`, `gates`, `all_gates_passed`, `security_veto`, `overall_avg`, `recommendation`, `recommendation_reason`, `remediation`, `insufficient_evidence_warnings`, `run_number`.

**Files**: `flows/eval-1.0/scripts/aggregate.py`
**Depends on**: Step 11 (needs to know exact input field names from FLOW.yaml wiring)

### Step 13: Write `scripts/publish.py` (30 min)

Self-contained HTML generation using `string.Template`:

```python
import json, os, string, datetime

# Read inputs from env
report_content = os.environ.get("report_content", "")
human_decision = os.environ.get("human_decision", "approve")
# ... etc

if human_decision.lower() == "reject":
    print(json.dumps({"status": "rejected", "report_path": None, ...}))
    sys.exit(0)

# Generate HTML with inline CSS template
html = TEMPLATE.safe_substitute(content=report_content, ...)

# Write to reports dir
out_path = os.path.join(project_path, f"reports/eval-1.0-run-{run_number}-{date}.html")
Path(out_path).parent.mkdir(parents=True, exist_ok=True)
Path(out_path).write_text(html)

# Output includes gate pass-through for harness
print(json.dumps({
    "report_path": out_path, "status": "published",
    "all_gates_passed": ..., "security_veto": ..., "overall_avg": ...,
    ...
}))
```

**Files**: `flows/eval-1.0/scripts/publish.py`
**Depends on**: Step 11

### Step 14: Write `run-eval-3x.sh` — outer harness (30 min)

```bash
#!/usr/bin/env bash
set -euo pipefail

FLOW="eval-1.0"
RESULTS=()

for RUN in 1 2 3; do
  echo "=== Run $RUN/3 ==="
  OUTPUT=$(stepwise run "$FLOW" --wait --var eval_run_number="$RUN" 2>/dev/null)
  RESULTS+=("$OUTPUT")

  if [ "$RUN" -eq 1 ]; then
    # Parse Run 1 for adaptive early stopping
    GATES_PASSED=$(echo "$OUTPUT" | python3 -c "
      import json, sys
      data = json.loads(sys.stdin.read())
      outputs = data.get('outputs', [{}])[0]
      print(outputs.get('all_gates_passed', 'false'))
    ")
    if [ "$GATES_PASSED" != "True" ]; then
      echo "Hard gate failed in Run 1. Skipping Runs 2 and 3."
      break
    fi
  fi
done

# Variance analysis across completed runs
python3 -c "
import json, sys
# ... parse RESULTS, compare dimension scores across runs
# ... flag >15% variance
"
```

**Files**: `flows/eval-1.0/run-eval-3x.sh`
**Depends on**: Step 11

### Step 15: Validate flow (15 min)

```bash
stepwise validate flows/eval-1.0/FLOW.yaml
```

Fix any errors:
- Input binding refs (typos in `step.field` paths)
- Missing output declarations
- Unbounded loops (should be none in this flow — no exit rules with `action: loop`)
- Warnings from `models.py:warnings()` at line 570

Target: exit 0, zero warnings.

**Depends on**: Steps 1-14

### Step 16: Dry-run preflight in isolation (15 min)

```bash
# Ensure server is running
stepwise server start

# Test preflight script directly
cd ~/work/stepwise
export stepwise_path="$HOME/work/stepwise"
export server_port="8340"
export STEPWISE_FLOW_DIR="$PWD/flows/eval-1.0"
python3 flows/eval-1.0/scripts/preflight.py | python3 -m json.tool
```

Verify:
- Output is valid JSON
- All 6 checks pass
- Ground truth calibration works (known-bad detected)

If `stepwise validate` is not on PATH in the script subprocess, ensure `uv run stepwise` works or that the dev install is active.

**Depends on**: Steps 1-3, 15

### Step 17: Full flow smoke test (30 min)

```bash
stepwise run eval-1.0 --watch
```

In web UI, verify:
- DAG shows all 16 steps
- preflight → discover → Phase 2 parallel launch
- Human approval gate renders with scorecard
- After approval, publish step generates HTML

This costs ~$12-15 for agent steps. Only run once during implementation. If agent API keys are not configured, agent steps will fail — that's expected and tests the error handling path.

**Depends on**: Steps 1-16

## Testing Strategy

### 1. Static Validation (automated, 0 cost)
```bash
# Must exit 0, zero warnings
stepwise validate flows/eval-1.0/FLOW.yaml
echo $?  # expect: 0
```

### 2. Ground Truth Verification (automated, 0 cost)
```bash
# Must fail — known-bad flow
stepwise validate flows/eval-1.0/data/known-bad.flow.yaml
echo $?  # expect: non-zero (1 or 2)

# Must pass — known-good flow
stepwise validate flows/welcome/FLOW.yaml
echo $?  # expect: 0
```

### 3. Individual Script Testing (automated, 0 cost except test_quality)

Each script can be tested in isolation by setting env vars:
```bash
cd ~/work/stepwise
export stepwise_path="$HOME/work/stepwise"
export server_port="8340"
export STEPWISE_FLOW_DIR="$PWD/flows/eval-1.0"

# Test each script, verify valid JSON output
python3 flows/eval-1.0/scripts/preflight.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/discover.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/test_core.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/test_security.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/test_migration.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/test_data_integrity.py 2>/dev/null | python3 -m json.tool
python3 flows/eval-1.0/scripts/test_quality.py 2>/dev/null | python3 -m json.tool  # ~2 min (runs pytest)
```

For each, verify:
- Exit code 0
- Output is valid JSON
- Contains `dimension`, `rubric_results`, `score_pct` fields
- Every rubric item has `result` in `["pass", "fail", "insufficient_evidence"]`
- Security items have `severity` in `["blocker", "major", "minor"]`

### 4. Aggregate Script Testing (automated, 0 cost)
```bash
# Mock inputs for aggregate.py
export core_score_pct="90"
export security_score_pct="60"
export security_has_blocker="true"
export security_blocker_ids='["S1"]'
export migration_score_pct="80"
export data_integrity_score_pct="100"
export quality_score_pct="75"
export docs_score_pct="70"
export code_score_pct="80"
export ux_score_pct="75"
export eval_run_number="1"

python3 flows/eval-1.0/scripts/aggregate.py | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
assert data['security_veto'] == True, 'Should veto on blocker'
assert data['recommendation'] == 'NO-GO', 'Should be NO-GO with blocker'
assert data['all_gates_passed'] == False, 'Security gate should fail'
print('Aggregate test PASSED')
"
```

### 5. Three-State Rubric Verification (automated)
```bash
# Verify no rubric item uses boolean instead of three-state
for script in flows/eval-1.0/scripts/test_*.py; do
  python3 "$script" 2>/dev/null | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for item in data.get('rubric_results', []):
    result = item.get('result')
    assert result in ('pass', 'fail', 'insufficient_evidence'), \
        f'{item[\"id\"]}: result={result!r} is not three-state'
print(f'  {data[\"dimension\"]}: all {len(data[\"rubric_results\"])} items are three-state')
"
done
```

### 6. Full Integration Test (~$12-15 agent cost)
```bash
stepwise server start
stepwise run eval-1.0 --watch
```

Check in web UI:
- All 16 steps appear in DAG
- Phase 0+1 complete in <1 min
- Phase 2 steps launch in parallel (7 simultaneous)
- Agent steps show streaming output
- `human-approval` presents scorecard with approve/reject/override options
- After approval, report HTML is generated

### 7. Harness Test (3x integration cost)
```bash
chmod +x flows/eval-1.0/run-eval-3x.sh
./flows/eval-1.0/run-eval-3x.sh
```

Verify:
- If Run 1 gates fail → Runs 2+3 skipped with message
- If Run 1 gates pass → all 3 runs complete
- Variance analysis printed at end

### 8. Regression (existing tests unaffected)
```bash
uv run pytest tests/                    # Python tests still pass
cd web && npm run test && npm run lint  # Web tests/lint pass
```

Adding flow files to `flows/` should not affect any existing tests.

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Agent steps expensive ($10-15/run) | Budget overrun over 3 runs | High | `limits.max_cost_usd: 5.00` per agent step (enforced when `billing_mode == "api_key"`, per `engine.py:2024-2036`). Single agent per synthesis dimension. Budget ~$12/run vs. v2's ~$36. |
| SQLite contention with 7 parallel Phase 2 steps | `database is locked` errors | Medium | Scripts access data via HTTP API (not direct DB). ThreadSafeStore (`server.py`) serializes SQLite access via `threading.Lock`. Only `test_data_integrity.py` does direct DB reads (read-only, WAL allows concurrent readers). |
| Welcome flow has human step → can't `run --wait` for C6 test | C6 rubric item fails or hangs | High | C6 uses a purpose-built minimal flow (temp file with single echo step) instead of welcome flow. Preflight only `validate`s welcome flow, never runs it. |
| `prompt_file` has no production usage precedent | Parse-time failure | Low | Feature is well-tested: 7 unit tests in `test_m10_flow_dir.py:247-380` covering LLM, agent, human, and directory flow variants. Tests verified against actual codebase code at `yaml_loader.py:179-216`. |
| Security S1 will always fail (no auth exists) | Automatic NO-GO on every run | High | This is by design — the eval honestly measures 1.0 readiness gaps. S1 failure means auth is a prerequisite for 1.0. The flow reports it; fixing it is out of scope (separate remediation). |
| Preflight ground truth becomes stale | False confidence in validator | Low | Known-bad tests fundamental validator behavior (circular deps, missing steps) unlikely to regress. The defects tested are structural, not feature-dependent. |
| `--wait` output format changes break harness | Harness fails to parse | Low | Output format is stable (`runner.py:709-715`), used by existing CLI consumers. Harness uses defensive JSON parsing with `.get()` defaults. |
| Agent synthesis scores inconsistent across runs | Unreliable scores | Medium | 3-run harness flags >15% variance. Agent prompts include "Score True only when clearly met" instructions. Script-determined pass/fail is authoritative. |
| test_quality runs pytest (60-120s) | Step times out | Medium | `limits.max_duration_minutes: 15` on step. Internal pytest timeout of 300s. If timeout hits, output `insufficient_evidence` for T1-T4 with "test suite timed out" evidence. |
| `insufficient_evidence` used excessively | Artificially inflated scores | Medium | 30% threshold triggers warning in aggregate. Each dimension's insufficient count reported in scorecard. Human reviewer sees the raw data in approval gate. |
| Scripts fail to find stepwise on PATH | All subprocess calls fail | Low | Preflight check #1 (`stepwise --version`) catches this immediately, aborts before any expensive steps run. |
| Direct DB access in DI5 conflicts with WAL lock | `test_data_integrity` fails | Low | WAL mode allows concurrent readers (`store.py:62` enables WAL). Script opens connection in read-only mode. If connection fails, mark DI5 as `insufficient_evidence`. |
