---
title: "Implementation Plan: DX Polish — N1 (stepwise new template) + N4 (server project visibility)"
date: "2026-03-21T14:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: DX Polish — N1 + N4

## Overview

Two small, independent DX improvements: (1) upgrade the `stepwise new` template from a trivial single-step echo to a 3-step script→LLM→script flow demonstrating dependencies and multiple executor types, and (2) add project path to the job submission message when delegating to a running server.

## Requirements

### N1: Multi-step `stepwise new` template

| # | Requirement | Acceptance Criteria |
|---|---|---|
| N1.1 | `stepwise new test-flow` creates a 3-step template | FLOW.yaml contains exactly 3 steps: `gather-info`, `analyze`, `format-report` |
| N1.2 | Template demonstrates script→LLM→script data flow | `gather-info` uses `run:`, `analyze` uses `executor: llm` with `prompt:` (top-level, not under `config:`), `format-report` uses `run:` |
| N1.3 | Input wiring between steps is demonstrated | `analyze.inputs` references `gather-info.*`, `format-report.inputs` references `analyze.*`; at least one step references `$job.*` |
| N1.4 | `stepwise validate` passes on the generated template | `load_workflow_yaml()` succeeds, `wf.validate()` returns `[]`, `wf.warnings()` returns `[]` |
| N1.5 | Comments explain key concepts | Template YAML contains inline `#` comments explaining `run:` vs `executor:`, `inputs:` wiring, and `outputs:` |
| N1.6 | Template run instructions are included | Header comments show how to run the flow (e.g. `stepwise run {name} --var topic="..."`) |
| N1.7 | Existing tests updated | Existing `TestNewCommand` tests in `tests/test_flow_resolution.py:240-269` updated to match new template content |

### N4: Server project visibility on submission

| # | Requirement | Acceptance Criteria |
|---|---|---|
| N4.1 | Submission message includes project path | Output shows `▸ job submitted to running server (~/work/project)` |
| N4.2 | Graceful fallback when project_path missing | If health JSON lacks `project_path` key or value is `null`, output is unchanged: `▸ job submitted to running server` |
| N4.3 | Graceful fallback when health request fails | If GET `/api/health` throws any exception, output is unchanged |
| N4.4 | Home directory shortened to `~` | `/home/user/work/project` → `~/work/project`; non-home paths pass through unchanged |
| N4.5 | No noticeable delay | Health fetch uses 2s timeout on a localhost socket; adds <5ms in normal case |
| N4.6 | Only `_submit_watch_job` is modified | Other submission paths (`_submit_job_when_ready` in `cli.py:1797`, `_delegated_create_and_start` in `runner.py:169`) are out of scope — they're either background threads or headless IOAdapter paths |

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | `FLOW_DIR_MARKER` = `"FLOW.yaml"` | Read `src/stepwise/flow_resolution.py:9`: `FLOW_DIR_MARKER = "FLOW.yaml"` |
| A2 | `FLOW_NAME_PATTERN` = `[a-zA-Z0-9_-]+` | Read `src/stepwise/flow_resolution.py:11`: `FLOW_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")` |
| A3 | `/api/health` already returns `project_path` | Read `src/stepwise/server.py:943-951`: response dict includes `"project_path": str(_project_dir) if _project_dir else None` |
| A4 | `_submit_watch_job` is the only user-facing "job submitted" print | Grep for `"job submitted"` across `src/stepwise/` returned exactly one hit: `cli.py:1788` |
| A5 | `_submit_watch_job` has `server_url` as a parameter | Read `cli.py:1740-1741`: `def _submit_watch_job(server_url: str, ...)` |
| A6 | `cmd_new` template is an inline f-string, not file-based | Read `cli.py:994-1002`: template built as `f"name: {name}\n..."` |
| A7 | Existing tests for `cmd_new` exist at `tests/test_flow_resolution.py:240-269` | Read class `TestNewCommand` with 3 test methods: `test_creates_directory_flow`, `test_existing_directory_errors`, `test_invalid_name_errors` |
| A8 | For `executor: llm`, `prompt` is a top-level step key, not nested under `config:` | Read `src/stepwise/yaml_loader.py:279-288`: LLM parser reads `step_data.get("prompt")`, `step_data.get("model")`, etc. directly from step dict |
| A9 | `urllib.request` and `json` are already imported inside `_submit_watch_job` | Read `cli.py:1749-1751`: local imports at function start |
| A10 | `Path` is already imported at module level in `cli.py` | Read `cli.py:36`: `from pathlib import Path` |
| A11 | `wf.validate()` checks that input bindings reference existing steps and declared outputs | Read `models.py:561-620`: validates source_step existence, source_field in outputs list, sequencing refs, duplicate locals |
| A12 | `wf.warnings()` checks for unbounded loops and missing catch-alls | Read `models.py:720-810`: warns on loop rules without `max_iterations`, exit rules without `when: 'True'` catch-all, uncovered output combos |
| A13 | Template with no exit rules produces no warnings | Read `models.py:727-729`: `if not step.exit_rules: continue` — steps without exit rules skip all warning checks |

## Out of Scope

| Item | Why excluded |
|---|---|
| H14 (agent retry), H16 (stagger parallel agents) | Spec explicitly excludes; needs deeper investigation |
| Server or web UI changes | Spec explicitly excludes |
| `_submit_job_when_ready` (`cli.py:1797`) | Background thread for new server startup; prints "entering flow..." not "job submitted"; project is always the current one |
| `_delegated_create_and_start` (`runner.py:169`) | Headless delegation path using `IOAdapter` — not user-facing terminal output |
| `_delegated_run_flow` / `_delegated_run_wait` / `_delegated_run_async` (`runner.py`) | Same — headless paths that don't print submission messages |

## Architecture

### N1: Template change — how it fits existing patterns

**Single file:** `src/stepwise/cli.py`, function `cmd_new` (lines 994-1002).

The template is an inline f-string assigned to `template` and written to `flow_dir / FLOW_DIR_MARKER`. This pattern stays the same — only the string content changes.

**Validation pipeline the template must pass** (from `cmd_validate` at `cli.py:625-660`):
1. `load_workflow_yaml(str(flow_path))` — parses YAML, calls `_parse_executor()` for each step, builds `WorkflowDefinition` (`yaml_loader.py:220-290`)
2. `wf.validate()` — checks input binding references, sequencing refs, duplicate locals (`models.py:561-620`)
3. `wf.warnings()` — checks unbounded loops, missing catch-alls, output coverage (`models.py:720-810`)

**Template must not produce warnings.** Per assumption A13, steps without exit rules skip all warning checks, so a simple 3-step linear flow with no exit rules will produce zero warnings.

**LLM executor YAML format** (from `yaml_loader.py:279-288`): The `prompt` field is a top-level step key, not nested under `config:`. The parser reads `step_data.get("prompt")` directly. The CLAUDE.md example showing `config: { prompt: ... }` is outdated/misleading for the `llm` executor type. The correct format is:

```yaml
analyze:
  executor: llm
  prompt: "..."     # top-level, not config.prompt
  inputs: { ... }
  outputs: [...]
```

### N4: Submission message enhancement — how it fits existing patterns

**Single file:** `src/stepwise/cli.py`, function `_submit_watch_job` (lines 1787-1789).

**Why a separate health fetch is acceptable:** The `detect_server()` call in `_run_watch()` (`cli.py:1701`) already probes `/api/health` via `_probe_health()` (`server_detect.py:114-125`), but it only returns a URL string — not the health payload. Refactoring `detect_server()` to also return the payload would touch `server_detect.py` + all callers. Since this is a localhost request (<5ms), adding one GET inside `_submit_watch_job` is simpler and has no user-perceptible cost.

**`Path.home()`** is available via the module-level `from pathlib import Path` at `cli.py:36`. No new imports needed.

**`urllib.request` and `json`** are already imported locally inside `_submit_watch_job` at `cli.py:1749-1751`.

## Implementation Steps

### Step 1: Update template string in `cmd_new`

**File:** `src/stepwise/cli.py`, lines 994-1002
**Depends on:** Nothing
**What:** Replace the 7-line single-step f-string template with a ~30-line 3-step template.

**Sub-steps:**
1. **1a.** Write the new template string as a Python f-string. Must handle:
   - `{name}` interpolation for flow name
   - `{{` / `}}` for literal YAML/JSON braces (f-string escaping)
   - Inline YAML `#` comments for each step explaining the pattern
   - Header comments showing `stepwise run {name} --var topic="your topic"`
2. **1b.** The 3 steps:
   - `gather-info`: `run:` step with `inputs: { topic: $job.topic }`, `outputs: [topic, timestamp]`. Shell command echoes JSON with the topic and a timestamp.
   - `analyze`: `executor: llm` with top-level `prompt:` field (not under `config:`), `inputs: { topic: gather-info.topic, timestamp: gather-info.timestamp }`, `outputs: [summary]`.
   - `format-report`: `run:` step with `inputs: { topic: gather-info.topic, summary: analyze.summary }`, `outputs: [report]`.
3. **1c.** Update the output messages at `cli.py:1006-1007` to include a hint about `--var topic=...`:
   - Change `"Run:  stepwise run {name}"` to `"Run:  stepwise run {name} --var topic=\"your topic\""`

**Verification:** Write the template to a temp file, run `load_workflow_yaml()` + `wf.validate()` + `wf.warnings()` in a Python REPL to confirm zero errors and zero warnings before committing.

### Step 2: Update existing `TestNewCommand` tests

**File:** `tests/test_flow_resolution.py`, lines 240-269 (class `TestNewCommand`)
**Depends on:** Step 1

**Sub-steps:**
1. **2a.** Update `test_creates_directory_flow` (`test_flow_resolution.py:243-254`):
   - Current assertion `assert "hello from my-flow" in content` will fail — replace with assertions for the new template content:
     - `assert "name: my-flow" in content` (unchanged)
     - `assert "gather-info:" in content`
     - `assert "analyze:" in content`
     - `assert "format-report:" in content`
     - `assert "executor: llm" in content`
   - Keep the `assert "Created" in combined` check
2. **2b.** Add `test_new_template_validates` to `TestNewCommand`:
   - Create flow via `main(["--project-dir", str(tmp_path), "new", "test-flow"])`
   - Load with `load_workflow_yaml(str(marker_path))`
   - Assert `wf.validate() == []`
   - Assert `wf.warnings() == []`
   - Assert `len(wf.steps) == 3`
   - Assert step names are `{"gather-info", "analyze", "format-report"}`
   - Assert `wf.steps["analyze"].executor.type == "llm"`
   - Assert `wf.steps["analyze"].inputs` references `gather-info`
   - Assert `wf.steps["format-report"].inputs` references `analyze`
3. **2c.** `test_existing_directory_errors` and `test_invalid_name_errors` (`test_flow_resolution.py:256-269`) — no changes needed, these test guard rails not template content.

### Step 3: Add project path to submission message

**File:** `src/stepwise/cli.py`, lines 1787-1789 (inside `_submit_watch_job`)
**Depends on:** Nothing (independent of N1)

**Sub-steps:**
1. **3a.** Insert ~8 lines between the current line 1786 (`return EXIT_JOB_FAILED`) and line 1787 (`job_url = ...`). After `job_url` is computed, before the print:
   ```python
   project_label = ""
   try:
       with urllib.request.urlopen(
           f"{server_url}/api/health", timeout=2
       ) as health_resp:
           health_data = json.loads(health_resp.read())
           pp = health_data.get("project_path")
           if pp:
               home = str(Path.home())
               if pp.startswith(home):
                   pp = "~" + pp[len(home):]
               project_label = f" ({pp})"
   except Exception:
       pass
   ```
2. **3b.** Change line 1788 from:
   ```python
   print(f"▸ job submitted to running server")
   ```
   to:
   ```python
   print(f"▸ job submitted to running server{project_label}")
   ```

### Step 4: Add tests for project path in submission message

**File:** `tests/test_watch.py` (add new class `TestSubmitWatchJobProjectPath`)
**Depends on:** Step 3

The existing `TestWatchMode` class (`test_watch.py:35`) tests `_submit_watch_job` indirectly via `main()` with mocks. For N4 testing, we need to test `_submit_watch_job` more directly to control the health response.

**Sub-steps:**
1. **4a.** Import `_submit_watch_job` from `stepwise.cli` (add to existing import line at `test_watch.py:8`).
2. **4b.** Create `TestSubmitWatchJobProjectPath` class with these tests:

   - **`test_shows_project_path`**: Mock `urllib.request.urlopen` to return different responses for `/api/jobs` (job creation), `/api/jobs/.../start` (start), and `/api/health` (health with `project_path`). Call `_submit_watch_job(...)`. Capture stdout via `capsys`. Assert output contains `(~/work/my-project)` (where `/home/...` is `Path.home()`-relative). Use `monkeypatch` on `Path.home()` to control the home prefix.

   - **`test_no_project_path_in_health`**: Same mock setup but health response omits `project_path`. Assert output is `▸ job submitted to running server` with no parenthetical.

   - **`test_health_request_fails`**: Mock health URL to raise `urllib.error.URLError`. Assert output is the original message. Job creation and start still succeed.

   - **`test_non_home_path_not_shortened`**: Health response returns `project_path: "/opt/projects/my-app"`. Assert output contains `(/opt/projects/my-app)` — no `~` substitution.

3. **4c.** Each test mocks `urllib.request.urlopen` using a side_effect function that inspects the URL to return different responses for `/api/jobs`, `/api/jobs/.../start`, and `/api/health`. Pattern from existing `test_watch.py:141-143`: use `unittest.mock.patch`.

### Step 5: Run full test suite

**Depends on:** Steps 1-4
**Commands:**
```bash
# Run changed test files
uv run pytest tests/test_flow_resolution.py::TestNewCommand -v
uv run pytest tests/test_watch.py::TestSubmitWatchJobProjectPath -v

# Full Python test suite
uv run pytest tests/

# Web tests (no changes expected, but confirm no regressions)
cd web && npm run test
```

## Testing Strategy

### N1 Test Matrix

| Test case | File:line | Method | Requirement |
|---|---|---|---|
| Template has 3 named steps | `test_flow_resolution.py:TestNewCommand::test_creates_directory_flow` | Check raw YAML for `gather-info:`, `analyze:`, `format-report:` | N1.1 |
| Template parses and validates clean | `test_flow_resolution.py:TestNewCommand::test_new_template_validates` | `load_workflow_yaml()` + `wf.validate() == []` + `wf.warnings() == []` | N1.4 |
| LLM executor type on analyze step | `test_flow_resolution.py:TestNewCommand::test_new_template_validates` | `wf.steps["analyze"].executor.type == "llm"` | N1.2 |
| Input wiring correct | `test_flow_resolution.py:TestNewCommand::test_new_template_validates` | Check `inputs` on `analyze` and `format-report` reference correct source steps/fields | N1.3 |
| Comments present | `test_flow_resolution.py:TestNewCommand::test_creates_directory_flow` | `assert "#" in content` (at least one comment line) | N1.5 |
| Existing guard: duplicate dir | `test_flow_resolution.py:TestNewCommand::test_existing_directory_errors` | Unchanged | — |
| Existing guard: invalid name | `test_flow_resolution.py:TestNewCommand::test_invalid_name_errors` | Unchanged | — |

### N4 Test Matrix

| Test case | File:class | Method | Requirement |
|---|---|---|---|
| Project path shown with ~ shortening | `test_watch.py::TestSubmitWatchJobProjectPath::test_shows_project_path` | Mock health, capture stdout, assert `(~/work/...)` | N4.1, N4.4 |
| Missing project_path → no parenthetical | `test_watch.py::TestSubmitWatchJobProjectPath::test_no_project_path_in_health` | Mock health without key, assert original message | N4.2 |
| Health failure → graceful fallback | `test_watch.py::TestSubmitWatchJobProjectPath::test_health_request_fails` | Mock URLError, assert original message | N4.3 |
| Non-home path passes through unchanged | `test_watch.py::TestSubmitWatchJobProjectPath::test_non_home_path_not_shortened` | Health returns `/opt/...`, assert no `~` | N4.4 |

### Commands

```bash
# Targeted test runs
uv run pytest tests/test_flow_resolution.py::TestNewCommand -v
uv run pytest tests/test_watch.py::TestSubmitWatchJobProjectPath -v

# Full regression
uv run pytest tests/
cd web && npm run test
```

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| f-string escaping of `{{`/`}}` in template produces invalid YAML | Medium | Template generates unparseable YAML | Step 2b test explicitly runs `load_workflow_yaml()` on the generated file; catches this in CI |
| Shell quoting in `run:` blocks breaks with special chars in `$topic` | Low | Script step fails at runtime | Template is a demo; use YAML `|` block scalar so shell gets literal content; document `--var` in comments |
| `prompt:` placed under `config:` instead of top-level | Medium | `LLM executor requires 'prompt'` error from `yaml_loader.py:287` | Verified correct format via `yaml_loader.py:279-282`; test validates parsing |
| Health request adds perceptible latency | Very Low | Submission feels slower | 2s timeout on localhost; request adds <5ms; wrapped in try/except so failures don't block |
| Older server version lacks `project_path` in health | Low | `None` or missing key | Use `.get("project_path")` → returns `None` → `project_label` stays empty string |
| `test_creates_directory_flow` assertion `"hello from my-flow"` breaks | Certain | Existing test fails | Step 2a explicitly updates this assertion to match new template |

## File Change Summary

| File | Change type | Lines affected |
|---|---|---|
| `src/stepwise/cli.py` | Edit `cmd_new` template (lines 994-1002) | ~25 lines replaced |
| `src/stepwise/cli.py` | Edit `_submit_watch_job` message (lines 1787-1789) | ~12 lines added |
| `tests/test_flow_resolution.py` | Update `TestNewCommand` (lines 243-269) | ~30 lines modified/added |
| `tests/test_watch.py` | Add `TestSubmitWatchJobProjectPath` class | ~50 lines added |

Total: ~120 lines across 3 files. No new files created.
