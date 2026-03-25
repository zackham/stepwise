# Plan: Document Job Staging Features

**Scope:** Update `agent_help.py`, `cli.md`, `concepts.md`, `patterns.md`, `quickstart.md` to document job staging — `job create/show/run/dep/cancel/rm`, groups, data wiring, and the plan-light to implement pattern.

**Why now:** Job staging shipped in v0.9.0 (commit `ed33fdb`) with data wiring following in v1.0.0-rc1 (commit `aec586a`). The CLI commands, engine integration, and store layer are fully implemented and tested. But the user-facing docs (`docs/`) and agent-facing instructions (`agent_help.py`) don't mention any of it.

---

## Requirements

### R1: cli.md — Document `stepwise job` subcommand group
**Acceptance criteria:**
- New `## Job Staging Commands` section (or fold into existing `## Job Commands`) documenting all six subcommands: `job create`, `job show`, `job run`, `job dep`, `job cancel`, `job rm`
- Each command has: description, usage examples, flags table, example output
- Data wiring syntax (`--input plan=job-abc123.result`) documented with examples
- `--group` flag documented on `create`, `show`, `run`
- Cross-references to concepts.md for groups, deps, data wiring mental model
- Overview table at top updated to include `job create`, `job show`, `job run`, `job dep`, `job cancel`, `job rm`

### R2: concepts.md — Job staging mental model
**Acceptance criteria:**
- New `## Job Staging` section (after existing `## Jobs` section) covering:
  - STAGED status and lifecycle: STAGED → PENDING → RUNNING → COMPLETED/FAILED
  - Groups as batch labels for organizing and releasing jobs
  - Job dependencies (`depends_on`) — ordering between jobs (not steps)
  - Data wiring — cross-job output references (`job-id.field`)
  - Auto-start cascade on completion
- Quick reference table updated with Job Staging, Groups, Data Wiring entries
- Existing `## Jobs` section updated to mention STAGED as a valid initial status

### R3: patterns.md — Plan-light to implement pattern
**Acceptance criteria:**
- New `## 8. Plan-Light to Implement` section documenting the pattern:
  1. Run a planning flow (or single planning job)
  2. Stage implementation jobs referencing the plan's outputs via data wiring
  3. Organize into a group
  4. Review staged jobs (`job show --group`)
  5. Release with `job run --group`
  6. Engine cascades execution based on dependency graph
- Complete YAML + CLI example showing the end-to-end workflow
- Contrasts with static `for_each` (when decomposition is known at author time) vs plan-light (when decomposition emerges from a planning step)
- Summary table at bottom updated

### R4: quickstart.md — Job staging quick intro
**Acceptance criteria:**
- New `## Stage and batch jobs` section (after existing "Call it from your agent" section, before "What's next")
- Shows a minimal 3-command example: create two staged jobs, wire data, run the group
- Brief (15-25 lines) — not a full tutorial, just enough to show the concept exists
- Links to concepts.md and cli.md for depth

### R5: agent_help.py — Job staging in agent instructions
**Acceptance criteria:**
- `_format_compact()` updated with new `## Job Staging` section between "Orchestrating Multiple Jobs" and "Why Use Flows"
- Documents: `job create`, `job show`, `job run`, `job dep` commands with one-liner descriptions
- Data wiring syntax shown: `--input key=job-id.field`
- Group workflow shown: create → review → run
- CLI Reference section updated with job staging commands

---

## Assumptions (verified against source)

| Assumption | Verified in |
|---|---|
| STAGED is a valid JobStatus enum value | `models.py:30` — `STAGED = "staged"` |
| `job_group` field exists on Job | `models.py:1415` — `job_group: str \| None = None` |
| `depends_on` field exists on Job | `models.py:1416` — `depends_on: list[str]` |
| CLI subcommands exist: create, show, run, dep, cancel, rm | `cli.py:4139+` — all six handlers implemented |
| Data wiring parses `job-id.field` inputs | `runner.py:44+` — `parse_inputs()` regex for `$job_ref` |
| Auto-dependency creation on `$job_ref` inputs | `cli.py:4354-4360` — `store.add_job_dependency()` called |
| Cycle detection implemented | `store.py:301` — `would_create_cycle()` BFS |
| Group transition is atomic | `store.py:355` — `transition_group_to_pending()` |
| Engine auto-starts dependents on completion | `engine.py:753+` — `_check_dependent_jobs()` |
| `pending_jobs_with_deps_met()` used by engine | `engine.py:3443+` — `_start_queued_jobs()` |
| Test coverage exists | `test_job_staging.py`, `test_job_dep_readiness.py`, `test_job_ref_inputs.py` |
| `agent_help.py` `_format_compact()` has no job staging section | Verified — no mention of `job create/show/run/dep` |
| `cli.md` has no job staging section | Verified — no `stepwise job` subcommands documented |
| `concepts.md` mentions STAGED only in Jobs section header | Verified — STAGED not explained |
| `patterns.md` has 7 patterns, none about job staging | Verified — stops at §7 Error Recovery |
| `quickstart.md` has no job staging content | Verified — ends at "Call it from your agent" |

---

## Implementation Steps

### Step 1: Update `docs/concepts.md` — Job staging mental model
**File:** `docs/concepts.md`

1. Update the Quick Reference table (line 8) to add rows:
   - **Job staging** | Stage, review, and release jobs before execution | STAGED → PENDING lifecycle
   - **Groups** | Batch label for organizing staged jobs | `--group wave-1`
   - **Job dependencies** | Ordering between jobs (not steps) | `depends_on`, auto-start cascade
   - **Data wiring** | Cross-job output references | `--input plan=job-abc.field`

2. Update existing `## Jobs` section (line 18) — add a paragraph after "Jobs track their own lifecycle" mentioning STAGED as an optional starting state:
   > Jobs can also be **staged** — created in a holding state before execution. Staged jobs let you build a batch, add dependencies, wire data between jobs, review the plan, and then release everything at once. See [Job Staging](#job-staging) below.

3. Add new `## Job Staging` section after `## Jobs` (before `## Steps`). Content:
   - **Lifecycle diagram:** STAGED → (review, add deps) → `job run` → PENDING → RUNNING → COMPLETED
   - **Groups:** String labels for batch operations. `job create --group wave-1` assigns a job. `job run --group wave-1` atomically transitions all staged jobs in the group to PENDING.
   - **Job dependencies:** `job dep <id> --after <other-id>` — the job waits for the other to complete before starting. The engine auto-starts dependents when a job completes. Cycle detection prevents deadlocks.
   - **Data wiring:** `--input plan=job-abc123.result` — reference another job's output field. Auto-creates a dependency edge. On start, the engine resolves the reference to the actual value from the completed upstream job. Supports nested paths: `job-abc123.hero.headline`.
   - **Auto-start cascade:** When a job completes, the engine checks all its dependents. If a dependent is PENDING and all its dependencies are now COMPLETED, it auto-starts.

### Step 2: Update `docs/cli.md` — Job staging commands
**File:** `docs/cli.md`

1. Update Overview table (line 10) — add new row:
   `| [Job Staging](#job-staging-commands) | `job create`, `job show`, `job run`, `job dep`, `job cancel`, `job rm` |`

2. Add new `## Job Staging Commands` section after existing `## Job Commands` section (after line 446). Document each subcommand:

   **`stepwise job create`**
   - Description: Create a STAGED job from a flow file.
   - Usage examples: basic, with inputs, with group, with data wiring
   - Flags: `--flow`, `--input`, `--group`, `--name`, `--objective`
   - Example output showing job ID
   - Note: `--input key=job-id.field` auto-creates dependency

   **`stepwise job show`**
   - Description: List staged/pending jobs or show detail for one.
   - Usage: `job show` (all), `job show --group wave-1`, `job show job-abc123`
   - Flags: `--group`, `--output`
   - Example table output

   **`stepwise job run`**
   - Description: Transition STAGED jobs to PENDING.
   - Usage: single job, by group
   - Flags: `--group`
   - Example output
   - Note: warns if jobs have unmet external dependencies

   **`stepwise job dep`**
   - Description: Add, remove, or list job dependencies.
   - Usage: add (`--after`), remove (`--rm`), list (no flags)
   - Cycle detection note
   - Example output

   **`stepwise job cancel`**
   - Description: Cancel a staged or pending job.

   **`stepwise job rm`**
   - Description: Delete a staged job (cascade deletes deps).

### Step 3: Update `docs/patterns.md` — Plan-light to implement pattern
**File:** `docs/patterns.md`

1. Add `## 8. Plan-Light to Implement` section after §7 (after line 545). Content:

   **Problem:** Complex tasks need a planning phase that determines the implementation shape. Static flows can't adapt — the number and nature of implementation jobs emerges from planning.

   **Pattern:** Run a planning job, then stage implementation jobs that reference the plan's outputs via data wiring. Review the staged batch, then release.

   **Example:** Full CLI workflow showing:
   ```bash
   # 1. Run the planning flow
   stepwise run plan-task --wait --input spec="Build auth system"
   # Returns job-plan-abc with outputs: {plan, tasks}

   # 2. Stage implementation jobs referencing the plan
   stepwise job create plan-and-build.flow.yaml \
     --input spec="Implement auth middleware" \
     --input plan=job-plan-abc.plan \
     --group auth-batch

   stepwise job create plan-and-build.flow.yaml \
     --input spec="Implement auth tests" \
     --input plan=job-plan-abc.plan \
     --group auth-batch

   # 3. Review what's staged
   stepwise job show --group auth-batch

   # 4. Release the batch
   stepwise job run --group auth-batch
   ```

   **When to use vs alternatives:**
   - Static `for_each` — decomposition is known at author time (fixed shape)
   - `emit_flow` — agent decides decomposition at runtime (dynamic shape, single job)
   - Plan-light to implement — human reviews decomposition before execution (staged, multi-job)

2. Update Summary table (line 549) with new row:
   `| Planning determines implementation shape | **Plan-light to implement** — stage jobs referencing plan outputs (§8) |`

### Step 4: Update `docs/quickstart.md` — Brief job staging intro
**File:** `docs/quickstart.md`

1. Add `## Stage and batch jobs` section after "Call it from your agent" (after line 173), before "What's next". Content (~20 lines):

   Brief intro: "For multi-job workflows, stage jobs before running them."

   ```bash
   # Create two staged jobs in a group
   stepwise job create my-flow --input task="Build API" --group sprint-1
   stepwise job create my-flow --input task="Write tests" --group sprint-1

   # Wire data: second job depends on first job's output
   stepwise job dep job-<second-id> --after job-<first-id>

   # Review staged jobs
   stepwise job show --group sprint-1

   # Release the batch
   stepwise job run --group sprint-1
   ```

   Link to concepts.md for full mental model.

### Step 5: Update `src/stepwise/agent_help.py` — Agent instructions
**File:** `src/stepwise/agent_help.py`

1. In `_format_compact()` (line 238), add a `## Job Staging` section after "Orchestrating Multiple Jobs" (after line 433) and before "Why Use Flows" (line 437). Content:

   ```
   ## Job Staging

   Stage, review, and release jobs before execution. Wire data between jobs.

   **Create staged jobs:**
     `stepwise job create <flow> --input k=v --group <name>`
     Creates a job in STAGED state. Use `--group` to batch related jobs.

   **Wire data between jobs:**
     `stepwise job create <flow> --input plan=job-abc123.result --group batch`
     References another job's output. Auto-creates a dependency edge.

   **Review and release:**
     `stepwise job show --group <name>` — list staged jobs in a group.
     `stepwise job run --group <name>` — transition all to PENDING.
     `stepwise job run <job-id>` — transition a single job.

   **Manage dependencies:**
     `stepwise job dep <job-id> --after <other-id>` — add ordering constraint.
     `stepwise job dep <job-id>` — list dependencies.

   **Cleanup:**
     `stepwise job cancel <job-id>` — cancel a staged/pending job.
     `stepwise job rm <job-id>` — delete a staged job.

   Jobs auto-start when all dependencies complete. The engine cascades.
   ```

2. Update CLI Reference section (line 370) — add job staging commands:
   ```
   `stepwise job create <flow> --input k=v --group <name>` — stage a job.
   `stepwise job show [--group <name>]` — list staged jobs.
   `stepwise job run [<job-id>] [--group <name>]` — release to pending.
   `stepwise job dep <job-id> [--after <id>] [--rm <id>]` — manage deps.
   ```

---

## Testing Strategy

### Verify documentation accuracy

```bash
# Confirm all CLI commands exist and match documented flags
uv run stepwise job create --help
uv run stepwise job show --help
uv run stepwise job run --help
uv run stepwise job dep --help
uv run stepwise job cancel --help
uv run stepwise job rm --help
```

### Verify agent_help output includes job staging

```bash
# Generate agent help and verify job staging section appears
uv run stepwise agent-help 2>/dev/null | grep -A 5 "Job Staging"

# Verify CLI Reference section includes job commands
uv run stepwise agent-help 2>/dev/null | grep "job create"
```

### Run existing tests to ensure no regressions

```bash
# Full test suite
uv run pytest tests/

# Specifically job staging tests
uv run pytest tests/test_job_staging.py tests/test_job_dep_readiness.py tests/test_job_ref_inputs.py -v

# Agent help tests if they exist
uv run pytest tests/ -k "agent_help" -v
```

### Manual review checklist

- [ ] `cli.md` Overview table has Job Staging row
- [ ] All six `job` subcommands documented with flags tables
- [ ] Data wiring syntax (`--input k=job-id.field`) shown in cli.md and concepts.md
- [ ] `concepts.md` Quick Reference table has staging rows
- [ ] `concepts.md` Jobs section mentions STAGED status
- [ ] `patterns.md` has §8 with complete CLI example
- [ ] `patterns.md` Summary table has plan-light row
- [ ] `quickstart.md` has brief staging section with link to concepts
- [ ] `agent_help.py` compact output includes Job Staging section
- [ ] `agent_help.py` CLI Reference includes job commands
- [ ] No broken cross-references between docs
- [ ] Consistent terminology: "staged", "groups", "data wiring", "auto-start cascade"

---

## Ordering and Dependencies

```
Step 1 (concepts.md) ← no deps, establishes vocabulary
Step 2 (cli.md) ← can reference concepts.md anchors from Step 1
Step 3 (patterns.md) ← references both concepts and CLI
Step 4 (quickstart.md) ← links to concepts.md and cli.md
Step 5 (agent_help.py) ← standalone, but should use same terminology
```

Steps 1-4 are docs and can be done in any order. Step 5 is code and should match the terminology established in Steps 1-4. All steps are independent enough to parallelize, but reviewing in order (concepts → cli → patterns → quickstart → agent_help) ensures consistency.

## Estimated Scope

- ~5 files modified
- ~200-300 lines of new documentation content across docs/
- ~40-50 lines of new Python in agent_help.py
- No behavioral changes to engine, CLI, or store
- No new tests needed (documentation-only changes + agent_help.py output change verifiable via existing test patterns)
