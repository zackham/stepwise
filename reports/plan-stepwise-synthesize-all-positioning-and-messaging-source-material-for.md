# Plan: Synthesize Positioning & Messaging Source Material for Stepwise 1.0 Docs Overhaul

**Date:** 2026-03-29
**Status:** Ready for implementation
**Output:** `~/work/stepwise/reports/docs-messaging-brief.md`

## Overview

Synthesize three source documents into a comprehensive messaging brief at `~/work/stepwise/reports/docs-messaging-brief.md`. The three sources are:

1. **Roadmap** (`~/work/vita/data/reports/2026-03-19-stepwise-1.0-revised-roadmap.md`) — structured GTM strategy, competitive positioning table, consulting-as-discovery model, pricing benchmarks, launch strategy with homepage design and viral mechanics.

2. **Podcast transcript** (`~/work/vita/data/podcast/transcripts/2026-03-27-the-harness-not-the-intelligence.txt`) — founder monologue containing the sharpest positioning language. Core thesis: "the intelligence commoditizes, the harness does not." Rich with metaphors (power meter, cycling, brisket, air traffic control) and emotional language about trust and delegation anxiety.

3. **Grok conversation** (`~/Downloads/Grok-_54.md`) — voice riff with Grok on GTM execution. Contains the three-column homepage concept (Agent / Human / Server with DAG-line overlay), "skill killer" flows, registry-as-viral-loop, HN timing data, the @stepwise:process-feedback meta-flow, and cold-start strategy.

The brief will be the sole input for downstream docs/README implementation jobs — it must be comprehensive enough that implementers don't need to re-read the source documents.

No code changes required. This is pure synthesis and writing.

---

## Requirements

### R1: Core Positioning Statement
- **Output:** Three-level positioning hierarchy: tagline (< 10 words), positioning statement (1-2 sentences), thesis paragraph
- **Acceptance criteria:** Must resolve the tension between existing framings — "air traffic control for AI work" (README), "power meter for agent work" (podcast), "deterministic scaffolding around nondeterministic work" (podcast), "your agent's workflow engine" (homepage), "the harness, not the intelligence" (podcast title). Must make an opinionated recommendation at each level (tagline, statement, thesis) with rationale for why that framing wins.
- **Key source material:**
  - "The intelligence commoditizes. The harness does not." (podcast)
  - "Deterministic scaffolding around nondeterministic work." (podcast)
  - "Air traffic control for AI work." (podcast)
  - "The power meter for agent work." (podcast)
  - "The 3am question answered as a URL." (podcast + README)

### R2: Competitive Differentiation Matrix
- **Output:** Opinionated differentiator table covering Temporal, LangGraph, n8n/Dify, CrewAI, OpenHands, Claude skills/hooks
- **Acceptance criteria:** Each entry states: what they are, what Stepwise does differently, the one-line "why we win here." Must go beyond `docs/comparison.md` (which is balanced) — this should be the opinionated positioning layer. Must include the podcast's sharpest line: "One starts from intelligence and adds structure. The other starts from structure and contains intelligence."
- **Key source material:**
  - Podcast direct competitive analysis for Temporal, LangGraph, n8n/Dify, OpenHands
  - Roadmap competitive table (lines 271-277)
  - Roadmap key line: "The moat is packaged trust — observable runs, scoped delegation, human gates, replays, auditability. Not the DAG. Not the YAML."
  - Grok adds CrewAI positioning and pricing context

### R3: Target Audience Description
- **Output:** Primary and secondary buyer personas with psychographic depth
- **Acceptance criteria:** For each persona: who they are, what their week looks like, what they think their problem is (vs what it actually is), what language they use, how the sale works. Must include: "they don't think 'I need workflow orchestration' — they think 'I need a better spreadsheet'" and "A Notion doc. A Slack thread. Somebody's memory."
- **Key source material:**
  - Podcast: "AI-forward founder/operator/agency with one ugly recurring process held together by duct tape"
  - Podcast: "The sale is helping them see that the problem is orchestration."
  - Grok: secondary audience = developer using Claude Code/Codex who wants reliable multi-step execution

### R4: Voice and Tone Guidelines
- **Output:** 3-5 concrete tone directives with examples of good and bad patterns
- **Acceptance criteria:** Must capture the podcast's register: conversational-but-precise, analogy-before-definition, honest about gaps, no marketing superlatives. Must include an explicit anti-patterns list.
- **Key observations from sources:**
  - Podcast voice: first-person capable, builds slowly (problem before solution), concrete before abstract, questions as structural devices
  - Anti-patterns: "autonomy theater" language ("revolutionary," "game-changing"), enterprise jargon ("leverage," "synergize"), hedging ("kind of," "sort of")
  - The podcast acknowledges gaps honestly: "I want to be very honest about what is not figured out yet" — this is the voice, not a weakness

### R5: Key Metaphors and Analogies
- **Output:** Curated catalog with usage guidance per metaphor
- **Acceptance criteria:** For each metaphor: what it is, what concept it explains, where to use it (README / docs / homepage / talks), caveats. Minimum 8 metaphors.
- **Metaphors identified:**
  1. **Power meter** (cycling) — what Stepwise adds to agents; doesn't pedal, shows the watts
  2. **Harness** — the containing structure, not the intelligence; the moat
  3. **Air traffic control** — oversight role; agents are pilots, someone routes the planes
  4. **Brisket vs microwave** — why slow flows matter; the time IS the process
  5. **Training plan** — why workflows need structure: intervals, recovery, gates
  6. **Assisted anxiety** — current state of AI delegation; you sort of trust it, check 4x
  7. **Autonomy theater** — the industry's wrong frame; faster/more autonomous isn't the goal
  8. **DAG as emotional proof** — psychologically load-bearing, not just debugging; "therapeutic tool"
  9. **Packaged trust** — the actual moat; observable runs, scoped delegation, human gates

### R6: README First-30-Seconds Strategy
- **Output:** Section-by-section evaluation of current README with keep/cut/add directives
- **Acceptance criteria:** Must define what the first 30 seconds accomplish: (a) emotional hook, (b) "what is this" clarity, (c) "can I try it now" action, (d) three-audience split. Must address whether three-column layout from Grok applies to README or only homepage.
- **Current README analysis:**
  - Hero lines 8-9 ("Air traffic control" + "Deterministic scaffolding") — **strong, keep**
  - Opening paragraph ("You gave an agent a task...") — **IS the 3am question; keep and sharpen**
  - Install block (2 commands) — **critical for wow moment; keep**
  - "Three audiences, one system" (3 subsections) — **strong structure = text equivalent of three-column; evolve**
  - "What makes it different" (6 bullets) — **evaluate each bullet**
  - Code example ("A flow in 30 seconds") — **shows product immediately; keep**

### R7: Docs Structure and Ordering
- **Output:** Prioritized doc list with P0/P1/P2 tiers and reader journey
- **Acceptance criteria:** Must reconcile 20+ existing docs with launch priorities. Must define the journey from "what is this?" to "I'm building flows." Must identify stale docs to remove/consolidate and coverage gaps.
- **Existing docs inventory:**
  - P0 candidates: quickstart, concepts, agent-integration, cli, yaml-format, executors, comparison, why-stepwise
  - P1 candidates: api, patterns, boundary, flow-sharing, flows-vs-skills, troubleshooting, how-to guides
  - Stale/consolidation candidates: `agent-session-continuity-proposal.md`, `how-to-plugins.md`, `how-to-skills.md`, `how-to-generic-agents.md`, `extensions.md`
  - Gaps: no "gallery" page for skill-killer flows, no "getting started for agent users" fast path

### R8: Verbatim Quotes and Phrases
- **Output:** 15+ annotated quotes with source, meaning, and placement recommendation
- **Acceptance criteria:** Every quote is character-for-character accurate against the source. Tagged with source (podcast/roadmap/grok) and recommended placement (README/homepage/why-stepwise/comparison/talks).
- **Already identified (non-exhaustive):**
  - "The intelligence commoditizes. The harness does not." — podcast, core tagline
  - "Deterministic scaffolding around nondeterministic work." — podcast, subtitle
  - "Most of what we call AI delegation right now is not really delegation. It is assisted anxiety." — podcast, problem statement
  - "If handing something to an AI system makes you check four times instead of zero times, then you did not really hand it off." — podcast, problem amplification
  - "One is trying to make intelligence more capable. The other is trying to make intelligence more governable." — podcast, vs agent frameworks
  - "The moat is packaged trust — observable runs, scoped delegation, human gates, replays, auditability. Not the DAG. Not the YAML." — roadmap
  - "Some flows take days. That is the point." — podcast, long-running thesis
  - "Stepwise is built for the brisket, not the microwave." — podcast
  - "The visual DAG might be a therapeutic tool. I am not joking." — podcast, emotional proof

### R9: Homepage Design Concept
- **Output:** Summary of three-column concept with narrative arc and copy suggestions
- **Acceptance criteria:** Must capture the Grok conversation's three-column layout with enough detail that a designer could implement it. Must explain how it resolves the "dual narrative" tension.
- **Key source material:**
  - Three overlapping columns with DAG-style connecting lines
  - Left: Agent — "Tell your agent: 'Plan & run these 15 fixes'" → Claude prompt → CLI call
  - Middle: Human — "You get pinged: 'Approve step 7?'" → dashboard pause button glowing
  - Right: Server — "Server keeps going — resumes after reboot, logs everything" → timeline bar, recovery toast
  - Overlay: curved DAG lines — "Your session stays clean, jobs stay alive, you're always in charge"
  - The human is the pivot. The narrative is: you're in control.

### R10: Viral Mechanics and Launch Context
- **Output:** Summary of distribution strategy that informs what docs should showcase
- **Acceptance criteria:** Captures key viral mechanics without being a full launch plan. Informs docs decisions (what to highlight, what flows to feature).
- **Key source material:**
  - Five "skill killer" flows: council, deep research, podcast, plan+implement, TBD
  - Registry as viral loop: one command to pull and run, fork/improve/contribute
  - Shareable DAG assets: GIF/PNG export, "share this run" button
  - @stepwise:process-feedback meta-flow as dogfooding-as-marketing
  - HN timing: Sunday 5AM PDT, 11.75% breakout rate

---

## Assumptions (verified against files)

| # | Assumption | Verified against |
|---|-----------|-----------------|
| A1 | Podcast is the primary voice source — ~5,500-word founder monologue with the most distilled, emotionally resonant positioning | Read transcript — single-speaker essay, not interview format. 16 paragraph-length sections covering problem → thesis → product → competition → business → gaps |
| A2 | Roadmap is authority on structured GTM — contains competitive table, consulting model, pricing, launch strategy | Read roadmap — "Podcast-Derived GTM" (lines 243-278) and "Launch Strategy" (lines 280-332) are comprehensive summaries |
| A3 | Grok conversation is authority on launch mechanics — homepage layout, viral distribution, HN timing, cold-start tactics | Read conversation — ~500 lines of voice-riff brainstorm focused on GTM execution |
| A4 | Current README already uses some of this language and structure well | Read `README.md` — "Air traffic control" + "Deterministic scaffolding" taglines (lines 8-9), three-audience structure (lines 33-58), 3am question opener (line 18) |
| A5 | `docs/comparison.md` is balanced — needs an opinionated positioning layer on top | Read comparison.md — 104 lines, thorough feature comparison, no positioning framing |
| A6 | `docs/boundary.md` is comprehensive and should be referenced, not rewritten | Read boundary.md — 121 lines covering open vs. hosted principles, scope, gray zone |
| A7 | `docs/why-stepwise.md` captures "step over role" but misses the broader trust/delegation thesis | Read why-stepwise.md — 102 lines, anti-persona framing is strong but doesn't capture harness/trust/delegation thesis |
| A8 | Several docs are stale pre-1.0 artifacts | Files exist: `agent-session-continuity-proposal.md`, `how-to-plugins.md`, `how-to-skills.md`, `how-to-generic-agents.md`, `extensions.md` |
| A9 | The brief is an input document for downstream implementation, not final docs | User spec confirms: "This brief will be the input for the docs plan-strong and implementation jobs" |
| A10 | Zack's podcast voice is authoritative; Grok's brainstorm language is not docs-grade | Podcast voice: measured, precise, honest. Grok language: "gold," "killer," "genius" — brainstorm energy, not documentation voice |

---

## Implementation Steps

### Step 1: Extract and categorize from podcast transcript
**File:** `~/work/vita/data/podcast/transcripts/2026-03-27-the-harness-not-the-intelligence.txt`

The transcript has 5 thematic phases (one continuous text, argument moves through distinct stages):

1. **Opening / Problem statement** (paragraph 1): The 3am question, "assisted anxiety," the gap between handing off and actually delegating. Key extraction: emotional language about trust failure, the body-level feeling of not really letting go.

2. **Thesis: calibrated autonomy** (paragraph 2): "Autonomy theater" critique, power meter analogy, cycling/training plan analogy, "deterministic scaffolding around nondeterministic work." Key extraction: the thesis statement, the "not maximal autonomy, not minimal" framing.

3. **Product concreteness** (paragraph 3): What Stepwise IS — CLI, web DAG viewer, editor, flows, mediation points, registry. What it is NOT — not a skill, not a hook, not an agent framework. Key extraction: "Designed to be called by agents, not to be the agent," the IS/IS NOT binary, "60-second wow moment."

4. **The slow flow thesis** (paragraph 4): "Some flows take days — that is the point." Brisket vs microwave, air traffic control, the market gap. Key extraction: the metaphors, the list of processes that can't be speed-run, "the time is the process."

5. **Competition, business, gaps** (paragraphs 5-7): Competitive analysis with honest nuance, packaged trust as moat, consulting-as-discovery, target buyer, honest gap acknowledgment, DAG as therapeutic tool. Key extraction: competitive positioning lines, moat thesis, gap acknowledgments for voice authenticity.

### Step 2: Extract from roadmap GTM sections
**File:** `~/work/vita/data/reports/2026-03-19-stepwise-1.0-revised-roadmap.md` (lines 243-332)

**"Podcast-Derived GTM" (lines 243-278):**
- Core positioning thesis (one sentence distillation)
- Consulting-as-discovery 4-step model: dogfood → services-led pilots → learn what breaks → build control plane
- Target buyer profile with pain language
- Three things to strengthen case: design partners, case study, boundary doc
- Six metrics to collect
- Competitive positioning table (4 rows)
- Key line about packaged trust

**"Launch Strategy" (lines 280-332):**
- HN timing data
- Homepage three-column design concept
- Five "skill killer" flows
- Meta-flow for processing feedback
- Shareable DAG assets as viral mechanic
- Registry as viral loop
- Pricing benchmarks and direction

### Step 3: Extract from Grok conversation
**File:** `~/Downloads/Grok-_54.md`

Key extraction zones:
- **Lines 145-165:** Initial positioning riff, business angles
- **Lines 169-206:** Business model, no-lock-in, consulting, pricing
- **Lines 213-227:** Viral mechanics, "skill killer" concept, shareable screenshots
- **Lines 233-301:** HN launch strategy, timing data, cold-start tactics
- **Lines 345-369:** Meta-flow idea, dogfooding as marketing
- **Lines 375-494:** Dual narrative problem, three-column homepage design with detailed copy

**Filter:** Use tactical ideas (homepage layout, timing data, viral mechanics, pricing). Do NOT use positioning language — Grok's "gold," "killer," "genius" don't match the podcast's measured confidence.

### Step 4: Cross-reference with current README and docs
**Files:**
- `~/work/stepwise/README.md` — identify what's already working
- `~/work/stepwise/docs/why-stepwise.md` — identify gaps between current philosophy doc and podcast thesis
- `~/work/stepwise/docs/comparison.md` — ensure competitive positioning is consistent
- `~/work/stepwise/docs/boundary.md` — ensure messaging aligns with open/hosted decisions

Identify:
- Language already in use that's strong (keep)
- Language upgradeable with sharper source material
- Structural decisions that the brief should reinforce
- Gaps where source material has content but docs don't yet

### Step 5: Write the messaging brief
**Output:** `~/work/stepwise/reports/docs-messaging-brief.md`

Structure with 10 sections mapping to R1-R10:

```
# Stepwise 1.0 Docs Messaging Brief

## 1. Core Positioning
## 2. Competitive Positioning
## 3. Target Audience
## 4. Voice & Tone Guidelines
## 5. Metaphor Toolkit
## 6. README First-30-Seconds Strategy
## 7. Docs Structure & Ordering
## 8. Homepage Design Concept
## 9. Viral Mechanics & Launch Context
## 10. Verbatim Quotes & Phrases
```

Each section:
- Leads with the synthesized recommendation
- Includes source attribution for key claims
- Provides explicit guidance for downstream implementers
- Notes what's already in current docs vs what needs to change

### Step 6: Quality review
- Every requirement (R1-R10) maps to a section with substantive content
- No contradictions between sections
- Verbatim quotes are actually verbatim (spot-check against source files)
- Voice guidelines are consistent with the voice demonstrated in the podcast
- Brief is self-contained for downstream implementers
- The "dual narrative" tension is explicitly resolved

---

## Testing Strategy

This is a writing/synthesis deliverable, not code. Validation:

```bash
# Verify output file exists with substantial content
wc -l ~/work/stepwise/reports/docs-messaging-brief.md
# Expected: 300+ lines

# Verify all major sections present
grep -c "^## " ~/work/stepwise/reports/docs-messaging-brief.md
# Expected: 10-12

# Spot-check key phrases from sources appear in brief
grep -c "harness" ~/work/stepwise/reports/docs-messaging-brief.md        # expect 5+
grep -c "power meter" ~/work/stepwise/reports/docs-messaging-brief.md    # expect 2+
grep -c "commoditize" ~/work/stepwise/reports/docs-messaging-brief.md    # expect 2+
grep -c "brisket" ~/work/stepwise/reports/docs-messaging-brief.md        # expect 2+
grep -c "3am" ~/work/stepwise/reports/docs-messaging-brief.md            # expect 2+
grep -c "assisted anxiety" ~/work/stepwise/reports/docs-messaging-brief.md  # expect 1+
grep -c "packaged trust" ~/work/stepwise/reports/docs-messaging-brief.md    # expect 2+
grep -c "skill killer" ~/work/stepwise/reports/docs-messaging-brief.md      # expect 1+

# Verify competitive matrix covers all named competitors
grep -i "temporal" ~/work/stepwise/reports/docs-messaging-brief.md
grep -i "langgraph" ~/work/stepwise/reports/docs-messaging-brief.md
grep -i "n8n" ~/work/stepwise/reports/docs-messaging-brief.md
grep -i "crewai" ~/work/stepwise/reports/docs-messaging-brief.md
grep -i "openhands" ~/work/stepwise/reports/docs-messaging-brief.md

# Verify no code changes were made
cd ~/work/stepwise && git diff --name-only HEAD
# Should show no source code modifications — only untracked report files
```

### Manual quality checks (by reviewer):
1. **Positioning coherence:** Does the core statement in section 1 flow naturally into competitive matrix in section 2 and metaphor toolkit in section 5?
2. **Voice fidelity:** Would following tone guidelines in section 4 produce docs that sound like the podcast? Not marketing-speak, not enterprise jargon, not hype.
3. **Implementer self-sufficiency:** Could someone write the README using only this brief and the codebase, without re-reading the three source documents?
4. **Honest gaps preserved:** Does the brief acknowledge what's not figured out (no pricing tests, no design partners, no public case study) — matching the podcast's honesty?
5. **Dual narrative resolved:** Does the brief explicitly state how to handle agent-orchestrator vs background-infra tension, and does the resolution (three-column, "you're in control") flow through all relevant sections?

---

## Execution Notes

- **Podcast is the voice bible.** ~5,500 words of carefully articulated positioning. The brief should read like it was written by the same person who recorded this episode. When in doubt between a Grok phrasing and a podcast phrasing, use the podcast.
- **Grok is tactical, not strategic.** Use the homepage layout, viral mechanics, timing data, and pricing benchmarks. Don't use Grok's positioning language — "gold," "killer," "genius" are brainstorm energy, not documentation voice.
- **The "dual narrative" tension is real and resolved.** Agent-orchestrator vs background-infra is confusing if you pick one. The three-column concept (Agent / Human / Server) with the human as pivot resolves it visually and narratively. The brief must make this resolution explicit and usable.
- **The current README is already quite good.** The brief should validate what's working (3am question opener, three-audience structure, install-in-2-commands) and identify specific upgrades, not propose a wholesale rewrite.
- **"Some flows take days" is not an apology — it's the feature.** This is one of the sharpest positioning insights. The brief must frame this as a differentiator, not a limitation.
- **Estimated output size:** 300-400 lines of structured reference material. Dense, not fluffy. Every section should be actionable.
