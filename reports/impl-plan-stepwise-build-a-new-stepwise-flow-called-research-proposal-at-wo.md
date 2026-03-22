---
title: "Implementation Plan: research-proposal Flow"
date: "2026-03-20T18:00:00"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: research-proposal Flow

## Overview

Build a 10-step `research-proposal` flow at `~/work/stepwise/flows/research-proposal/` that automates deep research → two council review rounds → human checkpoint → finalize. The council logic is inlined (for_each LLM fan-out + synthesis) rather than delegating to the external vita council flow, making the flow self-contained.

## Requirements

### R1: Deep research init step
**Acceptance criteria:**
- Agent step with `output_mode: file` and `limits: {max_cost_usd: 10, max_duration_minutes: 30}`
- Outputs exactly: `title`, `slug`, `report_path`, `url`, `notes`, `draft_content`
- Accepts `$job.topic` (required), `$job.grounding_paths` (optional), `$job.project` (optional)
- Creates report file at `data/reports/{slug}.md` with YAML frontmatter (`status: draft`)
- Writes `output.json` with all 6 output fields
- `draft_content` contains the full report text (for downstream council steps)

### R2: Two automated council review rounds
**Acceptance criteria:**
- Each round uses `for_each` over a shared model list with `on_error: continue`
- Inline sub-flow contains single LLM step with `model: $model_id` and `prompt_file` referencing a review prompt
- Each round's synthesis step uses `model: strong` and produces a `synthesis` output
- Round 1 prompt asks about grounding, evidence, gaps in the initial draft
- Round 2 prompt asks about changes, remaining concerns, implementation readiness
- Synthesis prompt handles raw for_each results `[{"response": "..."}, {"_error": "..."}, ...]`

### R3: Agent revision steps
**Acceptance criteria:**
- Two revision steps (`revise-1`, `revise-2`), both with `output_mode: file`
- Each outputs `report_content` (full updated text) and `result` (summary of changes)
- `revise-2` accepts optional `human_feedback` input from `human-checkpoint.feedback` (optional: true, resolves to None on first pass)
- Both use shared `prompts/revise.md` prompt template with `$synthesis` and `$human_feedback` interpolation
- Agent reads report file at `$report_path` and updates it in-place

### R4: Human checkpoint with three options
**Acceptance criteria:**
- `executor: human` with typed output schema (`models.py:245-291` OutputFieldSpec)
- `choice` field: `type: choice`, `options: [approved, feedback, done]`
- `feedback` field: `type: text`, `required: false`
- Prompt displays `$title`, `$url`, and `$summary` (via `any_of` from revise-2.result or revise-1.result)
- Exit rules: `outputs.choice in ('approved', 'done')` → advance; `outputs.choice == 'feedback'` → loop target: revise-2
- `feedback` loop has no `max_iterations` (human-controlled exit)

### R5: Finalize step
**Acceptance criteria:**
- Agent step with `output_mode: file` and `sequencing: [human-checkpoint]`
- Reads report at `$report_path`, performs formatting cleanup, updates frontmatter `status: draft → published`
- Outputs `result` (final report path + title)

### R6: Config vars
**Acceptance criteria:**
- `topic` (required) — passed to init via `$job.topic`
- `grounding_paths` (optional) — passed to init via `$job.grounding_paths`
- `project` (optional) — passed to init via `$job.project`
- Flow invokable as: `stepwise run research-proposal --var topic="..." [--var grounding_paths="..."]`

### R7: Self-contained flow
**Acceptance criteria:**
- Zero external flow dependencies (no references to vita council flow)
- All prompts in `prompts/` subdirectory (6 files)
- `stepwise validate flows/research-proposal` produces zero warnings and zero errors
- YAML parses via `load_workflow_yaml()` without exceptions
- Terminal step is `[finalize]`

## Assumptions

### A1: `prompt_file` resolves correctly in inline for_each sub-flows
**Verification:** `yaml_loader.py:179-214` — `_resolve_prompt_file()` uses `base_dir` parameter. In `_resolve_flow_source()` at line 534, inline sub-flow steps are parsed via `_parse_step(sub_name, sub_data, base_dir=base_dir, ...)` — `base_dir` is the directory of the parent FLOW.yaml. Confirmed: `prompt_file: prompts/council-review-1.md` inside an inline for_each sub-flow resolves to `flows/research-proposal/prompts/council-review-1.md`.

### A2: for_each results aggregate as `{"results": [item_artifact, ...]}`
**Verification:** `engine.py:1449-1452` — `_check_for_each_completion()` builds `completed_results` list ordered by index, wraps as `{"results": results}`. Each item is the terminal step's `artifact` dict. With `on_error: continue`, failed items produce `{"_error": "Sub-job {id} failed"}` (line 1438). Confirmed in `test_for_each.py:95-103`.

### A3: `model: $model_id` resolves from inputs at runtime
**Verification:** The council flow at `/home/zack/work/vita/flows/council/FLOW.yaml:19` uses `model: $model_id` in the for_each sub-flow's LLM step with `inputs: {model_id: $job.model_id}`. `yaml_loader.py:265-268` stores `model` in executor config dict; LLM executor performs `$var` interpolation at runtime. Pattern proven in production.

### A4: Human checkpoint → loop to revise-2 → human checkpoint cycle works
**Verification:** The report flow at `/home/zack/work/vita/flows/report/FLOW.yaml:37-44` uses identical pattern: hub → loop to work → work completes → hub re-runs because dep changed (currency invalidation via `engine.py:_is_current()` at line 862). When revise-2 produces a new completed run, human-checkpoint's dep on `revise-2.result` is no longer current → human-checkpoint becomes ready again.

### A5: `output_mode: file` means agent writes `output.json` to CWD
**Verification:** `agent.py:650-670` — when `output_mode == "file"`, agent reads `output.json` from `workspace_path` after completion, parses as JSON, uses as artifact dict. Report flow's init step (`/home/zack/work/vita/flows/report/prompts/init.md:30-39`) instructs agent to write this file. Prompt must list exact JSON keys matching step `outputs`.

### A6: `any_of` input bindings resolve first-available-source
**Verification:** `yaml_loader.py:149-170` parses `any_of` as list of `(step, field)` pairs stored in `InputBinding.any_of_sources`. `engine.py:1563-1584` iterates sources in order, returns first with a completed run. For readiness (`engine.py:838-856`), step is ready when at least ONE any_of source has a current completed run. This enables human-checkpoint to receive `result` from either `revise-2` (loop case) or `revise-1` (first pass before revise-2 runs). Confirmed in `tests/test_optional_inputs.py:289-307`.

### A7: `limits` parsed via StepLimits.from_dict()
**Verification:** `yaml_loader.py:796-800` parses `limits` dict into `StepLimits` dataclass. `models.py:79-102` defines fields: `max_cost_usd`, `max_duration_minutes`, `max_iterations`. Enforcement in `engine.py:_check_limits()` at line 2000 — duration checked against wall clock, cost against accumulated billing. Confirmed in eval flow at `flows/eval-1-0/FLOW.yaml:31-32`.

### A8: `model: strong` resolves via config label system
**Verification:** `config.py:27-31` defines `DEFAULT_LABELS = {"fast": "...", "balanced": "...", "strong": "google/gemini-3.1-pro-preview"}`. Resolution in `config.py:resolve_model()` at line 116 — checks labels dict first, falls back to literal model ID. Users can override via `~/.config/stepwise/config.yaml`. The synthesis steps using `model: strong` will resolve to the user's configured strong model.

## Out of Scope

### Not doing: Council flow reuse via sub-flow delegation
**Why:** The council flow lives at `~/work/vita/flows/council/` which is not discoverable from the stepwise project. `flow_resolution.py:252-258` searches `project_dir`, `project_dir/flows/`, `project_dir/.stepwise/flows/`, `~/.stepwise/flows/` — none include vita. Inlining the council pattern (model list + for_each + synthesis) is the pragmatic choice. The council flow's scripts (`setup_models.py`, `format_responses.py`) are trivial — a `printf` command replaces setup_models, and the synthesis prompt replaces format_responses.

### Not doing: Session continuity across init → revise steps
**Why:** `continue_session: true` only works within loop iterations of the same step definition (`agent.py:_session_id` keyed by step name). Init and revise-1 are different step definitions, so they cannot share sessions. Each revision step receives the full report content + synthesis as inputs, providing sufficient context.

### Not doing: Custom model list configuration via job vars
**Why:** The model list is hardcoded in the `setup-models` script step. Making it configurable would require either a job input that's a JSON array (awkward for `--var` CLI) or a config file mechanism. Hardcoding keeps the flow simple; models can be changed by editing the FLOW.yaml directly.

### Not doing: Registry publishing or distribution
**Why:** This flow is local to the stepwise repo. No `@author:flow` ref needed. Registry publishing would require `stepwise publish` which is outside this scope.

### Not doing: Vita share URL integration
**Why:** Vita's share URL API (`curl -X POST http://localhost:33801/api/reports/{slug}/share`) is specific to the vita project at port 33801. The research-proposal flow runs from the stepwise repo which doesn't have that API. `report_path` serves as the primary output; the finalize agent can attempt share URL generation if a compatible API is available in the working_dir context.

### Not doing: Automatic re-running of council after human feedback
**Why:** The human feedback loop only cycles between human-checkpoint and revise-2. Re-running council-2 → synthesize-2 → revise-2 would require the loop target to be earlier in the chain and would add significant cost (~$3+ per loop). Human feedback is more authoritative than council feedback anyway.

### Not doing: Working_dir configuration for agent steps
**Why:** The spec doesn't specify a target codebase. Agent steps run in their default workspace. If needed later, `working_dir: $project_path` can be added (per `agent.py:95-97` and `yaml_loader.py:250-253`).

## Architecture

### Flow DAG

```
init ──→ setup-models ──┬──→ council-1 ──→ synthesize-1 ──→ revise-1 ──┐
                        │                                               │
                        └──→ council-2 ──→ synthesize-2 ──→ revise-2 ──→ human-checkpoint ──→ finalize
                                                              ↑                    │
                                                              └────────────────────┘
                                                                 (feedback loop)
```

**DAG dependencies (data flow):**
- `init` → entry step, no deps (receives `$job.*` inputs)
- `setup-models` → `sequencing: [init]` (ordering only, no data dep)
- `council-1` → `init.draft_content` (data), `setup-models.models` (for_each source)
- `synthesize-1` → `council-1.results` (data), `$job.topic`
- `revise-1` → `synthesize-1.synthesis`, `init.report_path`, `$job.topic`
- `council-2` → `revise-1.report_content` (data), `setup-models.models` (for_each source)
- `synthesize-2` → `council-2.results` (data), `$job.topic`
- `revise-2` → `synthesize-2.synthesis`, `init.report_path`, `$job.topic`, `human-checkpoint.feedback` (optional)
- `human-checkpoint` → `init.title`, `init.url`, summary via `any_of: [revise-2.result, revise-1.result]`
- `finalize` → `init.report_path`, `init.url`, `$job.topic`, `sequencing: [human-checkpoint]`

### Council integration (inlined)

Each council round is 2 steps within the main flow:

1. **for_each** over `setup-models.models` — inline sub-flow with single LLM step. Uses `prompt_file` for review questions and `model: $model_id` for dynamic model selection. `on_error: continue` tolerates individual model failures.
2. **synthesize** — `model: strong` LLM step. Receives raw `$results` array (aggregated for_each output per `engine.py:1449-1452`) and produces `synthesis` output.

The `setup-models` script step outputs the model list once, shared by both council rounds via input bindings. This mirrors the vita council flow's structure (`/home/zack/work/vita/flows/council/FLOW.yaml`) but eliminates the `format_responses.py` script — the synthesis prompt handles the raw `[{"response": "..."}, ...]` format directly.

**Pattern citation:** for_each with inline sub-flow follows `test_for_each.py:66-88` (basic pattern) and `flows/eval-1-0/FLOW.yaml` (production usage). `on_error: continue` behavior per `engine.py:1436-1438`.

### Human checkpoint loop

Mirrors the report flow's hub ↔ work pattern (`/home/zack/work/vita/flows/report/FLOW.yaml:18-44`):

1. human-checkpoint presents report + summary, collects typed choice
2. Exit rules evaluate `outputs.choice`:
   - `approved` or `done` → `action: advance` (proceeds to finalize)
   - `feedback` → `action: loop, target: revise-2` (loops to revision)
3. On loop: revise-2 re-runs with `human_feedback` populated (optional input, `None` on first pass)
4. After revise-2 completes, human-checkpoint's dep on `revise-2.result` is invalidated → human-checkpoint re-runs

The typed output schema uses `OutputFieldSpec(type="choice", options=[...])` per `models.py:245-291`, validated by `engine.py:_validate_fulfill_payload()`. The `any_of` input on human-checkpoint (`yaml_loader.py:149-170`) resolves to whichever of revise-2 or revise-1 has a current completed run — on first pass, revise-1 (before revise-2 runs); on loop, revise-2.

### File structure

```
flows/research-proposal/
  FLOW.yaml                    — 10-step flow definition (~150 lines)
  prompts/
    init.md                    — deep research + report creation agent prompt
    council-review-1.md        — round 1 review questions (initial draft)
    council-review-2.md        — round 2 review questions (revised version)
    council-synthesize.md      — shared synthesis prompt template
    revise.md                  — shared revision agent prompt template
    finalize.md                — cleanup + publish agent prompt
```

7 files total. No Python scripts — model list inlined as `printf` in setup-models step.

## Implementation Steps

### Step 1: Create directory structure
**Files:** `flows/research-proposal/`, `flows/research-proposal/prompts/`

Create the flow directory and prompts subdirectory. Verify parent exists.

**Rationale:** Must exist before any files can be written. Ordered first.

### Step 2: Write init and setup-models steps in FLOW.yaml
**File:** `flows/research-proposal/FLOW.yaml`

Write the YAML header (`name`, `description`, `author`, `tags`) and the first two steps:

- `init`: `executor: agent`, `prompt_file: prompts/init.md`, `output_mode: file`, `limits: {max_cost_usd: 10, max_duration_minutes: 30}`, outputs: `[title, slug, report_path, url, notes, draft_content]`, inputs: `topic: $job.topic`, optional inputs for `grounding_paths` and `project`
- `setup-models`: `run: printf '{"models": ["anthropic/claude-opus-4.6", "openai/gpt-5.4", "google/gemini-3.1-pro-preview", "x-ai/grok-4.1-fast"]}'`, outputs: `[models]`, `sequencing: [init]`

**Rationale:** These are the entry steps with no upstream deps. Must be defined before council steps reference them.

### Step 3: Write council-1 and synthesize-1 steps
**File:** `flows/research-proposal/FLOW.yaml` (append)

- `council-1`: `for_each: setup-models.models`, `as: model_id`, `on_error: continue`, inline sub-flow with single LLM step using `model: $model_id`, `prompt_file: prompts/council-review-1.md`, `temperature: 0.7`, outputs: `[results]`, inputs: `draft_content: init.draft_content`, `topic: $job.topic`
- `synthesize-1`: `executor: llm`, `model: strong`, `prompt_file: prompts/council-synthesize.md`, outputs: `[synthesis]`, inputs: `results: council-1.results`, `topic: $job.topic`

**Rationale:** Depends on init (for draft_content) and setup-models (for model list). Must precede revise-1.

### Step 4: Write revise-1 step
**File:** `flows/research-proposal/FLOW.yaml` (append)

- `revise-1`: `executor: agent`, `prompt_file: prompts/revise.md`, `output_mode: file`, outputs: `[report_content, result]`, inputs: `synthesis: synthesize-1.synthesis`, `report_path: init.report_path`, `topic: $job.topic`

**Rationale:** Depends on synthesize-1 output. Must precede council-2 (which reviews the revised content).

### Step 5: Write council-2 and synthesize-2 steps
**File:** `flows/research-proposal/FLOW.yaml` (append)

Same structure as council-1/synthesize-1 but:
- `council-2` inputs: `report_content: revise-1.report_content` (instead of init.draft_content)
- `council-2` uses `prompt_file: prompts/council-review-2.md` (different questions)

**Rationale:** Depends on revise-1.report_content. Must precede revise-2.

### Step 6: Write revise-2 and human-checkpoint steps
**File:** `flows/research-proposal/FLOW.yaml` (append)

- `revise-2`: same as revise-1 but adds optional input `human_feedback: {from: human-checkpoint.feedback, optional: true}` and uses `synthesize-2.synthesis`
- `human-checkpoint`: `executor: human`, typed outputs:
  ```yaml
  outputs:
    choice:
      type: choice
      options: [approved, feedback, done]
      description: "Approve the proposal, request changes, or finalize as-is"
    feedback:
      type: text
      required: false
      description: "Feedback for revision (only needed if choice is 'feedback')"
  ```
  Inputs: `title: init.title`, `url: init.url`, `summary: {any_of: [revise-2.result, revise-1.result]}`
  Exit rules:
  ```yaml
  exits:
    - name: approved
      when: "outputs.choice in ('approved', 'done')"
      action: advance
    - name: feedback
      when: "outputs.choice == 'feedback'"
      action: loop
      target: revise-2
  ```

**Rationale:** These form the feedback loop pair. revise-2's optional human_feedback input creates the loop data path. human-checkpoint's any_of handles both first-pass (from revise-1) and loop (from revise-2).

### Step 7: Write finalize step
**File:** `flows/research-proposal/FLOW.yaml` (append)

- `finalize`: `executor: agent`, `prompt_file: prompts/finalize.md`, `output_mode: file`, outputs: `[result]`, inputs: `report_path: init.report_path`, `url: init.url`, `topic: $job.topic`, `sequencing: [human-checkpoint]`

**Rationale:** Terminal step. `sequencing: [human-checkpoint]` ensures it runs after the human approves. No data dep on human-checkpoint outputs needed.

### Step 8: Write init.md prompt
**File:** `flows/research-proposal/prompts/init.md`

Agent instructions modeled on `/home/zack/work/vita/flows/report/prompts/init.md` but with deeper research:

- Read grounding files/paths if `$grounding_paths` is non-empty
- Search codebase for relevant patterns, browse web for context
- Create report file at `data/reports/{slug}.md` with YAML frontmatter (`title`, `date`, `tags`, `status: draft`)
- Write comprehensive initial draft (not just outline)
- Output `output.json` with all 6 fields: `title`, `slug`, `report_path`, `url` (use `report_path` as fallback), `notes`, `draft_content`

### Step 9: Write council review prompts
**Files:** `flows/research-proposal/prompts/council-review-1.md`, `prompts/council-review-2.md`

**Round 1** — `$draft_content` + `$topic`:
- Is the research question well-defined and grounded?
- Are claims supported by evidence?
- What's missing? What assumptions are unstated?
- Is the proposed approach viable?

**Round 2** — `$report_content` + `$topic`:
- Were council concerns from round 1 addressed?
- What are the remaining gaps or weaknesses?
- Is the proposal ready for implementation?
- Confidence assessment: high/medium/low with justification

### Step 10: Write council synthesis prompt
**File:** `flows/research-proposal/prompts/council-synthesize.md`

Template modeled on `/home/zack/work/vita/flows/council/prompts/synthesize.md`:
- Receives `$results` (raw for_each array) and `$topic`
- Must handle `_error` entries in the results array
- Structure: consensus → disagreements → unique insights → actionable recommendations

### Step 11: Write revision prompt
**File:** `flows/research-proposal/prompts/revise.md`

Shared agent prompt for revise-1 and revise-2:
- Read report at `$report_path`
- Apply feedback from `$synthesis` (council synthesis text)
- If `$human_feedback` is non-empty, prioritize over council feedback
- Update report file in-place
- Write `output.json` with `report_content` (full updated text) and `result` (change summary)

### Step 12: Write finalize prompt
**File:** `flows/research-proposal/prompts/finalize.md`

- Read report at `$report_path`
- Consistency and formatting cleanup
- Update frontmatter `status: draft → published`
- Write `output.json` with `result` (final path + title)

### Step 13: Validate flow
```bash
cd ~/work/stepwise && uv run stepwise validate flows/research-proposal
```
Fix any errors or warnings. Iterate until zero warnings.

### Step 14: Write and run unit tests
**File:** `tests/test_research_proposal_flow.py`

Write tests covering: YAML parsing, for_each + synthesis pipeline, human checkpoint exit rules, any_of input resolution, feedback loop. See Testing Strategy below for exact test cases.

```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py -v
```

## Testing Strategy

All tests in `tests/test_research_proposal_flow.py`. Uses fixtures from `tests/conftest.py:138-200` (store, registry, async_engine, register_step_fn, run_job_sync, CallableExecutor).

### T1: YAML parses without errors
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestFlowParsing -v
```

```python
from stepwise.yaml_loader import load_workflow_yaml
from pathlib import Path

class TestFlowParsing:
    def test_yaml_parses_successfully(self):
        """FLOW.yaml loads without parse errors."""
        wf = load_workflow_yaml(
            Path("flows/research-proposal/FLOW.yaml")
        )
        assert len(wf.steps) == 10

    def test_step_names_match(self):
        wf = load_workflow_yaml(...)
        expected = {
            "init", "setup-models",
            "council-1", "synthesize-1", "revise-1",
            "council-2", "synthesize-2", "revise-2",
            "human-checkpoint", "finalize",
        }
        assert set(wf.steps.keys()) == expected

    def test_terminal_step_is_finalize(self):
        wf = load_workflow_yaml(...)
        assert wf.terminal_steps() == ["finalize"]

    def test_for_each_steps_have_sub_flows(self):
        wf = load_workflow_yaml(...)
        assert wf.steps["council-1"].for_each is not None
        assert wf.steps["council-1"].sub_flow is not None
        assert wf.steps["council-2"].for_each is not None

    def test_human_checkpoint_has_typed_schema(self):
        wf = load_workflow_yaml(...)
        schema = wf.steps["human-checkpoint"].output_schema
        assert "choice" in schema
        assert schema["choice"].type == "choice"
        assert set(schema["choice"].options) == {
            "approved", "feedback", "done"
        }
```

### T2: Validation produces zero warnings
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestFlowValidation -v
```

```python
class TestFlowValidation:
    def test_validation_no_errors(self):
        """validate() raises no exceptions."""
        wf = load_workflow_yaml(...)
        wf.validate()  # raises ValueError on errors

    def test_validation_no_warnings(self):
        """warnings() returns empty list."""
        wf = load_workflow_yaml(...)
        warns = wf.warnings()
        assert warns == [], f"Unexpected warnings: {warns}"
```

### T3: Council for_each + synthesis pipeline
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestCouncilPipeline -v
```

Tests the core council pattern using mock executors (following `test_for_each.py:54-105`):

```python
class TestCouncilPipeline:
    def test_for_each_fans_out_to_models_and_synthesizes(
        self, async_engine
    ):
        """Simulates setup-models → council-1 → synthesize-1."""
        register_step_fn("init_stub", lambda inputs: {
            "draft_content": "Draft about X",
            "title": "X", "slug": "x", "report_path": "/tmp/x.md",
            "url": "/tmp/x.md", "notes": "n",
        })

        sub_flow = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review", outputs=["response"],
                executor=ExecutorRef("mock_llm", {
                    "responses": {"review": lambda inputs: {
                        "response": f"Review by {inputs['model_id']}"
                    }}
                }),
                inputs=[
                    InputBinding("model_id", "$job", "model_id"),
                    InputBinding("draft", "$job", "draft_content"),
                ],
            ),
        })

        wf = WorkflowDefinition(steps={
            "init": StepDefinition(
                name="init", outputs=[
                    "title", "slug", "report_path",
                    "url", "notes", "draft_content",
                ],
                executor=ExecutorRef("callable", {
                    "fn_name": "init_stub"
                }),
            ),
            "setup-models": StepDefinition(
                name="setup-models", outputs=["models"],
                executor=ExecutorRef("script", {
                    "command": "printf '{\"models\": [\"m1\", \"m2\"]}'"
                }),
                sequencing=["init"],
            ),
            "council-1": StepDefinition(
                name="council-1", outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                for_each=ForEachSpec(
                    source_step="setup-models",
                    source_field="models",
                    item_var="model_id",
                    on_error="continue",
                ),
                sub_flow=sub_flow,
                inputs=[
                    InputBinding(
                        "draft_content", "init", "draft_content"
                    ),
                ],
            ),
            "synthesize-1": StepDefinition(
                name="synthesize-1", outputs=["synthesis"],
                executor=ExecutorRef("mock_llm", {
                    "responses": {"synthesize-1": lambda inputs: {
                        "synthesis": f"Synthesis of {len(inputs.get('results', []))} reviews"
                    }}
                }),
                inputs=[
                    InputBinding("results", "council-1", "results"),
                ],
            ),
        })

        job = async_engine.create_job("council test", wf)
        result = run_job_sync(async_engine, job.id, timeout=15)

        assert result.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        synth_run = [
            r for r in runs if r.step_name == "synthesize-1"
        ][0]
        assert "synthesis" in synth_run.result.artifact
        assert "2 reviews" in synth_run.result.artifact["synthesis"]
```

### T4: Human checkpoint exit rules
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestHumanCheckpoint -v
```

Tests typed output validation and exit rule routing (following `test_output_schema.py:424-494`):

```python
class TestHumanCheckpoint:
    def test_approved_choice_advances(self, engine):
        """choice='approved' triggers advance exit rule."""
        register_step_fn("stub", lambda inputs: {"result": "ok"})

        wf = WorkflowDefinition(steps={
            "upstream": StepDefinition(
                name="upstream", outputs=["result"],
                executor=ExecutorRef("callable", {
                    "fn_name": "stub"
                }),
            ),
            "checkpoint": StepDefinition(
                name="checkpoint",
                outputs=["choice", "feedback"],
                output_schema={
                    "choice": OutputFieldSpec(
                        type="choice",
                        options=["approved", "feedback", "done"],
                    ),
                    "feedback": OutputFieldSpec(
                        type="text", required=False,
                    ),
                },
                executor=ExecutorRef("human", {
                    "prompt": "Review"
                }),
                inputs=[
                    InputBinding("result", "upstream", "result"),
                ],
                exit_rules=[
                    ExitRule("approved", "expression", {
                        "condition": "outputs.choice in ('approved', 'done')",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("feedback", "expression", {
                        "condition": "outputs.choice == 'feedback'",
                        "action": "loop",
                        "target": "upstream",
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("test", wf)
        engine.start_job(job.id)
        engine.tick()  # upstream runs

        engine.tick()  # checkpoint suspends
        runs = engine.get_runs(job.id, "checkpoint")
        run = runs[0]
        assert run.status == StepRunStatus.SUSPENDED

        # Fulfill with "approved"
        engine.fulfill_watch(run.id, {"choice": "approved"})
        engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_feedback_choice_loops(self, engine):
        """choice='feedback' triggers loop back to upstream."""
        register_step_fn("stub", lambda inputs: {"result": "v1"})
        # ... same setup ...
        # Fulfill with "feedback"
        engine.fulfill_watch(run.id, {
            "choice": "feedback",
            "feedback": "needs more detail"
        })
        engine.tick()
        engine.tick()

        # Upstream should re-run (loop target)
        upstream_runs = engine.get_runs(job.id, "upstream")
        assert len(upstream_runs) == 2  # re-ran

    def test_invalid_choice_rejected(self, engine):
        """Fulfilling with invalid choice value is rejected."""
        # ... setup human step with choice schema ...
        with pytest.raises(ValueError, match="invalid choice"):
            engine.fulfill_watch(run.id, {
                "choice": "maybe"
            })
```

### T5: any_of input resolution
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestAnyOfInputs -v
```

Tests that any_of resolves first-available source (following `test_optional_inputs.py:289-307`):

```python
class TestAnyOfInputs:
    def test_any_of_resolves_first_available(self, async_engine):
        """When both sources exist, first in list wins."""
        register_step_fn("a", lambda inputs: {"val": "from_a"})
        register_step_fn("b", lambda inputs: {"val": "from_b"})
        register_step_fn(
            "c", lambda inputs: {"got": inputs["x"]}
        )

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["val"],
                executor=ExecutorRef("callable", {
                    "fn_name": "a"
                }),
            ),
            "b": StepDefinition(
                name="b", outputs=["val"],
                executor=ExecutorRef("callable", {
                    "fn_name": "b"
                }),
            ),
            "c": StepDefinition(
                name="c", outputs=["got"],
                executor=ExecutorRef("callable", {
                    "fn_name": "c"
                }),
                inputs=[InputBinding(
                    "x", "", "",
                    any_of_sources=[("a", "val"), ("b", "val")],
                )],
            ),
        })

        job = async_engine.create_job("test", wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        c_run = [r for r in runs if r.step_name == "c"][0]
        # "a" is first in any_of list and has completed
        assert c_run.result.artifact["got"] == "from_a"
```

### T6: for_each with on_error: continue handles partial failures
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestCouncilErrorHandling -v
```

```python
class TestCouncilErrorHandling:
    def test_partial_model_failure_still_completes(
        self, async_engine
    ):
        """If 1 of 3 models fails, for_each still completes
        with _error entry."""
        # Register sub-flow fn that fails for one model
        def review(inputs):
            if inputs["model_id"] == "bad-model":
                raise RuntimeError("API error")
            return {"response": f"OK from {inputs['model_id']}"}

        register_step_fn("review", review)
        # ... build for_each with on_error="continue" ...
        # Assert: results has 3 entries, one with _error key
```

### T7: Feedback loop cycles correctly
```bash
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py::TestFeedbackLoop -v
```

```python
class TestFeedbackLoop:
    def test_human_feedback_loop_revise_rerun(self, engine):
        """Human 'feedback' → revise reruns → human reruns."""
        call_count = {"revise": 0}

        def revise(inputs):
            call_count["revise"] += 1
            hf = inputs.get("human_feedback")
            return {
                "report_content": f"v{call_count['revise']}",
                "result": f"revised with: {hf}",
            }

        register_step_fn("revise", revise)

        # Build minimal loop: revise-2 ↔ human-checkpoint
        # With optional human_feedback input on revise-2
        # ... fulfill human with "feedback" first time ...
        # ... assert revise reruns ...
        # ... fulfill human with "approved" second time ...
        # ... assert job completes ...
        assert call_count["revise"] == 2
```

### CLI-level tests

```bash
# Validate (must be warning-free)
cd ~/work/stepwise && uv run stepwise validate flows/research-proposal

# Parse verification
cd ~/work/stepwise && uv run python -c "
from stepwise.yaml_loader import load_workflow_yaml
from pathlib import Path
wf = load_workflow_yaml(Path('flows/research-proposal/FLOW.yaml'))
print(f'Steps ({len(wf.steps)}): {list(wf.steps.keys())}')
print(f'Terminal: {wf.terminal_steps()}')
wf.validate()
warns = wf.warnings()
print(f'Warnings: {len(warns)}')
for w in warns: print(f'  - {w}')
"

# Full test suite
cd ~/work/stepwise && uv run pytest tests/test_research_proposal_flow.py -v
```

### E2E smoke test (~$15-20)
```bash
cd ~/work/stepwise && uv run stepwise run flows/research-proposal --watch \
  --var topic="Should stepwise add a plugin system for custom executor types?"
```

Manual verification checklist:
1. Init creates report file, outputs all 6 fields
2. Council-1 fans out to 4 models (check web UI for sub-jobs)
3. Synthesize-1 produces coherent multi-model synthesis
4. Revise-1 updates report with council feedback
5. Council-2 runs on revised content with different review questions
6. Revise-2 applies second-round feedback
7. Human checkpoint shows choice picker (approved/feedback/done) + text field
8. Selecting "feedback" + text → loops to revise-2 with human_feedback populated
9. Selecting "approved" → advances to finalize
10. Finalize updates report status to published

## Risks & Mitigations

### R1: Frontier model availability on OpenRouter
**Risk:** Some models in the council list may be unavailable, rate-limited, or renamed.
**Mitigation:** `on_error: continue` on both for_each steps (`engine.py:1436-1438`). Failed items produce `{"_error": "..."}` entries. Synthesis prompt explicitly handles these. Flow succeeds with ≥1 model response. If ALL models fail, for_each step fails (`engine.py:1454-1456`) and job halts — this is correct behavior (no data to synthesize).

### R2: Large report content exceeds LLM context
**Risk:** `draft_content`/`report_content` passed to council models could be very large (>100k tokens), exceeding some models' context windows.
**Mitigation:** Init prompt instructs agent to keep draft focused and concise. Council review prompts ask for targeted feedback, not content reproduction. Most frontier models support >128k context. OpenRouter returns error for context overflow, which `on_error: continue` handles gracefully.

### R3: Human checkpoint stall detection warning
**Risk:** `warnings()` at `models.py:670-816` may flag uncovered output combinations for the human step (e.g., choice="feedback" + feedback=None).
**Mitigation:** The exit rules cover all three choice values. `feedback` field is `required: false`, so blank feedback with choice="feedback" is valid (revise agent handles empty feedback gracefully). Verify with `stepwise validate` in Step 13.

### R4: any_of resolution order on first pass
**Risk:** `any_of: [revise-2.result, revise-1.result]` — on first pass, neither may have completed when human-checkpoint evaluates readiness.
**Mitigation:** `revise-1` is upstream of `human-checkpoint` in the DAG (via council-2 → synthesize-2 → revise-2 chain). But `revise-2` completes before `human-checkpoint` runs (it's a direct dep via `synthesize-2`). On first pass, both sources will have completed runs. `any_of` returns first available — since revise-2 completes after revise-1, `revise-2.result` is the most recent. On loop iterations, revise-2 has a newer run, so it's always preferred. The ordering is correct.

### R5: No session continuity across agent steps
**Risk:** Revision agents lack context from the init agent's research session.
**Mitigation:** Revision steps receive `report_path` (agent reads the full file) + `synthesis` (council feedback) + `topic` as inputs. The report file itself carries all the accumulated context. This is the same pattern used by the vita report flow.
