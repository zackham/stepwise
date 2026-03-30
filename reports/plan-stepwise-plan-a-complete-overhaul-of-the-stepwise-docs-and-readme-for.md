# Plan: Complete Overhaul of Stepwise Docs & README for 1.0 Launch

**Date:** 2026-03-30
**Status:** Ready for implementation (v3 — strengthened architecture, decomposability, testability)

## Overview

Rewrite the Stepwise README and docs suite to embody the "packaged trust" positioning — transforming 20+ existing docs (7,084 total lines) into a focused, voice-consistent documentation set organized around the reader journey from "what is this?" through "I'm building flows." The current README (153 lines) is already strong structurally; the overhaul sharpens positioning, adds the missing web-ui and writing-flows guides, fixes stale content in `executors.md`, enhances the troubleshooting reference, and keeps `quickstart.md` canonical (no rename — it's read at runtime by `cli.py:3981`).

---

## Revision Log

**v3 changes** (architecture, decomposability, testability):
- Added **Documentation Architecture** section: doc-layer model, runtime integration map, reader journey graph, cross-reference topology
- Restructured steps into **three parallel tracks** with explicit critical path and checkpoint gates
- **Per-step acceptance tests** replace the monolithic testing section — each step has its own validation script
- Added **negative tests** per doc (what must NOT appear) alongside positive tests
- Split Step 8 (executors + comparison) into separate steps with independent validation
- Added **voice regression test**: automated diff of first 2000 chars of quickstart.md and concepts.md against what `cli.py:3981` will ingest at runtime

**v2 changes** (adversarial review):
- Removed messaging-brief out-of-scope contradiction; kept quickstart.md canonical; added troubleshooting as deliverable; fixed web-ui.md to match component source; fixed executors.md "four types" stale content; accurate file inventory; executable testing replaces vanity metrics

---

## Requirements

### R1: README — First 30 Seconds
- **Acceptance criteria:**
  - Under 200 lines total
  - First 15 lines deliver: one-line "what" statement, one-sentence "why" (the problem), and one-command install. Logo (existing `brand/stepwise-icon.png`) already present.
  - "Three audiences" section rewritten with podcast-derived language ("assisted anxiety" → "packaged trust")
  - "What makes it different" reframed around trust/delegation, not features
  - `stepwise welcome` command appears within first 5 lines of Install section
  - No broken internal doc links
  - GIF/screenshot placeholder deferred (see O4)

### R2: docs/quickstart.md — Enhanced (Not Renamed)
- **Acceptance criteria:**
  - 200–400 lines (current: 203)
  - Add **minimal 2-step script-only hello-world** before the 4-step code-review example (addresses `new-user-test-output.json` confusion_points[3])
  - Add explicit directory placement note ("no `.stepwise/` directory required")
  - Filename stays `quickstart.md` — runtime dependency at `cli.py:3981`
  - First 2000 chars remain useful as LLM context (consumed by `cli.py:3984`)

### R3: docs/concepts.md — Mental Model Refresh
- **Acceptance criteria:**
  - 400–550 lines (current: 418)
  - "Power meter" framing in opening
  - New "Trust Model" section (~100 lines)
  - New "How Agents Fit In" section (~80 lines)
  - New "Server vs CLI" subsection (~40 lines)
  - Reorder job staging after core concepts
  - First 2000 chars remain useful as LLM context (consumed by `cli.py:3984`)

### R4: docs/cli.md — Polish Pass
- **Acceptance criteria:**
  - Every command from `stepwise --help` is documented
  - New "Common Workflows" section with 5-6 realistic recipes
  - Cross-references to concepts.md, executors.md, writing-flows.md
  - ≤ 1,200 lines (current: 1,155)

### R5: docs/web-ui.md — New Guide (Code-Verified)
- **Acceptance criteria:**
  - 200–400 lines
  - All descriptions verified against component source
  - DAG coloring: **status-based** nodes (per `StepNode.tsx:387-394`), **executor left-border accent** (per `StepNode.tsx:20-28`)
  - View modes as `?view=` search params (per `router.tsx:51`), not separate pages
  - Port: configurable, not hardcoded 8340

### R6: docs/writing-flows.md — Flow Authorship Guide
- **Acceptance criteria:**
  - 300–500 lines
  - Every complete YAML example validates with `stepwise validate`
  - References `flow-reference.md` as canonical schema
  - 12 sections covering all executor types, wiring, control flow, caching, validation

### R7: Voice Consistency
- **Acceptance criteria:**
  - No marketing superlatives, enterprise jargon, or breathless startup energy (automated check)
  - Podcast analogies (power meter, brisket, air traffic control, harness) appear where natural

### R8: Doc Navigation & Link Integrity
- **Acceptance criteria:**
  - Repo-wide backlink audit with zero broken links
  - Every doc has "What's next" cross-links
  - `stepwise docs` command lists all new docs correctly

### R9: Troubleshooting Reference
- **Acceptance criteria:**
  - 300–400 lines (current: 228)
  - Adds engine runtime errors, agent executor errors, server errors (per `synthesize-docs-output.json` D7)
  - Each error: message pattern, cause, fix

### R10: Stale Content Cleanup
- **Acceptance criteria:**
  - `executors.md`: "four types" → "five types", poll executor section added
  - `comparison.md`: opinionated framing added, n8n section, OpenHands row
  - `web/README.md`: Vite template replaced
  - 5 stale docs removed from navigation (kept on disk)

---

## Assumptions (Verified Against Actual Files)

| # | Assumption | Verification |
|---|-----------|-------------|
| A1 | Messaging brief not yet written | `reports/docs-messaging-brief.md` does not exist; synthesis plan at `reports/plan-stepwise-synthesize-...-for.md` has 300 lines with R1-R10 structure |
| A2 | `quickstart.md` read at runtime by two code paths | `cli.py:3981`: `for name in ("quickstart", "concepts")` reads first 2000 chars for LLM context. `cli.py:3871-3914`: `cmd_docs` serves all `docs/*.md` files via `stepwise docs <topic>`. `agent_help.py:228-244`: `_append_docs_section` lists all docs in agent-help output. |
| A3 | Four docs link to `quickstart.md` by name | `cli.md:5`, `how-to/claude-code.md:13`, `how-to/codex-opencode.md:13`, `how-to/app-developer.md:13` |
| A4 | Docs audit: quickstart passes, error reference fails | `synthesize-docs-output.json` D1: "pass". D7: "fail" — no centralized error reference. |
| A5 | New-user test: hello-world is main gap | `new-user-test-output.json` confusion_points[3]: severity "moderate" |
| A6 | DAG nodes color by status, executor is left-border accent | `StepNode.tsx:387-394` (status), `StepNode.tsx:20-28` (executor accent) |
| A7 | Views are search params, not pages | `router.tsx:51`: `JOB_VIEW_VALUES = Set(["dag", "events", "timeline", "tree"])` |
| A8 | `executors.md` says "four types" (should be five) | `executors.md:1` and `:238`. `concepts.md:11` correctly lists five. |
| A9 | `flow-reference.md` is symlinked from `src/stepwise/flow-reference.md` | `readlink docs/flow-reference.md` → `../src/stepwise/flow-reference.md`. 700 lines. |
| A10 | No local `council` flow | `flows/` contains: welcome, research-proposal, test-polling, test-concurrency, eval-1-0 |
| A11 | `stepwise welcome` is interactive chooser | `cli.py:5494-5519`: prompts Browser/Terminal/Skip, then runs `@stepwise:welcome` |
| A12 | `web/README.md` is stock Vite template | 74 lines of "React + TypeScript + Vite" boilerplate |
| A13 | `docs/troubleshooting.md` covers validation but not runtime | 228 lines. Has: YAML syntax, step definition, input bindings, exit rules. Missing: engine runtime, agent failures, server errors. |

---

## Out of Scope

| # | Boundary | Rationale |
|---|----------|-----------|
| O1 | Homepage (stepwise.run) redesign | Separate deliverable |
| O2 | API reference rewrite (`docs/api.md`, 507 lines) | Scored "pass" in audit (D5) |
| O3 | Flow reference rewrite (`docs/flow-reference.md`, 700 lines) | Scored "pass" in audit (D6) |
| O4 | Screenshot/GIF creation | Requires running server with interesting data — separate task |
| O5 | Patterns doc rewrite (`docs/patterns.md`, 617 lines) | Comprehensive. Linked from writing-flows.md. |
| O6 | Version bump to 1.0 | Separate release step |
| O7 | Deleting stale doc files from disk | Navigation removal only; file deletion is a follow-up |

---

## Documentation Architecture

### Doc-Layer Model

The docs form four layers, each serving a different reader and reading mode:

```
Layer 1: ENTRY (< 30 seconds — scanning)
  README.md ─── "What is this? Should I care? How do I try it?"
    └──→ quickstart.md

Layer 2: ONBOARDING (5 minutes — following along)
  quickstart.md ─── "Install, run, create, see results"
    └──→ concepts.md, writing-flows.md

Layer 3: CONCEPTUAL (15 minutes — understanding)
  concepts.md ──── "Mental model: jobs, steps, trust, agents"
  why-stepwise.md  "Philosophy: harness, not intelligence"
  web-ui.md ────── "What the dashboard shows and means"
  writing-flows.md "How to author workflows"
  comparison.md ── "How this differs from alternatives"

Layer 4: REFERENCE (as-needed — looking up)
  cli.md ──────── "Every command, flag, exit code"
  flow-reference.md "Every YAML field"
  yaml-format.md ── "Data model mapping"
  executors.md ──── "Executor deep dive"
  patterns.md ───── "Advanced idioms"
  api.md ────────── "REST + WebSocket endpoints"
  troubleshooting.md "Error → cause → fix"
  agent-integration.md "Agent caller guide"
```

**Key constraint:** Layers 1-2 must work without API keys. The hello-world example (script-only) and welcome flow (mock executors) require zero configuration.

### Runtime Integration

Docs are not just static files — the product reads them at runtime through three paths:

```
┌─────────────────────────┐     ┌──────────────────────────────────────┐
│ stepwise docs <topic>   │────→│ cli.py:3871 cmd_docs()               │
│  (user reads docs)      │     │ get_docs_dir() → rglob("*.md")      │
│                         │     │ Serves ANY .md file by stem match    │
└─────────────────────────┘     └──────────────────────────────────────┘

┌─────────────────────────┐     ┌──────────────────────────────────────┐
│ LLM context injection   │────→│ cli.py:3976-3985                     │
│  (agent-facing commands) │     │ Reads quickstart.md[:2000] +         │
│                         │     │       concepts.md[:2000] as context  │
│                         │     │ CONSTRAINT: first 2000 chars of both │
│                         │     │ files must be high-value LLM context │
└─────────────────────────┘     └──────────────────────────────────────┘

┌─────────────────────────┐     ┌──────────────────────────────────────┐
│ stepwise agent-help     │────→│ agent_help.py:228-244                │
│  (flow catalog for CLAUDE.md) │ Lists all docs/*.md with descriptions│
│                         │     │ New docs auto-appear in agent-help   │
└─────────────────────────┘     └──────────────────────────────────────┘
```

**Implications for this plan:**
1. `quickstart.md` and `concepts.md` cannot be renamed — hardcoded at `cli.py:3981`
2. First 2000 chars (~35-40 lines) of both files are injected into LLM prompts → must be dense, self-contained summaries, not preamble
3. New docs (`web-ui.md`, `writing-flows.md`) automatically appear in `stepwise docs` and `stepwise agent-help` output — no registration needed
4. Stale docs that stay on disk will still appear in `stepwise docs` listings — acceptable since they contain valid (if outdated) information

### Reader Journey Graph

```
                    README.md
                       │
              ┌────────┴────────┐
              ▼                 ▼
        quickstart.md     why-stepwise.md
              │                 │
     ┌────────┼────────┐       │
     ▼        ▼        ▼       ▼
 concepts  writing   web-ui  comparison
     │     flows       │
     │        │        │
     ▼        ▼        ▼
  ┌──┴──┬─────┴──┬─────┴──┐
  ▼     ▼        ▼        ▼
cli  executors  flow-ref  troubleshooting
     patterns   yaml-fmt  agent-integration
                          api
```

Every downward arrow is a link in a "What's next" section. No dead ends: every Layer 3-4 doc links back up to at least one Layer 2-3 doc.

### Cross-Reference Topology

| Source doc | Links to (must exist post-overhaul) |
|-----------|--------------------------------------|
| README.md | quickstart, concepts, writing-flows, cli, web-ui, agent-integration, why-stepwise, executors, comparison, troubleshooting |
| quickstart.md | concepts, writing-flows, cli, executors, yaml-format, why-stepwise |
| concepts.md | executors, extensions, agent-integration, flow-reference, writing-flows |
| writing-flows.md | flow-reference, yaml-format, executors, patterns, agent-integration, troubleshooting |
| web-ui.md | quickstart, writing-flows, cli |
| cli.md | quickstart, troubleshooting, yaml-format, concepts, agent-integration |
| why-stepwise.md | quickstart, concepts, comparison |
| executors.md | flow-reference, concepts, writing-flows |
| comparison.md | quickstart, concepts |
| troubleshooting.md | cli, writing-flows, concepts |

### YAML Reference Strategy

Two complementary references:
- `docs/flow-reference.md` (700 lines, symlink to `src/stepwise/flow-reference.md`) — **canonical exhaustive schema**
- `docs/yaml-format.md` (606 lines) — **data-model mapping** complement

`writing-flows.md` links to `flow-reference.md` as "Full schema reference." All other docs link to `flow-reference.md` first.

### File Inventory (Complete)

**New files (3):** `docs/web-ui.md`, `docs/writing-flows.md`, `reports/docs-messaging-brief.md`

**Modified files (9):**

| File | Change scope | Layer |
|------|-------------|-------|
| `README.md` | Rewrite hero, sharpen positioning, update doc table | Entry |
| `docs/quickstart.md` | Add hello-world, directory placement note, polish | Onboarding |
| `docs/concepts.md` | Add trust model, agent taxonomy, server/CLI; reorder | Conceptual |
| `docs/cli.md` | Add workflow recipes, cross-references | Reference |
| `docs/why-stepwise.md` | Integrate podcast thesis, packaged trust, slow flow | Conceptual |
| `docs/comparison.md` | Add opinionated framing, n8n, OpenHands | Conceptual |
| `docs/executors.md` | Fix "four" → "five", add poll section | Reference |
| `docs/troubleshooting.md` | Expand with runtime, agent, server errors | Reference |
| `web/README.md` | Replace Vite template | — |

**Navigation cleanup (5 stale docs):** Remove from cross-references, keep on disk.

**Total: 14 files touched (3 new, 9 modified, 2 cleanup actions)**

---

## Implementation Steps

### Execution Tracks

Steps are organized into three parallel tracks after the shared prerequisite. The critical path is **Track A** (the reader journey: README → quickstart → concepts → new guides).

```
Step 0: Messaging Brief (prerequisite)
        │
        ├──── Track A (critical path) ──────────────────────────────┐
        │     Step 1: README                                        │
        │       │                                                   │
        │     Step 2: quickstart.md                                 │
        │       │                                                   │
        │     Step 3: concepts.md                                   │
        │       │                                                   │
        │     ├─────── Step 4: web-ui.md ──┐                       │
        │     └─────── Step 5: writing-flows.md ──┐                │
        │                                         │                │
        ├──── Track B (independent polish) ──────────────────────── │
        │     Step 6: cli.md (after Step 2,3)                      │
        │     Step 7: why-stepwise.md (after Step 0 only)          │
        │     Step 8a: executors.md (after Step 0 only)            │
        │     Step 8b: comparison.md (after Step 0 only)           │
        │                                                          │
        ├──── Track C (independent expansion) ──────────────────── │
        │     Step 9: troubleshooting.md (after Step 0 only)       │
        │                                                          │
        └──── Step 10: Navigation + link audit (after ALL above) ──┘

Checkpoint 1: After Steps 1-2     → README + quickstart validated
Checkpoint 2: After Steps 3-5     → Core reader journey complete
Checkpoint 3: After Steps 6-9     → All content complete
Checkpoint 4: After Step 10       → Links verified, navigation final
```

**Parallelization:** After Step 3 completes, Steps 4+5 (Track A tail) can run concurrently with Steps 6-9 (Tracks B+C). Steps 7, 8a, 8b, and 9 only depend on Step 0 and can run in parallel with each other and with Track A.

---

### Step 0: Write the messaging brief (prerequisite)
**Time:** ~1.5 hrs
**Files:** `reports/docs-messaging-brief.md`
**Skip condition:** File already exists.
**Fallback:** The synthesis plan at `reports/plan-stepwise-synthesize-...-for.md` lines 28-107 cites 15+ verbatim quotes with placements. Use those directly.
**Why first:** All subsequent steps need resolved positioning language.

**Gate test:**
```bash
# Brief exists and has substance
test -f reports/docs-messaging-brief.md && [ $(wc -l < reports/docs-messaging-brief.md) -ge 200 ] \
  && echo "✓ GATE 0: Brief ready" \
  || echo "✗ GATE 0: Using synthesis plan fallback"
# Fallback: verify synthesis plan has quotes
grep -c "harness" reports/plan-stepwise-synthesize-*-for.md | awk '{if($1>=3)print "✓ Fallback viable";else print "✗ No fallback"}'
```

---

### Step 1: Rewrite README.md
**Time:** ~1.5 hrs | **Files:** `README.md` | **Track:** A | **Deps:** Step 0
**Target:** ≤ 180 lines

**Changes:**
1. **Hero block** (lines 1-16): Keep logo + heading. Replace subtitle (lines 8-9) with resolved tagline.
2. **Opening** (lines 18-22): Sharpen 3am question, add "assisted anxiety" problem statement.
3. **Install** (lines 24-29): Keep as-is.
4. **Three audiences** (lines 31-58): Rewrite with podcast language (power meter, zero-infrastructure).
5. **Differentiators** (lines 60-72): Reframe as trust/delegation ("harness not intelligence," "packaged trust").
6. **YAML example** (lines 74-106): Keep.
7. **CLI table** (lines 108-123): Minor polish.
8. **Docs table** (lines 125-138): Replace with updated navigation (see cross-reference topology).
9. **Dev section** (lines 139-153): Keep.

**Acceptance test:**
```bash
# Structural
lines=$(wc -l < README.md)
[ "$lines" -le 200 ] && echo "✓ README ≤200 lines ($lines)" || echo "✗ $lines lines"

# Positioning present
grep -q "packaged trust\|harness" README.md && echo "✓ Positioning" || echo "✗ Missing positioning"
grep -q "stepwise welcome" README.md && echo "✓ Welcome cmd" || echo "✗ Missing welcome"

# No anti-patterns
for w in revolutionary game-changing groundbreaking leverage synergize; do
  grep -qi "$w" README.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done

# All doc links resolve
grep -oP '\]\((docs/[^)#]+)\)' README.md | grep -oP 'docs/[^)]+' | while read -r l; do
  test -f "$l" && echo "✓ $l" || echo "✗ BROKEN: $l"
done
```

---

### Step 2: Enhance docs/quickstart.md
**Time:** ~1 hr | **Files:** `docs/quickstart.md` | **Track:** A | **Deps:** Step 0

**Changes (additive, not rewrite):**
1. **Insert hello-world** (after line 29 "Create your own flow"): 2-step script-only example from `new-user-test-output.json` pattern.
2. **Directory placement note:** "No `.stepwise/` directory needed for basic flows."
3. **Input variable note:** Clarify `$data` in `run:` resolves from `inputs:`.
4. **Accurate welcome description:** Interactive chooser (`cli.py:5502-5508`), first step is external pause.
5. **Keep existing structure** (scored "pass" in audit).

**Acceptance test:**
```bash
# Structural
lines=$(wc -l < docs/quickstart.md)
[ "$lines" -ge 200 ] && [ "$lines" -le 400 ] && echo "✓ quickstart $lines lines" || echo "✗ $lines"

# Hello-world example present and validates
grep -q "hello" docs/quickstart.md && echo "✓ Hello-world present" || echo "✗ Missing"

# Extract and validate the hello-world YAML
python3 -c "
import re, subprocess, tempfile
with open('docs/quickstart.md') as f: content = f.read()
# Find the hello-world block (first complete flow with 'hello' in name)
blocks = re.findall(r'\`\`\`yaml\n(.*?)\`\`\`', content, re.DOTALL)
for b in blocks:
    if 'name: hello' in b and 'steps:' in b:
        with tempfile.NamedTemporaryFile(suffix='.flow.yaml', mode='w', delete=False) as f:
            f.write(b); f.flush()
            r = subprocess.run(['uv', 'run', 'stepwise', 'validate', f.name], capture_output=True, text=True)
            print('✓ Hello-world validates' if r.returncode == 0 else f'✗ Validation failed: {r.stderr.strip()}')
            break
else:
    print('✗ No hello-world YAML block found')
"

# First 2000 chars useful for LLM context (runtime dependency)
head_content=$(head -c 2000 docs/quickstart.md)
echo "$head_content" | grep -q "Install\|install" && echo "✓ First 2000 chars has install" || echo "✗ Install not in first 2000 chars"
echo "$head_content" | grep -q "stepwise run" && echo "✓ First 2000 chars has run command" || echo "✗ Run not in first 2000 chars"

# No anti-patterns
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/quickstart.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

**--- Checkpoint 1: README + quickstart validated ---**

---

### Step 3: Refresh docs/concepts.md
**Time:** ~1.5 hrs | **Files:** `docs/concepts.md` | **Track:** A | **Deps:** Step 0

**Changes:**
1. **New opening** (replace lines 1-4): "Power meter" framing.
2. **New "Trust Model" section** (~100 lines, after Executors): Human gates, scoped delegation, escalation, audit trail.
3. **New "How Agents Fit In"** (~80 lines): Agents as callers/workers/emitters.
4. **New "Server vs CLI"** (~40 lines, under Jobs): Ownership, orphan detection.
5. **Reorder job staging** (lines 36-88): Move after exit rules.
6. **Trim:** Hooks (27→10 lines + link), flows-as-tools (41→15 lines + link).
**Line budget:** 418 - 45 + 220 = 593, compress to ≤550. **Overflow valve:** If over 550, move "How Agents Fit In" to `agent-integration.md` as a new "Agent Taxonomy" section.

**Acceptance test:**
```bash
lines=$(wc -l < docs/concepts.md)
[ "$lines" -ge 400 ] && [ "$lines" -le 550 ] && echo "✓ concepts $lines lines" || echo "✗ $lines"

# New sections exist
grep -q "Trust Model\|trust model\|Packaged Trust\|packaged trust" docs/concepts.md && echo "✓ Trust model" || echo "✗ Missing"
grep -q "Agents Fit In\|agents fit\|Agent.*Caller\|agent.*caller" docs/concepts.md && echo "✓ Agent taxonomy" || echo "✗ Missing"
grep -q "Server vs CLI\|server.*cli\|CLI.*server" docs/concepts.md && echo "✓ Server vs CLI" || echo "✗ Missing"

# First 2000 chars useful for LLM context (runtime dependency)
head_content=$(head -c 2000 docs/concepts.md)
echo "$head_content" | grep -q -i "step\|job\|executor" && echo "✓ First 2000 chars has core concepts" || echo "✗ Preamble too long"

# Job staging appears AFTER exit rules
staging_line=$(grep -n "Job Staging\|job staging" docs/concepts.md | head -1 | cut -d: -f1)
exits_line=$(grep -n "Exit Rules\|exit rule" docs/concepts.md | head -1 | cut -d: -f1)
[ "$staging_line" -gt "$exits_line" ] 2>/dev/null && echo "✓ Staging after exits" || echo "✗ Staging before exits"

# No anti-patterns
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/concepts.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

---

### Step 4: Write docs/web-ui.md (code-verified)
**Time:** ~1.5 hrs | **Files:** `docs/web-ui.md` | **Track:** A | **Deps:** Steps 0, 3
**Can run in parallel with Step 5.**

**Sections** (each verified against source before writing):
1. **Opening the Dashboard:** `server start` (port 8340 default) / `--watch` (random port).
2. **Job List (JobDashboard):** Filtering, search, time ranges (`today|7d|30d` per `router.tsx:26`).
3. **DAG View** (`?view=dag`): Status-based node colors, executor left-border accent, edge types, follow-flow.
4. **View Modes** (search params `?view=dag|events|timeline|tree` per `router.tsx:51`).
5. **Step Detail Panel** (tabs `?tab=step|data-flow|job` per `router.tsx:50`). Agent streaming.
6. **External Input:** Typed form fields. Fulfill from UI, CLI, or API.
7. **Canvas View (CanvasPage):** Multi-job overview.
8. **Flow Editor (EditorPage):** CodeMirror, chat-assisted, registry browser.
9. **Flows Page (FlowsPage):** Browse local flows.
10. **Settings (SettingsPage):** API keys, model registry.

**Acceptance test:**
```bash
lines=$(wc -l < docs/web-ui.md)
[ "$lines" -ge 200 ] && [ "$lines" -le 400 ] && echo "✓ web-ui $lines lines" || echo "✗ $lines"

# Verify documented colors match source
echo "--- Executor accent verification ---"
documented_accents=$(grep -oP '(script|agent|llm|external|poll)=\w+' docs/web-ui.md | sort)
source_accents=$(grep -oP '"(script|agent|llm|external|poll)": "border-l-(\w+)' web/src/components/dag/StepNode.tsx | sed 's/"//g;s/: border-l-/=/' | sort)
[ "$documented_accents" = "$source_accents" ] 2>/dev/null && echo "✓ Accents match source" || echo "⚠ Verify accents manually"

# Verify view modes match source
grep "JOB_VIEW_VALUES" web/src/router.tsx | grep -oP '"[a-z]+"' | sort > /tmp/source_views
grep -oP '\?view=(dag|events|timeline|tree)' docs/web-ui.md | sort -u | sed 's/?view=/"/;s/$/"/' > /tmp/doc_views
diff /tmp/source_views /tmp/doc_views > /dev/null && echo "✓ View modes match" || echo "✗ View mode mismatch"

# Verify pages exist
for page in JobDashboard JobDetailPage CanvasPage EditorPage FlowsPage SettingsPage; do
  test -f "web/src/pages/${page}.tsx" && echo "✓ $page" || echo "✗ MISSING $page"
done

# No hardcoded port
grep -P "port 8340(?!.*default)" docs/web-ui.md && echo "✗ Hardcoded port" || echo "✓ Port not hardcoded"

# No anti-patterns
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/web-ui.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

---

### Step 5: Write docs/writing-flows.md
**Time:** ~1.5 hrs | **Files:** `docs/writing-flows.md` | **Track:** A | **Deps:** Steps 0, 3
**Can run in parallel with Step 4.**

**Sections** with validated YAML examples:
1. FLOW.yaml structure — minimal example, file vs directory, `stepwise new`
2. Script steps — `run:`, JSON stdout, `STEPWISE_INPUT_*` env vars
3. Agent steps — `executor: agent`, output modes, limits
4. LLM steps — `executor: llm`, model + prompt, structured output
5. External steps — `executor: external`, typed fields, trust primitive
6. Poll steps — `executor: poll`, check_command, interval
7. Wiring inputs/outputs — step.field, $job.field, optional, any_of
8. For-each — parallel sub-flows, on_error
9. Exit rules — advance/loop/escalate/abandon, escalate pattern
10. Conditional branching — `when:`, any_of merge, settlement
11. Caching — cache: true, TTL, key_extra, --rerun
12. Validation — `stepwise validate`, `stepwise check`, `stepwise preflight`

**References:** `flow-reference.md` (canonical schema), `yaml-format.md` (data model), `executors.md` (executor deep dive), `patterns.md` (advanced).

**Acceptance test:**
```bash
lines=$(wc -l < docs/writing-flows.md)
[ "$lines" -ge 300 ] && [ "$lines" -le 500 ] && echo "✓ writing-flows $lines lines" || echo "✗ $lines"

# All 12 sections present
for section in "Script" "Agent" "LLM" "External" "Poll" "Wiring\|Inputs.*Outputs\|inputs/outputs" "For-Each\|for_each" "Exit" "Conditional\|Branching" "Cach" "Validat"; do
  grep -qi "$section" docs/writing-flows.md && echo "✓ Section: $section" || echo "✗ Missing: $section"
done

# Validate ALL complete YAML snippets
python3 -c "
import re, subprocess, tempfile, os
with open('docs/writing-flows.md') as f: content = f.read()
blocks = re.findall(r'\`\`\`yaml\n(.*?)\`\`\`', content, re.DOTALL)
total = passed = 0
for i, block in enumerate(blocks):
    if 'steps:' in block and 'name:' in block:
        total += 1
        with tempfile.NamedTemporaryFile(suffix='.flow.yaml', mode='w', delete=False) as f:
            f.write(block); f.flush()
            r = subprocess.run(['uv', 'run', 'stepwise', 'validate', f.name], capture_output=True, text=True)
            if r.returncode == 0: passed += 1; print(f'  ✓ Block {i}')
            else: print(f'  ✗ Block {i}: {r.stderr.strip()[:100]}')
            os.unlink(f.name)
print(f'YAML validation: {passed}/{total} passed')
assert passed == total, f'{total-passed} blocks failed validation'
"

# References canonical schema
grep -q "flow-reference" docs/writing-flows.md && echo "✓ Links to flow-reference" || echo "✗ Missing flow-reference link"

# No anti-patterns
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/writing-flows.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

**--- Checkpoint 2: Core reader journey complete (README → quickstart → concepts → web-ui + writing-flows) ---**

---

### Step 6: Polish docs/cli.md
**Time:** ~1 hr | **Files:** `docs/cli.md` | **Track:** B | **Deps:** Steps 2, 3
**Can run in parallel with Steps 4, 5, 7, 8a, 8b, 9.**

**Changes:**
1. Add "Common Workflows" section (6 recipes) after Overview table.
2. Add cross-references to concepts.md, executors.md, writing-flows.md.
3. Verify completeness vs `stepwise --help`.
4. Verify line 5 links correct.

**Acceptance test:**
```bash
lines=$(wc -l < docs/cli.md)
[ "$lines" -le 1200 ] && echo "✓ cli $lines lines" || echo "✗ $lines (over 1200)"

# Recipes section exists
grep -q "Common Workflows\|Workflow Recipes\|common workflows" docs/cli.md && echo "✓ Recipes section" || echo "✗ Missing recipes"

# Completeness: compare documented commands against --help
uv run stepwise --help 2>/dev/null | grep -oP '^\s+\{?(\w[\w-]*)' | sort -u > /tmp/help_cmds
for cmd in run new validate check preflight jobs status output tail logs wait cancel fulfill list server config init templates schema diagram agent-help flows extensions docs cache update version welcome uninstall; do
  grep -q "$cmd" docs/cli.md && echo "✓ $cmd documented" || echo "✗ $cmd missing from cli.md"
done

# Cross-references exist
grep -q "concepts.md" docs/cli.md && echo "✓ Links to concepts" || echo "✗ Missing concepts link"
grep -q "writing-flows.md\|yaml-format.md\|flow-reference.md" docs/cli.md && echo "✓ Links to flow docs" || echo "✗ Missing flow doc link"
```

---

### Step 7: Refresh docs/why-stepwise.md
**Time:** ~1 hr | **Files:** `docs/why-stepwise.md` | **Track:** B | **Deps:** Step 0 only
**Can run in parallel with Steps 1-6, 8a, 8b, 9.**

**Changes:**
1. Integrate "The intelligence commoditizes. The harness does not."
2. Add "Packaged Trust" section after Design Principles.
3. Add "The Slow Flow Thesis" section (brisket vs microwave).
4. Sharpen competitive subsections with "structure vs intelligence" framing.
5. Keep: Step Over Role (lines 11-48), Design Principles (75-87), Who It's For (89-94), What It's Not (96-101).

**Acceptance test:**
```bash
grep -q "harness" docs/why-stepwise.md && echo "✓ Harness thesis" || echo "✗ Missing"
grep -q -i "packaged trust\|Packaged Trust" docs/why-stepwise.md && echo "✓ Packaged trust" || echo "✗ Missing"
grep -q -i "brisket\|slow flow\|flows take days" docs/why-stepwise.md && echo "✓ Slow flow thesis" || echo "✗ Missing"
grep -q "Step Over Role\|step over role" docs/why-stepwise.md && echo "✓ Step Over Role kept" || echo "✗ Removed"
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/why-stepwise.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

---

### Step 8a: Fix docs/executors.md
**Time:** ~30 min | **Files:** `docs/executors.md` | **Track:** B | **Deps:** Step 0 only
**Can run in parallel with everything except Step 10.**

**Changes:**
1. Fix line 1: "four types" → "five types" (and line 238 "all four" → "all five").
2. Add **Poll Executor** section after External (before Decorators): `check_command`, `interval_seconds`, `prompt`, JSON-on-stdout semantics. YAML example from CLAUDE.md poll section.
3. Update "Choosing an Executor" decision tree to include poll ("Waiting for an external condition to be met? → Poll executor").

**Acceptance test:**
```bash
# "five" appears, "four types" does not
grep -c "five" docs/executors.md | awk '{if($1>=2)print "✓ Says five";else print "✗ Missing"}'
grep -qi "four types\|four executor\|all four" docs/executors.md && echo "✗ Still says four" || echo "✓ No stale four"

# Poll section exists
grep -q "Poll Executor\|poll executor\|check_command" docs/executors.md && echo "✓ Poll section" || echo "✗ Missing poll"

# Decision tree includes poll
grep -A 15 "Choosing an Executor\|choosing.*executor" docs/executors.md | grep -qi "poll" && echo "✓ Poll in decision tree" || echo "✗ Poll missing from decision tree"
```

---

### Step 8b: Sharpen docs/comparison.md
**Time:** ~30 min | **Files:** `docs/comparison.md` | **Track:** B | **Deps:** Step 0 only
**Can run in parallel with everything except Step 10.**

**Changes:**
1. Add opinionated framing to opening paragraph.
2. Add n8n section.
3. Add OpenHands row to summary table.
4. Strengthen closing with "packaged trust" language.

**Acceptance test:**
```bash
grep -qi "n8n" docs/comparison.md && echo "✓ n8n section" || echo "✗ Missing n8n"
grep -qi "openhands" docs/comparison.md && echo "✓ OpenHands" || echo "✗ Missing OpenHands"
grep -qi "packaged trust\|harness" docs/comparison.md && echo "✓ Positioning" || echo "✗ Missing positioning"
for w in revolutionary game-changing leverage synergize; do
  grep -qi "$w" docs/comparison.md && echo "✗ Anti-pattern: $w" || echo "✓ No $w"
done
```

---

### Step 9: Expand docs/troubleshooting.md
**Time:** ~1 hr | **Files:** `docs/troubleshooting.md` | **Track:** C | **Deps:** Step 0 only
**Can run in parallel with everything except Step 10.**

Expand from 228 → 300-400 lines. Add categories per `synthesize-docs-output.json` D7:
1. **Engine Runtime Errors:** Cycle detection, artifact validation, exit rule evaluation, cost/duration limits.
2. **Agent Executor Errors:** Timeout, cost limit, output file missing, emit_flow parse failure.
3. **Server Errors:** Port in use, stale PID, database locked, orphan detection.
4. **CLI Errors** (expand): Server not running, project not found, flow not found, input validation.

Each error: message pattern, cause, fix.

**Source material for error messages:** Grep `engine.py`, `executors.py`, `agent.py`, `server.py`, `cli.py` for error strings, warning messages, and exception messages. Cross-reference with existing `troubleshooting.md` to avoid duplication.

**Acceptance test:**
```bash
lines=$(wc -l < docs/troubleshooting.md)
[ "$lines" -ge 300 ] && echo "✓ troubleshooting $lines lines" || echo "✗ $lines (under 300)"

# New sections present
grep -qi "Engine Runtime\|runtime error\|engine error" docs/troubleshooting.md && echo "✓ Engine section" || echo "✗ Missing"
grep -qi "Agent.*Error\|agent.*fail\|agent.*timeout" docs/troubleshooting.md && echo "✓ Agent section" || echo "✗ Missing"
grep -qi "Server.*Error\|server.*error\|port.*in use\|PID" docs/troubleshooting.md && echo "✓ Server section" || echo "✗ Missing"

# Error entries have cause+fix pattern
error_entries=$(grep -c "| \`\|Cause\|Fix\|cause\|fix" docs/troubleshooting.md)
[ "$error_entries" -ge 30 ] && echo "✓ $error_entries error entries" || echo "✗ Only $error_entries entries (need 30+)"
```

**--- Checkpoint 3: All content complete ---**

---

### Step 10: Navigation + link audit + cleanup
**Time:** ~45 min | **Files:** README.md, web/README.md, various cross-references | **Deps:** ALL prior steps

1. **Final README doc table** update.
2. **Replace `web/README.md`** with Stepwise web UI description (5-10 lines).
3. **Remove stale docs from navigation** (5 files, links only).
4. **Repo-wide link audit** (see comprehensive test below).
5. **Verify runtime integration:**
   - `stepwise docs` lists new docs (`web-ui`, `writing-flows`)
   - `cli.py:3981` still finds `quickstart.md` and `concepts.md`
   - First 2000 chars of both are high-value content

**Acceptance test (comprehensive):**
```bash
echo "=== Repo-wide broken link scan ==="
# Check ALL markdown files for broken relative links (handles ../path correctly)
find . -name '*.md' -not -path './.git/*' -not -path './node_modules/*' | while read -r file; do
  dir=$(dirname "$file")
  grep -oP '\]\(([^)#:]+\.md)\)' "$file" | grep -oP '[^(]+\.md' | while read -r link; do
    target=$(cd "$dir" && realpath -q "$link" 2>/dev/null || echo "MISSING")
    if [ ! -f "$target" ]; then
      echo "✗ BROKEN: $file → $link"
    fi
  done
done

echo ""
echo "=== Anchor link verification ==="
# Verify anchor links in cross-reference topology
for link in "concepts.md#exit-rules" "concepts.md#job-staging" "concepts.md#executors" "concepts.md#trust-model"; do
  file="docs/$(echo $link | cut -d# -f1)"
  anchor=$(echo $link | cut -d# -f2)
  # Convert anchor to heading pattern (replace - with .* for flexibility)
  pattern=$(echo "$anchor" | sed 's/-/.*/g')
  grep -qi "$pattern" "$file" 2>/dev/null && echo "✓ $link" || echo "✗ BROKEN ANCHOR: $link"
done

echo ""
echo "=== Runtime integration ==="
# Verify quickstart.md and concepts.md exist where CLI expects them
grep -q "quickstart" src/stepwise/cli.py && echo "✓ CLI references quickstart" || echo "✗"
test -f docs/quickstart.md && echo "✓ quickstart.md exists" || echo "✗ MISSING"
test -f docs/concepts.md && echo "✓ concepts.md exists" || echo "✗ MISSING"

# Verify new docs will appear in 'stepwise docs'
for doc in web-ui writing-flows; do
  test -f "docs/$doc.md" && echo "✓ $doc.md will appear in 'stepwise docs'" || echo "✗ MISSING $doc.md"
done

# Verify first 2000 chars of key docs are dense content, not preamble
for doc in quickstart concepts; do
  first_line=$(head -c 2000 "docs/$doc.md" | head -1)
  echo "  $doc.md starts with: $first_line"
done

echo ""
echo "=== Stale doc cleanup ==="
# Verify stale docs removed from README navigation
for stale in flows-vs-skills how-to-plugins how-to-skills how-to-generic-agents agent-session-continuity-proposal; do
  grep -q "$stale" README.md && echo "✗ README still links to $stale" || echo "✓ $stale removed from README"
done

echo ""
echo "=== web/README.md ==="
grep -q "Vite" web/README.md && echo "✗ Still Vite template" || echo "✓ Replaced"

echo ""
echo "=== Backlink verification ==="
for backlink in "docs/cli.md:quickstart.md" "docs/how-to/claude-code.md:../quickstart.md" "docs/how-to/codex-opencode.md:../quickstart.md" "docs/how-to/app-developer.md:../quickstart.md"; do
  file=$(echo "$backlink" | cut -d: -f1)
  target=$(echo "$backlink" | cut -d: -f2)
  grep -q "$target" "$file" && echo "✓ $file → $target" || echo "✗ BROKEN backlink: $file → $target"
done
```

**--- Checkpoint 4: Links verified, navigation final ---**

---

## Cross-Cutting Test: Voice Regression

After all steps complete, verify the runtime-facing doc surface (what LLMs and `stepwise docs` consumers see):

```bash
echo "=== Voice regression: LLM context quality ==="
# These 2000-char snippets are injected into LLM prompts by cli.py:3984
# They must be dense, actionable summaries — not marketing or meta-prose

for doc in quickstart concepts; do
  snippet=$(head -c 2000 "docs/$doc.md")

  # Must contain at least 2 code examples or commands
  code_blocks=$(echo "$snippet" | grep -c '```\|stepwise ')
  [ "$code_blocks" -ge 2 ] && echo "✓ $doc: $code_blocks code/command instances in first 2000 chars" \
    || echo "✗ $doc: only $code_blocks — needs more concrete content upfront"

  # Must NOT start with fluffy positioning language
  first_50=$(echo "$snippet" | head -c 50)
  echo "$first_50" | grep -qi "power meter\|packaged trust\|harness" \
    && echo "⚠ $doc: starts with positioning language (LLM context should lead with concrete info)" \
    || echo "✓ $doc: leads with concrete content"
done

echo ""
echo "=== Existing test suites ==="
uv run pytest tests/ -x -q 2>&1 | tail -3
cd web && npm run test -- --run 2>&1 | tail -3
```

---

## Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Source docs for messaging brief inaccessible | Medium | High | Verify file existence first. Fallback: synthesis plan has 15+ verbatim quotes. |
| R2 | Voice drift across docs | Medium | Medium | README sets tone first. Per-step voice anti-pattern tests catch drift immediately. |
| R3 | concepts.md bloats past 550 | Medium | Low | Overflow valve: move "How Agents Fit In" to `agent-integration.md`. |
| R4 | web-ui.md inaccuracy | Low | Medium | Code-first: every claim verified against source. HTML comments cite source lines. |
| R5 | writing-flows.md YAML examples invalid | Low | High | Every complete YAML block validated by `stepwise validate` in acceptance test. |
| R6 | Stale docs confuse `stepwise docs` users | Low | Low | Files stay on disk but navigation removed. Stale docs still contain valid (if dated) info. |
| R7 | LLM context degradation | Medium | Medium | Voice regression test verifies first 2000 chars of quickstart+concepts remain dense and actionable. |

---

## Commit Strategy

Each step = one commit. Track B/C steps can be committed in any order.

```
Step 0:  docs: write messaging brief for 1.0 launch
Step 1:  docs: rewrite README with packaged-trust positioning
Step 2:  docs: enhance quickstart with hello-world and polish
         ── Checkpoint 1 ──
Step 3:  docs: refresh concepts.md with trust model and agent taxonomy
Step 4:  docs: add web-ui.md guide (code-verified)
Step 5:  docs: add writing-flows.md authorship guide
         ── Checkpoint 2 ──
Step 6:  docs: polish cli.md with workflow recipes and cross-references
Step 7:  docs: refresh why-stepwise.md with podcast thesis
Step 8a: docs: fix executors.md — add poll, update count to five
Step 8b: docs: sharpen comparison.md with positioning and n8n/OpenHands
Step 9:  docs: expand troubleshooting.md with runtime and agent errors
         ── Checkpoint 3 ──
Step 10: docs: final navigation, link audit, stale doc cleanup
         ── Checkpoint 4 ──
```
